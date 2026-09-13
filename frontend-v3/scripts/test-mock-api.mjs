import assert from 'node:assert/strict'
import { test } from 'node:test'
import mockApiPlugin from '../plugins/mock-api.js'
import data from '../plugins/mock-data.mjs'
import { projectPageDocument } from '../src/state/review-document.js'

function request(method, url) {
  let handler
  mockApiPlugin().configureServer({ middlewares: { use(fn) { handler = fn } } })
  const headers = {}, response = { statusCode: 200,
    setHeader(key, value) { headers[key] = value }, end(body) { this.body = body },
  }
  handler({ method, url }, response, () => { response.next = true })
  return { ...response, headers, json: () => JSON.parse(response.body) }
}

test('demo mutations and downloads report unsupported instead of fabricated success', () => {
  for (const [method, url] of [
    ['PATCH', '/api/app/settings'], ['POST', '/api/app/settings/validate'],
    ['PUT', '/api/projects/demo/glossary'], ['DELETE', '/api/projects/demo'],
    ['POST', '/api/projects/demo/base-images'], ['POST', '/api/pages/demo/c002.png/commands'],
    ['POST', '/api/pages/demo/c002.png/advanced-erase/local-advanced-preview'],
    ['GET', '/api/download/demo'],
  ]) {
    const response = request(method, url)
    assert.equal(response.statusCode, 501, url)
    assert.match(response.json().detail, /演示/)
  }
})

test('demo restore, task and page identities agree and unknown projects fail', () => {
  for (const project of data.projects) {
    const restored = request('POST', `/api/projects/${project.project_id}/restore`).json()
    assert.equal(restored.session_id, project.project_id)
    assert.deepEqual(request('GET', `/api/projects/${project.project_id}/task`).json(), { task: null })
    for (const image of restored.images) {
      const artifact = restored.page_artifacts[image.stored_name]
      assert.equal(artifact.page_id, image.stored_name)
      assert.equal(image.artifact_state.capabilities.can_export, artifact.capabilities.can_export)
      const response = request('GET', `/api/pages/${project.project_id}/${image.stored_name}/document`)
      assert.equal(projectPageDocument(response.json()).page_id, image.stored_name)
    }
  }
  assert.equal(request('POST', '/api/projects/missing/restore').statusCode, 404)
  assert.equal(request('GET', '/api/pages/demo/missing/document').statusCode, 404)
})
