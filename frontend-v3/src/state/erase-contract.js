const clamp = value => Math.max(0, Math.min(1, value))
const BRUSH_MODES = new Set(['paint', 'erase', 'restore'])

function roundBrushValue(value) {
  return Math.round(value * 10) / 10
}

/** Normalize the UI's hex value before it is used for preview or wire data. */
export function normalizeBrushColor(value, fallback = '#ffffff') {
  const normalize = candidate => {
    let normalized = String(candidate || '').trim()
    if (!normalized) return ''
    if (!normalized.startsWith('#')) normalized = `#${normalized}`
    if (/^#[0-9a-f]{3}$/i.test(normalized)) {
      normalized = `#${normalized.slice(1).split('').map(char => char + char).join('')}`
    }
    return /^#[0-9a-f]{6}$/i.test(normalized) ? normalized.toLowerCase() : ''
  }
  return normalize(value) || normalize(fallback) || (fallback === '' ? '' : '#ffffff')
}

export function normalizeBrushSize(value, fallback = 20, max = 2048) {
  const upper = Math.max(1, Number(max) || 2048)
  const fallbackValue = Number(fallback)
  const safeFallback = Number.isFinite(fallbackValue) ? fallbackValue : 20
  const numeric = Number(value)
  return roundBrushValue(Math.max(1, Math.min(upper, Number.isFinite(numeric) ? numeric : safeFallback)))
}

export function normalizeBrushFeather(value, size = 20, fallback = 0) {
  const normalizedSize = normalizeBrushSize(size)
  const fallbackValue = Number(fallback)
  const safeFallback = Number.isFinite(fallbackValue) ? fallbackValue : 0
  const numeric = Number(value)
  return roundBrushValue(Math.max(0, Math.min(normalizedSize / 2, Number.isFinite(numeric) ? numeric : safeFallback)))
}

function colorTriplet(value, fallback) {
  if (Array.isArray(value) && value.length >= 3) {
    return value.slice(0, 3).map(channel => {
      const numeric = Number(channel)
      return Math.max(0, Math.min(255, Number.isFinite(numeric) ? Math.round(numeric) : 0))
    })
  }
  const hex = normalizeBrushColor(value, fallback)
  return [1, 3, 5].map(index => Number.parseInt(hex.slice(index, index + 2), 16))
}

function brushPoint(point) {
  if (Array.isArray(point)) return [point[0], point[1]]
  return [point?.x, point?.y]
}
function sizeOf(dimensions) {
  if (!(dimensions?.w > 0) || !(dimensions?.h > 0)) throw new Error('请等待页面尺寸加载完成。')
  return dimensions
}
export function normalizedPoint(point, dimensions) {
  const { w, h } = sizeOf(dimensions)
  if (!point?.every(Number.isFinite)) throw new Error('点击位置无效。')
  return { x: clamp(point[0] / w), y: clamp(point[1] / h) }
}
export function selectionBox(selection, dimensions) {
  const { w, h } = sizeOf(dimensions)
  if (!['x', 'y', 'width', 'height'].every(key => Number.isFinite(selection?.[key]))) return null
  return [clamp(selection.x) * w, clamp(selection.y) * h,
    clamp(selection.x + selection.width) * w, clamp(selection.y + selection.height) * h].map(Math.round)
}
export function eraseSelection(marks, dimensions, maskMode = 'stroke') {
  const { w, h } = sizeOf(dimensions)
  const selections = marks.filter(mark => mark.bbox).map(({ bbox }) => {
    const start = normalizedPoint(bbox.slice(0, 2), dimensions), end = normalizedPoint(bbox.slice(2), dimensions)
    return { x1: start.x, y1: start.y, x2: end.x, y2: end.y }
  })
  const selection_strokes = marks.filter(mark => mark.points?.length).map(mark => ({
    size: Math.min(mark.radius * 2, Math.min(w, h) / 4) / Math.min(w, h),
    points: mark.points.map(point => normalizedPoint(point, dimensions)),
  }))
  if (!selections.length && !selection_strokes.length) throw new Error('请先标记要处理的范围。')
  return { selections, selection_strokes, local_mask_mode: maskMode === 'region' ? 'selection' : 'text' }
}
export function eraseRequest({ provider, scope, marks = [], dimensions, maskMode, config = {} }) {
  if (!['local', 'online'].includes(provider)) throw new Error('请选择有效的擦除方式。')
  if (scope === 'full') return { action: provider === 'local' ? 'local-advanced-preview' : 'erase', config }
  return { action: provider === 'local' ? 'local-selection' : 'selection', config, ...eraseSelection(marks, dimensions, maskMode) }
}
export function previewAttempt(response) {
  const attempt = response?.advanced_erase
  if (!attempt?.attempt_id || !attempt.preview?.candidate_url) throw new Error('后端没有返回有效的擦除预览。')
  return { ...attempt, attempt_id: attempt.attempt_id, preview: attempt.preview }
}
export function brushOperations(marks, mode, color, feather = 0, dimensions = null) {
  if (!Array.isArray(marks) || !marks.length || marks.some(mark => !Array.isArray(mark?.points) || !mark.points.length)) {
    throw new Error('手工修补只接受画笔标记，请先移除矩形标记。')
  }
  if (!BRUSH_MODES.has(mode)) throw new Error('修补画笔模式无效。')
  if (!/^#[0-9a-f]{6}$/i.test(normalizeBrushColor(color, ''))) throw new Error('请选择有效的画笔颜色。')
  const normalized = dimensions?.w > 0 && dimensions?.h > 0
  const minSide = normalized ? Math.min(dimensions.w, dimensions.h) : 1
  const fallbackColor = normalizeBrushColor(color)
  return marks.map(mark => {
    // A mark is a snapshot of the controls at pointer-down time. The current
    // toolbar values are only fallbacks for legacy marks created before the
    // per-stroke fields existed.
    const operationMode = BRUSH_MODES.has(mark.mode) ? mark.mode : mode
    const operationColor = normalizeBrushColor(mark.color, fallbackColor)
    const radius = Number(mark.radius)
    const rawSize = mark.size ?? (Number.isFinite(radius) ? radius * 2 : 20)
    const operationSize = normalizeBrushSize(rawSize)
    const operationFeather = normalizeBrushFeather(mark.feather ?? feather, operationSize)
    const points = mark.points.map(point => {
      const [rawX, rawY] = brushPoint(point)
      if (rawX == null || rawY == null || !Number.isFinite(Number(rawX)) || !Number.isFinite(Number(rawY))) {
        throw new Error('画笔轨迹包含无效位置。')
      }
      if (normalized) return normalizedPoint([Number(rawX), Number(rawY)], dimensions)
      return { x: Number(rawX), y: Number(rawY) }
    })
    return {
      mode: operationMode,
      coordinate_space: normalized ? 'normalized' : 'pixel',
      ...(normalized ? { size_space: 'normalized' } : {}),
      points,
      size: normalized ? operationSize / minSide : operationSize,
      feather: normalized ? operationFeather / minSide : operationFeather,
      color: colorTriplet(operationColor, fallbackColor),
    }
  })
}
