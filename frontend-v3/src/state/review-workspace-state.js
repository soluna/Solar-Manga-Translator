export function mergeRegionCount(previousCount, nextCount) {
  const previous = normalizeCount(previousCount)
  const next = normalizeCount(nextCount)
  return Math.max(previous, next)
}

export function mergePageDocumentRevisions(previousRevisions = {}, pages = []) {
  const nextRevisions = { ...(previousRevisions || {}) }
  for (const page of pages || []) {
    const pageId = String(page?.stored_name || page?.page_id || '').trim()
    const revision = Number(page?.revision || 0)
    if (!pageId || !Number.isFinite(revision) || revision < 0) {
      continue
    }
    nextRevisions[pageId] = Math.max(Number(nextRevisions[pageId] || 0), revision)
  }
  return nextRevisions
}

export function normalizeSessionSourceImages({
  images = [],
  sessionId = '',
  toApiUrl = (url) => url,
} = {}) {
  const sourceImages = Array.isArray(images) ? images : []
  const safeSessionId = String(sessionId || 'session')
  const resolveUrl = typeof toApiUrl === 'function' ? toApiUrl : (url) => url

  return sourceImages.map((image, index) => {
    const regionCount = getImageRegionCount(image)
    return {
      id: `${safeSessionId}-source-${index}`,
      name: image?.name,
      url: resolveUrl(image?.url || ''),
      stored_name: image?.stored_name,
      region_count: regionCount,
      regionCount,
    }
  })
}

export function shouldRefreshBaseImageForTaskAction(action) {
  const normalizedAction = String(action || '').trim().toLowerCase()
  return ['detect', 'translate', 'resume-translate', 'translate-page'].includes(normalizedAction)
}

export function getBaseImageRefreshPageIds({
  action,
  payload = {},
  targetStoredName = '',
} = {}) {
  if (!shouldRefreshBaseImageForTaskAction(action)) {
    return []
  }

  const normalizedAction = String(action || '').trim().toLowerCase()
  const payloadImages = normalizedAction === 'detect'
    ? payload?.images
    : payload?.translated_images
  const pageIds = new Set(
    (Array.isArray(payloadImages) ? payloadImages : [])
      .map((image) => String(image?.stored_name || '').trim())
      .filter(Boolean)
  )
  const normalizedTarget = String(targetStoredName || '').trim()
  if (normalizedTarget) {
    pageIds.add(normalizedTarget)
  }
  return Array.from(pageIds)
}

export function resolveReviewRegionTranslation({
  region = {},
  drafts = {},
  overrides = {},
} = {}) {
  const regionId = String(region?.id || '').trim()
  if (regionId && Object.prototype.hasOwnProperty.call(drafts, regionId)) {
    return String(drafts[regionId] ?? '')
  }
  return String(
    (regionId ? overrides[regionId] : '')
    || region?.current_translation
    || region?.machine_translation
    || ''
  )
}

export function resolvePageEntryCoverUrl(entry = {}, workflowStage = '') {
  const normalizedStage = String(workflowStage || '').trim().toLowerCase()
  if (normalizedStage === 'detecting' || normalizedStage === 'translating') {
    return String(
      entry?.sourceThumbUrl
      || entry?.sourceUrl
      || entry?.blankThumbUrl
      || entry?.blankUrl
      || entry?.previewThumbUrl
      || entry?.previewUrl
      || ''
    )
  }

  const finalUrl = String(entry?.finalThumbUrl || entry?.finalUrl || '').trim()
  if (finalUrl) {
    return finalUrl
  }

  if (normalizedStage === 'detected') {
    return String(
      entry?.blankThumbUrl
      || entry?.blankUrl
      || entry?.sourceThumbUrl
      || entry?.sourceUrl
      || ''
    )
  }

  return String(
    entry?.previewThumbUrl
    || entry?.sourceThumbUrl
    || entry?.blankThumbUrl
    || entry?.previewUrl
    || entry?.sourceUrl
    || entry?.blankUrl
    || ''
  )
}

export function resolvePageListCoverWorkflowStage({
  workflowStage = '',
  translating = false,
  activeAction = '',
} = {}) {
  if (!translating) {
    return String(workflowStage || '')
  }
  return String(activeAction || '').trim().toLowerCase() === 'detect'
    ? 'detecting'
    : 'translating'
}

