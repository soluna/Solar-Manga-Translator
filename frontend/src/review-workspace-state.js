export function mergeRegionCount(previousCount, nextCount) {
  const previous = normalizeCount(previousCount)
  const next = normalizeCount(nextCount)
  return Math.max(previous, next)
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
    translated_image_url: String(entry?.finalUrl || entry?.previewUrl || ''),
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
