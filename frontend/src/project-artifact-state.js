export const PROJECT_ARTIFACT_SCHEMA_VERSION = 2

const artifactRevision = (revision = 0, derivedFrom = {}) => ({
  revision,
  content_hash: '',
  derived_from: derivedFrom,
})

const newPageArtifactState = (pageId) => ({
  page_id: pageId,
  source: artifactRevision(1),
  recognition: artifactRevision(),
  blank: artifactRevision(),
  translation: artifactRevision(),
  final: artifactRevision(),
  layout_revision: 1,
})

function requireProjectArtifactState(rawState) {
  if (!rawState || typeof rawState !== 'object' || Array.isArray(rawState)) {
    throw new TypeError('Project artifact state must be an object')
  }
  if (rawState.schema_version !== PROJECT_ARTIFACT_SCHEMA_VERSION) {
    throw new RangeError(`Unsupported project artifact schema version: ${String(rawState.schema_version)}`)
  }
  if (!rawState.pages || typeof rawState.pages !== 'object' || Array.isArray(rawState.pages)) {
    throw new TypeError('Project artifact pages must be an object')
  }
  return rawState
}

function requirePageArtifactState(rawState, pageId) {
  const state = requireProjectArtifactState(rawState)
  const normalizedPageId = String(pageId || '').trim()
  const page = state.pages[normalizedPageId]
  if (!normalizedPageId || !page || page.page_id !== normalizedPageId) {
    throw new RangeError(`Unknown page artifact: ${normalizedPageId}`)
  }
  return { state, page, pageId: normalizedPageId }
}

function cloneProjectArtifactState(state) {
  return {
    schema_version: state.schema_version,
    pages: Object.fromEntries(Object.entries(state.pages).map(([pageId, page]) => [
      pageId,
      {
        ...page,
        source: { ...page.source, derived_from: { ...page.source.derived_from } },
        recognition: { ...page.recognition, derived_from: { ...page.recognition.derived_from } },
        blank: { ...page.blank, derived_from: { ...page.blank.derived_from } },
        translation: { ...page.translation, derived_from: { ...page.translation.derived_from } },
        final: { ...page.final, derived_from: { ...page.final.derived_from } },
      },
    ])),
  }
}

export function createProjectArtifactState(pageIds) {
  if (!Array.isArray(pageIds)) {
    throw new TypeError('Page artifact state requires page ids')
  }
  const normalizedPageIds = pageIds.map((pageId) => String(pageId || '').trim())
  if (normalizedPageIds.some((pageId) => !pageId)) {
    throw new TypeError('Page artifact state requires non-empty page ids')
  }
  if (new Set(normalizedPageIds).size !== normalizedPageIds.length) {
    throw new TypeError('Page artifact state requires unique page ids')
  }
  return {
    schema_version: PROJECT_ARTIFACT_SCHEMA_VERSION,
    pages: Object.fromEntries(normalizedPageIds.map((pageId) => [
      pageId,
      newPageArtifactState(pageId),
    ])),
  }
}

export function restoreProjectArtifactState(rawState) {
  const state = requireProjectArtifactState(rawState)
  for (const [pageId, page] of Object.entries(state.pages)) {
    if (!page || typeof page !== 'object' || Array.isArray(page) || page.page_id !== pageId) {
      throw new TypeError(`Invalid page artifact state: ${pageId}`)
    }
  }
  return cloneProjectArtifactState(state)
}

