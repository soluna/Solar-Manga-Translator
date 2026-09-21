/** Pure style and preview helpers shared by the review view and regressions. */

const asText = (value) => String(value ?? '').trim()

export function normalizeReviewDirection(value) {
  const normalized = asText(value).toLowerCase()
  if (['vertical', 'v', 'vertical-rl'].includes(normalized)) return 'vertical'
  if (['horizontal', 'h', 'horizontal-tb'].includes(normalized)) return 'horizontal'
  return 'auto'
}

export function resolveLayoutDirectionOverride(region, config) {
  const regionId = asText(region?.id || region?.region_id)
  if (!regionId) return 'auto'

  const latest = config?.overrides
  const hasLatestMap = latest && typeof latest === 'object'
    && Object.prototype.hasOwnProperty.call(latest, 'translation_region_layout_overrides')
  const overrides = hasLatestMap
    ? latest.translation_region_layout_overrides
    : config?.translation_region_layout_overrides
  if (!overrides || typeof overrides !== 'object' || Array.isArray(overrides)) return 'auto'

  const override = overrides[regionId]
  if (!override || typeof override !== 'object') return 'auto'
  return normalizeReviewDirection(override.direction ?? override.text_direction)
}

export function resolveReviewDirection(region = {}, config = {}, draft = {}) {
  const detectedDirection = normalizeReviewDirection(region?.direction)
  const draftDirection = normalizeReviewDirection(draft?.direction)
  const explicitDirectionIntent = normalizeReviewDirection(draft?.directionIntent)
  const hasDirectionIntent = draft?.directionIntent != null

  if (hasDirectionIntent && explicitDirectionIntent !== 'auto') return explicitDirectionIntent

  // A draft is current only when it differs from the saved document value.
  // The saved region direction can also be the detection result when no layout
  // override exists, so it must not be promoted to a manual override here.
  if (draftDirection !== 'auto' && draftDirection !== detectedDirection) return draftDirection

  const layoutDirection = hasDirectionIntent ? 'auto' : resolveLayoutDirectionOverride(region, config)
  if (layoutDirection !== 'auto') return layoutDirection

  const targetLang = asText(config?.target_lang).toUpperCase()
  if (targetLang === 'CHS' || targetLang === 'CHT') return 'vertical'

  if (detectedDirection !== 'auto') return detectedDirection

  const bbox = Array.isArray(region?.bbox) && region.bbox.length === 4
    ? region.bbox.map(Number)
    : [0, 0, 1, 1]
  const width = Math.max(1, (Number.isFinite(bbox[2]) ? bbox[2] : 1) - (Number.isFinite(bbox[0]) ? bbox[0] : 0))
  const height = Math.max(1, (Number.isFinite(bbox[3]) ? bbox[3] : 1) - (Number.isFinite(bbox[1]) ? bbox[1] : 0))
  return height > width * 1.15 ? 'vertical' : 'horizontal'
}

function fontCandidates(font) {
  return [font?.id, font?.name, font?.label, font?.file_name, font?.filename]
    .map(asText)
    .filter(Boolean)
}

function findFontId(candidate, fonts) {
  const value = asText(candidate)
  if (!value) return ''
  const match = (Array.isArray(fonts) ? fonts : []).find((font) => fontCandidates(font).includes(value))
  return asText(match?.id) || (match ? fontCandidates(match)[0] : '')
}

export function resolveReviewFontStyle(region = {}, draft = {}) {
  return asText(
    draft?.fontStyle
      || region?.font_style_override
      || region?.override_style
      || region?.font_style
      || region?.auto_font_style,
  )
}

/** Resolve the same usable font key that the preview and renderer should follow. */
export function resolveReviewFontKey(region = {}, { draft = {}, config = {}, fonts = [] } = {}) {
  const style = resolveReviewFontStyle(region, draft)
  const rawCandidates = [
    draft?.fontKey,
    region?.font_key_override,
    style && config?.[`style_font_${style}_key`],
    config?.font_key,
    region?.font_key,
    region?.font_family,
  ].map(asText).filter(Boolean)

  // Prefer a known ID/name. A stale style mapping must not hide a valid page
  // font that is already present in the document or global configuration.
  for (const candidate of rawCandidates) {
    const matched = findFontId(candidate, fonts)
    if (matched) return matched
  }
  return rawCandidates[0] || ''
}

