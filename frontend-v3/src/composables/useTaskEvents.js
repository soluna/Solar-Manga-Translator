/** 任务事件连接：连接 /ws/translate/{sessionId}，订阅已有任务或启动新任务。 */
import { ref } from 'vue'
import { createApiWebSocket } from '../api/client.js'
import { deriveTaskEventUpdate, createEmptyTaskPhase } from '../state/task-event-state.js'

const TERMINAL_EVENTS = new Set(['completed', 'failed', 'error', 'cancelled', 'interrupted'])

export function useTaskEvents() {
  const sessionId = ref('')
  const connected = ref(false)
  const taskState = ref({
    activeTaskId: '',
    activeTaskSequence: 0,
    activeAction: 'translate',
    activeTaskTargetStoredName: '',
    phase: createEmptyTaskPhase(),
    progress: null,
    statusMessage: '',
    eventName: '',
  })

  let socket = null
  let reconnectTimer = null
  let reconnectAttempts = 0
  let pendingStart = null // {action, config, target_stored_name}
  let pendingSubscribe = null // {task_id}

  function clearReconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    reconnectAttempts = 0
  }

  function applyRaw(payload) {
    const update = deriveTaskEventUpdate(payload, taskState.value)
    if (update?.ignore) {
      return
    }
    taskState.value = {
      activeTaskId: update.activeTaskId,
      activeTaskSequence: update.activeTaskSequence,
      activeAction: update.activeAction,
      activeTaskTargetStoredName: update.activeTaskTargetStoredName,
      phase: update.phase || taskState.value.phase,
      progress: update.progress || taskState.value.progress,
      statusMessage: update.statusMessage,
      eventName: update.eventName,
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer || reconnectAttempts > 30) {
      return
    }
    const delay = Math.min(2000 * 2 ** reconnectAttempts, 15000)
    reconnectAttempts += 1
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      open()
    }, delay)
  }

  function open() {
    const current = createApiWebSocket(`/ws/translate/${sessionId.value}`)
    socket = current

    current.onopen = () => {
      connected.value = true
      if (pendingSubscribe) {
        current.send(JSON.stringify({
          task_id: pendingSubscribe.task_id,
          after_sequence: taskState.value.activeTaskSequence,
        }))
      } else if (pendingStart) {
        current.send(JSON.stringify({
          action: pendingStart.action,
          config: pendingStart.config || {},
          target_stored_name: pendingStart.target_stored_name || undefined,
        }))
      }
    }

    current.onmessage = (event) => {
      try {
        const payload = JSON.parse(String(event.data || '{}'))
        reconnectAttempts = 0
        applyRaw(payload)
        if (TERMINAL_EVENTS.has(String(payload.event || ''))) {
          // 任务结束：保持连接（等待可能的 start 事件不需要），断开。
          connected.value = false
          clearReconnect()
          try {
            current.close()
          } catch {
            /* ignore */
          }
        }
      } catch {
        /* 忽略无法解析的事件 */
      }
    }

    current.onerror = () => {
      /* 由 onclose 统一处理 */
    }

    current.onclose = () => {
      if (current !== socket) {
        return
      }
      socket = null
      connected.value = false
      if (pendingSubscribe || pendingStart) {
        scheduleReconnect()
      }
    }
  }

  /** 订阅一个进行中的任务（task_id），不断开前不再次发起。 */
  function subscribe(targetSessionId, taskId) {
    disconnect()
    sessionId.value = targetSessionId
    pendingStart = null
    pendingSubscribe = { task_id: taskId }
    open()
  }

  /** 启动新任务 action（detect/translate/translate-page/rerender/...）。 */
  function start(targetSessionId, action, config = {}, targetStoredName = '') {
    disconnect()
    sessionId.value = targetSessionId
    pendingSubscribe = null
    taskState.value = { ...taskState.value, activeTaskId: '' }
    pendingStart = { action, config, target_stored_name: targetStoredName || '' }
    open()
  }

  function disconnect() {
    clearReconnect()
    pendingStart = null
    pendingSubscribe = null
    if (socket) {
      const target = socket
      socket = null
      try {
        target.close()
      } catch {
        /* ignore */
      }
    }
    connected.value = false
  }

  function reset() {
    disconnect()
    taskState.value = {
      activeTaskId: '',
      activeTaskSequence: 0,
      activeAction: 'translate',
      activeTaskTargetStoredName: '',
      phase: createEmptyTaskPhase(),
      progress: null,
      statusMessage: '',
      eventName: '',
    }
  }

  return { connected, taskState, subscribe, start, disconnect, reset }
}