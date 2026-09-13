import assert from 'node:assert/strict'
import { test } from 'node:test'
import { pageStatus, projectReadiness } from '../src/state/page-status.js'

test('mixed projects retain page-level stale results and only export current final artifacts', () => {
  const page = (id, capabilities) => ({ stored_name: id, artifact_state: { capabilities } })
  const ready = page('1.png', { recognition_ready: true, translation_ready: true, can_render: true, can_export: true, blank_ready: true })
  const stale = page('2.png', { recognition_ready: true, translation_ready: true, can_render: true, final_stale: true, blank_ready: true })
  assert.equal(pageStatus(ready).text, '已嵌字')
  assert.equal(pageStatus(stale).text, '需重新嵌字')
  assert.equal(projectReadiness([ready, stale]).rendered, false)
  assert.equal(projectReadiness([ready]).rendered, true)
  assert.equal(projectReadiness([]).rendered, false)
  assert.equal(pageStatus({ stored_name: '3.png' }).text, '状态待同步')
  assert.equal(pageStatus(stale, { busy: true, pageId: '1.png', action: 'rerender' }).text, '需重新嵌字')
})
