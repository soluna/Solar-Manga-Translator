import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  applyProjectArtifactEvent,
  createProjectArtifactState,
  getPageArtifactView,
  mergePageArtifact,
  normalizePageArtifacts,
  projectArtifactsAllowExport,
  restoreProjectArtifactState,
} from '../src/project-artifact-state.js'

const scriptDir = path.dirname(fileURLToPath(import.meta.url))
const transitionContract = JSON.parse(fs.readFileSync(
  path.resolve(scriptDir, '../../contracts/page-artifact-transitions-v1.json'),
  'utf8',
))

assert.equal(transitionContract.schema_version, 1)
assert.equal(transitionContract.artifact_schema_version, 2)
assert.equal(transitionContract.workflow_stage_role, 'compatibility_projection')
for (const scenario of transitionContract.scenarios) {
  let state = createProjectArtifactState(['0001.png'])
  const savedStates = []
  for (const event of scenario.events) {
    state = applyProjectArtifactEvent(state, '0001.png', event)
    savedStates.push(state)
  }
  if (scenario.restore) {
    state = restoreProjectArtifactState(savedStates[scenario.restore.after_event_index])
  }
  const view = getPageArtifactView(state, '0001.png')
  const actual = {
    artifacts: Object.fromEntries(Object.entries(view.artifacts).map(([name, artifact]) => [
      name,
      {
        revision: artifact.revision,
        ready: artifact.ready,
        current: artifact.current,
        stale: artifact.stale,
      },
    ])),
    capabilities: view.capabilities,
  }
  assert.deepEqual(actual, scenario.expected, scenario.id)
}

const untouchedState = createProjectArtifactState(['0001.png'])
assert.throws(
  () => applyProjectArtifactEvent(untouchedState, '0001.png', 'future_unknown_event'),
  /Unsupported page artifact event/,
)
assert.deepEqual(untouchedState, createProjectArtifactState(['0001.png']))
assert.throws(
  () => restoreProjectArtifactState({ ...untouchedState, schema_version: 99 }),
  /Unsupported project artifact schema version/,
)
assert.throws(
  () => applyProjectArtifactEvent(
    { ...untouchedState, schema_version: 99 },
    '0001.png',
    'recognized',
  ),
  /Unsupported project artifact schema version/,
)


const currentPage = (pageId) => ({
  page_id: pageId,
  capabilities: {
    can_export: true,
    final_ready: true,
    final_stale: false,
  },
})

assert.equal(projectArtifactsAllowExport(0, {}), false)
assert.equal(projectArtifactsAllowExport(99, { '0001.png': currentPage('0001.png') }), false)
assert.equal(projectArtifactsAllowExport(2, {}), false)

const normalized = normalizePageArtifacts({
  '0001.png': currentPage('0001.png'),
  invalid: null,
})
assert.deepEqual(Object.keys(normalized), ['0001.png'])
assert.equal(projectArtifactsAllowExport(2, normalized), true)

const stale = mergePageArtifact(normalized, {
  page_id: '0001.png',
  capabilities: {
    can_export: false,
    final_ready: false,
    final_stale: true,
  },
})
assert.equal(projectArtifactsAllowExport(2, stale), false)
assert.equal(stale['0001.png'].capabilities.final_stale, true)

console.log('Project artifact state tests passed.')
