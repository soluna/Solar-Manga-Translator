function clamp(value, minimum, maximum) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) {
    return minimum
  }
  return Math.max(minimum, Math.min(maximum, numeric))
}

function normalizeSelection(rect) {
  const x = clamp(rect?.x, 0, 1)
  const y = clamp(rect?.y, 0, 1)
  return {
    x,
    y,
    width: clamp(rect?.width, 0, 1 - x),
    height: clamp(rect?.height, 0, 1 - y),
  }
}

export function normalizeEraseBrushStroke(stroke) {
  return {
    size: clamp(stroke?.size, 0.001, 0.25),
    points: Array.isArray(stroke?.points)
      ? stroke.points.map((point) => ({
          x: clamp(point?.x, 0, 1),
          y: clamp(point?.y, 0, 1),
        }))
      : [],
  }
}

export function buildEraseExecution({
  provider,
  scope,
  selections = [],
  selectionStrokes = [],
  localMaskMode = 'text',
} = {}) {
  const normalizedProvider = provider === 'online' ? 'online' : 'local'
  const normalizedScope = scope === 'selection' ? 'selection' : 'full'
  const normalizedSelections = normalizedScope === 'selection'
    ? selections
        .map(normalizeSelection)
        .filter((rect) => rect.width > 0 && rect.height > 0)
    : []
  const normalizedStrokes = normalizedScope === 'selection'
    ? selectionStrokes
        .map(normalizeEraseBrushStroke)
        .filter((stroke) => stroke.points.length > 0)
    : []

  if (
    normalizedScope === 'selection'
    && !normalizedSelections.length
    && !normalizedStrokes.length
  ) {
    throw new Error('请至少添加一个点击选区、框选区域或画笔标记。')
  }

  const action = normalizedScope === 'full'
    ? (normalizedProvider === 'online' ? 'erase' : 'local-advanced-preview')
    : (normalizedProvider === 'online' ? 'selection' : 'local-selection')

  return {
    action,
    selections: normalizedSelections,
    selectionStrokes: normalizedStrokes,
    localMaskMode: localMaskMode === 'selection' ? 'selection' : 'text',
    opensPreview: action === 'local-advanced-preview',
    requiresOnlineConfig: normalizedProvider === 'online',
  }
}
