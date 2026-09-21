import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  resizeCanvasBBox,
  selectCanvasRegions,
} from '../src/state/review-canvas-geometry.js'

const page = { width: 200, height: 160 }
const origin = [40, 30, 100, 90]

test('all eight resize handles update only their legacy axis', () => {
  assert.deepEqual(resizeCanvasBBox(origin, 'nw', -10, -8, page), [30, 22, 100, 90])
  assert.deepEqual(resizeCanvasBBox(origin, 'n', 0, -8, page), [40, 22, 100, 90])
  assert.deepEqual(resizeCanvasBBox(origin, 'ne', 10, -8, page), [40, 22, 110, 90])
  assert.deepEqual(resizeCanvasBBox(origin, 'e', 10, 0, page), [40, 30, 110, 90])
  assert.deepEqual(resizeCanvasBBox(origin, 'se', 10, 8, page), [40, 30, 110, 98])
  assert.deepEqual(resizeCanvasBBox(origin, 's', 0, 8, page), [40, 30, 100, 98])
  assert.deepEqual(resizeCanvasBBox(origin, 'sw', -10, 8, page), [30, 30, 100, 98])
  assert.deepEqual(resizeCanvasBBox(origin, 'w', -10, 0, page), [30, 30, 100, 90])
})

test('Shift corners preserve ratio and Alt edges resize around center', () => {
  assert.deepEqual(resizeCanvasBBox(origin, 'se', 20, 0, { ...page, proportional: true }), [40, 30, 120, 110])
  assert.deepEqual(resizeCanvasBBox(origin, 'e', 10, 0, { ...page, fromCenter: true }), [30, 30, 110, 90])
})

test('marquee selection returns intersecting regions and preserves additive selection', () => {
  const regions = [
    { id: 'a', bbox: [10, 10, 30, 30] },
    { id: 'b', bbox: [40, 10, 70, 30] },
    { id: 'c', bbox: [100, 100, 130, 130] },
  ]
  assert.deepEqual(selectCanvasRegions(regions, [0, 0, 50, 40]), ['a', 'b'])
  assert.deepEqual(selectCanvasRegions(regions, [95, 95, 120, 120], { selectedIds: ['a'], additive: true }), ['a', 'c'])
  assert.deepEqual(selectCanvasRegions(regions, [0, 0, 5, 5], { selectedIds: ['a'], additive: true }), ['a'])
})
