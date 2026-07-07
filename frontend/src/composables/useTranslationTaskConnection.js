import { createEmptyTaskPhase } from '../task-event-state.js'

const TERMINAL_TASK_STATUSES = new Set(['completed', 'failed', 'cancelled'])

export function useTranslationTaskConnection({
  refs,
  createApiWebSocket,
  buildRuntimeConfig,
  apiFetch,
  toApiUrl,
  handleTaskEvent,
  clearTaskError,
  getBackendLogHint,
  setTimeoutFn = defaultSetTimeout,
  clearTimeoutFn = defaultClearTimeout,
  logger = console,
  maxReconnectAttempts = 20,
}) {
  let socket = null
  const expectedClosingSockets = new WeakSet()
  let reconnectTimer = null
  let reconnectAttempts = 0

  function clearReconnectTimer() {
    if (reconnectTimer != null) {
      clearTimeoutFn(reconnectTimer)
      reconnectTimer = null
    }
  }

  function resetActiveTaskConnection() {
    clearReconnectTimer()
    reconnectAttempts = 0
    refs.activeTaskId.value = ''
    refs.activeTaskSequence.value = 0
    refs.activeTaskTargetStoredName.value = ''
    refs.activeTaskPhase.value = createEmptyTaskPhase()
  }

  function closeSocket(targetSocket = socket) {
    if (targetSocket) {
      const socketToClose = targetSocket
      if (socket === socketToClose) {
        socket = null
      }
      expectedClosingSockets.add(socketToClose)
      socketToClose.close()
    }
  }

  function getSocket() {
    return socket
  }

  function hasSocket() {
    return Boolean(socket)
  }

  function connect({
    action = refs.activeAction.value,
    pageTargetStoredName = refs.activeTaskTargetStoredName.value,
    taskId = refs.activeTaskId.value,
  } = {}) {
    if (!refs.sessionId.value || !refs.translating.value) {
      return null
    }

    clearReconnectTimer()
    const currentSocket = createApiWebSocket(`/ws/translate/${refs.sessionId.value}`)
    socket = currentSocket

    currentSocket.onopen = () => {
      if (taskId) {
        currentSocket.send(JSON.stringify({
          task_id: taskId,
          after_sequence: refs.activeTaskSequence.value,
        }))
        refs.status.value = '已重新连接，正在同步任务进度…'
        return
      }
      currentSocket.send(JSON.stringify({
        action,
        config: buildRuntimeConfig(),
        target_stored_name: pageTargetStoredName || undefined,
      }))
    }

    currentSocket.onmessage = (event) => {
      if (currentSocket !== socket) {
        return
      }
      try {
        const payload = JSON.parse(event.data)
        reconnectAttempts = 0
        void handleTaskEvent(payload, currentSocket)
      } catch (error) {
        logger.warn?.('[TranslationTask] invalid task event', error)
      }
    }

    currentSocket.onerror = () => {
      if (currentSocket !== socket || expectedClosingSockets.has(currentSocket)) {
        return
      }
      refs.status.value = '任务仍在后台运行，连接中断后正在恢复…'
    }

    currentSocket.onclose = () => {
      if (expectedClosingSockets.has(currentSocket)) {
        expectedClosingSockets.delete(currentSocket)
        return
      }
      if (currentSocket !== socket) {
        return
      }
      socket = null
      if (refs.translating.value) {
        scheduleReconnect()
      }
    }

    return currentSocket
  }

  function scheduleReconnect() {
    if (!refs.translating.value || !refs.sessionId.value) {
      return null
    }
    clearReconnectTimer()
    reconnectAttempts += 1
    if (reconnectAttempts > maxReconnectAttempts) {
      refs.translating.value = false
      refs.errorMessage.value = `无法重新连接后台任务。${getBackendLogHint()}`
      refs.status.value = '任务连接恢复失败。'
      resetActiveTaskConnection()
      return { exhausted: true }
    }

    refs.status.value = `任务仍在后台运行，正在重新连接（${reconnectAttempts}/${maxReconnectAttempts}）…`
    const delayMs = Math.min(5000, 750 + reconnectAttempts * 250)
    reconnectTimer = setTimeoutFn(() => {
      reconnectTimer = null
      void reconnect()
    }, delayMs)
    return { exhausted: false, attempts: reconnectAttempts, delayMs }
  }

  async function reconnect() {
    if (!refs.translating.value || !refs.sessionId.value) {
      return false
    }
    try {
      if (!refs.activeTaskId.value) {
        const task = await fetchProjectTask(refs.sessionId.value)
        if (!task?.task_id) {
          throw new Error('后台没有找到可恢复的任务')
        }
        refs.activeTaskId.value = String(task.task_id)
        refs.activeAction.value = String(task.action || refs.activeAction.value)
        refs.activeTaskTargetStoredName.value = String(task.metadata?.target_stored_name || '')
        for (const event of Array.isArray(task.events) ? task.events : []) {
          await handleTaskEvent(event, null)
        }
        reconnectAttempts = 0
        if (!refs.translating.value || TERMINAL_TASK_STATUSES.has(String(task.status))) {
          return false
        }
      }
      connect()
      return true
    } catch (error) {
      logger.warn?.('[TranslationTask] reconnect failed', error)
      scheduleReconnect()
      return false
    }
  }

  async function resumeProjectTaskSubscription(projectId) {
    const normalizedProjectId = String(projectId || '').trim()
    if (!normalizedProjectId) {
      return false
    }
    try {
      const task = await fetchProjectTask(normalizedProjectId)
      if (!task?.task_id || TERMINAL_TASK_STATUSES.has(String(task.status))) {
        return false
      }

      resetActiveTaskConnection()
      clearTaskError()
      refs.translating.value = true
      refs.activeTaskId.value = String(task.task_id)
      refs.activeAction.value = String(task.action || 'translate')
      refs.activeTaskTargetStoredName.value = String(task.metadata?.target_stored_name || '')
      refs.progress.value = { current: 0, total: 0 }
      for (const event of Array.isArray(task.events) ? task.events : []) {
        await handleTaskEvent(event, null)
      }
      reconnectAttempts = 0
      if (refs.translating.value) {
        connect()
      }
      return true
    } catch (error) {
      logger.warn?.('[TranslationTask] failed to resume project task', error)
      return false
    }
  }

  async function cancelActiveTask() {
    if (!refs.translating.value || !refs.activeTaskId.value) {
      return false
    }
    refs.status.value = '正在停止当前任务…'
    try {
      const response = await apiFetch(
        toApiUrl(`/api/tasks/${encodeURIComponent(refs.activeTaskId.value)}/cancel`),
        { method: 'POST' },
      )
      const payload = await response.json()
      if (!response.ok) {
        throw new Error(payload.detail || '停止任务失败')
      }
      if (!socket) {
        scheduleReconnect()
      }
      return true
    } catch (error) {
      refs.errorMessage.value = error instanceof Error ? error.message : '停止任务失败'
      refs.status.value = '停止任务失败，后台任务可能仍在运行。'
      return false
    }
  }

  async function fetchProjectTask(projectId) {
    const response = await apiFetch(
      toApiUrl(`/api/projects/${encodeURIComponent(projectId)}/task`),
    )
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '查询后台任务失败')
    }
    return payload?.task
  }

  return {
    cancelActiveTask,
    clearReconnectTimer,
    closeSocket,
    connect,
    getSocket,
    hasSocket,
    reconnect,
    resetActiveTaskConnection,
    resumeProjectTaskSubscription,
    scheduleReconnect,
  }
}

function defaultSetTimeout(callback, delayMs) {
  return window.setTimeout(callback, delayMs)
}

function defaultClearTimeout(timerId) {
  window.clearTimeout(timerId)
}