export function resolveSelectedReviewPage({
  selectedPageKey,
  inspectionPages = [],
  pageEntries = [],
} = {}) {
  const normalizedKey = String(selectedPageKey || '').trim()
  if (!inspectionPages.length && !pageEntries.length) {
    return null
  }

  if (normalizedKey) {
    const loadedPage = inspectionPages.find((page) => page?.stored_name === normalizedKey)
    if (loadedPage) {
      return loadedPage
    }
    const pendingEntry = pageEntries.find((page) => page?.stored_name === normalizedKey)
    if (pendingEntry) {
      return createPendingReviewPage(pendingEntry)
    }
  }

  return inspectionPages[0] || (pageEntries[0] ? createPendingReviewPage(pageEntries[0]) : null)
}

function createPendingReviewPage(entry) {
  return {
    stored_name: String(entry?.stored_name || '').trim(),
    name: String(entry?.name || entry?.stored_name || '未命名页面'),
    source_image_url: String(entry?.sourceUrl || ''),
    base_image_url: String(entry?.blankUrl || entry?.sourceUrl || ''),
    translated_image_url: String(entry?.finalUrl || ''),
    image_url: String(entry?.previewUrl || entry?.finalUrl || entry?.sourceUrl || ''),
    image_width: Number(entry?.image_width || 0),
    image_height: Number(entry?.image_height || 0),
    regions: [],
  }
}

function normalizeCount(value) {
  const numeric = Number(value || 0)
  return Number.isFinite(numeric) && numeric > 0 ? Math.round(numeric) : 0
}

function getImageRegionCount(image) {
  if (!image || typeof image !== 'object') {
    return 0
  }
  return mergeRegionCount(image.region_count, image.regionCount)
}

export const REVIEW_LOCATION_VERSION = 1

/** Keep only serializable, bounded UI state in the per-project work location. */
export function normalizeReviewLocation(value = {}) {
  const source = value && typeof value === 'object' ? value : {}
  const panes = source.panes && typeof source.panes === 'object' ? source.panes : {}
  const panelWidth = Number(source.panelWidth)
  const listScrollTop = Number(source.listScrollTop)
  const normalizedPanes = {
    frame: panes.frame !== false,
    final: panes.final !== false,
    src: Boolean(panes.src),
    blank: Boolean(panes.blank),
  }
  if (!Object.values(normalizedPanes).some(Boolean)) normalizedPanes.frame = true
  return {
    version: REVIEW_LOCATION_VERSION,
    pageId: String(source.pageId || '').trim(),
    regionId: String(source.regionId || '').trim(),
    previousPageId: String(source.previousPageId || '').trim(),
    previousRegionId: String(source.previousRegionId || '').trim(),
    filter: ['all', 'attention', 'manual', 'disabled'].includes(source.filter) ? source.filter : 'all',
    searchQuery: String(source.searchQuery || '').slice(0, 160),
    panelCollapsed: Boolean(source.panelCollapsed),
    panelWidth: Number.isFinite(panelWidth) ? Math.min(520, Math.max(280, Math.round(panelWidth))) : 348,
    listScrollTop: Number.isFinite(listScrollTop) ? Math.max(0, Math.round(listScrollTop)) : 0,
    previewTypography: source.previewTypography !== false,
    panes: normalizedPanes,
  }
}

export function reviewLocationStorageKey(projectId) {
  const id = String(projectId || '').trim()
  return id ? `inkstage-review-location-v${REVIEW_LOCATION_VERSION}:${id}` : ''
}

export function nextIssueRegion(regions = [], currentId = '', { isIssue = (region) => Boolean(region?.issueReason) } = {}) {
  const candidates = (Array.isArray(regions) ? regions : []).filter((region) => isIssue(region))
  if (!candidates.length) return null
  const index = candidates.findIndex((region) => region.id === currentId)
  return candidates[(index + 1 + candidates.length) % candidates.length]
}

export function selectRegionRange(regions = [], anchorId = '', targetId = '', existingIds = []) {
  const list = Array.isArray(regions) ? regions : []
  const anchor = list.findIndex((region) => region?.id === anchorId)
  const target = list.findIndex((region) => region?.id === targetId)
  if (anchor < 0 || target < 0) return new Set(existingIds || [])
  const [start, end] = anchor <= target ? [anchor, target] : [target, anchor]
  const next = new Set(existingIds || [])
  for (const region of list.slice(start, end + 1)) {
    if (region?.id) next.add(region.id)
  }
  return next
}
