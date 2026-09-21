import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  eraseSelection,
  normalizedPoint,
  selectionBox,
  eraseRequest,
  previewAttempt,
  brushOperations,
  normalizeBrushColor,
  normalizeBrushFeather,
  normalizeBrushSize,
} from '../src/state/erase-contract.js'

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

test('brush controls normalize colors, sizes, and feather to valid values', () => {
  assert.equal(normalizeBrushColor(' F0A '), '#ff00aa')
  assert.equal(normalizeBrushColor('bad'), '#bbaadd')
  assert.equal(normalizeBrushColor('not-a-color', '#123456'), '#123456')
  assert.equal(normalizeBrushColor('not-a-color', ''), '')
  assert.equal(normalizeBrushSize(5000, 20, 32), 32)
  assert.equal(normalizeBrushSize(Number.NaN, 12, 32), 12)
  assert.equal(normalizeBrushFeather(80, 20), 10)
  assert.equal(normalizeBrushFeather(Number.NaN, 20, 4.5), 4.5)
})

test('brush edit operations keep each stroke mode and controls instead of applying toolbar fallbacks', () => {
  const operations = brushOperations([
    { mode: 'paint', color: '#a1b2c3', size: 37, feather: 8.5, points: [[120, 360]] },
    { mode: 'erase', color: '#0f0', size: 84.5, feather: 40, points: [{ x: 600, y: 900 }] },
    { mode: 'restore', color: '#654321', size: 19.5, feather: 7.5, points: [[1080, 1620]] },
  ], 'paint', '#010203', 1.25, { w: 1200, h: 1800 })

  assert.deepEqual(operations.map(({ mode, color }) => ({ mode, color })), [
    { mode: 'paint', color: [161, 178, 195] },
    { mode: 'erase', color: [0, 255, 0] },
    { mode: 'restore', color: [101, 67, 33] },
  ])
  assert.deepEqual(operations.map(operation => operation.size), [37 / 1200, 84.5 / 1200, 19.5 / 1200])
  assert.deepEqual(operations.map(operation => operation.feather), [8.5 / 1200, 40 / 1200, 7.5 / 1200])
  assert.deepEqual(operations.map(operation => operation.points[0]), [
    { x: 0.1, y: 0.2 },
    { x: 0.5, y: 0.5 },
    { x: 0.9, y: 0.9 },
  ])
  assert.ok(operations.every(operation => operation.coordinate_space === 'normalized' && operation.size_space === 'normalized'))
})

test('legacy brush marks inherit toolbar defaults and malformed trajectories are rejected', () => {
  const [operation] = brushOperations([{ points: [[60, 90]], radius: 7 }], 'restore', '#345678', 4, { w: 600, h: 900 })
  assert.equal(operation.mode, 'restore')
  assert.deepEqual(operation.color, [52, 86, 120])
  assert.equal(operation.size, 14 / 600)
  assert.equal(operation.feather, 4 / 600)
  assert.throws(() => brushOperations([{ points: [[null, 2]] }], 'paint', '#fff'), /位置/)
  assert.throws(() => brushOperations([{ points: [[1, 2]] }], 'invalid', '#ffffff'), /模式/)
  assert.throws(() => brushOperations([{ points: [[1, 2]] }], 'paint', 'invalid'), /颜色/)
})
