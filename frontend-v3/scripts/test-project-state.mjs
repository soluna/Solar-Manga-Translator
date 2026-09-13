import assert from 'node:assert/strict'
import { test } from 'node:test'
import { useProject } from '../src/composables/useProject.js'

const view = (id, generation) => ({ session_id: id, images: [{ stored_name: '001.png' }],
  project_head_generation: generation, project_head_revision_id: `${id}-${generation}`,
  download_url: `/api/download/${id}`,
  project: { project_id: id, title: id }, page_artifacts: { '001.png': { capabilities: { can_export: true } } } })

test('project state rejects older heads and merges compact page responses', async () => {
  const state = useProject({ request: async () => view('a', 3) })
  await state.loadProject('a')
  state.adoptResponse(view('a', 2))
  assert.equal(state.project.value.project_head_generation, 3)
  state.adoptResponse({ session_id: 'a', project_head_generation: 4, project_head_revision_id: 'a-4', page_artifact: { page_id: '001.png', capabilities: { can_export: false } } })
  assert.equal(state.project.value.images.length, 1)
  assert.equal(state.project.value.page_artifacts['001.png'].capabilities.can_export, false)
  assert.equal(state.project.value.download_url, '')
  state.adoptResponse(view('b', 100))
  assert.equal(state.project.value.session_id, 'a')
})

test('a late restore response cannot change the newly selected project', async () => {
  let finishA
  const state = useProject({ request: (url) => url.includes('/a/') ? new Promise(resolve => { finishA = resolve }) : Promise.resolve(view('b', 1)) })
  const pending = state.loadProject('a')
  await state.loadProject('b')
  finishA(view('a', 10))
  await pending
  assert.equal(state.project.value.session_id, 'b')
  assert.equal(state.loading.value, false)
})
