/** Mock 数据 —— 仅用于无后端时的视觉验证（形状贴近 backend 契约，见 docs/API-CONTRACT.md）。 */

const nowIso = new Date(Date.now() - 86400000).toISOString()

const demoSummary = {
  project_id: 'demo',
  title: '夜帷喫茶店（第 3 卷）',
  note: '示例项目：静置渲染风格，二次元漫画',
  review_mode: 'auto',
  created_at: nowIso,
  updated_at: nowIso,
  page_count: 6,
  region_count: 14,
  workflow_stage: 'translated',
  cover_image: '',
  latest_snapshot_id: 'snap-001',
  latest_snapshot_kind: 'manual',
  latest_snapshot_summary: '第 3 卷校对完成',
  snapshot_count: 3,
  glossary_count: 5,
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
  return {
    id,
    bbox,
    source_text,
    translation,
    machine_translation: translation,
    recognition_status: 'ready',
    translation_status: extra.translation_status || 'translated',
    font_size: extra.font_size ?? 22,
    font_family: extra.font_family || 'sans-serif',
    alignment: extra.alignment || 'left',
    direction: extra.direction || 'horizontal',
    auto_style: { bold: false, italic: false },
    resolved_style: { bold: false, italic: false },
    ...extra,
  }
}

const demoDocument = {
  revision: 7,
  page_id: 'c002.png',
  image: { url: '/api/pages/demo/c002.png/translated-image' },
  dimensions: { width: 800, height: 1200 },
  base_image: { url: '/api/pages/demo/c002.png/base-image' },
  source_image: { url: '/api/pages/demo/c002.png/source-image' },
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

export default {
  projects: [
    demoSummary,
    {
      ...demoSummary,
      project_id: 'demo2',
      title: '迷宫饭 番外',
      workflow_stage: 'detecting',
      page_count: 42,
      region_count: 0,
      updated_at: new Date(Date.now() - 3600000 * 5).toISOString(),
    },
  ],
  projectViews: {
    demo: {
      session_id: 'demo',
      project_head_generation: 3,
      project_head_revision_id: 'rev-demo-3',
      review_mode: 'auto',
      total_images: 6,
      images: demoImages,
      translated_images: demoImages.slice(0, 4).map((img) => ({ id: `demo-t-${img.stored_name}`, name: img.name, url: `/api/pages/demo/${img.stored_name}/translated-image`, stored_name: img.stored_name })),
      workflow_stage: 'translated',
      artifact_schema_version: 1,
      page_artifacts: Object.fromEntries(demoImages.map((img) => [img.stored_name, { capabilities: { can_export: true } }])),
      download_url: '/api/download/demo',
      download_path: '',
      translated_dir: '',
      mask_debug_dir: '',
      project: demoSummary,
      glossary: { entries: [
        { id: 'g1', term: '喫茶店', translation: '咖啡屋', note: '昭和风咖啡店', category: 'general' },
        { id: 'g2', term: 'ミルクたっぷり', translation: '加了大量牛奶', note: '', category: 'general' },
      ] },
      config: { translation_service: 'gemini', provider: 'gemini' },
      overrides: {},
      task: null,
    },
  },
  pageDocuments: {
    'c002.png': demoDocument,
  },
  settings: {
    translation_service: 'gemini',
    translation_provider: 'gemini',
    translation_model: 'gemini-2.5-flash',
    translation_api_key: '',
    detect_model: 'mfd',
    ocr: 'mocr',
    language: '简体中文',
    rendering_backend: 'manga-translator',
    rerender_output_format: 'webp',
  },
  glossaries: { demo: { entries: demoDocument.regions.map((r) => {
    const t = r.translation && typeof r.translation === 'object'
      ? (r.translation.resolved || r.translation.edited || r.translation.machine || '')
      : (r.translation || '')
    return { id: r.id, term: r.source_text.slice(0, 8), translation: String(t).slice(0, 8) }
  }) } },
  fonts: [
    { name: '系统默认（思源黑体）', source: 'system', url: '/api/fonts/file/user/NotoSansSC' },
    { name: '方正准圆', source: 'user', url: '/api/fonts/file/user/FZXiaoBiaoSong' },
  ],
}