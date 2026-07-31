const PROJECT_ID = 'zero-text-regions-e2e-fixture'
const PAGE_ID = '0001.png'
const PROJECT_TITLE = 'Zero Text Regions E2E Fixture'
const PAGE_NAME = '微信图片_20260725215741_101_3.png'

const emptyOverrides = () => ({
  translation_region_overrides: {},
  translation_region_skip_overrides: {},
  translation_region_disabled_overrides: {},
  translation_region_layout_overrides: {},
  style_region_overrides: {},
})

function currentArtifact(revision, derivedFrom = {}) {
  return {
    revision,
    ready: true,
    current: true,
    stale: false,
    content_hash: `fixture-${revision}`,
    derived_from: derivedFrom,
  }
}

function jsonResponse(json) {
  return {
    status: 200,
    contentType: 'application/json; charset=utf-8',
    body: JSON.stringify(json),
  }
}

export function createZeroTextRegionsWorkspaceFixture() {
  const sourcePath = `/api/pages/${PROJECT_ID}/${PAGE_ID}/source-image`
  const blankPath = `/api/pages/${PROJECT_ID}/${PAGE_ID}/base-image`
  const translatedPath = `/api/pages/${PROJECT_ID}/${PAGE_ID}/translated-image`
  const previewPath = `/api/pages/${PROJECT_ID}/${PAGE_ID}/preview-image`
  const localAdvancedPath = `/api/pages/${PROJECT_ID}/${PAGE_ID}/advanced-erase`
  const eraseSelectionSuggestPath = `${localAdvancedPath}/suggest-selection`
  const localAdvancedAttemptId = 'fixture-local-advanced-preview'
  const localAdvancedPreviewBase = `${localAdvancedPath}/previews/${localAdvancedAttemptId}`
  const downloadPath = `/api/download/${PROJECT_ID}`
  const pageArtifact = {
    page_id: PAGE_ID,
    artifacts: {
      source: currentArtifact(1),
      recognition: currentArtifact(1, { source: 1 }),
      blank: currentArtifact(1, { source: 1, recognition: 1 }),
      translation: currentArtifact(1, { recognition: 1 }),
      final: currentArtifact(1, { blank: 1, translation: 1, layout: 1 }),
    },
    capabilities: {
      recognition_ready: true,
      blank_ready: true,
      translation_ready: true,
      final_available: true,
      final_ready: true,
      final_stale: false,
      can_review_recognition: true,
      can_translate: true,
      can_render: true,
      can_export: true,
    },
  }
  const project = {
    project_id: PROJECT_ID,
    title: PROJECT_TITLE,
    note: 'A deterministic completed project whose Page Document has zero Text Regions.',
    review_mode: 'canvas_beta',
    created_at: '2026-07-15T00:00:00+00:00',
    updated_at: '2026-07-15T00:00:00+00:00',
    page_count: 1,
    region_count: 0,
    workflow_stage: 'translated',
    cover_image: sourcePath,
    snapshot_count: 0,
    glossary_count: 0,
    archived: false,
    is_busy: false,
    busy_action: '',
  }
  const inspectionPage = {
    name: PAGE_NAME,
    stored_name: PAGE_ID,
    image_url: translatedPath,
    source_image_url: sourcePath,
    base_image_url: blankPath,
    translated_image_url: translatedPath,
    preview_image_url: previewPath,
    image_width: 640,
    image_height: 960,
    document_revision: 1,
    regions: [],
    artifact_state: pageArtifact,
  }
  const sessionPayload = {
    session_id: PROJECT_ID,
    review_mode: 'canvas_beta',
    total_images: 1,
    images: [{
      name: PAGE_NAME,
      stored_name: PAGE_ID,
      url: sourcePath,
      region_count: 0,
      artifact_state: pageArtifact,
    }],
    translated_images: [{
      id: `${PROJECT_ID}-translated-${PAGE_ID}`,
      name: PAGE_NAME,
      stored_name: PAGE_ID,
      url: translatedPath,
    }],
    workflow_stage: 'translated',
    artifact_schema_version: 2,
    page_artifacts: { [PAGE_ID]: pageArtifact },
    download_url: downloadPath,
    download_path: '',
    translated_dir: '',
    mask_debug_dir: '',
    project,
    glossary: { entries: [], occurrences_loaded: true },
    config: {
      target_lang: 'CHS',
      rerender_output_format: 'png',
      font_style_mode: 'manual',
    },
    overrides: emptyOverrides(),
  }
  const inspectionPayload = {
    workflow_stage: 'translated',
    pages: [inspectionPage],
    overrides: emptyOverrides(),
  }
  const imageBody = [
    '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="960">',
    '<rect width="640" height="960" fill="#f7f3ea"/>',
    '<text x="320" y="460" text-anchor="middle" font-size="28" fill="#334155">Zero Text Regions</text>',
    '<text x="320" y="510" text-anchor="middle" font-size="20" fill="#64748b">Translated page is complete</text>',
    '</svg>',
  ].join('')
  const imageResponse = {
    status: 200,
    contentType: 'image/svg+xml; charset=utf-8',
    body: imageBody,
  }
  const maskOverlayResponse = {
    status: 200,
    contentType: 'image/svg+xml; charset=utf-8',
    body: [
      '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="960">',
      '<rect x="245" y="410" width="150" height="120" rx="18" fill="#f84a48" fill-opacity="0.52"/>',
      '</svg>',
    ].join(''),
  }
  const downloadFilename = `${PROJECT_ID}-translated.zip`
  const routeResponses = {
    [`POST /api/projects/${PROJECT_ID}/restore`]: jsonResponse(sessionPayload),
    [`GET /api/projects/${PROJECT_ID}/task`]: jsonResponse({ task: null }),
    [`POST /api/review-regions/${PROJECT_ID}`]: jsonResponse(inspectionPayload),
    [`POST /api/style-regions/${PROJECT_ID}`]: jsonResponse(inspectionPayload),
    [`GET ${sourcePath}`]: imageResponse,
    [`GET ${blankPath}`]: imageResponse,
    [`GET ${translatedPath}`]: imageResponse,
    [`GET ${previewPath}`]: imageResponse,
    [`POST ${eraseSelectionSuggestPath}`]: jsonResponse({
      selection: {
        x: 0.34,
        y: 0.42,
        width: 0.32,
        height: 0.12,
        source: 'detector',
        confidence: 0.96,
      },
    }),
    [`POST ${localAdvancedPath}`]: jsonResponse({
      advanced_erase: {
        action: 'local-advanced-preview',
        page_id: PAGE_ID,
        attempt_id: localAdvancedAttemptId,
        model: 'lama_large',
        device: 'cuda',
        detector_fallback_used: false,
        inpainting_size: 2048,
        fallback_used: false,
        erase_ratio: 0.031,
        included_region_count: 3,
        skipped_region_count: 1,
        preview: {
          source_url: `${localAdvancedPreviewBase}/source`,
          current_url: `${localAdvancedPreviewBase}/current`,
          candidate_url: `${localAdvancedPreviewBase}/candidate`,
          mask_url: `${localAdvancedPreviewBase}/mask`,
          overlay_url: `${localAdvancedPreviewBase}/overlay`,
        },
      },
    }),
    [`GET ${localAdvancedPreviewBase}/source`]: imageResponse,
    [`GET ${localAdvancedPreviewBase}/current`]: imageResponse,
    [`GET ${localAdvancedPreviewBase}/candidate`]: imageResponse,
    [`GET ${localAdvancedPreviewBase}/mask`]: maskOverlayResponse,
    [`GET ${localAdvancedPreviewBase}/overlay`]: maskOverlayResponse,
    [`GET ${downloadPath}`]: {
      status: 200,
      contentType: 'application/zip',
      headers: {
        'Content-Disposition': `attachment; filename="${downloadFilename}"`,
      },
      body: 'PK\u0005\u0006\u0000\u0000\u0000\u0000\u0000\u0000\u0000\u0000\u0000\u0000\u0000\u0000\u0000\u0000\u0000\u0000\u0000\u0000',
    },
  }

  return {
    project,
    pageName: PAGE_NAME,
    sessionPayload,
    routeResponses,
    downloadFilename,
    requiredRouteKeys: [
      'GET /api/projects',
      `POST /api/projects/${PROJECT_ID}/restore`,
      `GET /api/projects/${PROJECT_ID}/task`,
      `POST /api/review-regions/${PROJECT_ID}`,
      `POST ${eraseSelectionSuggestPath}`,
      `POST ${localAdvancedPath}`,
      `GET ${downloadPath}`,
    ],
  }
}