export function applyProjectArtifactEvent(rawState, pageId, rawEvent) {
  const { state, page, pageId: normalizedPageId } = requirePageArtifactState(rawState, pageId)
  const event = String(rawEvent || '').trim()
  if (![
    'recognized',
    'source_edited',
    'translated',
    'translation_edited',
    'layout_edited',
    'style_edited',
    'rendered',
    'blank_attached',
    'text_region_deleted',
    'text_regions_merged',
  ].includes(event)) {
    throw new RangeError(`Unsupported page artifact event: ${event}`)
  }

  const nextState = cloneProjectArtifactState(state)
  if (event === 'recognized') {
    nextState.pages[normalizedPageId] = {
      ...page,
      recognition: artifactRevision(page.recognition.revision + 1, {
        source: page.source.revision,
      }),
      blank: artifactRevision(page.blank.revision + 1, {
        source: page.source.revision,
        recognition: page.recognition.revision + 1,
      }),
    }
    return nextState
  }

  const currentView = getPageArtifactView(state, normalizedPageId)
  if (event === 'blank_attached') {
    nextState.pages[normalizedPageId] = {
      ...page,
      blank: artifactRevision(page.blank.revision + 1, {
        source: page.source.revision,
      }),
    }
    return nextState
  }

  if (event === 'layout_edited' || event === 'style_edited') {
    nextState.pages[normalizedPageId] = {
      ...page,
      layout_revision: page.layout_revision + 1,
    }
    return nextState
  }

  if (event === 'source_edited') {
    if (!currentView.capabilities.recognition_ready) {
      throw new RangeError(`source_edited requires current recognition for page ${normalizedPageId}`)
    }
    const recognition = artifactRevision(page.recognition.revision + 1, {
      source: page.source.revision,
    })
    let blank = page.blank
    if (currentView.capabilities.blank_ready) {
      const derivedFrom = { source: page.source.revision }
      if (Object.prototype.hasOwnProperty.call(page.blank.derived_from, 'recognition')) {
        derivedFrom.recognition = recognition.revision
      }
      blank = { ...page.blank, derived_from: derivedFrom }
    }
    nextState.pages[normalizedPageId] = {
      ...page,
      recognition,
      blank,
    }
    return nextState
  }

  if (event === 'text_region_deleted' || event === 'text_regions_merged') {
    if (!currentView.capabilities.recognition_ready) {
      throw new RangeError(`${event} requires current recognition for page ${normalizedPageId}`)
    }
    nextState.pages[normalizedPageId] = {
      ...page,
      recognition: artifactRevision(page.recognition.revision + 1, {
        source: page.source.revision,
      }),
    }
    return nextState
  }

  if (event === 'rendered') {
    if (!currentView.capabilities.can_render) {
      throw new RangeError(`rendered requires current blank and translation for page ${normalizedPageId}`)
    }
    nextState.pages[normalizedPageId] = {
      ...page,
      final: artifactRevision(page.final.revision + 1, {
        blank: page.blank.revision,
        translation: page.translation.revision,
        layout: page.layout_revision,
      }),
    }
    return nextState
  }

  if (!currentView.capabilities.can_translate) {
    throw new RangeError(`translated requires current recognition and blank for page ${normalizedPageId}`)
  }
  const translation = artifactRevision(page.translation.revision + 1, {
    recognition: page.recognition.revision,
  })
  if (event === 'translation_edited') {
    nextState.pages[normalizedPageId] = { ...page, translation }
    return nextState
  }
  nextState.pages[normalizedPageId] = {
    ...page,
    translation,
    final: artifactRevision(page.final.revision + 1, {
      blank: page.blank.revision,
      translation: translation.revision,
      layout: page.layout_revision,
    }),
  }
  return nextState
}

function artifactView(artifact, current) {
  const ready = artifact.revision > 0
  return {
    revision: artifact.revision,
    ready,
    current,
    stale: ready && !current,
    content_hash: artifact.content_hash,
    derived_from: { ...artifact.derived_from },
  }
}

function isCurrent(artifact, dependencies) {
  if (!(artifact.revision > 0)) {
    return false
  }
  const entries = Object.entries(dependencies)
  return entries.length === Object.keys(artifact.derived_from).length
    && entries.every(([name, revision]) => artifact.derived_from[name] === revision)
}

export function getPageArtifactView(rawState, pageId) {
  const { page, pageId: normalizedPageId } = requirePageArtifactState(rawState, pageId)
  const recognitionReady = isCurrent(page.recognition, { source: page.source.revision })
  const blankDependencies = { source: page.source.revision }
  if (Object.prototype.hasOwnProperty.call(page.blank.derived_from, 'recognition')) {
    blankDependencies.recognition = page.recognition.revision
  }
  const blankReady = isCurrent(page.blank, blankDependencies)
  const translationReady = recognitionReady && isCurrent(page.translation, {
    recognition: page.recognition.revision,
  })
  const finalReady = blankReady && translationReady && isCurrent(page.final, {
    blank: page.blank.revision,
    translation: page.translation.revision,
    layout: page.layout_revision,
  })
  const finalAvailable = page.final.revision > 0
  return {
    page_id: normalizedPageId,
    artifacts: {
      source: artifactView(page.source, page.source.revision > 0),
      recognition: artifactView(page.recognition, recognitionReady),
      blank: artifactView(page.blank, blankReady),
      translation: artifactView(page.translation, translationReady),
      final: artifactView(page.final, finalReady),
    },
    capabilities: {
      recognition_ready: recognitionReady,
      blank_ready: blankReady,
      translation_ready: translationReady,
      final_available: finalAvailable,
      final_ready: finalReady,
      final_stale: finalAvailable && !finalReady,
      can_review_recognition: recognitionReady && blankReady,
      can_translate: recognitionReady && blankReady,
      can_render: blankReady && translationReady,
      can_export: finalReady,
    },
  }
}

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
  if (schemaVersion !== PROJECT_ARTIFACT_SCHEMA_VERSION) {
    return false
  }
  const pages = Object.values(normalizePageArtifacts(rawArtifacts))
  return pages.length > 0 && pages.every((page) => Boolean(page?.capabilities?.can_export))
}
