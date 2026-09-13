/** Synthetic visual data built on a document exported by the real backend.
 * Inference/provider behavior is not simulated; see docs/API-CONTRACT.md.
 */
import { readFileSync } from 'node:fs'
const fixture = JSON.parse(readFileSync(new URL('../test-fixtures/page-document.json', import.meta.url), 'utf8'))

const nowIso = new Date(Date.now() - 86400000).toISOString()

const demoSummary = {
  project_id: 'demo',
  title: '合成演示项目',
  note: '仅供浏览界面；保存、导入、模型处理与导出需连接真实后端',
  review_mode: 'auto',
  created_at: nowIso,
  updated_at: nowIso,
  page_count: 6,
  region_count: 16,
  workflow_stage: 'translated',
  cover_image: '',
  latest_snapshot_id: '',
  latest_snapshot_kind: '',
  latest_snapshot_summary: '',
  snapshot_count: 0,
  glossary_count: 4,
  archived: false,
}

const demoImages = [
  { name: '封面.png', stored_name: 'c001.png', url: '/api/pages/demo/c001.png/source-image', region_count: 2 },
  { name: '第1页.png', stored_name: 'c002.png', url: '/api/pages/demo/c002.png/source-image', region_count: 4 },
  { name: '第2页.png', stored_name: 'c003.png', url: '/api/pages/demo/c003.png/source-image', region_count: 3 },
  { name: '第3页.png', stored_name: 'c004.png', url: '/api/pages/demo/c004.png/source-image', region_count: 5 },
  { name: '第4页.png', stored_name: 'c005.png', url: '/api/pages/demo/c005.png/source-image', region_count: 0 },
  { name: '第5页.png', stored_name: 'c006.png', url: '/api/pages/demo/c006.png/source-image', region_count: 0 },
]

function demoRegion(id, bbox, source_text, translation, extra = {}) {
  const seed = structuredClone(fixture.document.regions[0])
  return {
    ...seed, region_id: id, page_id: 'c002.png', bbox, source_text,
    kind: 'auto', origin: 'automatic',
    translation: typeof translation === 'object' ? translation : { machine: translation, edited: '', resolved: translation },
    direction: extra.direction || 'horizontal',
    recognition: { status: 'ready', error: '', translation_status: extra.translation_status === 'failed' ? 'failed' : 'ready', translation_error: '' },
    style: { ...seed.style, font_size: extra.font_size ?? 22, font_family: extra.font_family || seed.style.font_family,
      font_size_override: null, rotation: 0, alignment: extra.alignment || 'auto' },
    flags: { disabled: false, keep_original: false, translation_enabled: true, preserve_background: false },
  }
}

const demoDocument = {
  metadata: { document_version: 2, revision: 7, updated_at: nowIso, review: { status: 'unreviewed' } },
  page_id: 'c002.png',
  preview_image: '/api/pages/demo/c002.png/preview-image',
  translated_image: '/api/pages/demo/c002.png/translated-image',
  dimensions: { width: 800, height: 1200 },
  base_image: '/api/pages/demo/c002.png/base-image',
  source_image: '/api/pages/demo/c002.png/source-image',
  regions: [
    demoRegion('r1', [64, 92, 532, 168], '欢迎光临「夜帷喫茶店」。', '欢迎光临「夜帷喫茶店」。', { translation_status: 'confirmed' }),
    demoRegion('r2', [88, 210, 508, 296], '今天也请慢慢享受。', '今天也请慢慢享受。', { translation_status: 'translated' }),
    // 真实后端契约：translation 为 {machine, edited, resolved} 字典（取值 resolved > edited > machine）
    demoRegion('r3', [120, 318, 470, 386], '「ミルクたっぷりのコーヒーをどうぞ」',
      { machine: '「请用这杯加了很多牛奶的咖啡。」', edited: '「请用这杯加了大量牛奶的咖啡。」', resolved: '「请用这杯加了大量牛奶的咖啡。」' },
      { translation_status: 'edited' }),
    demoRegion('r4', [96, 404, 500, 460], 'ああ……いい香り。',
      { machine: '啊……好香的味道。', edited: '', resolved: '啊……好香。' },
      { translation_status: 'translated' }),
  ],
}

const artifacts = Object.fromEntries(demoImages.map((image, index) => {
  const state = structuredClone(fixture.page_artifact)
  state.page_id = image.stored_name
  if (index < 4) {
    state.artifacts.final = { ...state.artifacts.final, revision: 1, ready: true, current: true,
      derived_from: { blank: 1, translation: 1, layout: 1 } }
    Object.assign(state.capabilities, { final_available: true, final_ready: true, can_export: true })
  } else {
    for (const name of ['recognition', 'blank', 'translation']) {
      state.artifacts[name] = { ...state.artifacts[name], revision: 0, ready: false, current: false, derived_from: {} }
    }
    for (const key of Object.keys(state.capabilities)) state.capabilities[key] = false
  }
  return [image.stored_name, state]
}))
const images = demoImages.map((image, index) => ({ ...image, region_count: index < 4 ? 4 : 0,
  artifact_state: artifacts[image.stored_name] }))
const glossary = { entries: demoDocument.regions.map(region => ({
  id: region.region_id, source: region.source_text.slice(0, 8), translation: region.translation.resolved.slice(0, 8),
  replacement: '', category: '其他', note: '', source_kind: 'user',
  occurrences: [{ page_id: 'c002.png', region_id: region.region_id, source_text: region.source_text }],
})) }
const fonts = ['SourceHanSansSC-Regular-2.otf', 'SourceHanSansSC-Medium-2.otf', 'SourceHanSansSC-Bold.otf'].map(name => ({
  id: `system:${name}`, name, label: `${name.replace('.otf', '')} (预置)`, source: 'system',
  extension: '.otf', format_hint: 'opentype', url: `/api/fonts/file/system/${name}`,
}))

export default {
  projects: [demoSummary],
  projectViews: { demo: {
    session_id: 'demo', project_head_generation: 3, project_head_revision_id: 'rev-demo-3',
    review_mode: 'auto', total_images: images.length, images,
    translated_images: images.slice(0, 4).map(image => ({ name: image.name, stored_name: image.stored_name,
      url: `/api/pages/demo/${image.stored_name}/translated-image` })),
    workflow_stage: 'translated', artifact_schema_version: 2, page_artifacts: artifacts,
    download_url: '', download_path: '', translated_dir: '', mask_debug_dir: '',
    project: demoSummary, glossary, config: { translator: 'gemini', selected_translator: 'gemini', target_lang: 'CHS' },
    overrides: {}, task: null,
  } },
  pageDocuments: Object.fromEntries(images.map((image, index) => [image.stored_name, {
    ...structuredClone(demoDocument), page_id: image.stored_name,
    regions: index < 4 ? demoDocument.regions.map(region => ({ ...structuredClone(region), page_id: image.stored_name })) : [],
    source_image: `/api/pages/demo/${image.stored_name}/source-image`,
    base_image: index < 4 ? `/api/pages/demo/${image.stored_name}/base-image` : '',
    preview_image: `/api/pages/demo/${image.stored_name}/preview-image`,
    translated_image: index < 4 ? `/api/pages/demo/${image.stored_name}/translated-image` : '',
  }])),
  settings: fixture.settings,
  glossaries: { demo: glossary },
  fonts,
}
