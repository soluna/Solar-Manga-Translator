import assert from 'node:assert/strict'

import {
  buildEraseExecution,
  normalizeEraseBrushStroke,
} from '../src/erase-workflow.js'

assert.deepEqual(
  buildEraseExecution({ provider: 'online', scope: 'full' }),
  {
    action: 'erase',
    selections: [],
    selectionStrokes: [],
    localMaskMode: 'text',
    opensPreview: false,
    requiresOnlineConfig: true,
  },
)

assert.deepEqual(
  buildEraseExecution({ provider: 'local', scope: 'full' }),
  {
    action: 'local-advanced-preview',
    selections: [],
    selectionStrokes: [],
    localMaskMode: 'text',
    opensPreview: true,
    requiresOnlineConfig: false,
  },
)

const selection = { x: 0.1, y: 0.2, width: 0.3, height: 0.4 }
const stroke = {
  size: 0.04,
  points: [
    { x: 0.2, y: 0.3 },
    { x: 0.4, y: 0.5 },
  ],
}

assert.deepEqual(
  buildEraseExecution({
    provider: 'online',
    scope: 'selection',
    selections: [selection],
    selectionStrokes: [stroke],
  }),
  {
    action: 'selection',
    selections: [selection],
    selectionStrokes: [stroke],
    localMaskMode: 'text',
    opensPreview: false,
    requiresOnlineConfig: true,
  },
)

assert.deepEqual(
  buildEraseExecution({
    provider: 'local',
    scope: 'selection',
    selectionStrokes: [stroke],
    localMaskMode: 'selection',
  }),
  {
    action: 'local-selection',
    selections: [],
    selectionStrokes: [stroke],
    localMaskMode: 'selection',
    opensPreview: false,
    requiresOnlineConfig: false,
  },
)

assert.throws(
  () => buildEraseExecution({ provider: 'local', scope: 'selection' }),
  /至少添加一个点击选区、框选区域或画笔标记/,
)

assert.deepEqual(
  normalizeEraseBrushStroke({
    size: 2,
    points: [
      { x: -0.2, y: 0.25 },
      { x: 1.4, y: 0.75 },
    ],
  }),
  {
    size: 0.25,
    points: [
      { x: 0, y: 0.25 },
      { x: 1, y: 0.75 },
    ],
  },
)

console.log('Erase workflow tests passed.')
