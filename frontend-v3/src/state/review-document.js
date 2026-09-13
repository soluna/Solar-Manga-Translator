/**
 * Canonical Page Document -> Review editor projection.
 *
 * The backend owns the canonical shape.  ReviewView uses the small, stable
 * projection returned here so one missing field can never make every region
 * share an `undefined` key or silently reset nested style/flag values.
 */

export const PAGE_COMMAND_TYPES = Object.freeze([
  'update_source_text',
  'update_translation',
  'set_keep_original',
  'disable_region',
  'restore_region',
  'update_region_bbox',
  'update_font_size',
  'update_text_direction',
  'update_region_font',
  'update_region_style',
  'update_font_style',
  'create_region',
  'duplicate_region',
  'merge_regions',
  'delete_manual_region',
  'retry_region_ocr',
  'restore_region_ocr_snapshot',
  'set_review_status',
  'recognize_manual_region',
  'restore_manual_region',
])

const PAGE_COMMAND_SET = new Set(PAGE_COMMAND_TYPES)

export function assertSupportedPageCommands(commands = []) {
  if (!Array.isArray(commands) || !commands.length) {
    throw new Error('至少需要一条页面命令。')
  }
  for (const command of commands) {
    const type = String(command?.type || '').trim().toLowerCase()
    if (!PAGE_COMMAND_SET.has(type)) {
      throw new Error(`暂不支持的页面命令：${type || '（空）'}`)
    }
  }
  return commands
}

export function getPageDocumentRevision(document) {
  const revision = Number(document?.metadata?.revision)
  return Number.isFinite(revision) && revision >= 0 ? Math.trunc(revision) : 0
}

export function getPageDocumentId(document) {
  return String(document?.page_id || '').trim()
}

export function getRegionId(region) {
  return String(region?.region_id || '').trim()
}

function asText(value) {
  return String(value ?? '').replace(/\r\n?/g, '\n')
}

function asNumber(value, fallback = 0) {
  const number = Number(value)
  return Number.isFinite(number) ? number : fallback
}

function asInt(value, fallback = 0) {
  return Math.round(asNumber(value, fallback))
}

function asBBox(value) {
  if (!Array.isArray(value) || value.length !== 4) return [0, 0, 0, 0]
  return value.map((item) => asInt(item, 0))
}

function resolveTranslation(translation) {
  const source = translation && typeof translation === 'object' ? translation : {}
  const machine = asText(source.machine)
  const edited = asText(source.edited)
  const resolved = asText(source.resolved)
  return {
    machine,
    edited,
    resolved: resolved || edited || machine,
  }
}

function styleHas(style, name) {
  return String(style || '').toLowerCase().split(/[\s,|]+/).includes(name)
}

