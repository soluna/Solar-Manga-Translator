import assert from 'node:assert/strict'

import {
  mergePageArtifact,
  normalizePageArtifacts,
  projectArtifactsAllowExport,
} from '../src/project-artifact-state.js'


const currentPage = (pageId) => ({
  page_id: pageId,
  capabilities: {
    can_export: true,
    final_ready: true,
    final_stale: false,
  },
})

assert.equal(projectArtifactsAllowExport(0, {}), true)
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
