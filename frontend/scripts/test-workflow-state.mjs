import assert from 'node:assert/strict'

import {
  getPrimaryProjectCommand,
  getReviewPrimaryCommand,
  getTaskFailureStatus,
  getTaskProgressStatus,
  getTaskStartStatus,
  getWorkflowStageLabel,
  normalizeTaskAction,
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

console.log('Workflow state tests passed.')