function normalizeColor(value, fallback) {
  if (Array.isArray(value)) return value.slice(0, 3)
  const text = asText(value)
  if (!/^#[0-9a-f]{6}$/i.test(text)) return fallback.slice()
  return [1, 3, 5].map((index) => Number.parseInt(text.slice(index, index + 2), 16))
}

function finite(value, fallback) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

export function sourceCropStyle(bbox, pageWidth, pageHeight) {
  const values = Array.isArray(bbox) && bbox.length === 4 ? bbox.map(Number) : [0, 0, 1, 1]
  const [x1, y1, x2, y2] = values.map((value) => Number.isFinite(value) ? value : 0)
  const width = Math.max(1, x2 - x1)
  const height = Math.max(1, y2 - y1)
  const fullWidth = Math.max(1, finite(pageWidth, 1))
  const fullHeight = Math.max(1, finite(pageHeight, 1))
  return {
    width: `${(fullWidth / width) * 100}%`,
    height: `${(fullHeight / height) * 100}%`,
    left: `${-(x1 / width) * 100}%`,
    top: `${-(y1 / height) * 100}%`,
  }
}

export function reviewStyleSnapshot(region = {}, {
  draft = {},
  config = {},
  fonts = [],
} = {}) {
  const advanced = {
    rotation: finite(draft?.rotation, finite(region?.rotation, 0)),
    stroke_width: finite(draft?.strokeWidth, finite(region?.stroke_width, 0.2)),
    letter_spacing: finite(draft?.letterSpacing, finite(region?.letter_spacing, 1)),
    line_spacing: finite(draft?.lineSpacing, finite(region?.line_spacing, 1)),
    fg_color: normalizeColor(draft?.fgColor, Array.isArray(region?.fg_color) ? region.fg_color : [0, 0, 0]),
    bg_color: normalizeColor(draft?.bgColor, Array.isArray(region?.bg_color) ? region.bg_color : [255, 255, 255]),
    preserve_background: draft?.preserveBackground == null
      ? Boolean(region?.preserve_background)
      : Boolean(draft.preserveBackground),
  }
  return {
    enabled: draft?.enabled == null ? !Boolean(region?.disabled) : Boolean(draft.enabled),
    direction: resolveReviewDirection(region, config, draft),
    fontKey: resolveReviewFontKey(region, { draft, config, fonts }),
    fontSize: Math.max(8, Math.round(finite(draft?.fontSize, finite(region?.font_size, 12)))),
    fontStyle: resolveReviewFontStyle(region, draft),
    advanced,
  }
}

export function buildReviewStyleCommands(regionId, snapshot = {}) {
  const id = asText(regionId)
  if (!id) return []
  const advanced = snapshot.advanced && typeof snapshot.advanced === 'object' ? snapshot.advanced : snapshot
  return [
    { type: snapshot.enabled === false ? 'disable_region' : 'restore_region', region_id: id },
    { type: 'update_text_direction', region_id: id, direction: normalizeReviewDirection(snapshot.direction) },
    { type: 'update_region_font', region_id: id, font_key: asText(snapshot.fontKey) },
    { type: 'update_font_size', region_id: id, font_size: snapshot.fontSize == null
      ? null : Math.max(8, Math.round(finite(snapshot.fontSize, 12))) },
    { type: 'update_font_style', region_id: id, style: asText(snapshot.fontStyle) },
    {
      type: 'update_region_style',
      region_id: id,
      rotation: finite(advanced.rotation, 0),
      stroke_width: finite(advanced.stroke_width, 0.2),
      letter_spacing: finite(advanced.letter_spacing, 1),
      line_spacing: finite(advanced.line_spacing, 1),
      fg_color: normalizeColor(advanced.fg_color, [0, 0, 0]),
      bg_color: normalizeColor(advanced.bg_color, [255, 255, 255]),
      preserve_background: Boolean(advanced.preserve_background),
    },
  ]
}
