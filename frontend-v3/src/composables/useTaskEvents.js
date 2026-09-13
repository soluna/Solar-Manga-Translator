/** One application task connection; navigation never replays a start command. */
import { computed, ref } from 'vue'
import { apiGetJson, apiPostJson, createApiWebSocket } from '../api/client.js'
import { deriveTaskEventUpdate, createEmptyTaskPhase } from '../state/task-event-state.js'

export const TERMINAL_TASK_EVENTS = new Set(['completed', 'failed', 'error', 'cancelled', 'interrupted'])
const emptyState = () => ({
  activeTaskId: '', activeTaskSequence: 0, activeAction: '', activeTaskTargetStoredName: '',
  phase: createEmptyTaskPhase(), progress: null, statusMessage: '', eventName: '', result: null,
})

export function createTaskEvents({
  createSocket = createApiWebSocket, getJson = apiGetJson,
  postJson = apiPostJson,
  setTimer = (fn, ms) => setTimeout(fn, ms), clearTimer = (id) => clearTimeout(id),
  maxReconnectAttempts = 20,
} = {}) {
  const sessionId = ref('')
  const connected = ref(false)
  const taskState = ref(emptyState())
  const batch = ref(null)
  const busy = computed(() => Boolean(batch.value) || (Boolean(taskState.value.eventName)
    && !TERMINAL_TASK_EVENTS.has(taskState.value.eventName)))
  let socket = null, timer = null, epoch = 0, attempts = 0, intent = null
  let batchTimer = null, cancelPromise = null
  // A task snapshot can repeat the terminal event already seen on its socket.
  // Keep this by task ID so a completed page advances a batch only once.
  const terminalTasks = new Map()

  function clearReconnect() {
    if (timer !== null) clearTimer(timer)
    timer = null
  }
  function closeSocket() {
    const previous = socket
    socket = null
    connected.value = false
    try { previous?.close() } catch { /* Already closed. */ }
  }
  function disconnect(keepBatch = false) {
    if (!keepBatch) {
      batch.value = null
      if (batchTimer !== null) clearTimer(batchTimer)
      batchTimer = null
    }
    epoch += 1
    clearReconnect()
    intent = null
    closeSocket()
  }
  function finish(event, message = '') {
    const taskId = String(taskState.value.activeTaskId || '')
    if (taskId && terminalTasks.has(taskId)) return
    if (taskId) terminalTasks.set(taskId, { event, sequence: taskState.value.activeTaskSequence })
    intent = null
    clearReconnect()
    closeSocket()
    taskState.value = { ...taskState.value, eventName: event,
      statusMessage: message || taskState.value.statusMessage }
    const sequence = batch.value
    if (!sequence) return
    if (event !== 'completed' || !sequence.remaining.length) {
      batch.value = null
      return
    }
    sequence.completed += 1
    const nextPage = sequence.remaining.shift()
    taskState.value = { ...taskState.value, eventName: 'batch-next', statusMessage: '正在准备下一页…' }
    batchTimer = setTimer(() => {
      batchTimer = null
      if (batch.value === sequence) startOne(sequence.projectId, sequence.action, sequence.config, nextPage)
    }, 0)
  }
  function applyRaw(payload) {
    const currentId = taskState.value.activeTaskId
    if (currentId && payload.task_id && currentId !== payload.task_id) return
    const update = deriveTaskEventUpdate(payload, taskState.value)
    if (update.ignore) return
    taskState.value = { ...taskState.value, ...update,
      phase: update.phase || taskState.value.phase,
      progress: update.progress || taskState.value.progress,
      result: payload.session_id ? payload : taskState.value.result,
    }
    if (update.activeTaskId && intent) intent = { kind: 'subscribe', taskId: update.activeTaskId }
    if (TERMINAL_TASK_EVENTS.has(update.eventName)) finish(update.eventName)
  }
  function applySnapshot(task) {
    if (!task?.task_id) return false
    const taskId = String(task.task_id)
    const status = String(task.status || '').trim().toLowerCase()
    const different = taskState.value.activeTaskId !== taskId
    const knownTerminal = terminalTasks.get(taskId)
    // Reconnecting can return the same completed snapshot after finish() has
    // already advanced a batch. Preserve batch-next/current terminal state and
    // never schedule the next page twice.
    if (!different && knownTerminal && (TERMINAL_TASK_EVENTS.has(status)
      || TERMINAL_TASK_EVENTS.has(taskState.value.eventName))) {
      if (taskState.value.eventName === 'batch-next') return true
      taskState.value = { ...taskState.value,
        activeTaskId: taskId,
        activeAction: String(task.action || taskState.value.activeAction || ''),
        activeTaskTargetStoredName: String(task.metadata?.target_stored_name
          || task.target_stored_name || taskState.value.activeTaskTargetStoredName || ''),
        eventName: knownTerminal.event,
        statusMessage: String(task.message || taskState.value.statusMessage || ''),
      }
      return true
    }
    taskState.value = { ...(different ? emptyState() : taskState.value),
      activeTaskId: taskId, activeAction: String(task.action || ''),
      activeTaskTargetStoredName: String(task.metadata?.target_stored_name || task.target_stored_name || ''),
      eventName: 'running',
    }
    intent = { kind: 'subscribe', taskId }
    for (const event of task.events || []) applyRaw(event)
    if (TERMINAL_TASK_EVENTS.has(status)
      && taskState.value.eventName !== 'batch-next'
      && !TERMINAL_TASK_EVENTS.has(taskState.value.eventName)) finish(status, task.message || '')
    return true
  }
  function scheduleReconnect() {
    if (!intent || timer !== null) return
    if (++attempts > maxReconnectAttempts) {
      finish('interrupted', '任务连接恢复失败。请重新查询后台任务；未重新发起处理。')
      return
    }
    taskState.value = { ...taskState.value, statusMessage: '连接中断，正在同步后台任务…' }
    const expected = epoch
    timer = setTimer(async () => {
      timer = null
      if (expected !== epoch || !intent) return
      // The transport never opened, so the command has not left this client.
      // A project snapshot here could belong to an unrelated previous run.
      if (intent.kind === 'start') { open(); return }
      try {
        const activeId = taskState.value.activeTaskId
        const response = await getJson(activeId
          ? `/api/tasks/${encodeURIComponent(activeId)}`
          : `/api/projects/${encodeURIComponent(sessionId.value)}/task`)
        if (expected !== epoch || !intent) return
        const task = activeId ? response : response?.task
        if (!applySnapshot(task)) throw new Error('后台尚未返回任务')
        if (intent && busy.value) open()
      } catch {
        if (expected === epoch) scheduleReconnect()
      }
    }, Math.min(750 * 2 ** (attempts - 1), 10000))
  }
  function open() {
    const expected = epoch
    let current
    try {
      current = createSocket(`/ws/translate/${encodeURIComponent(sessionId.value)}`)
      socket = current
    } catch (error) {
      if (error?.code === 'MOCK_READ_ONLY') finish('error', error.message)
      else scheduleReconnect()
      return
    }
    const isCurrent = () => expected === epoch && current === socket
    current.onopen = () => {
      if (!isCurrent() || !intent) return
      connected.value = true
      if (intent.kind === 'subscribe') {
        current.send(JSON.stringify({ task_id: intent.taskId, after_sequence: taskState.value.activeTaskSequence }))
      } else if (intent.kind === 'start') {
        const command = intent.command
        // Consume before send: even an uncertain acknowledgement must recover by task ID.
        intent = { kind: 'recover' }
        current.send(JSON.stringify(command))
      }
    }
    current.onmessage = (event) => {
      if (!isCurrent()) return
      let payload
      try { payload = JSON.parse(String(event.data)) } catch { return }
      attempts = 0
      applyRaw(payload)
    }
    current.onerror = () => { /* onclose owns recovery. */ }
    current.onclose = () => {
      if (!isCurrent()) return
      socket = null
      connected.value = false
      if (intent) scheduleReconnect()
    }
  }
  function start(projectId, action, config = {}, pageId = '') {
    if (busy.value) throw new Error('请先等待或停止当前任务。')
    terminalTasks.clear()
    batch.value = null
    startOne(projectId, action, config, pageId)
  }
  function startOne(projectId, action, config = {}, pageId = '') {
    if (!projectId || !action) throw new Error('缺少项目或任务类型。')
    disconnect(true)
    sessionId.value = String(projectId)
    taskState.value = { ...emptyState(), eventName: 'starting', activeAction: action,
      activeTaskTargetStoredName: pageId, statusMessage: '正在启动任务…' }
    attempts = 0
    intent = { kind: 'start', command: { action, config: JSON.parse(JSON.stringify(config)),
      ...(pageId ? { target_stored_name: pageId } : {}) } }
    open()
  }
  function startBatch(projectId, action, config, pageIds) {
    if (busy.value) throw new Error('请先等待或停止当前任务。')
    const remaining = [...new Set((pageIds || []).map(String).filter(Boolean))]
    if (!projectId || !action || !remaining.length) throw new Error('没有可处理的页面。')
    terminalTasks.clear()
    disconnect()
    const first = remaining.shift()
    batch.value = { projectId, action, config: JSON.parse(JSON.stringify(config || {})),
      remaining, completed: 0, total: remaining.length + 1 }
    startOne(projectId, action, config, first)
  }
  function subscribe(projectId, taskId) {
    terminalTasks.clear()
    disconnect()
    sessionId.value = String(projectId)
    taskState.value = { ...emptyState(), activeTaskId: String(taskId), eventName: 'running' }
    attempts = 0
    intent = { kind: 'subscribe', taskId: String(taskId) }
    open()
  }
  function cancel() {
    if (cancelPromise) return cancelPromise
    const activeTaskId = String(taskState.value.activeTaskId || '')
    const waitingForNext = Boolean(batch.value && (batchTimer !== null || taskState.value.eventName === 'batch-next'))
    const commandAwaitingTaskId = intent?.kind === 'recover' && !activeTaskId
    const sequence = batch.value
    // Cancel the local batch immediately. This closes the race between a
    // completed page and the timer that would otherwise start the next page.
    if (sequence) batch.value = null
    if (batchTimer !== null) clearTimer(batchTimer)
    batchTimer = null
    // The start command has already crossed the WebSocket boundary. We cannot
    // honestly claim cancellation until the backend gives us its task ID.
    // Stop the local batch queue, retain the connection and let the next task
    // event make the real cancel request possible.
    if (commandAwaitingTaskId) {
      taskState.value = { ...taskState.value,
        statusMessage: '任务已发送，正在确认后台任务；确认后可取消。' }
      return Promise.resolve(false)
    }
    if (!activeTaskId || waitingForNext || taskState.value.eventName === 'starting') {
      intent = null
      epoch += 1
      clearReconnect()
      closeSocket()
      taskState.value = { ...taskState.value, eventName: 'cancelled', statusMessage: '任务已停止。' }
      cancelPromise = Promise.resolve(true).finally(() => { cancelPromise = null })
      return cancelPromise
    }
    cancelPromise = (async () => {
      await postJson(`/api/tasks/${encodeURIComponent(activeTaskId)}/cancel`, {}, '取消任务失败')
      return true
    })().finally(() => { cancelPromise = null })
    return cancelPromise
  }
  async function resume(projectId) {
    if (!projectId) return false
    if (batch.value) return sessionId.value === projectId
    if (busy.value && sessionId.value === projectId && (socket || timer !== null)) return true
    // Keep another project's active connection visible; do not detach its task.
    if (busy.value && taskState.value.eventName !== 'syncing' && sessionId.value !== projectId) return false
    disconnect()
    sessionId.value = String(projectId)
    taskState.value = { ...emptyState(), eventName: 'syncing', statusMessage: '正在同步后台任务…' }
    const expected = epoch
    try {
      const response = await getJson(`/api/projects/${encodeURIComponent(projectId)}/task`)
      if (expected !== epoch) return false
      if (!applySnapshot(response?.task)) { taskState.value = emptyState(); return false }
      attempts = 0
      if (intent && busy.value) open()
      return busy.value
    } catch (error) {
      if (expected === epoch) finish('error', error.message || '无法读取后台任务。')
      return false
    }
  }
  function reset() { disconnect(); terminalTasks.clear(); taskState.value = emptyState(); sessionId.value = '' }
  return { sessionId, connected, busy, taskState, batch, start, startBatch, subscribe, resume, cancel, disconnect, shutdown: disconnect, reset }
}

let shared = null
export function useTaskEvents() {
  if (!shared) shared = createTaskEvents()
  // Views release their presentation, not the application's background subscription.
  return { ...shared, disconnect() {} }
}
