function finiteNumber(value) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

export function resolveRegionFontSizeValue({
  draftValue,
  explicitValue,
  detectedValue,
  fallback = 12,
} = {}) {
  if (draftValue !== undefined) {
    return draftValue
  }
  const explicit = finiteNumber(explicitValue)
  if (explicit !== null) {
    return explicit
  }
  const detected = finiteNumber(detectedValue)
  return detected !== null ? detected : fallback
}

export function resolveRegionRenderFontSize(options = {}) {
  const resolved = finiteNumber(resolveRegionFontSizeValue(options))
  const fallback = finiteNumber(options.fallback) ?? 12
  return Math.max(8, Math.round(resolved ?? fallback))
}
