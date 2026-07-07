import assert from 'node:assert/strict'

import {
  buildBatchTranslationConfirmation,
  getProjectStageCommands,
  getPrimaryProjectCommand,
  getReviewPrimaryCommand,
  getTaskFailureStatus,
  getTaskProgressStatus,
  getTaskStartStatus,
  getWorkflowStageLabel,
  normalizeTaskAction,
  shouldConfirmBatchTranslation,
} from '../src/workflow-state.js'


assert.equal(normalizeTaskAction('resume_translate'), 'resume-translate')
assert.equal(normalizeTaskAction('translate_page'), 'translate-page')
assert.equal(getWorkflowStageLabel('detected'), '待校对')

assert.deepEqual(
  getPrimaryProjectCommand({
    workflowStage: 'idle',
    hasPartialTranslatedResults: false,
    pauseAfterDetection: true,
    translating: false,
    activeAction: '',
  }),
  {
    action: 'detect',
    label: '开始识别',
  },
)

assert.equal(
  getPrimaryProjectCommand({
    workflowStage: 'detected',
    hasPartialTranslatedResults: false,
    pauseAfterDetection: false,
    translating: false,
    activeAction: '',
  }).action,
  'resume-translate',
)

assert.equal(
  getPrimaryProjectCommand({
    workflowStage: 'translated',
    hasPartialTranslatedResults: false,
    pauseAfterDetection: false,
    translating: true,
    activeAction: 'translate-page',
  }).label,
  '本页翻译中...',
)

assert.deepEqual(
  getReviewPrimaryCommand({
    translating: false,
    canContinueSegmentedTranslation: false,
    canTranslateCurrentPage: true,
    canRerender: true,
    canRunInitialDetection: false,
    workflowStage: 'detected',
  }),
  {
    action: 'translate-page',
    label: '翻译本页',
  },
)

assert.equal(getTaskStartStatus('rerender', { totalPages: 4 }), '重嵌字已开始，共 4 张图片。')
assert.equal(
  getTaskStartStatus('rerender', { totalPages: 4, targetStoredName: '001.png' }),
  '当前页重嵌字已开始。',
)
assert.equal(getTaskProgressStatus('resume-translate', { current: 2, total: 5 }), '继续翻译进行中：2 / 5')
assert.equal(getTaskFailureStatus('detect'), '文本框识别失败。')

const idleStageCommands = getProjectStageCommands({
  hasProject: true,
  translating: false,
  activeAction: '',
  workflowStage: 'idle',
  pauseAfterDetection: true,
  hasPartialTranslatedResults: false,
  canRunInitialDetection: true,
  canContinueSegmentedTranslation: false,
  canRerender: false,
  canRetranslate: false,
})

assert.deepEqual(
  idleStageCommands.map((command) => command.key),
  ['detect', 'translate', 'rerender'],
)
assert.equal(idleStageCommands[0].label, '识别文本框')
assert.equal(idleStageCommands[0].enabled, true)
assert.equal(idleStageCommands[1].enabled, false)
assert.match(idleStageCommands[1].disabledReason, /识别/)

const detectedStageCommands = getProjectStageCommands({
  hasProject: true,
  translating: false,
  activeAction: '',
  workflowStage: 'detected',
  pauseAfterDetection: true,
  hasPartialTranslatedResults: false,
  canRunInitialDetection: false,
  canContinueSegmentedTranslation: true,
  canRerender: false,
  canRetranslate: false,
})
assert.equal(detectedStageCommands[1].action, 'resume-translate')
assert.equal(detectedStageCommands[1].label, '继续翻译')
assert.equal(detectedStageCommands[1].enabled, true)

assert.equal(shouldConfirmBatchTranslation('detect'), false)
assert.equal(shouldConfirmBatchTranslation('translate-page'), false)
assert.equal(shouldConfirmBatchTranslation('rerender'), false)
assert.equal(shouldConfirmBatchTranslation('resume-translate'), true)
assert.equal(shouldConfirmBatchTranslation('translate', { targetStoredName: '001.png' }), false)

const confirmation = buildBatchTranslationConfirmation({
  action: 'resume-translate',
  pageCount: 8,
  regionCount: 126,
  providerLabel: 'OpenAI Compatible / deepseek-v4-flash',
  targetLanguageLabel: '简中',
})
assert.equal(confirmation.title, '开始批量翻译前确认')
assert.match(confirmation.summary, /8 页/)
assert.match(confirmation.summary, /126 个文本框/)
assert.match(confirmation.summary, /OpenAI Compatible/)
assert.equal(confirmation.confirmLabel, '确认开始翻译')
assert.equal(confirmation.items.some((item) => item.includes('可随时停止')), true)

console.log('Workflow state tests passed.')