function projectRegion(region, index) {
  const source = region && typeof region === 'object' ? region : {}
  const id = getRegionId(source)
  const translation = resolveTranslation(source.translation)
  const style = source.style && typeof source.style === 'object' ? source.style : {}
  const recognition = source.recognition && typeof source.recognition === 'object'
    ? source.recognition
    : {}
  const flags = source.flags && typeof source.flags === 'object' ? source.flags : {}
  const origin = asText(source.origin || '').trim() || (
    source.kind === 'manual' ? 'user' : source.kind === 'merged' ? 'derived' : 'automatic'
  )
  const fontStyle = asText(style.font_style || style.font_style_override || style.auto_font_style)
  const fontKey = asText(style.font_key_override || style.font_family).trim()
  const rawDirection = asText(source.direction || 'auto').trim().toLowerCase()
  const direction = ({ h: 'horizontal', v: 'vertical' })[rawDirection] || rawDirection || 'auto'
  const disabled = Boolean(flags.disabled)
  const keepOriginal = Boolean(flags.keep_original)
  const fontSize = Math.max(8, asInt(style.font_size, 12))

  return {
    // `id` is an editor alias of the canonical `region_id`; it is never read
    // from a top-level canonical field.
    id,
    region_id: id,
    // The server's order is the stable display order when no explicit order
    // field exists.  Keep both a zero-based sequence and a human-facing
    // number so filtering never renumbers regions on screen.
    index: Number.isFinite(Number(source.index)) ? Number(source.index) : index,
    sequence: index,
    number: index + 1,
    kind: asText(source.kind || 'auto'),
    origin,
    direction,
    source_ids: Array.isArray(source.source_ids) ? source.source_ids.map(asText).filter(Boolean) : [],
    bbox: asBBox(source.bbox),
    polygon: source.polygon ?? null,
    source_text: asText(source.source_text),
    ocr_confidence: asNumber(source.ocr_confidence, 0),
    translation,
    machine_translation: translation.machine,
    override_translation: translation.edited,
    current_translation: translation.resolved,
    resolved_translation: translation.resolved,
    recognition,
    recognition_status: asText(recognition.status),
    recognition_error: asText(recognition.error),
    translation_status: asText(recognition.translation_status),
    translation_error: asText(recognition.translation_error),
    style,
    auto_style: {
      bold: styleHas(style.auto_font_style, 'bold'),
      italic: styleHas(style.auto_font_style, 'italic'),
    },
    resolved_style: {
      bold: styleHas(fontStyle, 'bold'),
      italic: styleHas(fontStyle, 'italic'),
    },
    override_style: asText(style.font_style_override),
    font_style: fontStyle,
    font_family: asText(style.font_family),
    font_key: fontKey,
    font_key_override: asText(style.font_key_override),
    font_size: fontSize,
    detected_font_size: Math.max(8, asInt(style.detected_font_size, fontSize)),
    font_size_override: style.font_size_override == null
      ? null
      : Math.max(8, asInt(style.font_size_override, fontSize)),
    alignment: asText(style.alignment || 'auto'),
    letter_spacing: asNumber(style.letter_spacing, 1),
    line_spacing: asNumber(style.line_spacing, 1),
    fg_color: Array.isArray(style.fg_color) ? style.fg_color.slice(0, 3) : [0, 0, 0],
    bg_color: Array.isArray(style.bg_color) ? style.bg_color.slice(0, 3) : [255, 255, 255],
    stroke_width: asNumber(style.stroke_width, 0.2),
    rotation: asNumber(style.rotation, 0),
    flags,
    disabled,
    keep_original: keepOriginal,
    override_skip: keepOriginal,
    translation_enabled: flags.translation_enabled == null
      ? !disabled && !keepOriginal
      : Boolean(flags.translation_enabled),
    preserve_background: Boolean(flags.preserve_background),
    is_manual: origin !== 'automatic',
    manual: origin !== 'automatic',
    audit: source.audit && typeof source.audit === 'object' ? source.audit : {},
    canonical: source,
  }
}

/**
 * Convert one canonical GET /document response into the editor page model.
 */
export function projectPageDocument(input, { pageName = '', artifact = null } = {}) {
  const document = input?.document && typeof input.document === 'object'
    ? input.document
    : input
  if (!document || typeof document !== 'object') return null
  const pageId = getPageDocumentId(document)
  if (!pageId) return null
  if (!Array.isArray(document.regions) || !Number.isInteger(document.metadata?.revision)) {
    throw new Error('页面文档缺少有效的文本区域列表或版本。')
  }
  const ids = document.regions.map(getRegionId)
  if (ids.some(id => !id) || new Set(ids).size !== ids.length) {
    throw new Error('页面文本区域缺少唯一标识，请检查文档接口。')
  }
  const dimensions = document.dimensions && typeof document.dimensions === 'object'
    ? document.dimensions
    : {}
  const metadata = document.metadata && typeof document.metadata === 'object' ? document.metadata : {}
  const reviewState = metadata.review && typeof metadata.review === 'object'
    ? metadata.review
    : document.review_state && typeof document.review_state === 'object'
      ? document.review_state
      : { status: String(document.review_status || 'unreviewed') }
  const reviewStatus = ['reviewed', 'unreviewed'].includes(String(reviewState.status || '').toLowerCase())
    ? String(reviewState.status).toLowerCase()
    : 'unreviewed'
  const regions = document.regions.map(projectRegion)
  return {
    page_id: pageId,
    stored_name: pageId,
    name: asText(pageName || pageId),
    revision: getPageDocumentRevision(document),
    metadata,
    review_state: { ...reviewState, status: reviewStatus },
    review_status: reviewStatus,
    dimensions: {
      width: Math.max(0, asInt(dimensions.width, 0)),
      height: Math.max(0, asInt(dimensions.height, 0)),
    },
    source_image: asText(document.source_image),
    base_image: asText(document.base_image || document.source_image),
    preview_image: asText(document.preview_image || document.source_image),
    translated_image: asText(document.translated_image),
    regions,
    erase_regions: Array.isArray(document.erase_regions) ? document.erase_regions : [],
    artifact: artifact || null,
    canonical: document,
  }
}

export function projectPageCommandResponse(payload, options = {}) {
  if (!payload || typeof payload !== 'object') return null
  const page = projectPageDocument(payload.document, options)
  if (!page) return null
  return {
    ...page,
    artifact: payload.page_artifact || options.artifact || null,
    commandResponse: payload,
  }
}

