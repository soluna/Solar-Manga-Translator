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

export function getProjectStageCommands({
  hasProject,
  translating,
  activeAction,
  workflowStage,
  pauseAfterDetection,
  hasPartialTranslatedResults,
  canRunInitialDetection,
  canContinueSegmentedTranslation,
  canRerender,
  canRetranslate
}) {
  const stage = String(workflowStage || '').trim().toLowerCase()
  const normalizedActiveAction = normalizeTaskAction(activeAction)
  const projectReady = Boolean(hasProject)
  const busy = Boolean(translating)
  const translateAction = stage === 'detected' || hasPartialTranslatedResults
    ? 'resume-translate'
    : 'translate'

  const detectEnabled = projectReady && !busy && Boolean(canRunInitialDetection)
  const translateEnabled = projectReady && !busy && (
    Boolean(canContinueSegmentedTranslation)
    || Boolean(canRetranslate)
    || (!pauseAfterDetection && stage === 'idle')
  )
  const rerenderEnabled = projectReady && !busy && Boolean(canRerender)

  return [
    {
      key: 'detect',
      action: 'detect',
      label: stage === 'idle' ? '识别文本框' : '重新识别',
      enabled: detectEnabled,
      active: busy && normalizedActiveAction === 'detect',
      disabledReason: detectEnabled
        ? ''
        : busy
          ? '当前任务完成或停止后可重新识别'
          : projectReady
            ? '已有识别结果时请谨慎重新识别'
            : '请先上传漫画素材',
    },
    {
      key: 'translate',
      action: translateAction,
      label: stage === 'detected' || hasPartialTranslatedResults
        ? '继续翻译'
        : stage === 'translated'
          ? '重新翻译'
          : '翻译整本',
      enabled: translateEnabled,
      active: busy && ['translate', 'resume-translate'].includes(normalizedActiveAction),
      disabledReason: translateEnabled
        ? ''
        : busy
          ? '当前任务完成或停止后可继续翻译'
          : pauseAfterDetection && stage === 'idle'
            ? '请先识别文本框并确认结果'
            : projectReady
              ? '当前项目暂时没有可翻译内容'
              : '请先上传漫画素材',
    },
    {
      key: 'rerender',
      action: 'rerender',
      label: '重新嵌字',
      enabled: rerenderEnabled,
      active: busy && normalizedActiveAction === 'rerender',
      disabledReason: rerenderEnabled
        ? ''
        : busy
          ? '当前任务完成或停止后可重新嵌字'
          : projectReady
            ? '需要先生成翻译结果'
            : '请先上传漫画素材',
    }
  ]
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

export function shouldConfirmBatchTranslation(action, { targetStoredName = '' } = {}) {
  const normalized = normalizeTaskAction(action)
  if (String(targetStoredName || '').trim()) {
    return false
  }
  return normalized === 'translate' || normalized === 'resume-translate'
}

export function buildBatchTranslationConfirmation({
  action = 'translate',
  pageCount = 0,
  regionCount = 0,
  providerLabel = '当前翻译服务',
  targetLanguageLabel = '目标语言',
} = {}) {
  const descriptor = getTaskActionDescriptor(action)
  const safePageCount = Math.max(0, Number(pageCount || 0))
  const safeRegionCount = Math.max(0, Number(regionCount || 0))
  const safeProviderLabel = String(providerLabel || '当前翻译服务').trim()
  const safeTargetLanguageLabel = String(targetLanguageLabel || '目标语言').trim()

  return {
    title: '开始批量翻译前确认',
    action: descriptor.action,
    actionLabel: descriptor.actionLabel,
    confirmLabel: '确认开始翻译',
    cancelLabel: '再检查一下',
    summary: `${descriptor.actionLabel}将通过 ${safeProviderLabel} 处理 ${safePageCount} 页、约 ${safeRegionCount} 个文本框，目标语言为 ${safeTargetLanguageLabel}。`,
    items: [
      `翻译服务：${safeProviderLabel}`,
      '费用和限额由你的翻译服务商实际收取，本地无法精确估算。',
      '任务开始后可随时停止；已成功生成的旧结果会被保留到新任务完成后再替换。',
    ],
  }
}

function renderTemplate(template, values) {
  return String(template || '').replace(/\{([a-zA-Z0-9_]+)\}/g, (_match, key) => (
    Object.prototype.hasOwnProperty.call(values, key) ? String(values[key]) : ''
  ))
}
