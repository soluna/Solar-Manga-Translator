import assert from 'node:assert/strict'
import { test } from 'node:test'
import { eraseSelection, normalizedPoint, selectionBox, eraseRequest, previewAttempt, brushOperations } from '../src/state/erase-contract.js'

test('erase marks use the backend normalized coordinates, diameter and mask modes', () => {
  const size = { w: 2400, h: 3600 }
  assert.deepEqual(normalizedPoint([600, 900], size), { x: 0.25, y: 0.25 })
  assert.deepEqual(selectionBox({ x: 0.25, y: 0.25, width: 0.5, height: 0.25 }, size), [600, 900, 1800, 1800])
  const marks = [{ bbox: [600, 900, 1800, 1800] }, { points: [[600, 900], [1200, 1800]], radius: 24 }]
  const body = eraseSelection(marks, size, 'region')
  assert.deepEqual(body.selections, [{ x1: 0.25, y1: 0.25, x2: 0.75, y2: 0.5 }])
  assert.deepEqual(body.selection_strokes, [{ size: 0.02, points: [{ x: 0.25, y: 0.25 }, { x: 0.5, y: 0.5 }] }])
  assert.equal(body.local_mask_mode, 'selection')
})

test('full local processing previews, online processing stays explicit, and preview IDs are nested', () => {
  assert.equal(eraseRequest({ provider: 'local', scope: 'full' }).action, 'local-advanced-preview')
  assert.equal(eraseRequest({ provider: 'online', scope: 'full' }).action, 'erase')
  assert.equal(eraseRequest({ provider: 'online', scope: 'selection', marks: [{ bbox: [0, 0, 200, 100] }], dimensions: { w: 600, h: 900 } }).action, 'selection')
  assert.throws(() => eraseSelection([], { w: 600, h: 900 }), /标记/)
  assert.deepEqual(previewAttempt({ advanced_erase: { attempt_id: 'attempt-a', preview: { candidate_url: '/candidate' } } }).attempt_id, 'attempt-a')
  assert.throws(() => previewAttempt({ attempt_id: 'wrong-shape' }), /预览/)
  const operation = brushOperations([{ points: [[120, 240]], radius: 12 }], 'paint', '#112233')[0]
  assert.equal(operation.coordinate_space, 'pixel')
  assert.deepEqual(operation.color, [17, 34, 51])
  assert.equal(operation.size, 24)
})

test('brush operations can use normalized coordinates and min-side size ratios', () => {
  const operation = brushOperations([{ points: [[600, 900], [1200, 1800]], radius: 24 }], 'paint', '#112233', 6, { w: 2400, h: 3600 })[0]
  assert.equal(operation.coordinate_space, 'normalized')
  assert.equal(operation.size_space, 'normalized')
  assert.deepEqual(operation.points, [{ x: 0.25, y: 0.25 }, { x: 0.5, y: 0.5 }])
  assert.equal(operation.size, 0.02)
  assert.equal(operation.feather, 6 / 2400)
})