export function commandRegionIds(commands = []) {
  const ids = new Set()
  for (const command of commands) {
    const regionId = String(command?.region_id || '').trim()
    if (regionId) ids.add(regionId)
    for (const id of Array.isArray(command?.region_ids) ? command.region_ids : []) {
      const normalized = String(id || '').trim()
      if (normalized) ids.add(normalized)
    }
  }
  return Array.from(ids)
}

export function artifactRevision(artifact, kind = 'final') {
  const artifacts = artifact?.artifacts && typeof artifact.artifacts === 'object'
    ? artifact.artifacts
    : artifact || {}
  const candidate = artifacts?.[kind]
  const revision = Number(candidate?.revision ?? candidate)
  return Number.isFinite(revision) && revision >= 0 ? Math.trunc(revision) : 0
}

export function pageImageRevision({ document, artifact, kind = 'final' } = {}) {
  const documentRevision = getPageDocumentRevision(document?.canonical || document)
  const artifactKind = kind === 'source-image'
    ? 'source'
    : kind === 'base-image'
      ? 'blank'
      : kind === 'preview-image' || kind === 'translated-image'
        ? 'final'
        : 'final'
  const revision = (artifact?.artifacts || artifact)?.[artifactKind]
  if (revision != null) return `${artifactRevision(artifact, artifactKind)}:${revision.content_hash || ''}`
  return artifactKind === 'source' ? 'source' : `d${documentRevision}`
}

export function buildStablePageImageUrl({
  sessionId,
  pageId,
  kind,
  maxSide,
  document,
  artifact,
  toApiUrl = (value) => value,
  withImagePreviewSize = (value) => value,
} = {}) {
  const sid = encodeURIComponent(String(sessionId || ''))
  const pid = encodeURIComponent(String(pageId || ''))
  const path = `/api/pages/${sid}/${pid}/${String(kind || 'source-image')}`
  const base = toApiUrl(path)
  const separator = base.includes('?') ? '&' : '?'
  const keyed = `${base}${separator}artifact_revision=${encodeURIComponent(pageImageRevision({ document, artifact, kind }))}`
  return withImagePreviewSize(keyed, maxSide)
}

export function mergePageDrafts({ drafts = {}, incomingDocument, dirtyIds = [] } = {}) {
  const dirty = new Set((dirtyIds || []).map((id) => String(id || '').trim()).filter(Boolean))
  const validIds = new Set((incomingDocument?.regions || []).map((region) => region.id))
  const next = {}
  for (const [id, draft] of Object.entries(drafts || {})) {
    if (dirty.has(id) || validIds.has(id)) next[id] = draft
  }
  return next
}

/**
 * Resolve text for issue checks and compact review cards.  Keeping this here
 * avoids each view making a different choice between machine/edited/resolved
 * translation fields.
 */
export function resolveRegionText(region = {}) {
  const translation = region?.translation
  if (translation && typeof translation === 'object') {
    return String(
      translation.resolved
      || translation.edited
      || translation.machine
      || region?.current_translation
      || region?.machine_translation
      || '',
    ).trim()
  }
  return String(region?.current_translation || region?.machine_translation || translation || '').trim()
}

/**
 * Return a user-facing reason for an item in the attention queue.  Empty OCR
 * text alone is not a failure; only an explicit status/error or missing
 * translation after a ready status is actionable.
 */
export function regionIssueReason(region = {}) {
  const recognition = region?.recognition && typeof region.recognition === 'object'
    ? region.recognition
    : {}
  const recognitionStatus = String(region?.recognition_status || recognition.status || '').trim().toLowerCase()
  const recognitionError = String(region?.recognition_error || recognition.error || '').trim()
  if (['failed', 'error'].includes(recognitionStatus) || recognitionError) {
    return `OCR 失败${recognitionError ? `：${recognitionError}` : ''}`
  }
  if (['running', 'processing'].includes(recognitionStatus)) return 'OCR 处理中'
  if (['pending', 'queued'].includes(recognitionStatus)) return '等待 OCR'

  const translationStatus = String(
    region?.translation_status || recognition.translation_status || '',
  ).trim().toLowerCase()
  const translationError = String(
    region?.translation_error || recognition.translation_error || '',
  ).trim()
  if (['failed', 'error'].includes(translationStatus) || translationError) {
    return `翻译失败${translationError ? `：${translationError}` : ''}`
  }
  if (region?.source_text && !region?.keep_original && ['ready', 'translated', 'completed', 'success'].includes(translationStatus)
    && !resolveRegionText(region)) {
    return '缺少译文'
  }
  return ''
}

export function regionNeedsAttention(region = {}) {
  return Boolean(regionIssueReason(region))
}
