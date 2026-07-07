export const workflowStageLabelMap = Object.freeze({
  idle: '未开始',
  detecting: '识别中',
  detected: '待校对',
  translating: '翻译中',
  translated: '已翻译'
})

const PHASE_TOTAL = 6

const taskActionDescriptors = Object.freeze({
  detect: {
    action: 'detect',
    actionLabel: '文本框识别',
    workflowPhase: 'recognize',
    phaseLabel: '检测与 OCR',
    phaseIndex: 2,
    phaseTotal: PHASE_TOTAL,
    startMessage: '文本框识别已开始，共 {total} 张图片。',
    progressMessage: '正在识别并准备校对：{current} / {total}',
    failureMessage: '文本框识别失败。',
    busyLabel: '识别进行中...',
    scope: 'project',
    scopeLabel: '整组页面'
  },
  translate: {
    action: 'translate',
    actionLabel: '整本翻译',
    workflowPhase: 'translate',
    phaseLabel: '翻译与嵌字',
    phaseIndex: 4,
    phaseTotal: PHASE_TOTAL,
    startMessage: '翻译已开始，共 {total} 张图片。',
    progressMessage: '翻译进行中：{current} / {total}',
    failureMessage: '翻译失败。',
    busyLabel: '翻译进行中...',
    scope: 'project',
    scopeLabel: '整组页面'
  },
  'resume-translate': {
    action: 'resume-translate',
    actionLabel: '继续翻译',
    workflowPhase: 'translate',
    phaseLabel: '翻译与嵌字',
    phaseIndex: 4,
    phaseTotal: PHASE_TOTAL,
    startMessage: '继续翻译已开始，共 {total} 张图片。',
    progressMessage: '继续翻译进行中：{current} / {total}',
    failureMessage: '继续翻译失败。',
    busyLabel: '翻译进行中...',
    scope: 'project',
    scopeLabel: '整组页面'
  },
  'translate-page': {
    action: 'translate-page',
    actionLabel: '当前页翻译',
    workflowPhase: 'translate',
    phaseLabel: '翻译与嵌字',
    phaseIndex: 4,
    phaseTotal: PHASE_TOTAL,
    startMessage: '当前页翻译已开始。',
    progressMessage: '当前页翻译进行中：{current} / {total}',
    failureMessage: '当前页翻译失败。',
    busyLabel: '本页翻译中...',
    scope: 'page',
    scopeLabel: '当前页'
  },
  rerender: {
    action: 'rerender',
    actionLabel: '重新嵌字',
    workflowPhase: 'render',
    phaseLabel: '重新嵌字',
    phaseIndex: 6,
    phaseTotal: PHASE_TOTAL,
    startMessage: '重嵌字已开始，共 {total} 张图片。',
    progressMessage: '重嵌字进行中：{current} / {total}',
    pageStartMessage: '当前页重嵌字已开始。',
    pageProgressMessage: '当前页重嵌字进行中：{current} / {total}',
    failureMessage: '重嵌字失败。',
    busyLabel: '重嵌字进行中...',
    scope: 'project',
    scopeLabel: '整组页面'
  }
})

export function normalizeTaskAction(action) {
  const normalized = String(action || '').trim().toLowerCase()
  if (normalized === 'resume_translate' || normalized === 'resume') {
    return 'resume-translate'
  }
  if (normalized === 'translate_page' || normalized === 'page-translate') {
    return 'translate-page'
  }
  if (normalized === 'render' || normalized === 're-render') {
    return 'rerender'
  }
  return normalized || 'translate'
}

export function getTaskActionDescriptor(action, options = {}) {
  const normalized = normalizeTaskAction(action)
  const descriptor = taskActionDescriptors[normalized] || taskActionDescriptors.translate
  const targetStoredName = String(options.targetStoredName || '').trim()
  if (descriptor.action === 'rerender' && targetStoredName) {
    return {
      ...descriptor,
      scope: 'page',
      scopeLabel: '当前页',
      startMessage: descriptor.pageStartMessage,
      progressMessage: descriptor.pageProgressMessage
    }
  }
  return descriptor
}

export function getWorkflowStageLabel(stage) {
  const normalized = String(stage || '').trim().toLowerCase()
  return workflowStageLabelMap[normalized] || normalized || '未开始'
}

export function getPrimaryProjectCommand({
  workflowStage,
  hasPartialTranslatedResults,
  pauseAfterDetection,
  translating,
  activeAction
}) {
  if (translating) {
    const action = normalizeTaskAction(activeAction)
    return {
      action,
      label: getTaskActionDescriptor(action).busyLabel
    }
  }

  const stage = String(workflowStage || '').trim().toLowerCase()
  if (stage === 'detected' || hasPartialTranslatedResults) {
    return { action: 'resume-translate', label: '继续翻译' }
  }
  if (stage === 'translated') {
    return { action: 'translate', label: '重新翻译' }
  }
  if (pauseAfterDetection) {
    return { action: 'detect', label: '开始识别' }
  }
  return { action: 'translate', label: '开始翻译' }
}

export function getReviewPrimaryCommand({
  translating,
  canContinueSegmentedTranslation,
  canTranslateCurrentPage,
  canRerender,
  canRunInitialDetection,
  workflowStage
}) {
  if (translating) {
    return { action: '', label: '处理中…' }
  }
  if (canContinueSegmentedTranslation) {
    return { action: 'resume-translate', label: '继续翻译' }
  }
  if (canTranslateCurrentPage) {
    return { action: 'translate-page', label: '翻译本页' }
  }
  if (canRerender) {
    return { action: 'rerender', label: '保存并重渲染' }
  }
  if (canRunInitialDetection) {
    return { action: 'detect', label: '开始识别' }
  }
  return {
    action: 'translate',
    label: String(workflowStage || '').trim().toLowerCase() === 'translated'
      ? '重新翻译'
      : '开始翻译'
  }
}

export function getTaskStartStatus(action, { totalPages = 0, targetStoredName = '' } = {}) {
  const descriptor = getTaskActionDescriptor(action, { targetStoredName })
  return renderTemplate(descriptor.startMessage, {
    total: Number(totalPages || 0)
  })
}

export function getTaskProgressStatus(action, {
  current = 0,
  total = 0,
  targetStoredName = ''
} = {}) {
  const descriptor = getTaskActionDescriptor(action, { targetStoredName })
  return renderTemplate(descriptor.progressMessage, {
    current: Number(current || 0),
    total: Number(total || 0)
  })
}

export function getTaskFailureStatus(action) {
  return getTaskActionDescriptor(action).failureMessage
}

function renderTemplate(template, values) {
  return String(template || '').replace(/\{([a-zA-Z0-9_]+)\}/g, (_match, key) => (
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : ''
  ))
}
