const clamp = value => Math.max(0, Math.min(1, value))
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
  return { attempt_id: attempt.attempt_id, preview: attempt.preview }
}
export function brushOperations(marks, mode, color, feather = 0, dimensions = null) {
  if (!marks.length || marks.some(mark => !mark.points?.length)) throw new Error('手工修补只接受画笔标记，请先移除矩形标记。')
  if (!['paint', 'restore'].includes(mode)) throw new Error('修补画笔模式无效。')
  if (!/^#[0-9a-f]{6}$/i.test(color)) throw new Error('请选择有效的画笔颜色。')
  const normalized = dimensions?.w > 0 && dimensions?.h > 0
  const minSide = normalized ? Math.min(dimensions.w, dimensions.h) : 1
  return marks.map(mark => ({ mode, coordinate_space: normalized ? 'normalized' : 'pixel',
    ...(normalized ? { size_space: 'normalized' } : {}),
    points: mark.points.map(([x, y]) => ({
    x: normalized ? Math.max(0, Math.min(1, x / dimensions.w)) : x,
    y: normalized ? Math.max(0, Math.min(1, y / dimensions.h)) : y,
  })),
    size: normalized ? (mark.radius * 2) / minSide : mark.radius * 2,
    feather: normalized ? feather / minSide : feather,
    color: [1, 3, 5].map(index => parseInt(color.slice(index, index + 2), 16)) }))
}
