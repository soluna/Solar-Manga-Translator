import {
  getTaskFailureStatus,
  getTaskProgressStatus,
  getTaskStartStatus,
  normalizeTaskAction,
} from './workflow-state.js'

export function createEmptyTaskPhase() {
  return {
    label: '',
    index: 0,
    total: 0,
    scopeLabel: '',
    current: 0,
    progressTotal: 0,
  }
}

export function deriveTaskEventUpdate(payload, currentState = {}) {
  const eventPayload = payload && typeof payload === 'object' ? payload : {}
  const payloadTaskId = cleanString(eventPayload.task_id)
  const payloadSequence = safeNumber(eventPayload.sequence)
  const currentTaskId = cleanString(currentState.activeTaskId)
  const currentSequence = safeNumber(currentState.activeTaskSequence)

  if (
    payloadTaskId
    && payloadTaskId === currentTaskId
    && payloadSequence > 0
    && payloadSequence <= currentSequence
  ) {
    return { ignore: true }
  }

  const activeTaskId = payloadTaskId || currentTaskId
  const activeTaskSequence = payloadSequence > 0
    ? Math.max(currentSequence, payloadSequence)
    : currentSequence
  const activeAction = eventPayload.task_action || eventPayload.action
    ? normalizeTaskAction(eventPayload.task_action || eventPayload.action)
    : cleanString(currentState.activeAction) || 'translate'
  const targetFromPayload = cleanString(eventPayload.metadata?.target_stored_name)
  const activeTaskTargetStoredName = targetFromPayload || cleanString(currentState.activeTaskTargetStoredName)
  const phase = deriveTaskPhase(eventPayload)
  const eventName = cleanString(eventPayload.event)

  return {
    ignore: false,
    eventName,
    activeTaskId,
    activeTaskSequence,
    activeAction,
    activeTaskTargetStoredName,
    phase,
    progress: deriveTaskProgress(eventPayload, eventName),
    statusMessage: deriveTaskStatusMessage(eventPayload, {
      eventName,
      activeAction,
      activeTaskTargetStoredName,
    }),
  }
}

export function deriveTaskPhase(payload) {
  const eventPayload = payload && typeof payload === 'object' ? payload : {}
  const phaseLabel = cleanString(eventPayload.phase_label || eventPayload.workflow_phase)
  const phaseIndex = safeNumber(eventPayload.phase_index)
  const phaseTotal = safeNumber(eventPayload.phase_total)
  const scopeLabel = cleanString(eventPayload.scope_label)
  const progressCurrent = safeNumber(eventPayload.progress_current || eventPayload.current)
  const progressTotal = safeNumber(eventPayload.progress_total || eventPayload.total)

  if (!phaseLabel && !phaseIndex && !progressCurrent) {
    return null
  }

  return {
    label: phaseLabel,
    index: phaseIndex,
    total: phaseTotal,
    scopeLabel,
    current: progressCurrent,
    progressTotal,
  }
}

function deriveTaskProgress(payload, eventName) {
  if (eventName === 'start') {
    return {
      current: 0,
      total: safeNumber(payload.progress_total || payload.total_pages),
    }
  }
  if (eventName === 'progress') {
    return {
      current: safeNumber(payload.progress_current || payload.current),
      total: safeNumber(payload.progress_total || payload.total),
    }
  }
  return null
}

function deriveTaskStatusMessage(payload, {
  eventName,
  activeAction,
  activeTaskTargetStoredName,
}) {
  const explicitMessage = cleanString(payload.message || payload.default_message)
  if (explicitMessage) {
    return explicitMessage
  }

  if (eventName === 'start') {
    return getTaskStartStatus(activeAction, {
      totalPages: safeNumber(payload.progress_total || payload.total_pages),
      targetStoredName: activeTaskTargetStoredName,
    })
  }

  if (eventName === 'progress') {
    return getTaskProgressStatus(activeAction, {
      current: safeNumber(payload.progress_current || payload.current),
      total: safeNumber(payload.progress_total || payload.total),
      targetStoredName: activeTaskTargetStoredName,
    })
  }

  if (eventName === 'status') {
    return '正在进行复杂页增强修复...'
  }

  if (eventName === 'cancelled') {
    return '任务已停止。'
  }

  if (eventName === 'error') {
    return getTaskFailureStatus(activeAction)
  }

  return ''
}

function cleanString(value) {
  return String(value || '').trim()
}

function safeNumber(value) {
  const number = Number(value || 0)
  return Number.isFinite(number) ? number : 0
}
