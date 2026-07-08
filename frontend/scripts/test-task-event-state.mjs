import assert from 'node:assert/strict'

import {
  createEmptyTaskPhase,
  deriveTaskEventUpdate,
  deriveTaskPhase,
} from '../src/task-event-state.js'

assert.deepEqual(createEmptyTaskPhase(), {
  label: '',
  index: 0,
  total: 0,
  scopeLabel: '',
  current: 0,
  progressTotal: 0,
  step: '',
  stepLabel: '',
  stepIndex: 0,
  stepTotal: 0,
})

assert.equal(
  deriveTaskEventUpdate(
    { event: 'progress', task_id: 'task-a', sequence: 2 },
    { activeTaskId: 'task-a', activeTaskSequence: 3 },
  ).ignore,
  true,
)

const startUpdate = deriveTaskEventUpdate(
  {
    event: 'start',
    task_id: 'task-a',
    sequence: 1,
    task_action: 'detect',
    total_pages: 6,
    phase_label: '检测与 OCR',
    phase_index: 2,
    phase_total: 6,
    scope_label: '整组页面',
    workflow_step: 'detect',
    step_label: '检测文本框',
    step_index: 3,
    step_total: 5,
  },
  {
    activeTaskId: '',
    activeTaskSequence: 0,
    activeAction: 'translate',
    activeTaskTargetStoredName: '',
  },
)
assert.equal(startUpdate.ignore, false)
assert.equal(startUpdate.activeTaskId, 'task-a')
assert.equal(startUpdate.activeTaskSequence, 1)
assert.equal(startUpdate.activeAction, 'detect')
assert.deepEqual(startUpdate.progress, { current: 0, total: 6 })
assert.equal(startUpdate.statusMessage, '文本框识别已开始，共 6 张图片。')
assert.deepEqual(startUpdate.phase, {
  label: '检测与 OCR',
  index: 2,
  total: 6,
  scopeLabel: '整组页面',
  current: 0,
  progressTotal: 0,
  step: 'detect',
  stepLabel: '检测文本框',
  stepIndex: 3,
  stepTotal: 5,
})

const progressUpdate = deriveTaskEventUpdate(
  {
    event: 'progress',
    task_id: 'task-a',
    sequence: 2,
    current: 3,
    total: 6,
    metadata: { target_stored_name: '001.png' },
  },
  {
    activeTaskId: 'task-a',
    activeTaskSequence: 1,
    activeAction: 'rerender',
    activeTaskTargetStoredName: '',
  },
)
assert.equal(progressUpdate.activeTaskTargetStoredName, '001.png')
assert.deepEqual(progressUpdate.progress, { current: 3, total: 6 })
assert.equal(progressUpdate.statusMessage, '当前页重嵌字进行中：3 / 6')

assert.equal(
  deriveTaskEventUpdate(
    { event: 'status' },
    { activeAction: 'translate' },
  ).statusMessage,
  '正在进行复杂页增强修复...',
)

assert.equal(
  deriveTaskEventUpdate(
    { event: 'error' },
    { activeAction: 'resume-translate' },
  ).statusMessage,
  '继续翻译失败。',
)

assert.equal(
  deriveTaskPhase({ event: 'task' }),
  null,
)

console.log('Task event state tests passed.')
