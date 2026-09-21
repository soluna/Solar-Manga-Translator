/** Pure page-coordinate helpers used by the review canvas.
 *
 * Keeping the geometry independent from DOM state makes the modifier-key
 * behavior testable without a browser and keeps the view responsible only for
 * translating pointer coordinates into page coordinates.
 */

export function clampCanvasBBox(bbox, width, height, { minWidth = 20, minHeight = 12 } = {}) {
  const pageWidth = Math.max(1, Number(width) || 1)
  const pageHeight = Math.max(1, Number(height) || 1)
  const safeMinWidth = Math.min(pageWidth, Math.max(1, Number(minWidth) || 1))
  const safeMinHeight = Math.min(pageHeight, Math.max(1, Number(minHeight) || 1))
  let [x1, y1, x2, y2] = (Array.isArray(bbox) ? bbox : [0, 0, safeMinWidth, safeMinHeight])
    .slice(0, 4).map(value => Math.round(Number(value) || 0))
  if (x2 < x1) [x1, x2] = [x2, x1]
  if (y2 < y1) [y1, y2] = [y2, y1]
  x1 = Math.min(Math.max(0, x1), pageWidth)
  x2 = Math.min(Math.max(0, x2), pageWidth)
  y1 = Math.min(Math.max(0, y1), pageHeight)
  y2 = Math.min(Math.max(0, y2), pageHeight)
  if (x2 - x1 < safeMinWidth) {
    if (x1 + safeMinWidth <= pageWidth) x2 = x1 + safeMinWidth
    else { x2 = pageWidth; x1 = Math.max(0, pageWidth - safeMinWidth) }
  }
  if (y2 - y1 < safeMinHeight) {
    if (y1 + safeMinHeight <= pageHeight) y2 = y1 + safeMinHeight
    else { y2 = pageHeight; y1 = Math.max(0, pageHeight - safeMinHeight) }
  }
  return [x1, y1, x2, y2]
}

export function resizeCanvasBBox(origin, handle, dx, dy, {
  width, height, proportional = false, fromCenter = false, minWidth = 20, minHeight = 12,
} = {}) {
  let [x1, y1, x2, y2] = (Array.isArray(origin) ? origin : [0, 0, minWidth, minHeight])
    .slice(0, 4).map(value => Number(value) || 0)
  const direction = String(handle || '')
  const deltaX = Number(dx) || 0
  const deltaY = Number(dy) || 0
  if (direction.includes('n')) { y1 += deltaY; if (fromCenter) y2 -= deltaY }
  if (direction.includes('s')) { y2 += deltaY; if (fromCenter) y1 -= deltaY }
  if (direction.includes('w')) { x1 += deltaX; if (fromCenter) x2 -= deltaX }
  if (direction.includes('e')) { x2 += deltaX; if (fromCenter) x1 -= deltaX }

  // Legacy proportional resizing applies to corners only. Edge handles keep
  // their single axis even when Shift is held.
  if (proportional && direction.length === 2) {
    const originWidth = Math.max(8, origin[2] - origin[0])
    const originHeight = Math.max(8, origin[3] - origin[1])
    const ratio = originWidth / originHeight
    let nextWidth = Math.max(8, Math.abs(x2 - x1))
    let nextHeight = Math.max(8, Math.abs(y2 - y1))
    if (nextWidth / originWidth >= nextHeight / originHeight) nextHeight = nextWidth / ratio
    else nextWidth = nextHeight * ratio

    if (fromCenter) {
      const centerX = (origin[0] + origin[2]) / 2
      const centerY = (origin[1] + origin[3]) / 2
      x1 = centerX - nextWidth / 2; x2 = centerX + nextWidth / 2
      y1 = centerY - nextHeight / 2; y2 = centerY + nextHeight / 2
    } else if (direction === 'se') {
      x1 = origin[0]; y1 = origin[1]; x2 = x1 + nextWidth; y2 = y1 + nextHeight
    } else if (direction === 'ne') {
      x1 = origin[0]; y2 = origin[3]; x2 = x1 + nextWidth; y1 = y2 - nextHeight
    } else if (direction === 'sw') {
      x2 = origin[2]; y1 = origin[1]; x1 = x2 - nextWidth; y2 = y1 + nextHeight
    } else if (direction === 'nw') {
      x2 = origin[2]; y2 = origin[3]; x1 = x2 - nextWidth; y1 = y2 - nextHeight
    }
  }
  return clampCanvasBBox([x1, y1, x2, y2], width, height, { minWidth, minHeight })
}

export function canvasBBoxesIntersect(left, right) {
  if (!Array.isArray(left) || !Array.isArray(right) || left.length < 4 || right.length < 4) return false
  return left[0] <= right[2] && left[2] >= right[0] && left[1] <= right[3] && left[3] >= right[1]
}

export function selectCanvasRegions(regions, marquee, { selectedIds = [], additive = false } = {}) {
  const next = (Array.isArray(regions) ? regions : [])
    .filter(region => canvasBBoxesIntersect(region?.bbox, marquee))
    .map(region => region.id)
    .filter(Boolean)
  return additive ? Array.from(new Set([...(selectedIds || []), ...next])) : next
}
