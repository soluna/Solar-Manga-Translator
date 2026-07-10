export function normalizePageArtifacts(rawArtifacts) {
  if (!rawArtifacts || typeof rawArtifacts !== 'object' || Array.isArray(rawArtifacts)) {
    return {}
  }

  const normalized = {}
  for (const [fallbackPageId, rawPage] of Object.entries(rawArtifacts)) {
    if (!rawPage || typeof rawPage !== 'object' || Array.isArray(rawPage)) {
      continue
    }
    const pageId = String(rawPage.page_id || fallbackPageId || '').trim()
    if (!pageId) {
      continue
    }
    normalized[pageId] = rawPage
  }
  return normalized
}

export function mergePageArtifact(currentArtifacts, rawPageArtifact) {
  const current = normalizePageArtifacts(currentArtifacts)
  if (!rawPageArtifact || typeof rawPageArtifact !== 'object' || Array.isArray(rawPageArtifact)) {
    return current
  }
  const pageId = String(rawPageArtifact.page_id || '').trim()
  if (!pageId) {
    return current
  }
  return {
    ...current,
    [pageId]: rawPageArtifact,
  }
}

export function projectArtifactsAllowExport(schemaVersion, rawArtifacts) {
  if (!(Number(schemaVersion) > 0)) {
    return true
  }
  const pages = Object.values(normalizePageArtifacts(rawArtifacts))
  return pages.length > 0 && pages.every((page) => Boolean(page?.capabilities?.can_export))
}
