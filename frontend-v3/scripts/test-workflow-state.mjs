import assert from 'node:assert/strict'
import { test } from 'node:test'
import { getProjectStageCommands, getProjectTranslateAction, shouldConfirmBatchTranslation } from '../src/state/workflow-state.js'
import { hasPartialTranslation, projectReadiness } from '../src/state/page-status.js'

const page = (capabilities) => ({ artifact_state: { capabilities } })

test('project translation keeps the initial command distinct from resume', () => {
  assert.equal(getProjectTranslateAction({ workflowStage: 'idle' }), 'translate')
  assert.equal(getProjectTranslateAction({ workflowStage: 'detected' }), 'resume-translate')
  assert.equal(getProjectTranslateAction({ workflowStage: 'idle', hasPartialTranslatedResults: true }), 'resume-translate')
})

test('pause-after-detection false exposes direct idle translation', () => {
  const pages = [page({})]
  assert.equal(projectReadiness(pages, { pauseAfterDetection: false, workflowStage: 'idle' }).canTranslate, true)
  assert.equal(projectReadiness(pages, { pauseAfterDetection: true, workflowStage: 'idle' }).canTranslate, false)
  assert.equal(getProjectStageCommands({
    hasProject: true,
    translating: false,
    workflowStage: 'idle',
    pauseAfterDetection: false,
    canRunInitialDetection: true,
    canContinueSegmentedTranslation: false,
    canRerender: false,
    canRetranslate: false,
  }).find(command => command.key === 'translate').action, 'translate')
})

test('partial translated pages use resume and batch translation requires confirmation', () => {
  const pages = [page({ translation_ready: true }), page({ can_translate: true })]
  assert.equal(hasPartialTranslation(pages), true)
  assert.equal(getProjectTranslateAction({ workflowStage: 'idle', hasPartialTranslatedResults: hasPartialTranslation(pages) }), 'resume-translate')
  assert.equal(shouldConfirmBatchTranslation('translate'), true)
  assert.equal(shouldConfirmBatchTranslation('resume-translate'), true)
  assert.equal(shouldConfirmBatchTranslation('translate-page', { targetStoredName: '001.png' }), false)
})
