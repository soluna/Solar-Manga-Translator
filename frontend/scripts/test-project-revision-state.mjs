import assert from 'node:assert/strict'
import { useProjectRevisionState } from '../src/composables/useProjectRevisionState.js'

const state = useProjectRevisionState()

assert.equal(state.shouldApplyProjectPayload({ session_id: 'project-a', project_head_generation: 1 }, ''), true)
state.recordProjectRevision({ project_head_generation: 1, project_head_revision_id: 'head-1' })
assert.equal(state.projectHeadGeneration.value, 1)
assert.equal(state.projectHeadRevisionId.value, 'head-1')

assert.equal(
  state.shouldApplyProjectPayload(
    { session_id: 'project-a', project_head_generation: 1, project_head_revision_id: 'other-head' },
    'project-a',
  ),
  false,
)
assert.equal(
  state.shouldApplyProjectPayload(
    { session_id: 'project-a', project_head_generation: 0, project_head_revision_id: '' },
    'project-a',
  ),
  false,
)
assert.equal(
  state.shouldApplyProjectPayload(
    { session_id: 'project-a', project_head_generation: 2, project_head_revision_id: 'head-2' },
    'project-a',
  ),
  true,
)

state.recordProjectRevision({ project_head_generation: 2, project_head_revision_id: 'head-2' })
assert.equal(
  state.isPayloadForActiveProject({ session_id: 'project-a' }, 'project-a'),
  true,
)
assert.equal(
  state.isPayloadForActiveProject({ session_id: 'project-b' }, 'project-a'),
  false,
)
assert.equal(
  state.shouldApplyProjectPayload(
    { session_id: 'project-b', project_head_generation: 1, project_head_revision_id: 'project-b-head' },
    'project-a',
  ),
  true,
)

state.resetProjectRevision()
assert.equal(state.projectHeadGeneration.value, 0)
assert.equal(state.projectHeadRevisionId.value, '')

console.log('project revision state tests passed')
