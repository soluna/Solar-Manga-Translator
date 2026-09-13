/**
 * Keep the last page opened for each project in browser-local UI state.
 *
 * The project and page identifiers are opaque backend identifiers.  They are
 * stored as strings and only used after a caller validates them against the
 * current project page list.  A broken localStorage implementation must never
 * prevent the workbench from opening a project.
 */
export const RECENT_LOCATION_STORAGE_KEY = 'solar-v3-recent-location'
export const RECENT_PAGE_STORAGE_KEY = RECENT_LOCATION_STORAGE_KEY

const MAX_PROJECTS = 32

function storage() {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null
  } catch {
    return null
  }
}

function readMap() {
  const source = storage()
  if (!source) return {}
  try {
    const value = JSON.parse(source.getItem(RECENT_LOCATION_STORAGE_KEY) || '{}')
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch {
    return {}
  }
}

function writeMap(value) {
  const source = storage()
  if (!source) return
  try { source.setItem(RECENT_LOCATION_STORAGE_KEY, JSON.stringify(value)) } catch { /* optional UI state */ }
}

function id(value) {
  return String(value || '').trim()
}

/** Return a remembered page only when it is still present in the supplied list. */
export function recentPageFor(projectId, pages = null) {
  const project = id(projectId)
  const record = readMap()[project]
  const page = id(record?.pageId || record?.page_id)
  if (!project || !page) return ''
  if (!Array.isArray(pages)) return page
  const available = new Set(pages.map(item => id(item?.stored_name || item?.page_id || item)))
  return available.has(page) ? page : ''
}

/** Remember the page after a user opens it, without changing project data. */
export function rememberRecentPage(projectId, pageId) {
  const project = id(projectId)
  const page = id(pageId)
  if (!project || !page) return false
  const current = readMap()
  const next = {
    ...current,
    [project]: { pageId: page, updatedAt: new Date().toISOString() },
  }
  const entries = Object.entries(next)
    .sort(([, left], [, right]) => String(right?.updatedAt || '').localeCompare(String(left?.updatedAt || '')))
    .slice(0, MAX_PROJECTS)
  writeMap(Object.fromEntries(entries))
  return true
}

export function forgetRecentPage(projectId) {
  const project = id(projectId)
  if (!project) return false
  const current = readMap()
  if (!Object.hasOwn(current, project)) return false
  delete current[project]
  writeMap(current)
  return true
}
