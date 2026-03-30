<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const configStorageKey = 'manga-translator.ui-config'
const doubaoModelOptions = [
  { value: 'doubao-seed-translation-250915', label: 'doubao-seed-translation-250915 (翻译增强 / 推荐)' },
  { value: 'doubao-seed-2-0-pro-260215', label: 'doubao-seed-2-0-pro-260215 (高质量通用文本 / OCR 漫画翻译实验)' },
  { value: 'doubao-seed-2-0-lite-260215', label: 'doubao-seed-2-0-lite-260215 (轻量通用文本 / OCR 漫画翻译实验)' },
  { value: 'doubao-seed-2-0-mini-260215', label: 'doubao-seed-2-0-mini-260215 (通用文本 / 多模态 / OCR 漫画翻译实验)' }
]
const translatorDefaultModels = {
  'doubao-ark': 'doubao-seed-translation-250915'
}
const translatorAllowedModels = {
  'doubao-ark': new Set(doubaoModelOptions.map((option) => option.value))
}
const imageCleanupDefaultModels = {
  'gemini-image': 'gemini-2.5-flash-image',
  'seedream-image': 'doubao-seedream-5-0-lite-260128'
}
const imageCleanupAllowedModels = {
  'gemini-image': new Set([
    'gemini-2.5-flash-image',
    'gemini-3.1-flash-image-preview',
    'gemini-3-pro-image-preview'
  ]),
  'seedream-image': new Set([
    'doubao-seedream-5-0-lite-260128'
  ])
}
const styleBucketOptions = [
  { value: 'gothic', label: '黑体' },
  { value: 'mincho', label: '宋体 / 明体' },
  { value: 'rounded', label: '圆体' },
  { value: 'cartoon', label: '卡通' },
  { value: 'handwritten', label: '手写' },
  { value: 'sfx', label: '拟声' }
]
const textDirectionOptions = [
  { value: 'auto', label: '自动（中文默认竖排）' },
  { value: 'vertical', label: '竖排' },
  { value: 'horizontal', label: '横排' }
]
const canvasHandleOptions = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']
const maxCanvasHistoryEntries = 50
const styleBucketLabelMap = Object.fromEntries(styleBucketOptions.map((option) => [option.value, option.label]))
const defaultStyleFontNameMap = {
  gothic: ['華康儷中黑.ttf', '華康儷中黑'],
  rounded: ['華康儷粗圓.ttf', '華康儷粗圓'],
  mincho: ['華康儷粗宋.ttf', '華康儷粗宋'],
  cartoon: ['華康布丁體.ttf', '華康布丁體'],
  handwritten: ['華康竹風體.ttf', '華康竹風體'],
  sfx: ['方正剪紙GBK.ttf', '方正剪紙GBK']
}
const previewFallbackFontNameMap = {
  gothic: ['華康儷中黑.ttf', '華康儷粗黑.ttf', '華康粗黑體.ttf', 'SourceHanSansSC-Bold.otf', 'NotoSansSC-Bold.otf', 'msyh.ttc'],
  rounded: ['華康儷粗圓.ttf', 'SourceHanSansSC-Medium-2.otf', 'NotoSansSC-Bold.otf'],
  mincho: ['華康儷粗宋.ttf', 'SourceHanSansSC-Regular-2.otf', 'Arial-Unicode-Regular.ttf'],
  cartoon: ['華康儷粗圓.ttf', 'SourceHanSansSC-Bold.otf', 'NotoSansSC-Bold.otf'],
  handwritten: ['華康儷粗圓.ttf', 'SourceHanSansSC-Regular-2.otf', 'NotoSansSC-Bold.otf'],
  sfx: ['方正剪紙GBK.ttf', '華康超黑體(P).ttf', '華康儷粗黑.ttf', 'NotoSansSC-Bold.otf']
}
const styleFontConfigKeyMap = {
  gothic: 'style_font_gothic_key',
  mincho: 'style_font_mincho_key',
  rounded: 'style_font_rounded_key',
  cartoon: 'style_font_cartoon_key',
  handwritten: 'style_font_handwritten_key',
  sfx: 'style_font_sfx_key'
}

function getDefaultImageCleanupModel(mode) {
  return imageCleanupDefaultModels[mode] || imageCleanupDefaultModels['gemini-image']
}

function getDefaultTranslatorModel(translator) {
  return translatorDefaultModels[translator] || ''
}

function isValidTranslatorModel(translator, model) {
  if (!translatorAllowedModels[translator]) {
    return model === '' || model == null
  }

  return Boolean(translatorAllowedModels[translator]?.has(model))
}

function isValidImageCleanupModel(mode, model) {
  return Boolean(imageCleanupAllowedModels[mode]?.has(model))
}

function isValidMaskCleanupStrength(value) {
  return ['standard', 'clean', 'aggressive'].includes(value)
}

function isValidFontStyleMode(value) {
  return ['single', 'auto-map'].includes(value)
}

function isValidRerenderOutputFormat(value) {
  return ['png', 'source'].includes(value)
}

function isValidReviewMode(value) {
  return ['classic', 'canvas_beta'].includes(value)
}

function createDefaultConfig() {
  return {
    translator: 'gemini',
    translator_model: '',
    translator_model_custom: '',
    target_lang: 'CHT',
    use_gpu: true,
    api_key: '',
    font_key: '',
    font_style_mode: 'single',
    style_font_gothic_key: '',
    style_font_mincho_key: '',
    style_font_rounded_key: '',
    style_font_cartoon_key: '',
    style_font_handwritten_key: '',
    style_font_sfx_key: '',
    render_alignment: 'left',
    render_letter_spacing: 1.08,
    rerender_output_format: 'png',
    default_review_mode: 'classic',
    pause_after_detection: false,
    mask_cleanup_strength: 'standard',
    export_mask_debug: false,
    advanced_text_repair: 'auto',
    image_cleanup_mode: 'off',
    image_cleanup_model: 'gemini-2.5-flash-image',
    image_cleanup_api_key: ''
  }
}

function normalizeStoredConfig(rawValue) {
  const defaults = createDefaultConfig()
  if (!rawValue || typeof rawValue !== 'object') {
    return defaults
  }

  const rawTranslator = typeof rawValue.translator === 'string'
    ? rawValue.translator
    : defaults.translator
  const translator = rawTranslator === 'custom_openai'
    ? 'doubao-ark'
    : rawTranslator
  const storedTranslatorModel = typeof rawValue.translator_model === 'string'
    ? rawValue.translator_model
    : defaults.translator_model
  const storedTranslatorModelCustom = typeof rawValue.translator_model_custom === 'string'
    ? rawValue.translator_model_custom.trim()
    : ''
  const translatorModel = isValidTranslatorModel(translator, storedTranslatorModel)
    ? storedTranslatorModel
    : getDefaultTranslatorModel(translator)
  const translatorModelCustom = translator === 'doubao-ark'
    ? (
        storedTranslatorModelCustom
        || (!isValidTranslatorModel(translator, storedTranslatorModel) && storedTranslatorModel ? storedTranslatorModel : '')
      )
    : defaults.translator_model_custom
  const imageCleanupMode = typeof rawValue.image_cleanup_mode === 'string'
    ? rawValue.image_cleanup_mode
    : defaults.image_cleanup_mode
  const storedImageCleanupModel = typeof rawValue.image_cleanup_model === 'string'
    ? rawValue.image_cleanup_model
    : defaults.image_cleanup_model
  const imageCleanupModel = isValidImageCleanupModel(imageCleanupMode, storedImageCleanupModel)
    ? storedImageCleanupModel
    : getDefaultImageCleanupModel(imageCleanupMode)
  const maskCleanupStrength = typeof rawValue.mask_cleanup_strength === 'string' && isValidMaskCleanupStrength(rawValue.mask_cleanup_strength)
    ? rawValue.mask_cleanup_strength
    : defaults.mask_cleanup_strength
  const fontStyleMode = typeof rawValue.font_style_mode === 'string' && isValidFontStyleMode(rawValue.font_style_mode)
    ? rawValue.font_style_mode
    : defaults.font_style_mode
  const rerenderOutputFormat = typeof rawValue.rerender_output_format === 'string' && isValidRerenderOutputFormat(rawValue.rerender_output_format)
    ? rawValue.rerender_output_format
    : defaults.rerender_output_format
  const defaultReviewMode = typeof rawValue.default_review_mode === 'string' && isValidReviewMode(rawValue.default_review_mode)
    ? rawValue.default_review_mode
    : defaults.default_review_mode

  return {
    translator,
    translator_model: translatorModel,
    translator_model_custom: translatorModelCustom,
    target_lang: typeof rawValue.target_lang === 'string' ? rawValue.target_lang : defaults.target_lang,
    use_gpu: typeof rawValue.use_gpu === 'boolean' ? rawValue.use_gpu : defaults.use_gpu,
    api_key: typeof rawValue.api_key === 'string' ? rawValue.api_key : defaults.api_key,
    font_key: typeof rawValue.font_key === 'string' ? rawValue.font_key : defaults.font_key,
    font_style_mode: fontStyleMode,
    // 黑体统一跟随主字体，避免“主字体改了但对白黑体不动”的混乱体验。
    style_font_gothic_key: defaults.style_font_gothic_key,
    style_font_mincho_key: typeof rawValue.style_font_mincho_key === 'string'
      ? rawValue.style_font_mincho_key
      : defaults.style_font_mincho_key,
    style_font_rounded_key: typeof rawValue.style_font_rounded_key === 'string'
      ? rawValue.style_font_rounded_key
      : defaults.style_font_rounded_key,
    style_font_cartoon_key: typeof rawValue.style_font_cartoon_key === 'string'
      ? rawValue.style_font_cartoon_key
      : defaults.style_font_cartoon_key,
    style_font_handwritten_key: typeof rawValue.style_font_handwritten_key === 'string'
      ? rawValue.style_font_handwritten_key
      : defaults.style_font_handwritten_key,
    style_font_sfx_key: typeof rawValue.style_font_sfx_key === 'string'
      ? rawValue.style_font_sfx_key
      : defaults.style_font_sfx_key,
    render_alignment: typeof rawValue.render_alignment === 'string'
      ? rawValue.render_alignment
      : defaults.render_alignment,
    render_letter_spacing: typeof rawValue.render_letter_spacing === 'number'
      ? Math.min(1.35, Math.max(0.85, rawValue.render_letter_spacing))
      : defaults.render_letter_spacing,
    rerender_output_format: rerenderOutputFormat,
    default_review_mode: defaultReviewMode,
    pause_after_detection: typeof rawValue.pause_after_detection === 'boolean'
      ? rawValue.pause_after_detection
      : defaults.pause_after_detection,
    mask_cleanup_strength: maskCleanupStrength,
    export_mask_debug: typeof rawValue.export_mask_debug === 'boolean'
      ? rawValue.export_mask_debug
      : defaults.export_mask_debug,
    advanced_text_repair: typeof rawValue.advanced_text_repair === 'string'
      ? rawValue.advanced_text_repair
      : defaults.advanced_text_repair,
    image_cleanup_mode: imageCleanupMode,
    image_cleanup_model: imageCleanupModel,
    image_cleanup_api_key: typeof rawValue.image_cleanup_api_key === 'string'
      ? rawValue.image_cleanup_api_key
      : defaults.image_cleanup_api_key
  }
}

function loadStoredConfig() {
  if (typeof window === 'undefined') {
    return createDefaultConfig()
  }

  try {
    const rawValue = window.localStorage.getItem(configStorageKey)
    if (!rawValue) {
      return createDefaultConfig()
    }

    return normalizeStoredConfig(JSON.parse(rawValue))
  } catch (error) {
    return createDefaultConfig()
  }
}

function saveStoredConfig(value) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    window.localStorage.setItem(configStorageKey, JSON.stringify(normalizeStoredConfig(value)))
  } catch (error) {
    console.warn('Failed to persist UI config locally.', error)
  }
}

const selectedFile = ref(null)
const status = ref('正在检查后端状态...')
const backendOnline = ref(false)
const uploading = ref(false)
const translating = ref(false)
const historyLoading = ref(false)
const restoringProjectId = ref('')
const restoringSnapshotId = ref('')
const savingProjectMeta = ref(false)
const activeAction = ref('translate')
const renderNonce = ref(Date.now())
const sessionId = ref('')
const originalImages = ref([])
const translatedImages = ref([])
const errorMessage = ref('')
const downloadUrl = ref('')
const downloadPath = ref('')
const translatedDirPath = ref('')
const maskDebugDirPath = ref('')
const progress = ref({ current: 0, total: 0 })
const availableFonts = ref([])
const previewFontLoadState = ref({})
const reviewInspectionPages = ref([])
const reviewInspectionLoading = ref(false)
const translationRegionOverrides = ref({})
const translationRegionSkipOverrides = ref({})
const translationRegionDisabledOverrides = ref({})
const translationRegionLayoutOverrides = ref({})
const translationInputDrafts = ref({})
const fontSizeInputDrafts = ref({})
const styleInspectionPages = ref([])
const styleInspectionLoading = ref(false)
const styleRegionOverrides = ref({})
const pageEditHistory = ref({})
const canvasPreviewDirtyPages = ref({})
const creatingManualRegion = ref(false)
const manualDrawMode = ref(false)
const adjustingRegionId = ref('')
const manualDrawDraft = ref(null)
const canvasTransformState = ref(null)
const mergeMode = ref(false)
const mergeRegionSelection = ref({})
const selectedEditPageKey = ref('')
const selectedEditRegionKey = ref('')
const showAdvancedSettings = ref(false)
const workflowStage = ref('idle')
const projectHistory = ref([])
const projectSnapshots = ref({})
const snapshotLoadingProjectId = ref('')
const expandedProjectId = ref('')
const currentProject = ref(null)
const projectTitleDraft = ref('')
const projectNoteDraft = ref('')
const translatedPreviewCanvasRef = ref(null)
const translatedPreviewScale = ref(1)
const exportingOcrDebug = ref(false)
const exportingTranslationInputDebug = ref(false)
const exportingTranslationRequestDebug = ref(false)

const config = ref(loadStoredConfig())

let socket = null

const acceptValue = '.zip,.cbz,.jpg,.jpeg,.png,.webp'

const canUpload = computed(() => Boolean(selectedFile.value) && !uploading.value)
const canTranslate = computed(() => Boolean(sessionId.value) && !translating.value)
const canContinueSegmentedTranslation = computed(
  () => Boolean(sessionId.value) && !translating.value && workflowStage.value === 'detected'
)
const canRerender = computed(
  () => Boolean(sessionId.value) && !translating.value && workflowStage.value === 'translated' && Boolean(downloadUrl.value || translatedImages.value.length)
)
const canInspectEditor = computed(() => Boolean(sessionId.value))
const canCreateManualRegion = computed(() => Boolean(sessionId.value) && !translating.value && !creatingManualRegion.value)
const activeReviewMode = computed(() => currentProject.value?.review_mode || config.value.default_review_mode || 'classic')
const isCanvasReviewMode = computed(() => activeReviewMode.value === 'canvas_beta')
const hasTranslationOverrides = computed(
  () =>
    Object.keys(translationRegionOverrides.value).length > 0
    || Object.keys(translationRegionSkipOverrides.value).length > 0
    || Object.keys(translationRegionDisabledOverrides.value).length > 0
    || Object.keys(translationRegionLayoutOverrides.value).length > 0
)
const hasStyleOverrides = computed(() => Object.keys(styleRegionOverrides.value).length > 0)
const editInspectionLoading = computed(() => reviewInspectionLoading.value || styleInspectionLoading.value)
const isAdjustingRegionBBox = computed(() => Boolean(adjustingRegionId.value))
const canDirectManipulateCanvas = computed(
  () => Boolean(isCanvasReviewMode.value && !manualDrawMode.value && !mergeMode.value && !isAdjustingRegionBBox.value && !translating.value)
)
const selectedPageHistoryState = computed(() => {
  const pageId = selectedEditPage.value?.stored_name || ''
  if (!pageId) {
    return { undo: [], redo: [] }
  }
  return pageEditHistory.value[pageId] || { undo: [], redo: [] }
})
const canUndoCanvasEdit = computed(() => Boolean(isCanvasReviewMode.value && selectedPageHistoryState.value.undo.length > 0 && !translating.value))
const canRedoCanvasEdit = computed(() => Boolean(isCanvasReviewMode.value && selectedPageHistoryState.value.redo.length > 0 && !translating.value))
const mergeSelectionCount = computed(() => Object.keys(mergeRegionSelection.value).length)
const disabledRegionCountForSelectedPage = computed(() => {
  const storedName = selectedEditPage.value?.stored_name
  if (!storedName) {
    return 0
  }
  return Object.keys(translationRegionDisabledOverrides.value).filter((regionId) => regionId.includes(storedName)).length
})
const latestTranslatedImageUrlByPage = computed(() => {
  const mapping = {}
  for (const image of translatedImages.value) {
    const storedName = String(image?.stored_name || '').trim()
    const url = String(image?.url || '').trim()
    if (!storedName || !url) {
      continue
    }
    mapping[storedName] = url
  }
  return mapping
})
const mergedInspectionPages = computed(() => {
  const pageOrder = []
  const pageMap = new Map()

  const ensurePage = (page) => {
    if (!page) {
      return null
    }

    if (!pageMap.has(page.stored_name)) {
      pageOrder.push(page.stored_name)
      pageMap.set(page.stored_name, {
        stored_name: page.stored_name,
        name: page.name,
        image_url: page.image_url,
        source_image_url: page.source_image_url || '',
        base_image_url: page.base_image_url || page.source_image_url || page.image_url,
        translated_image_url: page.translated_image_url || page.image_url,
        image_width: page.image_width,
        image_height: page.image_height,
        regions: new Map()
      })
    }

    return pageMap.get(page.stored_name)
  }

  for (const page of reviewInspectionPages.value) {
    const mergedPage = ensurePage(page)
    for (const region of page.regions || []) {
      mergedPage.regions.set(region.id, {
        ...region
      })
    }
  }

  for (const page of styleInspectionPages.value) {
    const mergedPage = ensurePage(page)
    if (!mergedPage.image_url) {
      mergedPage.image_url = page.image_url
    }
    if (!mergedPage.source_image_url && page.source_image_url) {
      mergedPage.source_image_url = page.source_image_url
    }
    if (!mergedPage.base_image_url && page.base_image_url) {
      mergedPage.base_image_url = page.base_image_url
    }
    if (!mergedPage.translated_image_url && page.translated_image_url) {
      mergedPage.translated_image_url = page.translated_image_url
    }
    for (const region of page.regions || []) {
      const existing = mergedPage.regions.get(region.id) || { id: region.id, index: region.index, bbox: region.bbox }
      mergedPage.regions.set(region.id, {
        ...existing,
        ...region
      })
    }
  }

  for (const storedName of pageOrder) {
    const mergedPage = pageMap.get(storedName)
    const latestTranslatedUrl = latestTranslatedImageUrlByPage.value[storedName] || ''
    if (!mergedPage || !latestTranslatedUrl) {
      continue
    }
    mergedPage.image_url = latestTranslatedUrl
    mergedPage.translated_image_url = latestTranslatedUrl
  }

  return pageOrder.map((storedName) => {
    const page = pageMap.get(storedName)
    return {
      ...page,
      regions: Array.from(page.regions.values()).sort((left, right) => left.index - right.index)
    }
  })
})
const comparisonImages = computed(() => {
  const originalMap = new Map(
    originalImages.value.map((image) => [String(image?.stored_name || image?.name || ''), image])
  )
  const translatedMap = new Map(
    translatedImages.value.map((image) => [String(image?.stored_name || image?.name || ''), image])
  )
  const orderedKeys = []
  const seen = new Set()

  for (const image of originalImages.value) {
    const key = String(image?.stored_name || image?.name || '').trim()
    if (!key || seen.has(key)) {
      continue
    }
    seen.add(key)
    orderedKeys.push(key)
  }

  for (const image of translatedImages.value) {
    const key = String(image?.stored_name || image?.name || '').trim()
    if (!key || seen.has(key)) {
      continue
    }
    seen.add(key)
    orderedKeys.push(key)
  }

  return orderedKeys.map((key) => ({
    key,
    original: originalMap.get(key) || null,
    translated: translatedMap.get(key) || null,
    name: originalMap.get(key)?.name || translatedMap.get(key)?.name || key,
  }))
})
const selectedEditPage = computed(() => {
  if (!mergedInspectionPages.value.length) {
    return null
  }

  return (
    mergedInspectionPages.value.find((page) => page.stored_name === selectedEditPageKey.value)
    || mergedInspectionPages.value[0]
    || null
  )
})
const selectedEditPageIndex = computed(() => {
  if (!selectedEditPage.value) {
    return -1
  }
  return mergedInspectionPages.value.findIndex((page) => page.stored_name === selectedEditPage.value?.stored_name)
})
const canSelectPreviousEditPage = computed(() => selectedEditPageIndex.value > 0)
const canSelectNextEditPage = computed(() => {
  return selectedEditPageIndex.value >= 0 && selectedEditPageIndex.value < mergedInspectionPages.value.length - 1
})
const selectedEditPageSummary = computed(() => {
  const page = selectedEditPage.value
  if (!page) {
    return ''
  }
  const pageNumber = selectedEditPageIndex.value >= 0 ? selectedEditPageIndex.value + 1 : 1
  return `第 ${pageNumber} / ${mergedInspectionPages.value.length} 页 · ${page.regions.length} 个文本框`
})
const selectedEditRegion = computed(() => {
  const page = selectedEditPage.value
  if (!page) {
    return null
  }
  return page.regions.find((region) => region.id === selectedEditRegionKey.value) || page.regions[0] || null
})
const selectedRegionPreviewDebug = ref({
  regionId: '',
  requestedFont: '',
  requestedAlias: '',
  computedFontFamily: '',
  computedFontWeight: '',
  previewLayer: '',
})
const progressPercent = computed(() => {
  if (!progress.value.total) {
    return 0
  }

  return Math.min(100, Math.round((progress.value.current / progress.value.total) * 100))
})
const translatorLabelMap = {
  sugoi: 'sugoi',
  gemini: 'Gemini',
  'doubao-ark': 'Doubao',
  chatgpt: 'ChatGPT',
  youdao: '有道',
  baidu: '百度',
  offline: 'offline',
  none: 'none'
}
const targetLangLabelMap = {
  CHS: '简中',
  CHT: '繁中',
  ENG: '英语',
  JPN: '日语',
  KOR: '韩语'
}
const workflowStageLabelMap = {
  idle: '未开始',
  detecting: '识别中',
  detected: '待校对',
  translating: '翻译中',
  translated: '已翻译'
}
const reviewModeLabelMap = {
  classic: '经典审校',
  canvas_beta: '画布审校（Beta）'
}
const compactConfigSummary = computed(() => {
  const translator = translatorLabelMap[config.value.translator] || config.value.translator
  const targetLang = targetLangLabelMap[config.value.target_lang] || config.value.target_lang
  const styleMode = config.value.font_style_mode === 'auto-map' ? '多字体映射' : '单字体'
  const cleanup = config.value.image_cleanup_mode === 'off' ? '稳定流程' : 'AI 去字'
  const workflow = config.value.pause_after_detection ? '先校对再翻译' : '直接翻译'
  const reviewMode = reviewModeLabelMap[config.value.default_review_mode] || config.value.default_review_mode
  const translatorModel = config.value.translator === 'doubao-ark'
    ? ` / ${getResolvedTranslatorModel(config.value)}`
    : ''
  return `${translator}${translatorModel} / ${targetLang} / ${styleMode} / ${cleanup} / ${workflow} / 默认${reviewMode}`
})
const projectMetaDirty = computed(() => {
  const project = currentProject.value
  if (!project) {
    return false
  }
  return (
    String(projectTitleDraft.value || '').trim() !== String(project.title || '').trim()
    || String(projectNoteDraft.value || '').trim() !== String(project.note || '').trim()
  )
})
const previewFontFaceCss = computed(() => {
  const rules = []
  for (const font of availableFonts.value) {
    if (!font?.id || !font?.url) {
      continue
    }
    const family = getPreviewFontAlias(font.id)
    const formatHint = getPreviewFontFormatHint(font)
    const srcValue = formatHint
      ? `url('${toApiUrl(font.url)}') format('${formatHint}')`
      : `url('${toApiUrl(font.url)}')`
    rules.push(`@font-face{font-family:'${family}';src:${srcValue};font-style:normal;font-weight:400;font-display:swap;}`)
  }
  return rules.join('\n')
})

const primaryTranslateAction = computed(() => {
  if (workflowStage.value === 'detected') {
    return 'resume-translate'
  }
  return config.value.pause_after_detection ? 'detect' : 'translate'
})

const primaryTranslateLabel = computed(() => {
  if (translating.value) {
    if (activeAction.value === 'detect') {
      return '识别进行中...'
    }
    if (activeAction.value === 'resume-translate') {
      return '翻译进行中...'
    }
    return '翻译进行中...'
  }

  if (workflowStage.value === 'detected') {
    return '继续翻译并嵌字'
  }
  if (config.value.pause_after_detection) {
    return workflowStage.value === 'translated' ? '重新识别并校对' : '先识别文本框'
  }
  return '开始翻译'
})

function toApiUrl(path) {
  if (!path) {
    return ''
  }

  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path
  }

  return `${apiBaseUrl}${path.startsWith('/') ? path : `/${path}`}`
}

function toWebSocketUrl(path) {
  const url = new URL(toApiUrl(path))
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

function withCacheBust(url) {
  if (!url) {
    return ''
  }

  const separator = url.includes('?') ? '&' : '?'
  return `${url}${separator}t=${renderNonce.value}`
}

function markCanvasPreviewDirty(pageId) {
  const normalizedPageId = String(pageId || '').trim()
  if (!normalizedPageId) {
    return
  }
  canvasPreviewDirtyPages.value = {
    ...canvasPreviewDirtyPages.value,
    [normalizedPageId]: true
  }
}

function clearCanvasPreviewDirty(pageId = '') {
  const normalizedPageId = String(pageId || '').trim()
  if (!normalizedPageId) {
    canvasPreviewDirtyPages.value = {}
    return
  }
  const nextState = { ...canvasPreviewDirtyPages.value }
  delete nextState[normalizedPageId]
  canvasPreviewDirtyPages.value = nextState
}

function sleep(ms) {
  return new Promise((resolve) => window.setTimeout(resolve, ms))
}

function formatOcrConfidence(value) {
  const numericValue = Number(value)
  if (!Number.isFinite(numericValue) || numericValue <= 0) {
    return 'OCR 置信度未知'
  }
  return `OCR ${(numericValue * 100).toFixed(numericValue >= 0.995 ? 1 : 0)}%`
}

function getRegionDirectionLabel(region) {
  const value = String(region?.direction || 'auto')
  const option = textDirectionOptions.find((item) => item.value === value)
  return option?.label || '自动'
}

async function exportCurrentPageOcrDebug() {
  if (!sessionId.value || !selectedEditPage.value || exportingOcrDebug.value) {
    return
  }

  exportingOcrDebug.value = true
  errorMessage.value = ''
  try {
    const response = await fetch(
      toApiUrl(`/api/pages/${sessionId.value}/${selectedEditPage.value.stored_name}/ocr-debug`)
    )
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '导出 OCR 调试信息失败')
    }

    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json;charset=utf-8'
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${selectedEditPage.value.stored_name.replace(/\.[^.]+$/, '') || 'page'}-ocr-debug.json`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)

    status.value = '已导出当前页 OCR 调试 JSON，可以直接对照看原始识别文本。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出 OCR 调试信息失败'
  } finally {
    exportingOcrDebug.value = false
  }
}

async function exportCurrentPageTranslationInputDebug() {
  if (!sessionId.value || !selectedEditPage.value || exportingTranslationInputDebug.value) {
    return
  }

  exportingTranslationInputDebug.value = true
  errorMessage.value = ''
  try {
    const response = await fetch(
      toApiUrl(`/api/pages/${sessionId.value}/${selectedEditPage.value.stored_name}/translation-input-debug`)
    )
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '导出翻译输入调试信息失败')
    }

    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json;charset=utf-8'
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${selectedEditPage.value.stored_name.replace(/\.[^.]+$/, '') || 'page'}-translation-input-debug.json`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)

    status.value = '已导出当前页翻译输入调试 JSON，可以直接对照 OCR 结果看真正送翻译的文本。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出翻译输入调试信息失败'
  } finally {
    exportingTranslationInputDebug.value = false
  }
}

async function exportCurrentProjectTranslationRequestDebug() {
  if (!sessionId.value || exportingTranslationRequestDebug.value) {
    return
  }

  exportingTranslationRequestDebug.value = true
  errorMessage.value = ''
  try {
    const response = await fetch(
      toApiUrl(`/api/projects/${sessionId.value}/translation-request-debug`)
    )
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '导出翻译请求调试信息失败')
    }

    const blob = new Blob([JSON.stringify(payload, null, 2)], {
      type: 'application/json;charset=utf-8'
    })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `${sessionId.value || 'project'}-translation-request-debug.json`
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)

    status.value = '已导出本次翻译请求/响应调试 JSON，可以直接核对模型调用链路。'
  } catch (error) {
    const message = error instanceof Error ? error.message : '导出翻译请求调试信息失败'
    errorMessage.value = message
    status.value = message
    if (typeof window !== 'undefined') {
      window.alert(message)
    }
  } finally {
    exportingTranslationRequestDebug.value = false
  }
}

function closeSocket() {
  if (socket) {
    socket.close()
    socket = null
  }
}

function resetStyleInspector() {
  styleInspectionPages.value = []
  styleInspectionLoading.value = false
  styleRegionOverrides.value = {}
}

function resetTranslationReview() {
  reviewInspectionPages.value = []
  reviewInspectionLoading.value = false
  translationRegionOverrides.value = {}
  translationRegionSkipOverrides.value = {}
  translationRegionDisabledOverrides.value = {}
  translationRegionLayoutOverrides.value = {}
  mergeRegionSelection.value = {}
  mergeMode.value = false
  adjustingRegionId.value = ''
}

function resetEditInspectorSelection() {
  selectedEditPageKey.value = ''
  selectedEditRegionKey.value = ''
  manualDrawDraft.value = null
  adjustingRegionId.value = ''
  canvasTransformState.value = null
  mergeRegionSelection.value = {}
  mergeMode.value = false
  translationInputDrafts.value = {}
  fontSizeInputDrafts.value = {}
  pageEditHistory.value = {}
}

function normalizeHistoryProject(project) {
  return {
    ...project,
    cover_image: project?.cover_image ? toApiUrl(project.cover_image) : '',
    updated_at: project?.updated_at || '',
    created_at: project?.created_at || '',
    title: project?.title || project?.project_id || '未命名项目',
    note: project?.note || '',
    review_mode: project?.review_mode || 'classic',
    workflow_stage: project?.workflow_stage || 'idle',
    page_count: Number(project?.page_count || 0)
  }
}

function normalizeSnapshot(snapshot) {
  return {
    ...snapshot,
    snapshot_id: snapshot?.snapshot_id || '',
    created_at: snapshot?.created_at || '',
    kind: snapshot?.kind || '',
    summary: snapshot?.summary || '',
    workflow_stage: snapshot?.workflow_stage || 'idle',
    cover_image: snapshot?.cover_image ? toApiUrl(snapshot.cover_image) : '',
    pinned: Boolean(snapshot?.pinned)
  }
}

function applySessionPayload(payload, options = {}) {
  const resetInspectors = Boolean(options.resetInspectors)
  renderNonce.value = Date.now()
  sessionId.value = payload?.session_id || ''
  workflowStage.value = payload?.workflow_stage || 'idle'
  downloadUrl.value = payload?.download_url ? withCacheBust(toApiUrl(payload.download_url)) : ''
  downloadPath.value = payload?.download_path || ''
  translatedDirPath.value = payload?.translated_dir || ''
  maskDebugDirPath.value = payload?.mask_debug_dir || ''
  originalImages.value = (payload?.images || []).map((image, index) => ({
    id: `${sessionId.value || 'session'}-source-${index}`,
    name: image.name,
    url: toApiUrl(image.url),
    stored_name: image.stored_name
  }))
  translatedImages.value = (payload?.translated_images || []).map((image, index) => ({
    id: image.id || `${sessionId.value || 'session'}-translated-${index}`,
    name: image.name,
    url: withCacheBust(toApiUrl(image.url)),
    stored_name: image.stored_name
  }))

  const payloadConfig = payload?.config && typeof payload.config === 'object' ? payload.config : {}
  config.value = normalizeStoredConfig({
    ...config.value,
    ...payloadConfig,
    api_key: config.value.api_key,
    image_cleanup_api_key: config.value.image_cleanup_api_key
  })

  const overrides = payload?.overrides || {}
  translationRegionOverrides.value = { ...(overrides.translation_region_overrides || {}) }
  translationRegionSkipOverrides.value = { ...(overrides.translation_region_skip_overrides || {}) }
  translationRegionDisabledOverrides.value = { ...(overrides.translation_region_disabled_overrides || {}) }
  translationRegionLayoutOverrides.value = { ...(overrides.translation_region_layout_overrides || {}) }
  styleRegionOverrides.value = { ...(overrides.style_region_overrides || {}) }
  translationInputDrafts.value = {}
  fontSizeInputDrafts.value = {}
  pageEditHistory.value = {}
  canvasPreviewDirtyPages.value = {}

  currentProject.value = payload?.project ? normalizeHistoryProject(payload.project) : null
  projectTitleDraft.value = currentProject.value?.title || ''
  projectNoteDraft.value = currentProject.value?.note || ''

  if (resetInspectors) {
    resetTranslationReview()
    resetStyleInspector()
    resetEditInspectorSelection()
  }
}

async function loadProjectHistory(options = {}) {
  const silent = Boolean(options.silent)
  if (!silent) {
    historyLoading.value = true
  }
  try {
    const response = await fetch(toApiUrl('/api/projects'))
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '读取历史翻译列表失败')
    }
    projectHistory.value = (payload.projects || []).map(normalizeHistoryProject)
  } catch (error) {
    if (!silent) {
      errorMessage.value = error instanceof Error ? error.message : '读取历史翻译列表失败'
    }
  } finally {
    if (!silent) {
      historyLoading.value = false
    }
  }
}

async function loadProjectSnapshots(projectId, options = {}) {
  if (!projectId) {
    return
  }
  const silent = Boolean(options.silent)
  if (!silent) {
    snapshotLoadingProjectId.value = projectId
  }
  try {
    const response = await fetch(toApiUrl(`/api/projects/${projectId}/snapshots`))
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '读取项目快照失败')
    }
    projectSnapshots.value = {
      ...projectSnapshots.value,
      [projectId]: (payload.snapshots || []).map(normalizeSnapshot)
    }
  } catch (error) {
    if (!silent) {
      errorMessage.value = error instanceof Error ? error.message : '读取项目快照失败'
    }
  } finally {
    if (!silent) {
      snapshotLoadingProjectId.value = ''
    }
  }
}

async function toggleProjectSnapshots(projectId) {
  if (!projectId) {
    return
  }
  if (expandedProjectId.value === projectId) {
    expandedProjectId.value = ''
    return
  }
  expandedProjectId.value = projectId
  if (!projectSnapshots.value[projectId]) {
    await loadProjectSnapshots(projectId)
  }
}

async function restoreProject(projectId) {
  if (!projectId) {
    return
  }
  restoringProjectId.value = projectId
  errorMessage.value = ''
  closeSocket()
  try {
    const response = await fetch(toApiUrl(`/api/projects/${projectId}/restore`), {
      method: 'POST'
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '恢复历史项目失败')
    }
    applySessionPayload(payload, { resetInspectors: true })
    await loadEditInspection({ silent: true })
    await loadProjectHistory({ silent: true })
    status.value = `已恢复项目「${currentProject.value?.title || projectId}」，可以继续编辑。`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '恢复历史项目失败'
  } finally {
    restoringProjectId.value = ''
  }
}

async function restoreSnapshot(projectId, snapshotId) {
  if (!projectId || !snapshotId) {
    return
  }
  restoringSnapshotId.value = snapshotId
  errorMessage.value = ''
  closeSocket()
  try {
    const response = await fetch(toApiUrl(`/api/projects/${projectId}/snapshots/${snapshotId}/restore`), {
      method: 'POST'
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '恢复历史快照失败')
    }
    applySessionPayload(payload, { resetInspectors: true })
    await loadEditInspection({ silent: true })
    await loadProjectHistory({ silent: true })
    status.value = `已从历史快照恢复项目「${currentProject.value?.title || payload.session_id}」，可以继续编辑。`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '恢复历史快照失败'
  } finally {
    restoringSnapshotId.value = ''
  }
}

async function toggleSnapshotPin(projectId, snapshot) {
  if (!projectId || !snapshot?.snapshot_id) {
    return
  }
  errorMessage.value = ''
  try {
    const response = await fetch(toApiUrl(`/api/projects/${projectId}/snapshots/${snapshot.snapshot_id}/pin`), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        pinned: !snapshot.pinned
      })
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '更新快照固定状态失败')
    }
    projectSnapshots.value = {
      ...projectSnapshots.value,
      [projectId]: (payload.snapshots || []).map(normalizeSnapshot)
    }
    await loadProjectHistory({ silent: true })
    status.value = snapshot.pinned ? '已取消固定这个快照。' : '已固定这个快照。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '更新快照固定状态失败'
  }
}

async function deleteProject(project) {
  if (!project?.project_id) {
    return
  }
  const confirmed = typeof window === 'undefined'
    ? true
    : window.confirm(`确定要删除项目「${project.title}」吗？相关历史记录和输出文件会一起移除。`)
  if (!confirmed) {
    return
  }

  errorMessage.value = ''
  try {
    const response = await fetch(toApiUrl(`/api/projects/${project.project_id}`), {
      method: 'DELETE'
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '删除项目失败')
    }

    if (sessionId.value === project.project_id) {
      closeSocket()
      sessionId.value = ''
      originalImages.value = []
      translatedImages.value = []
      downloadUrl.value = ''
      downloadPath.value = ''
      translatedDirPath.value = ''
      maskDebugDirPath.value = ''
      workflowStage.value = 'idle'
      currentProject.value = null
      projectTitleDraft.value = ''
      projectNoteDraft.value = ''
      resetTranslationReview()
      resetStyleInspector()
      resetEditInspectorSelection()
    }

    const nextSnapshots = { ...projectSnapshots.value }
    delete nextSnapshots[project.project_id]
    projectSnapshots.value = nextSnapshots
    if (expandedProjectId.value === project.project_id) {
      expandedProjectId.value = ''
    }

    await loadProjectHistory({ silent: true })
    status.value = '已删除该历史项目。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '删除项目失败'
  }
}

async function saveProjectMetadata() {
  if (!sessionId.value || !currentProject.value || !projectMetaDirty.value) {
    return
  }
  savingProjectMeta.value = true
  errorMessage.value = ''
  try {
    const response = await fetch(toApiUrl(`/api/projects/${sessionId.value}`), {
      method: 'PATCH',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        title: projectTitleDraft.value,
        note: projectNoteDraft.value
      })
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '保存项目名称或备注失败')
    }
    currentProject.value = normalizeHistoryProject(payload.project || {})
    projectTitleDraft.value = currentProject.value?.title || ''
    projectNoteDraft.value = currentProject.value?.note || ''
    await loadProjectHistory({ silent: true })
    status.value = '已保存项目名称和备注。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '保存项目名称或备注失败'
  } finally {
    savingProjectMeta.value = false
  }
}

function buildRuntimeConfig() {
  return {
    ...config.value,
    translator_model: getResolvedTranslatorModel(config.value),
    style_region_overrides: { ...styleRegionOverrides.value },
    translation_region_overrides: { ...translationRegionOverrides.value },
    translation_region_skip_overrides: { ...translationRegionSkipOverrides.value },
    translation_region_disabled_overrides: { ...translationRegionDisabledOverrides.value },
    translation_region_layout_overrides: { ...translationRegionLayoutOverrides.value }
  }
}

function getResolvedTranslatorModel(value = config.value) {
  if (value.translator !== 'doubao-ark') {
    return value.translator_model || ''
  }

  const customModel = String(value.translator_model_custom || '').trim()
  if (customModel) {
    return customModel
  }

  return value.translator_model || getDefaultTranslatorModel(value.translator)
}

function getStyleLabel(style) {
  return styleBucketLabelMap[style] || '未分类'
}

function getResolvedRegionStyleLabel(region) {
  const resolvedStyle = getResolvedStyle(region)
  return resolvedStyle ? getStyleLabel(resolvedStyle) : '未分类'
}

function getRegionOverrideValue(region) {
  return styleRegionOverrides.value[region.id] || ''
}

function getEditRegionText(region) {
  if (Object.prototype.hasOwnProperty.call(translationInputDrafts.value, region.id)) {
    return translationInputDrafts.value[region.id]
  }
  return translationRegionOverrides.value[region.id] || region.current_translation || region.machine_translation || ''
}

function isRegionSkipEnabled(region) {
  return Boolean(translationRegionSkipOverrides.value[region.id] || region.override_skip)
}

function isRegionDisabled(region) {
  return Boolean(translationRegionDisabledOverrides.value[region.id])
}

function getResolvedStyle(region) {
  return styleRegionOverrides.value[region.id] || region.resolved_style || region.auto_style || ''
}

function normalizeFontFamilyName(value) {
  return String(value || '')
    .split(/[\\/]/)
    .pop()
    .replace(/\.(ttf|ttc|otf)$/i, '')
    .trim()
}

function hashPreviewFontKey(value) {
  let hash = 2166136261
  for (const char of String(value || '')) {
    hash ^= char.codePointAt(0) || 0
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(36)
}

function getPreviewFontAlias(fontId) {
  const normalized = String(fontId || '').trim()
  if (!normalized) {
    return 'codex-preview-font-default'
  }
  return `codex-preview-font-${hashPreviewFontKey(normalized)}`
}

function getConfiguredFontId() {
  return String(config.value.font_key || '').trim()
}

function getConfiguredStyleFontId(styleBucket) {
  if (styleBucket === 'gothic') {
    return ''
  }
  const configKey = styleFontConfigKeyMap[styleBucket]
  if (!configKey) {
    return ''
  }
  return String(config.value[configKey] || '').trim()
}

function getFontById(fontId) {
  return availableFonts.value.find((font) => font.id === fontId) || null
}

function getPreviewFontState(fontId) {
  const normalizedFontId = String(fontId || '').trim()
  if (!normalizedFontId) {
    return { status: 'unknown', error: '' }
  }
  return previewFontLoadState.value[normalizedFontId] || { status: 'unknown', error: '' }
}

function isPreviewFontUnsupported(fontId) {
  return getPreviewFontState(fontId).status === 'unsupported'
}

function findFontIdByFamily(fontFamily) {
  const normalizedFamily = normalizeFontFamilyName(fontFamily).toLowerCase()
  if (!normalizedFamily) {
    return ''
  }
  for (const font of availableFonts.value) {
    const candidates = [
      font?.id,
      font?.name,
      font?.label,
      font?.file_name,
      font?.filename
    ]
      .map((item) => normalizeFontFamilyName(item).toLowerCase())
      .filter(Boolean)
    if (candidates.includes(normalizedFamily)) {
      return String(font.id || '')
    }
  }
  return ''
}

function getPreviewFontFormatHint(font) {
  const explicitHint = String(font?.format_hint || '').trim().toLowerCase()
  if (explicitHint) {
    return explicitHint
  }
  const extension = String(font?.extension || font?.name || '')
    .trim()
    .toLowerCase()
  if (extension.endsWith('.ttf')) {
    return 'truetype'
  }
  if (extension.endsWith('.otf')) {
    return 'opentype'
  }
  return ''
}

function getRegionFontOverrideId(region) {
  const override = translationRegionLayoutOverrides.value[region?.id]
  return String(override?.font_key || region?.font_key_override || '').trim()
}

function getFollowRegionFontId(region) {
  if (config.value.font_style_mode === 'auto-map') {
    return getConfiguredStyleFontId(getResolvedStyle(region)) || getConfiguredFontId() || findFontIdByFamily(region?.font_family || '')
  }
  return getConfiguredFontId() || findFontIdByFamily(region?.font_family || '')
}

function getEffectiveRegionFontId(region) {
  return getRegionFontOverrideId(region) || getFollowRegionFontId(region)
}

function getRegionFontPlaceholderLabel(_region) {
  return config.value.font_style_mode === 'auto-map' ? '跟随识别结果' : '跟随主字体'
}

function pickPreviewFallbackFontId(region) {
  const fallbackStyle = config.value.font_style_mode === 'auto-map'
    ? (getResolvedStyle(region) || 'gothic')
    : 'gothic'
  const preferredNames = previewFallbackFontNameMap[fallbackStyle] || previewFallbackFontNameMap.gothic
  const compatibleFonts = availableFonts.value.filter((font) => !isPreviewFontUnsupported(font.id))
  const matchedFont = pickMappedStyleFont(compatibleFonts, preferredNames)
  return matchedFont?.id || ''
}

function getResolvedPreviewFontId(region) {
  const effectiveFontId = getEffectiveRegionFontId(region)
  if (effectiveFontId && !isPreviewFontUnsupported(effectiveFontId)) {
    return effectiveFontId
  }
  return pickPreviewFallbackFontId(region)
}

function getEffectiveRegionFontLabel(region) {
  const effectiveFontId = getEffectiveRegionFontId(region)
  const effectiveFont = effectiveFontId ? getFontById(effectiveFontId) : null
  const effectiveFontLabel = effectiveFont?.label || getFontById(config.value.font_key)?.label || '默认字体'
  const previewFontId = getResolvedPreviewFontId(region)
  const usesPreviewFallback = Boolean(
    effectiveFontId
    && previewFontId
    && previewFontId !== effectiveFontId
  )

  if (config.value.font_style_mode === 'auto-map') {
    return usesPreviewFallback
      ? `${getResolvedRegionStyleLabel(region)} · ${effectiveFontLabel}（预览替代）`
      : `${getResolvedRegionStyleLabel(region)} · ${effectiveFontLabel}`
  }

  return usesPreviewFallback
    ? `${effectiveFontLabel}（预览替代）`
    : effectiveFontLabel
}

function getPreviewFontOptionLabel(font) {
  if (!font) {
    return ''
  }
  return isPreviewFontUnsupported(font.id)
    ? `${font.label}（仅最终重嵌）`
    : font.label
}

async function refreshSelectedRegionPreviewDebug() {
  if (typeof window === 'undefined') {
    return
  }

  const region = selectedEditRegion.value
  const page = selectedEditPage.value
  if (!region || !page) {
    selectedRegionPreviewDebug.value = {
      regionId: '',
      requestedFont: '',
      requestedAlias: '',
      computedFontFamily: '',
      computedFontWeight: '',
      previewLayer: '',
    }
    return
  }

  const requestedFontId = getEffectiveRegionFontId(region)
  const requestedFont = requestedFontId ? getFontById(requestedFontId) : null
  const previewFontId = getResolvedPreviewFontId(region)
  const requestedAlias = previewFontId ? getPreviewFontAlias(previewFontId) : ''
  const layer = shouldShowCanvasTextOverlay(region, page)
    ? '实时叠字预览'
    : shouldShowSourceCropPreview(region, page)
      ? '原文裁切预览'
      : '最终译图'

  await nextTick()
  await new Promise((resolve) => window.requestAnimationFrame(() => resolve()))

  const overlay = translatedPreviewCanvasRef.value?.querySelector?.(
    `.style-box-preview-text-content[data-region-id="${region.id}"]`
  )
  const computedStyle = overlay ? window.getComputedStyle(overlay) : null

  selectedRegionPreviewDebug.value = {
    regionId: region.id,
    requestedFont: requestedFont?.label || getEffectiveRegionFontLabel(region),
    requestedAlias,
    computedFontFamily: computedStyle?.fontFamily || '(当前未走叠字预览层)',
    computedFontWeight: computedStyle?.fontWeight || '',
    previewLayer: layer,
  }

  console.log('[CanvasFontDebug]', {
    regionId: region.id,
    requestedFontId,
    requestedFontLabel: requestedFont?.label || '',
    previewFontId,
    requestedAlias,
    computedFontFamily: computedStyle?.fontFamily || '',
    computedFontWeight: computedStyle?.fontWeight || '',
    previewLayer: layer,
  })
}

function getRegionPreviewFontFamily(region) {
  const fontId = getResolvedPreviewFontId(region)
  if (fontId) {
    return `"${getPreviewFontAlias(fontId)}","Microsoft JhengHei","PingFang TC","Noto Sans CJK TC",sans-serif`
  }

  const fallbackFamily = normalizeFontFamilyName(region?.font_family || '')
  if (!fallbackFamily) {
    return '"Microsoft JhengHei","PingFang TC","Noto Sans CJK TC",sans-serif'
  }
  return `"${fallbackFamily}","Microsoft JhengHei","PingFang TC","Noto Sans CJK TC",sans-serif`
}

function isChineseTargetLanguage() {
  const targetLang = String(config.value.target_lang || '').trim().toUpperCase()
  return targetLang === 'CHS' || targetLang === 'CHT'
}

function normalizeDirectionValue(value) {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'vertical' || normalized === 'v' || normalized === 'vertical-rl') {
    return 'vertical'
  }
  if (normalized === 'horizontal' || normalized === 'h' || normalized === 'horizontal-tb') {
    return 'horizontal'
  }
  return 'auto'
}

function getRegionDirectionOverride(region) {
  const override = translationRegionLayoutOverrides.value[region?.id]
  return normalizeDirectionValue(override?.direction)
}

function getRegionDirectionValue(region) {
  return getRegionDirectionOverride(region)
}

function getResolvedRegionDirection(region) {
  const override = getRegionDirectionOverride(region)
  if (override !== 'auto') {
    return override
  }
  if (isChineseTargetLanguage()) {
    return 'vertical'
  }
  return normalizeDirectionValue(region?.direction) === 'vertical' ? 'vertical' : 'horizontal'
}

function isVerticalRegion(region) {
  return getResolvedRegionDirection(region) === 'vertical'
}

function getResolvedRegionAlignment(region) {
  const normalized = String(region?.alignment || config.value.render_alignment || 'left')
    .trim()
    .toLowerCase()
  if (normalized === 'center' || normalized === 'right' || normalized === 'left') {
    return normalized
  }
  return 'left'
}

function getRegionFontSize(region) {
  if (Object.prototype.hasOwnProperty.call(fontSizeInputDrafts.value, region.id)) {
    return fontSizeInputDrafts.value[region.id]
  }
  const override = translationRegionLayoutOverrides.value[region.id]
  if (override && typeof override.font_size === 'number') {
    return override.font_size
  }
  return Number(region.font_size || 12)
}

function getPageHistoryState(pageId) {
  return pageEditHistory.value[pageId] || { undo: [], redo: [] }
}

function replacePageHistoryState(pageId, nextState) {
  pageEditHistory.value = {
    ...pageEditHistory.value,
    [pageId]: nextState
  }
}

function pushCanvasHistory(pageId, entry) {
  if (!pageId || !entry) {
    return
  }
  const current = getPageHistoryState(pageId)
  const nextUndo = [...current.undo, entry].slice(-maxCanvasHistoryEntries)
  replacePageHistoryState(pageId, {
    undo: nextUndo,
    redo: []
  })
}

function isEditableTextTarget(target) {
  if (!target || !(target instanceof HTMLElement)) {
    return false
  }
  return Boolean(target.closest('input, textarea, select, [contenteditable="true"]'))
}

function isRegionSelectedForMerge(region) {
  return Boolean(mergeRegionSelection.value[region.id])
}

function syncEditSelection() {
  if (!mergedInspectionPages.value.length) {
    resetEditInspectorSelection()
    return
  }

  if (!mergedInspectionPages.value.some((page) => page.stored_name === selectedEditPageKey.value)) {
    selectedEditPageKey.value = mergedInspectionPages.value[0].stored_name
  }

  const currentPage = mergedInspectionPages.value.find((page) => page.stored_name === selectedEditPageKey.value)
  if (!currentPage) {
    selectedEditRegionKey.value = ''
    return
  }

  if (!currentPage.regions.some((region) => region.id === selectedEditRegionKey.value)) {
    selectedEditRegionKey.value = currentPage.regions[0]?.id || ''
  }
}

function getEffectiveRegionBBox(region) {
  const override = translationRegionLayoutOverrides.value[region?.id]
  const bbox = Array.isArray(override?.bbox) && override.bbox.length === 4
    ? override.bbox
    : region?.bbox
  return Array.isArray(bbox) && bbox.length === 4 ? bbox : [0, 0, 0, 0]
}

function getStyleRegionBoxStyle(region, page) {
  const [x1, y1, x2, y2] = getEffectiveRegionBBox(region)
  const width = Math.max(page?.image_width || 1, 1)
  const height = Math.max(page?.image_height || 1, 1)
  const left = Math.max(0, (x1 / width) * 100)
  const top = Math.max(0, (y1 / height) * 100)
  const boxWidth = Math.max(0.6, ((x2 - x1) / width) * 100)
  const boxHeight = Math.max(1, ((y2 - y1) / height) * 100)
  return {
    left: `${left}%`,
    top: `${top}%`,
    width: `${boxWidth}%`,
    height: `${boxHeight}%`
  }
}

function getStyleRegionLabelClass(region, page) {
  const [x1, y1, x2] = getEffectiveRegionBBox(region)
  const width = Math.max(page?.image_width || 1, 1)
  const height = Math.max(page?.image_height || 1, 1)
  const nearTop = (y1 / height) < 0.08
  const nearRight = (x2 / width) > 0.84
  return {
    'label-below': nearTop,
    'label-right': nearRight
  }
}

function isManualRegion(region) {
  return Boolean(region?.manual)
}

function getCanvasPoint(event, page) {
  const canvas = event.currentTarget?.closest?.('.style-preview-canvas') || event.currentTarget
  const rect = canvas.getBoundingClientRect()
  return getCanvasPointFromRect(rect, event.clientX, event.clientY, page)
}

function getCanvasPointFromRect(rect, clientX, clientY, page) {
  const imageWidth = Math.max(page?.image_width || 1, 1)
  const imageHeight = Math.max(page?.image_height || 1, 1)
  const safeWidth = Math.max(rect?.width || 1, 1)
  const safeHeight = Math.max(rect?.height || 1, 1)
  const x = Math.min(imageWidth, Math.max(0, ((clientX - rect.left) / safeWidth) * imageWidth))
  const y = Math.min(imageHeight, Math.max(0, ((clientY - rect.top) / safeHeight) * imageHeight))
  return {
    x: Math.round(x),
    y: Math.round(y)
  }
}

function clampValue(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

function normalizeBBoxToPage(bbox, page) {
  const imageWidth = Math.max(page?.image_width || 1, 1)
  const imageHeight = Math.max(page?.image_height || 1, 1)
  const minSize = 8

  let [x1, y1, x2, y2] = (Array.isArray(bbox) ? bbox : [0, 0, 0, 0]).map((value) => Math.round(Number(value) || 0))
  x1 = clampValue(x1, 0, imageWidth)
  x2 = clampValue(x2, 0, imageWidth)
  y1 = clampValue(y1, 0, imageHeight)
  y2 = clampValue(y2, 0, imageHeight)

  if (x2 < x1) {
    [x1, x2] = [x2, x1]
  }
  if (y2 < y1) {
    [y1, y2] = [y2, y1]
  }

  if (x2 - x1 < minSize) {
    if (x1 + minSize <= imageWidth) {
      x2 = x1 + minSize
    } else {
      x1 = Math.max(0, imageWidth - minSize)
      x2 = imageWidth
    }
  }

  if (y2 - y1 < minSize) {
    if (y1 + minSize <= imageHeight) {
      y2 = y1 + minSize
    } else {
      y1 = Math.max(0, imageHeight - minSize)
      y2 = imageHeight
    }
  }

  return [x1, y1, x2, y2]
}

function translateBBoxWithinPage(originBBox, deltaX, deltaY, page) {
  const imageWidth = Math.max(page?.image_width || 1, 1)
  const imageHeight = Math.max(page?.image_height || 1, 1)
  const width = Math.max(8, originBBox[2] - originBBox[0])
  const height = Math.max(8, originBBox[3] - originBBox[1])
  let x1 = originBBox[0] + deltaX
  let y1 = originBBox[1] + deltaY
  x1 = clampValue(x1, 0, Math.max(0, imageWidth - width))
  y1 = clampValue(y1, 0, Math.max(0, imageHeight - height))
  return [x1, y1, x1 + width, y1 + height].map((value) => Math.round(value))
}

function resizeBBoxWithinPage(originBBox, handle, deltaX, deltaY, page) {
  let [x1, y1, x2, y2] = originBBox
  if (handle.includes('n')) {
    y1 += deltaY
  }
  if (handle.includes('s')) {
    y2 += deltaY
  }
  if (handle.includes('w')) {
    x1 += deltaX
  }
  if (handle.includes('e')) {
    x2 += deltaX
  }
  return normalizeBBoxToPage([x1, y1, x2, y2], page)
}

function getCanvasHandleCursor(handle) {
  const cursorMap = {
    nw: 'nwse-resize',
    se: 'nwse-resize',
    ne: 'nesw-resize',
    sw: 'nesw-resize',
    n: 'ns-resize',
    s: 'ns-resize',
    e: 'ew-resize',
    w: 'ew-resize'
  }
  return cursorMap[handle] || 'move'
}

function getCanvasPreviewImageUrl(page) {
  const dirty = Boolean(page?.stored_name && canvasPreviewDirtyPages.value[page.stored_name])
  if (isCanvasReviewMode.value && dirty) {
    const baseImagePath = page?.base_image_url || page?.translated_image_url || page?.image_url || ''
    return withCacheBust(toApiUrl(baseImagePath))
  }

  const latestTranslatedUrl = page?.stored_name
    ? String(latestTranslatedImageUrlByPage.value[page.stored_name] || '').trim()
    : ''
  if (latestTranslatedUrl) {
    return latestTranslatedUrl
  }

  const imagePath = page?.translated_image_url || page?.image_url || page?.base_image_url || ''
  return withCacheBust(toApiUrl(imagePath))
}

function getSavedTranslatedImageUrl(page) {
  const latestTranslatedUrl = page?.stored_name
    ? String(latestTranslatedImageUrlByPage.value[page.stored_name] || '').trim()
    : ''
  if (latestTranslatedUrl) {
    return latestTranslatedUrl
  }

  const translatedPath = String(page?.translated_image_url || '').trim()
  if (translatedPath) {
    return withCacheBust(toApiUrl(translatedPath))
  }

  return ''
}

function selectAdjacentEditPage(offset) {
  if (!mergedInspectionPages.value.length || selectedEditPageIndex.value < 0) {
    return
  }
  const nextIndex = Math.min(
    mergedInspectionPages.value.length - 1,
    Math.max(0, selectedEditPageIndex.value + offset),
  )
  const nextPage = mergedInspectionPages.value[nextIndex]
  if (nextPage?.stored_name) {
    selectedEditPageKey.value = nextPage.stored_name
  }
}

function getCanvasPreviewText(region) {
  if (isRegionSkipEnabled(region) || isRegionDisabled(region)) {
    return ''
  }
  const draft = Object.prototype.hasOwnProperty.call(translationInputDrafts.value, region.id)
    ? String(translationInputDrafts.value[region.id] || '')
    : ''
  return draft || getEditRegionText(region) || region.preview_text || ''
}

function shouldShowSourceCropPreview(region, page) {
  return Boolean(
    isCanvasReviewMode.value
    && page?.stored_name
    && canvasPreviewDirtyPages.value[page.stored_name]
    && (isRegionSkipEnabled(region) || isRegionDisabled(region))
  )
}

function shouldShowCanvasTextOverlay(region, page) {
  return Boolean(
    isCanvasReviewMode.value
    && page?.stored_name
    && canvasPreviewDirtyPages.value[page.stored_name]
    && !isRegionSkipEnabled(region)
    && !isRegionDisabled(region)
    && getCanvasPreviewText(region)
  )
}

function getSourceCropImageStyle(region, page) {
  const [x1, y1, x2, y2] = getEffectiveRegionBBox(region)
  const regionWidth = Math.max(1, x2 - x1)
  const regionHeight = Math.max(1, y2 - y1)
  const pageWidth = Math.max(page?.image_width || 1, 1)
  const pageHeight = Math.max(page?.image_height || 1, 1)
  return {
    width: `${(pageWidth / regionWidth) * 100}%`,
    height: `${(pageHeight / regionHeight) * 100}%`,
    left: `-${(x1 / regionWidth) * 100}%`,
    top: `-${(y1 / regionHeight) * 100}%`
  }
}

function getCanvasPreviewTextStyle(region) {
  const rawFontSize = Number(getRegionFontSize(region) || region?.font_size || 12)
  const scaledFontSize = Math.max(8, Math.round(rawFontSize * Math.max(translatedPreviewScale.value || 1, 0.1)))
  const spacingMultiplier = Number(region?.letter_spacing || config.value.render_letter_spacing || 1.08)
  const normalizedLineSpacing = Number(region?.line_spacing || 1.08)
  const letterSpacing = Math.max(0, (spacingMultiplier - 1) * scaledFontSize * 0.2)
  const lineHeight = Math.max(0.92, Math.min(1.6, normalizedLineSpacing || 1.08))
  return {
    fontFamily: getRegionPreviewFontFamily(region),
    fontSize: `${scaledFontSize}px`,
    letterSpacing: `${letterSpacing.toFixed(2)}px`,
    lineHeight: String(lineHeight),
    fontSynthesis: 'none',
    writingMode: isVerticalRegion(region) ? 'vertical-rl' : 'horizontal-tb',
    textOrientation: isVerticalRegion(region) ? 'mixed' : 'initial',
    textAlign: isVerticalRegion(region)
      ? 'start'
      : getResolvedRegionAlignment(region) === 'center'
        ? 'center'
        : getResolvedRegionAlignment(region) === 'right'
          ? 'right'
          : 'left'
  }
}

function getCanvasPreviewTextContainerStyle(region) {
  const alignment = getResolvedRegionAlignment(region)
  if (isVerticalRegion(region)) {
    return {
      justifyContent: 'flex-end',
      alignItems: alignment === 'center'
        ? 'center'
        : alignment === 'right'
          ? 'flex-end'
          : 'flex-start'
    }
  }

  return {
    justifyContent: alignment === 'center'
      ? 'center'
      : alignment === 'right'
        ? 'flex-end'
        : 'flex-start',
    alignItems: 'flex-start'
  }
}

function refreshTranslatedPreviewScale() {
  const page = selectedEditPage.value
  const canvasElement = translatedPreviewCanvasRef.value
  if (!page?.image_width || !canvasElement) {
    translatedPreviewScale.value = 1
    return
  }
  const safeWidth = Math.max(canvasElement.clientWidth || 0, 1)
  translatedPreviewScale.value = safeWidth / Math.max(page.image_width || 1, 1)
}

function getManualDraftBBox(page) {
  const draft = manualDrawDraft.value
  if (!draft || !page || draft.stored_name !== page.stored_name) {
    return null
  }

  const x1 = Math.min(draft.startX, draft.currentX)
  const y1 = Math.min(draft.startY, draft.currentY)
  const x2 = Math.max(draft.startX, draft.currentX)
  const y2 = Math.max(draft.startY, draft.currentY)
  if (x2 - x1 < 2 || y2 - y1 < 2) {
    return null
  }
  return [x1, y1, x2, y2]
}

function getManualDraftStyle(page) {
  const bbox = getManualDraftBBox(page)
  if (!bbox) {
    return {}
  }
  return getStyleRegionBoxStyle({ bbox }, page)
}

function clearManualDraft(options = {}) {
  manualDrawDraft.value = null
  if (!options.keepMode) {
    manualDrawMode.value = false
    adjustingRegionId.value = ''
  }
}

async function submitManualDraw(page, bbox) {
  creatingManualRegion.value = true
  errorMessage.value = ''
  try {
    if (isCanvasReviewMode.value) {
      await runCanvasStructuredAction(page, {
        kind: 'create_region',
        bbox
      })
      return
    }

    const response = await fetch(toApiUrl(`/api/manual-regions/${sessionId.value}`), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        action: 'create',
        stored_name: page.stored_name,
        bbox,
        config: buildRuntimeConfig()
      })
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '新增补漏框失败')
    }
    await loadEditInspection({ silent: true })
    selectedEditPageKey.value = page.stored_name
    selectedEditRegionKey.value = payload.region?.id || selectedEditRegionKey.value
    status.value = '已新增补漏框，并自动尝试 OCR / 翻译。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '新增补漏框失败'
  } finally {
    creatingManualRegion.value = false
  }
}

function startRegionBBoxAdjustment(region) {
  if (!region) {
    return
  }
  manualDrawMode.value = false
  mergeMode.value = false
  mergeRegionSelection.value = {}
  adjustingRegionId.value = region.id
  manualDrawDraft.value = null
  selectedEditRegionKey.value = region.id
  status.value = '请在原图上拖一个新框，替换当前文本框范围。'
}

function startManualDraw(event, page) {
  if ((!manualDrawMode.value && !adjustingRegionId.value) || !canCreateManualRegion.value || !page) {
    return
  }
  if (event.button !== 0) {
    return
  }

  const point = getCanvasPoint(event, page)
  const existingDraft = manualDrawDraft.value
  if (existingDraft && existingDraft.stored_name === page.stored_name) {
    existingDraft.currentX = point.x
    existingDraft.currentY = point.y
    existingDraft.awaitingSecondPoint = false
    finishManualDraw(event, page, { commit: true })
    event.preventDefault()
    return
  }

  manualDrawDraft.value = {
    action: adjustingRegionId.value ? 'adjust' : 'create',
    regionId: adjustingRegionId.value || '',
    stored_name: page.stored_name,
    startX: point.x,
    startY: point.y,
    currentX: point.x,
    currentY: point.y,
    pointerId: event.pointerId,
    awaitingSecondPoint: true,
    moved: false
  }
  selectedEditPageKey.value = page.stored_name
  event.currentTarget.setPointerCapture?.(event.pointerId)
  event.preventDefault()
}

function updateManualDraw(event, page) {
  const draft = manualDrawDraft.value
  if (!draft || !page || draft.stored_name !== page.stored_name) {
    return
  }
  const point = getCanvasPoint(event, page)
  draft.currentX = point.x
  draft.currentY = point.y
  if (Math.abs(draft.currentX - draft.startX) >= 4 || Math.abs(draft.currentY - draft.startY) >= 4) {
    draft.awaitingSecondPoint = false
    draft.moved = true
  }
}

async function finishManualDraw(event, page, options = {}) {
  const draft = manualDrawDraft.value
  if (!draft || !page || draft.stored_name !== page.stored_name) {
    return
  }
  const point = getCanvasPoint(event, page)
  draft.currentX = point.x
  draft.currentY = point.y
  if (event.currentTarget.hasPointerCapture?.(draft.pointerId)) {
    event.currentTarget.releasePointerCapture?.(draft.pointerId)
  }

  if (!options.commit && draft.awaitingSecondPoint && !draft.moved) {
    event.preventDefault()
    return
  }

  const bbox = getManualDraftBBox(page)
  if (!bbox || bbox[2] - bbox[0] < 8 || bbox[3] - bbox[1] < 8) {
    clearManualDraft({ keepMode: true })
    status.value = adjustingRegionId.value ? '调整后的框太小了，拖大一点会更稳。' : '补漏框太小了，拖大一点会更稳。'
    return
  }

  const draftAction = draft.action
  const draftRegionId = draft.regionId
  clearManualDraft({ keepMode: true })
  if (draftAction === 'adjust' && draftRegionId) {
    updateRegionLayoutOverride(draftRegionId, { bbox })
    status.value = '已更新这个文本框的范围，重新嵌字时会按新框计算。'
    adjustingRegionId.value = ''
    return
  }
  await submitManualDraw(page, bbox)
}

async function deleteManualRegion(region) {
  if (!sessionId.value || !region?.id || !isManualRegion(region)) {
    return
  }
  creatingManualRegion.value = true
  errorMessage.value = ''
  try {
    if (isCanvasReviewMode.value && selectedEditPage.value) {
      await runCanvasStructuredAction(selectedEditPage.value, {
        kind: 'delete_manual_region',
        regionIds: [region.id]
      })
      return
    }

    const response = await fetch(toApiUrl(`/api/manual-regions/${sessionId.value}`), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        action: 'delete',
        region_id: region.id
      })
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '删除补漏框失败')
    }

    const nextTranslationOverrides = { ...translationRegionOverrides.value }
    delete nextTranslationOverrides[region.id]
    translationRegionOverrides.value = nextTranslationOverrides

    const nextSkipOverrides = { ...translationRegionSkipOverrides.value }
    delete nextSkipOverrides[region.id]
    translationRegionSkipOverrides.value = nextSkipOverrides

    const nextDisabledOverrides = { ...translationRegionDisabledOverrides.value }
    delete nextDisabledOverrides[region.id]
    translationRegionDisabledOverrides.value = nextDisabledOverrides

    const nextLayoutOverrides = { ...translationRegionLayoutOverrides.value }
    delete nextLayoutOverrides[region.id]
    translationRegionLayoutOverrides.value = nextLayoutOverrides

    const nextStyleOverrides = { ...styleRegionOverrides.value }
    delete nextStyleOverrides[region.id]
    styleRegionOverrides.value = nextStyleOverrides

    const nextMergeSelection = { ...mergeRegionSelection.value }
    delete nextMergeSelection[region.id]
    mergeRegionSelection.value = nextMergeSelection

    await loadEditInspection({ silent: true })
    status.value = '已删除这个手动补漏框。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '删除补漏框失败'
  } finally {
    creatingManualRegion.value = false
  }
}

function applyReviewInspectionPayload(payload) {
  reviewInspectionPages.value = payload.pages || []
  if (payload.workflow_stage) {
    workflowStage.value = payload.workflow_stage
  }
  const sessionOverrides = payload?.overrides && typeof payload.overrides === 'object'
    ? payload.overrides
    : {}
  const nextOverrides = { ...(sessionOverrides.translation_region_overrides || {}) }
  const nextSkipOverrides = { ...(sessionOverrides.translation_region_skip_overrides || {}) }
  const nextDisabledOverrides = { ...(sessionOverrides.translation_region_disabled_overrides || {}) }
  const nextLayoutOverrides = { ...(sessionOverrides.translation_region_layout_overrides || {}) }
  for (const page of reviewInspectionPages.value) {
    for (const region of page.regions || []) {
      if (region.override_translation) {
        nextOverrides[region.id] = region.override_translation
      }
      if (region.override_skip) {
        nextSkipOverrides[region.id] = true
      }
    }
  }
  translationRegionOverrides.value = nextOverrides
  translationRegionSkipOverrides.value = nextSkipOverrides
  translationRegionDisabledOverrides.value = nextDisabledOverrides
  translationRegionLayoutOverrides.value = nextLayoutOverrides
}

function applyStyleInspectionPayload(payload) {
  styleInspectionPages.value = payload.pages || []
  if (payload.workflow_stage) {
    workflowStage.value = payload.workflow_stage
  }
  const sessionOverrides = payload?.overrides && typeof payload.overrides === 'object'
    ? payload.overrides
    : {}
  styleRegionOverrides.value = { ...(sessionOverrides.style_region_overrides || styleRegionOverrides.value) }
}

function updatePagePreviewUrl(pages, storedName, nextImageUrl) {
  let changed = false
  const nextPages = pages.map((page) => {
    if (page.stored_name !== storedName) {
      return page
    }
    changed = true
    return {
      ...page,
      image_url: nextImageUrl,
      translated_image_url: nextImageUrl
    }
  })
  return changed ? nextPages : pages
}

function upsertTranslatedImage(images, payload, nextImageUrl, sessionIdValue) {
  const targetStoredName = payload.stored_name || ''
  const targetName = payload.name || ''
  const nextImage = {
    id: `${sessionIdValue}-translated-${targetStoredName || payload.current}`,
    name: targetName || `第 ${payload.current} 张`,
    url: nextImageUrl,
    stored_name: targetStoredName
  }

  let changed = false
  const nextImages = images.map((image) => {
    const sameStoredName = targetStoredName && image.stored_name === targetStoredName
    const sameName = targetName && image.name === targetName
    if (!sameStoredName && !sameName) {
      return image
    }
    changed = true
    return {
      ...image,
      ...nextImage,
      id: image.id || nextImage.id,
    }
  })

  if (changed) {
    return nextImages
  }

  return [...images, nextImage]
}

async function loadReviewInspection(options = {}) {
  const silent = Boolean(options.silent)
  if (!sessionId.value) {
    reviewInspectionPages.value = []
    syncEditSelection()
    return
  }

  if (!silent) {
    reviewInspectionLoading.value = true
  }
  try {
    const response = await fetch(toApiUrl(`/api/review-regions/${sessionId.value}`), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ config: buildRuntimeConfig() })
    })

    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '读取翻译审校结果失败')
    }

    applyReviewInspectionPayload(payload)
    syncEditSelection()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取翻译审校结果失败'
  } finally {
    if (!silent) {
      reviewInspectionLoading.value = false
    }
  }
}

async function loadStyleInspection(options = {}) {
  const silent = Boolean(options.silent)
  if (!sessionId.value || config.value.font_style_mode !== 'auto-map') {
    styleInspectionPages.value = []
    syncEditSelection()
    return
  }

  if (!silent) {
    styleInspectionLoading.value = true
  }
  try {
    const response = await fetch(toApiUrl(`/api/style-regions/${sessionId.value}`), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ config: buildRuntimeConfig() })
    })

    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '读取字体样式识别结果失败')
    }

    applyStyleInspectionPayload(payload)
    syncEditSelection()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '读取字体样式识别结果失败'
  } finally {
    if (!silent) {
      styleInspectionLoading.value = false
    }
  }
}

async function loadEditInspection(options = {}) {
  const silent = Boolean(options.silent)
  if (!sessionId.value) {
    resetTranslationReview()
    resetStyleInspector()
    resetEditInspectorSelection()
    return
  }

  const tasks = [loadReviewInspection({ silent })]
  if (config.value.font_style_mode === 'auto-map') {
    tasks.push(loadStyleInspection({ silent }))
  } else {
    resetStyleInspector()
  }

  await Promise.all(tasks)
  translationInputDrafts.value = {}
  fontSizeInputDrafts.value = {}
  syncEditSelection()
}

async function ensureEditInspectionReadyAfterDetect() {
  if (workflowStage.value !== 'detected') {
    return
  }

  const retryDelays = [180, 360, 700]
  for (const delay of retryDelays) {
    if (mergedInspectionPages.value.length > 0) {
      return
    }
    status.value = '文本框识别已完成，正在等待逐框校对数据就绪…'
    await sleep(delay)
    await loadEditInspection({ silent: true })
  }
}

function updateStyleOverride(region, nextStyle) {
  const normalizedStyle = styleBucketOptions.some((option) => option.value === nextStyle) ? nextStyle : ''
  const previousOverride = getRegionOverrideValue(region)
  const page = selectedEditPage.value
  const nextOverrides = { ...styleRegionOverrides.value }
  if (!normalizedStyle || normalizedStyle === region.auto_style) {
    delete nextOverrides[region.id]
  } else {
    nextOverrides[region.id] = normalizedStyle
  }
  styleRegionOverrides.value = nextOverrides
  selectedEditRegionKey.value = region.id

  if (!isCanvasReviewMode.value || !page || normalizedStyle === previousOverride) {
    return
  }

  void runCanvasCommand(page, {
    label: '修改字体样式',
    redoCommands: [
      {
        type: 'update_font_style',
        region_id: region.id,
        style: normalizedStyle
      }
    ],
    undoCommands: [
      {
        type: 'update_font_style',
        region_id: region.id,
        style: previousOverride
      }
    ],
    successMessage: '已更新文本框字体样式。',
    focusRegionId: region.id,
    rollback: () => {
      const rollbackOverrides = { ...styleRegionOverrides.value }
      if (previousOverride) {
        rollbackOverrides[region.id] = previousOverride
      } else {
        delete rollbackOverrides[region.id]
      }
      styleRegionOverrides.value = rollbackOverrides
    }
  })
}

function updateTranslationOverride(region, nextTranslation) {
  const normalizedTranslation = String(nextTranslation || '').trim()
  const machineTranslation = String(region.machine_translation || '').trim()
  const nextOverrides = { ...translationRegionOverrides.value }

  if (!normalizedTranslation || normalizedTranslation === machineTranslation) {
    delete nextOverrides[region.id]
  } else {
    nextOverrides[region.id] = normalizedTranslation
  }

  translationRegionOverrides.value = nextOverrides
  selectedEditRegionKey.value = region.id
}

function updateTranslationSkipOverride(region, enabled) {
  const previousValue = isRegionSkipEnabled(region)
  const nextOverrides = { ...translationRegionSkipOverrides.value }
  if (enabled) {
    nextOverrides[region.id] = true
  } else {
    delete nextOverrides[region.id]
  }
  translationRegionSkipOverrides.value = nextOverrides
  selectedEditRegionKey.value = region.id
  if (selectedEditPage.value?.stored_name) {
    markCanvasPreviewDirty(selectedEditPage.value.stored_name)
  }

  if (!isCanvasReviewMode.value || !selectedEditPage.value || enabled === previousValue) {
    return
  }

  void runCanvasCommand(selectedEditPage.value, {
    label: enabled ? '保留原文' : '取消保留原文',
    redoCommands: [
      {
        type: 'set_keep_original',
        region_id: region.id,
        enabled
      }
    ],
    undoCommands: [
      {
        type: 'set_keep_original',
        region_id: region.id,
        enabled: previousValue
      }
    ],
    successMessage: enabled ? '这个文本框已设置为保留原文。' : '这个文本框已恢复可翻译状态。',
    focusRegionId: region.id,
    rollback: () => {
      const rollbackOverrides = { ...translationRegionSkipOverrides.value }
      if (previousValue) {
        rollbackOverrides[region.id] = true
      } else {
        delete rollbackOverrides[region.id]
      }
      translationRegionSkipOverrides.value = rollbackOverrides
    }
  })
}

function updateRegionDisabledOverride(region, enabled) {
  const previousValue = isRegionDisabled(region)
  const nextOverrides = { ...translationRegionDisabledOverrides.value }
  if (enabled) {
    nextOverrides[region.id] = true
  } else {
    delete nextOverrides[region.id]
  }
  translationRegionDisabledOverrides.value = nextOverrides

  const nextStyleOverrides = { ...styleRegionOverrides.value }
  delete nextStyleOverrides[region.id]
  styleRegionOverrides.value = nextStyleOverrides

  const nextTranslationOverrides = { ...translationRegionOverrides.value }
  delete nextTranslationOverrides[region.id]
  translationRegionOverrides.value = nextTranslationOverrides

  const nextSkipOverrides = { ...translationRegionSkipOverrides.value }
  delete nextSkipOverrides[region.id]
  translationRegionSkipOverrides.value = nextSkipOverrides

  const nextLayoutOverrides = { ...translationRegionLayoutOverrides.value }
  delete nextLayoutOverrides[region.id]
  translationRegionLayoutOverrides.value = nextLayoutOverrides

  const nextMergeSelection = { ...mergeRegionSelection.value }
  delete nextMergeSelection[region.id]
  mergeRegionSelection.value = nextMergeSelection

  if (selectedEditRegionKey.value === region.id) {
    selectedEditRegionKey.value = ''
  }
  selectedEditPageKey.value = selectedEditPage.value?.stored_name || selectedEditPageKey.value
  if (selectedEditPage.value?.stored_name) {
    markCanvasPreviewDirty(selectedEditPage.value.stored_name)
  }

  if (!isCanvasReviewMode.value || !selectedEditPage.value || enabled === previousValue) {
    void loadEditInspection({ silent: true })
    return
  }

  void runCanvasCommand(selectedEditPage.value, {
    label: enabled ? '禁用文本框' : '恢复文本框',
    redoCommands: [
      enabled
        ? { type: 'disable_region', region_id: region.id }
        : { type: 'restore_region', region_id: region.id }
    ],
    undoCommands: [
      previousValue
        ? { type: 'disable_region', region_id: region.id }
        : { type: 'restore_region', region_id: region.id }
    ],
    successMessage: enabled ? '已禁用这个自动识别框。' : '已恢复这个自动识别框。',
    focusRegionId: region.id,
    rollback: () => {
      void loadEditInspection({ silent: true })
    }
  })
}

function updateRegionLayoutOverride(regionId, patch) {
  const nextOverrides = { ...translationRegionLayoutOverrides.value }
  const currentOverride = nextOverrides[regionId] ? { ...nextOverrides[regionId] } : {}
  if (patch.bbox) {
    currentOverride.bbox = patch.bbox
  }
  if (typeof patch.font_size === 'number' && !Number.isNaN(patch.font_size)) {
    currentOverride.font_size = Math.max(8, Math.min(240, Math.round(patch.font_size)))
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'direction')) {
    const normalizedDirection = normalizeDirectionValue(patch.direction)
    if (normalizedDirection === 'auto') {
      delete currentOverride.direction
    } else {
      currentOverride.direction = normalizedDirection
    }
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'font_key')) {
    const normalizedFontKey = String(patch.font_key || '').trim()
    if (!normalizedFontKey) {
      delete currentOverride.font_key
    } else {
      currentOverride.font_key = normalizedFontKey
    }
  }
  if (!Object.keys(currentOverride).length) {
    delete nextOverrides[regionId]
  } else {
    nextOverrides[regionId] = currentOverride
  }
  translationRegionLayoutOverrides.value = nextOverrides
  if (selectedEditPage.value?.stored_name) {
    markCanvasPreviewDirty(selectedEditPage.value.stored_name)
  }
}

async function applyPageCommands(pageId, commands) {
  if (!sessionId.value || !pageId || !Array.isArray(commands) || !commands.length) {
    return null
  }
  const response = await fetch(toApiUrl(`/api/pages/${sessionId.value}/${pageId}/commands`), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      config: buildRuntimeConfig(),
      commands
    })
  })
  const payload = await response.json()
  if (!response.ok) {
    throw new Error(payload.detail || '更新页面编辑状态失败')
  }
  return payload
}

async function runCanvasCommand(page, options) {
  if (!page?.stored_name) {
    return
  }
  const redoCommands = options?.redoCommands || []
  const undoCommands = options?.undoCommands || []
  if (!redoCommands.length) {
    return
  }

  try {
    await applyPageCommands(page.stored_name, redoCommands)
    if (undoCommands.length) {
      pushCanvasHistory(page.stored_name, {
        label: String(options?.label || '编辑文本框'),
        undoCommands,
        redoCommands
      })
    }
    if (options?.successMessage) {
      status.value = options.successMessage
    }
    await loadEditInspection({ silent: true })
    if (options?.focusRegionId) {
      selectedEditPageKey.value = page.stored_name
      selectedEditRegionKey.value = options.focusRegionId
    }
  } catch (error) {
    if (typeof options?.rollback === 'function') {
      options.rollback()
    }
    errorMessage.value = error instanceof Error ? error.message : '更新页面编辑状态失败'
  }
}

async function undoCanvasEdit() {
  const pageId = selectedEditPage.value?.stored_name || ''
  if (!pageId) {
    return
  }
  const current = getPageHistoryState(pageId)
  const entry = current.undo[current.undo.length - 1]
  if (!entry) {
    return
  }

  try {
    if (entry.kind === 'create_region') {
      const createdRegionId = String(entry.createdRegionId || '')
      await applyPageCommands(pageId, [{ type: 'delete_manual_region', region_id: createdRegionId }])
    } else if (entry.kind === 'merge_regions') {
      const createdRegionId = String(entry.createdRegionId || '')
      const undoCommands = []
      if (createdRegionId) {
        undoCommands.push({ type: 'delete_manual_region', region_id: createdRegionId })
      }
      for (const sourceRegionId of entry.regionIds || []) {
        undoCommands.push({ type: 'restore_region', region_id: sourceRegionId })
      }
      await applyPageCommands(pageId, undoCommands)
    } else if (entry.kind === 'delete_manual_region') {
      const restoreCommands = [
        {
          type: 'restore_manual_region',
          payload: entry.deletedPayload || {}
        }
      ]
      const restoredRegionId = String(entry.deletedPayload?.id || '')
      const overrides = entry.regionOverrides || {}
      if (overrides.translation) {
        restoreCommands.push({
          type: 'update_translation',
          region_id: restoredRegionId,
          text: overrides.translation
        })
      }
      if (overrides.keepOriginal) {
        restoreCommands.push({
          type: 'set_keep_original',
          region_id: restoredRegionId,
          enabled: true
        })
      }
      if (overrides.disabled) {
        restoreCommands.push({
          type: 'disable_region',
          region_id: restoredRegionId
        })
      }
      if (overrides.style) {
        restoreCommands.push({
          type: 'update_font_style',
          region_id: restoredRegionId,
          style: overrides.style
        })
      }
      if (overrides.layout?.bbox) {
        restoreCommands.push({
          type: 'update_region_bbox',
          region_id: restoredRegionId,
          bbox: overrides.layout.bbox
        })
      }
      if (typeof overrides.layout?.font_size === 'number') {
        restoreCommands.push({
          type: 'update_font_size',
          region_id: restoredRegionId,
          font_size: overrides.layout.font_size
        })
      }
      if (typeof overrides.layout?.font_key === 'string') {
        restoreCommands.push({
          type: 'update_region_font',
          region_id: restoredRegionId,
          font_key: overrides.layout.font_key
        })
      }
      if (typeof overrides.layout?.direction === 'string') {
        restoreCommands.push({
          type: 'update_text_direction',
          region_id: restoredRegionId,
          direction: overrides.layout.direction
        })
      }
      await applyPageCommands(pageId, restoreCommands)
    } else {
      await applyPageCommands(pageId, entry.undoCommands)
    }
    replacePageHistoryState(pageId, {
      undo: current.undo.slice(0, -1),
      redo: [...current.redo, entry].slice(-maxCanvasHistoryEntries)
    })
    status.value = `已撤销：${entry.label}`
    await loadEditInspection({ silent: true })
    selectedEditPageKey.value = pageId
    if (entry.kind === 'merge_regions' && Array.isArray(entry.regionIds) && entry.regionIds.length) {
      selectedEditRegionKey.value = entry.regionIds[0]
    } else if (entry.kind === 'delete_manual_region') {
      selectedEditRegionKey.value = String(entry.deletedPayload?.id || '')
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '撤销失败'
  }
}

async function redoCanvasEdit() {
  const pageId = selectedEditPage.value?.stored_name || ''
  if (!pageId) {
    return
  }
  const current = getPageHistoryState(pageId)
  const entry = current.redo[current.redo.length - 1]
  if (!entry) {
    return
  }

  try {
    if (entry.kind === 'create_region') {
      const result = await applyPageCommands(pageId, [{ type: 'create_region', bbox: entry.bbox }])
      const refreshedEntry = {
        ...entry,
        createdRegionId: String(result?.created_region_id || '')
      }
      replacePageHistoryState(pageId, {
        undo: [...current.undo, refreshedEntry].slice(-maxCanvasHistoryEntries),
        redo: current.redo.slice(0, -1)
      })
      selectedEditRegionKey.value = refreshedEntry.createdRegionId || selectedEditRegionKey.value
    } else if (entry.kind === 'merge_regions') {
      const result = await applyPageCommands(pageId, [{ type: 'merge_regions', region_ids: entry.regionIds || [] }])
      const refreshedEntry = {
        ...entry,
        createdRegionId: String(result?.created_region_id || '')
      }
      replacePageHistoryState(pageId, {
        undo: [...current.undo, refreshedEntry].slice(-maxCanvasHistoryEntries),
        redo: current.redo.slice(0, -1)
      })
      selectedEditRegionKey.value = refreshedEntry.createdRegionId || selectedEditRegionKey.value
    } else if (entry.kind === 'delete_manual_region') {
      await applyPageCommands(pageId, [{ type: 'delete_manual_region', region_id: String(entry.deletedPayload?.id || '') }])
      replacePageHistoryState(pageId, {
        undo: [...current.undo, entry].slice(-maxCanvasHistoryEntries),
        redo: current.redo.slice(0, -1)
      })
    } else {
      await applyPageCommands(pageId, entry.redoCommands)
      replacePageHistoryState(pageId, {
        undo: [...current.undo, entry].slice(-maxCanvasHistoryEntries),
        redo: current.redo.slice(0, -1)
      })
    }
    status.value = `已重做：${entry.label}`
    await loadEditInspection({ silent: true })
    selectedEditPageKey.value = pageId
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重做失败'
  }
}

async function runCanvasStructuredAction(page, options) {
  if (!page?.stored_name || !options?.kind) {
    return
  }

  const kind = String(options.kind)
  const regionIds = Array.isArray(options.regionIds) ? options.regionIds.map((item) => String(item || '')).filter(Boolean) : []
  const bbox = Array.isArray(options.bbox) ? options.bbox : null
  let result = null

  if (kind === 'create_region' && bbox) {
    markCanvasPreviewDirty(page.stored_name)
    result = await applyPageCommands(page.stored_name, [{ type: 'create_region', bbox }])
    const createdRegionId = String(result?.created_region_id || '')
    const historyEntry = {
      kind: 'create_region',
      label: '新增补漏框',
      pageId: page.stored_name,
      bbox,
      createdRegionId
    }
    pushCanvasHistory(page.stored_name, historyEntry)
    status.value = '已新增补漏框，并自动尝试 OCR / 翻译。'
    await loadEditInspection({ silent: true })
    selectedEditPageKey.value = page.stored_name
    selectedEditRegionKey.value = createdRegionId || selectedEditRegionKey.value
    return
  }

  if (kind === 'merge_regions' && regionIds.length >= 2) {
    markCanvasPreviewDirty(page.stored_name)
    result = await applyPageCommands(page.stored_name, [{ type: 'merge_regions', region_ids: regionIds }])
    const createdRegionId = String(result?.created_region_id || '')
    const historyEntry = {
      kind: 'merge_regions',
      label: '合并文本框',
      pageId: page.stored_name,
      regionIds,
      createdRegionId
    }
    pushCanvasHistory(page.stored_name, historyEntry)
    status.value = '已合并选中的文本框。原框会先隐藏，新的合并框可继续编辑。'
    await loadEditInspection({ silent: true })
    selectedEditPageKey.value = page.stored_name
    selectedEditRegionKey.value = createdRegionId || selectedEditRegionKey.value
    return
  }

  if (kind === 'delete_manual_region' && regionIds.length === 1) {
    const targetRegionId = regionIds[0]
    markCanvasPreviewDirty(page.stored_name)
    const result = await applyPageCommands(page.stored_name, [{ type: 'delete_manual_region', region_id: targetRegionId }])
    const deletedPayload = result?.deleted_region_payload || {}
    const historyEntry = {
      kind: 'delete_manual_region',
      label: '删除补漏框',
      pageId: page.stored_name,
      regionIds,
      deletedPayload,
      regionOverrides: {
        translation: translationRegionOverrides.value[targetRegionId] || '',
        keepOriginal: Boolean(translationRegionSkipOverrides.value[targetRegionId]),
        disabled: Boolean(translationRegionDisabledOverrides.value[targetRegionId]),
        layout: translationRegionLayoutOverrides.value[targetRegionId]
          ? { ...translationRegionLayoutOverrides.value[targetRegionId] }
          : null,
        style: styleRegionOverrides.value[targetRegionId] || ''
      }
    }
    pushCanvasHistory(page.stored_name, historyEntry)
    status.value = '已删除这个手动补漏框。'
    await loadEditInspection({ silent: true })
    selectedEditPageKey.value = page.stored_name
    return
  }
}

function startCanvasRegionTransform(event, region, page, mode = 'move', handle = '') {
  if (!region || !page) {
    return
  }
  if (mergeMode.value) {
    toggleMergeSelection(region)
    return
  }

  selectedEditRegionKey.value = region.id

  if (!canDirectManipulateCanvas.value) {
    return
  }

  if (event.button != null && event.button !== 0) {
    return
  }

  const canvas = event.currentTarget?.closest?.('.style-preview-canvas')
  if (!canvas) {
    return
  }

  const rect = canvas.getBoundingClientRect()
  const point = getCanvasPointFromRect(rect, event.clientX, event.clientY, page)
  canvasTransformState.value = {
    pointerId: event.pointerId,
    regionId: region.id,
    pageId: page.stored_name,
    mode,
    handle,
    rect,
    startPoint: point,
    originBBox: getEffectiveRegionBBox(region),
    moved: false,
    startedAt: Date.now()
  }

  event.preventDefault()
  event.stopPropagation()
}

function updateCanvasRegionTransform(event) {
  const draft = canvasTransformState.value
  if (!draft) {
    return
  }
  if (draft.pointerId != null && event.pointerId != null && draft.pointerId !== event.pointerId) {
    return
  }

  const page = mergedInspectionPages.value.find((item) => item.stored_name === draft.pageId)
  if (!page) {
    canvasTransformState.value = null
    return
  }

  const point = getCanvasPointFromRect(draft.rect, event.clientX, event.clientY, page)
  const deltaX = point.x - draft.startPoint.x
  const deltaY = point.y - draft.startPoint.y
  let nextBBox = draft.originBBox

  if (draft.mode === 'move') {
    nextBBox = translateBBoxWithinPage(draft.originBBox, deltaX, deltaY, page)
  } else {
    nextBBox = resizeBBoxWithinPage(draft.originBBox, draft.handle, deltaX, deltaY, page)
  }

  const changed = nextBBox.some((value, index) => value !== draft.originBBox[index])
  draft.moved = draft.moved || changed
  updateRegionLayoutOverride(draft.regionId, { bbox: nextBBox })
}

async function finishCanvasRegionTransform(event) {
  const draft = canvasTransformState.value
  if (!draft) {
    return
  }
  if (event?.pointerId != null && draft.pointerId != null && event.pointerId !== draft.pointerId) {
    return
  }

  canvasTransformState.value = null
  if (!draft.moved) {
    return
  }

  const page = mergedInspectionPages.value.find((item) => item.stored_name === draft.pageId)
  const region = page?.regions?.find((item) => item.id === draft.regionId)
  const nextBBox = region ? getEffectiveRegionBBox(region) : null
  if (!page || !nextBBox) {
    return
  }

  await runCanvasCommand(page, {
    label: draft.mode === 'move' ? '移动文本框' : '调整文本框大小',
    redoCommands: [
      {
        type: 'update_region_bbox',
        region_id: draft.regionId,
        bbox: nextBBox
      }
    ],
    undoCommands: [
      {
        type: 'update_region_bbox',
        region_id: draft.regionId,
        bbox: draft.originBBox
      }
    ],
    successMessage: draft.mode === 'move'
      ? '已移动文本框位置，重新嵌字时会按新位置计算。'
      : '已更新文本框大小，重新嵌字时会按新框重新排版。',
    focusRegionId: draft.regionId,
    rollback: () => updateRegionLayoutOverride(draft.regionId, { bbox: draft.originBBox })
  })
}

function cancelCanvasRegionTransform() {
  const draft = canvasTransformState.value
  if (!draft) {
    return
  }
  updateRegionLayoutOverride(draft.regionId, { bbox: draft.originBBox })
  canvasTransformState.value = null
}

function updateRegionFontSize(region, nextValue) {
  const parsed = Number(nextValue)
  if (Number.isNaN(parsed)) {
    return
  }
  updateRegionLayoutOverride(region.id, { font_size: parsed })
  selectedEditRegionKey.value = region.id
}

function updateRegionFontOverride(region, nextFontId) {
  const normalizedFontId = getFontById(nextFontId) ? String(nextFontId || '').trim() : ''
  const previousFontId = getRegionFontOverrideId(region)
  updateRegionLayoutOverride(region.id, { font_key: normalizedFontId })
  selectedEditRegionKey.value = region.id
  void warmPreviewFonts()

  if (!isCanvasReviewMode.value || !selectedEditPage.value || normalizedFontId === previousFontId) {
    return
  }

  void runCanvasCommand(selectedEditPage.value, {
    label: '修改字体',
    redoCommands: [
      {
        type: 'update_region_font',
        region_id: region.id,
        font_key: normalizedFontId
      }
    ],
    undoCommands: [
      {
        type: 'update_region_font',
        region_id: region.id,
        font_key: previousFontId
      }
    ],
    successMessage: normalizedFontId ? '已更新这个文本框的字体。' : '已恢复跟随默认字体。',
    focusRegionId: region.id,
    rollback: () => {
      updateRegionLayoutOverride(region.id, { font_key: previousFontId })
      void warmPreviewFonts()
    }
  })
}

function updateRegionTextDirection(region, nextDirection) {
  const normalizedDirection = normalizeDirectionValue(nextDirection)
  const previousDirection = getRegionDirectionValue(region)
  updateRegionLayoutOverride(region.id, { direction: normalizedDirection })
  selectedEditRegionKey.value = region.id

  if (!isCanvasReviewMode.value || !selectedEditPage.value || normalizedDirection === previousDirection) {
    return
  }

  void runCanvasCommand(selectedEditPage.value, {
    label: '调整文本方向',
    redoCommands: [
      {
        type: 'update_text_direction',
        region_id: region.id,
        direction: normalizedDirection
      }
    ],
    undoCommands: [
      {
        type: 'update_text_direction',
        region_id: region.id,
        direction: previousDirection
      }
    ],
    successMessage: normalizedDirection === 'horizontal'
      ? '已切换为横排预览。'
      : normalizedDirection === 'vertical'
        ? '已切换为竖排预览。'
        : '已恢复自动文本方向。',
    focusRegionId: region.id,
    rollback: () => {
      updateRegionLayoutOverride(region.id, { direction: previousDirection })
    }
  })
}

function handleRegionTextInput(region, nextValue) {
  selectedEditRegionKey.value = region.id
  if (isCanvasReviewMode.value) {
    translationInputDrafts.value = {
      ...translationInputDrafts.value,
      [region.id]: String(nextValue ?? '')
    }
    if (selectedEditPage.value?.stored_name) {
      markCanvasPreviewDirty(selectedEditPage.value.stored_name)
    }
    return
  }
  updateTranslationOverride(region, nextValue)
}

function commitRegionTextDraft(region) {
  if (!isCanvasReviewMode.value || !selectedEditPage.value) {
    return
  }
  const draftValue = String(translationInputDrafts.value[region.id] ?? getEditRegionText(region))
  const normalizedTranslation = draftValue.trim()
  const previousOverride = String(translationRegionOverrides.value[region.id] || '')
  if (normalizedTranslation === previousOverride) {
    return
  }

  const nextOverrides = { ...translationRegionOverrides.value }
  if (!normalizedTranslation || normalizedTranslation === String(region.machine_translation || '').trim()) {
    delete nextOverrides[region.id]
  } else {
    nextOverrides[region.id] = normalizedTranslation
  }
  translationRegionOverrides.value = nextOverrides

  void runCanvasCommand(selectedEditPage.value, {
    label: '修改译文',
    redoCommands: [
      {
        type: 'update_translation',
        region_id: region.id,
        text: normalizedTranslation
      }
    ],
    undoCommands: [
      {
        type: 'update_translation',
        region_id: region.id,
        text: previousOverride
      }
    ],
    successMessage: '已更新这个文本框的译文。',
    focusRegionId: region.id,
    rollback: () => {
      const rollbackOverrides = { ...translationRegionOverrides.value }
      if (previousOverride) {
        rollbackOverrides[region.id] = previousOverride
      } else {
        delete rollbackOverrides[region.id]
      }
      translationRegionOverrides.value = rollbackOverrides
    }
  })
}

function handleRegionFontSizeInput(region, nextValue) {
  selectedEditRegionKey.value = region.id
  const rawValue = String(nextValue ?? '')
  if (isCanvasReviewMode.value) {
    fontSizeInputDrafts.value = {
      ...fontSizeInputDrafts.value,
      [region.id]: rawValue
    }
    const parsed = Number(rawValue)
    if (!rawValue.trim() || Number.isNaN(parsed)) {
      return
    }
    updateRegionLayoutOverride(region.id, { font_size: parsed })
    return
  }
  fontSizeInputDrafts.value = {
    ...fontSizeInputDrafts.value,
    [region.id]: rawValue
  }
  const parsed = Number(rawValue)
  if (!rawValue.trim() || Number.isNaN(parsed)) {
    return
  }
  updateRegionFontSize(region, parsed)
}

function commitRegionFontSize(region) {
  const hasDraft = Object.prototype.hasOwnProperty.call(fontSizeInputDrafts.value, region.id)
  if (!hasDraft) {
    return
  }
  const rawDraft = String(fontSizeInputDrafts.value[region.id] ?? '')
  const nextValue = rawDraft.trim() === ''
    ? 8
    : Number(rawDraft)

  const nextDrafts = { ...fontSizeInputDrafts.value }
  delete nextDrafts[region.id]
  fontSizeInputDrafts.value = nextDrafts

  if (Number.isNaN(nextValue)) {
    return
  }
  const normalizedValue = Math.max(8, Math.min(240, Math.round(nextValue)))

  if (!isCanvasReviewMode.value || !selectedEditPage.value) {
    updateRegionFontSize(region, normalizedValue)
    return
  }

  const currentOverride = translationRegionLayoutOverrides.value[region.id] || {}
  const previousExplicit = typeof currentOverride.font_size === 'number' ? currentOverride.font_size : null

  updateRegionLayoutOverride(region.id, { font_size: normalizedValue })
  void runCanvasCommand(selectedEditPage.value, {
    label: '调整字号',
    redoCommands: [
      {
        type: 'update_font_size',
        region_id: region.id,
        font_size: normalizedValue
      }
    ],
    undoCommands: [
      previousExplicit == null
        ? {
            type: 'update_font_size',
            region_id: region.id
          }
        : {
            type: 'update_font_size',
            region_id: region.id,
            font_size: previousExplicit
          }
    ],
    successMessage: '已更新这个文本框的字号。',
    focusRegionId: region.id,
    rollback: () => {
      const rollbackOverrides = { ...translationRegionLayoutOverrides.value }
      const current = { ...(rollbackOverrides[region.id] || {}) }
      if (previousExplicit == null) {
        delete current.font_size
      } else {
        current.font_size = previousExplicit
      }
      rollbackOverrides[region.id] = current
      translationRegionLayoutOverrides.value = rollbackOverrides
    }
  })
}

function toggleMergeMode() {
  mergeMode.value = !mergeMode.value
  mergeRegionSelection.value = {}
  if (mergeMode.value) {
    manualDrawMode.value = false
    adjustingRegionId.value = ''
    status.value = '请在当前页选中至少两个文本框，然后点击“合并选中框”。'
  }
}

function toggleMergeSelection(region) {
  const nextSelection = { ...mergeRegionSelection.value }
  if (nextSelection[region.id]) {
    delete nextSelection[region.id]
  } else {
    nextSelection[region.id] = true
  }
  mergeRegionSelection.value = nextSelection
  selectedEditRegionKey.value = region.id
}

async function mergeSelectedRegions() {
  const page = selectedEditPage.value
  const regionIds = Object.keys(mergeRegionSelection.value)
  if (!sessionId.value || !page || regionIds.length < 2) {
    return
  }
  creatingManualRegion.value = true
  errorMessage.value = ''
  try {
    if (isCanvasReviewMode.value) {
      await runCanvasStructuredAction(page, {
        kind: 'merge_regions',
        regionIds
      })
      mergeRegionSelection.value = {}
      mergeMode.value = false
      return
    }

    const response = await fetch(toApiUrl(`/api/manual-regions/${sessionId.value}`), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        action: 'merge',
        stored_name: page.stored_name,
        region_ids: regionIds,
        config: buildRuntimeConfig()
      })
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '合并文本框失败')
    }

    const nextDisabledOverrides = { ...translationRegionDisabledOverrides.value }
    for (const regionId of regionIds) {
      nextDisabledOverrides[regionId] = true
    }
    translationRegionDisabledOverrides.value = nextDisabledOverrides
    mergeRegionSelection.value = {}
    mergeMode.value = false
    await loadEditInspection({ silent: true })
    selectedEditPageKey.value = page.stored_name
    selectedEditRegionKey.value = payload.region?.id || selectedEditRegionKey.value
    status.value = '已合并选中的文本框。原框会先隐藏，新的合并框可继续编辑。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '合并文本框失败'
  } finally {
    creatingManualRegion.value = false
  }
}

function restoreDisabledRegionsForPage(page) {
  if (!page?.stored_name) {
    return
  }
  const nextOverrides = { ...translationRegionDisabledOverrides.value }
  let changed = false
  for (const regionId of Object.keys(nextOverrides)) {
    if (!regionId.includes(page.stored_name)) {
      continue
    }
    delete nextOverrides[regionId]
    changed = true
  }
  if (!changed) {
    return
  }
  translationRegionDisabledOverrides.value = nextOverrides
  status.value = '已恢复当前页被禁用的自动识别框。'
  void loadEditInspection({ silent: true })
}

function clearTranslationOverrides() {
  translationRegionOverrides.value = {}
  translationRegionSkipOverrides.value = {}
  translationRegionDisabledOverrides.value = {}
  translationRegionLayoutOverrides.value = {}
  mergeRegionSelection.value = {}
  mergeMode.value = false
  adjustingRegionId.value = ''
  status.value = '已清空当前会话里的译文、保留原文、禁用框和框体调整设置。'
  void loadEditInspection({ silent: true })
}

function clearStyleOverrides() {
  styleRegionOverrides.value = {}
  status.value = '已清空当前会话里的手动样式覆盖。'
  void loadEditInspection({ silent: true })
}

function clearStoredApiKey() {
  config.value.api_key = ''
  saveStoredConfig(config.value)
  status.value = '已清除本机浏览器里保存的 API Key。'
}

function clearStoredImageApiKey() {
  config.value.image_cleanup_api_key = ''
  saveStoredConfig(config.value)
  status.value = '已清除本机浏览器里保存的图像去字 API Key。'
}

async function scrollSelectedRegionCardIntoView() {
  if (!selectedEditRegionKey.value) {
    return
  }
  await nextTick()
  const card = document.querySelector(`.style-region-card[data-region-id="${selectedEditRegionKey.value}"]`)
  if (card instanceof HTMLElement) {
    card.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
}

async function syncTranslatedPreviewScale() {
  await nextTick()
  refreshTranslatedPreviewScale()
}

function handleGlobalCanvasKeydown(event) {
  if (!isCanvasReviewMode.value || !selectedEditPage.value) {
    return
  }
  if (isEditableTextTarget(event.target)) {
    return
  }
  const isUndo = (event.metaKey || event.ctrlKey) && !event.shiftKey && event.key.toLowerCase() === 'z'
  const isRedo = (
    (event.metaKey || event.ctrlKey) &&
    ((event.shiftKey && event.key.toLowerCase() === 'z') || event.key.toLowerCase() === 'y')
  )
  if (isUndo && canUndoCanvasEdit.value) {
    event.preventDefault()
    void undoCanvasEdit()
  } else if (isRedo && canRedoCanvasEdit.value) {
    event.preventDefault()
    void redoCanvasEdit()
  }
}

async function copyText(text, successMessage) {
  if (!text) {
    return
  }

  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
    } else {
      const textarea = document.createElement('textarea')
      textarea.value = text
      textarea.setAttribute('readonly', '')
      textarea.style.position = 'absolute'
      textarea.style.left = '-9999px'
      document.body.appendChild(textarea)
      textarea.select()
      document.execCommand('copy')
      document.body.removeChild(textarea)
    }
    status.value = successMessage
  } catch (error) {
    errorMessage.value = '复制失败，请手动复制下方路径。'
  }
}

async function checkBackendStatus() {
  try {
    const response = await fetch(toApiUrl('/api/status'))
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const payload = await response.json()
    backendOnline.value = payload.status === 'running'
    status.value = backendOnline.value ? '后端在线，可以开始上传。' : '后端未就绪。'
  } catch (error) {
    backendOnline.value = false
    status.value = '无法连接后端，请先启动 FastAPI 服务。'
  }
}

function pickRecommendedFont(fonts, targetLang) {
  const preferredNames = targetLang === 'JPN'
    ? ['msgothic.ttc', 'NotoSansMonoCJK-VF.ttf.ttc']
    : ['msyh.ttc', 'Arial-Unicode-Regular.ttf', 'NotoSansMonoCJK-VF.ttf.ttc']

  return fonts.find((font) => preferredNames.includes(font.name)) || fonts[0] || null
}

function normalizeFontLookupValue(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/\.(ttf|ttc|otf)$/i, '')
    .replace(/\s+/g, '')
}

function pickMappedStyleFont(fonts, preferredNames) {
  const normalizedPreferred = preferredNames.map((name) => normalizeFontLookupValue(name))
  return (
    fonts.find((font) => {
      const candidates = [
        font.name,
        font.label,
        font.id
      ].map((item) => normalizeFontLookupValue(item))
      return normalizedPreferred.some((preferred) => candidates.some((candidate) => candidate.includes(preferred)))
    })
    || null
  )
}

function applyDefaultStyleFontMappings(fonts) {
  let changed = false
  for (const [styleKey, preferredNames] of Object.entries(defaultStyleFontNameMap)) {
    if (styleKey === 'gothic') {
      continue
    }
    const configKey = `style_font_${styleKey}_key`
    if (config.value[configKey]) {
      continue
    }

    const matchedFont = pickMappedStyleFont(fonts, preferredNames)
    if (!matchedFont) {
      continue
    }

    config.value[configKey] = matchedFont.id
    changed = true
  }
  return changed
}

async function loadFonts() {
  try {
    const response = await fetch(toApiUrl('/api/fonts'))
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const payload = await response.json()
    availableFonts.value = payload.fonts || []

    if (!config.value.font_key) {
      const recommended = pickRecommendedFont(availableFonts.value, config.value.target_lang)
      if (recommended) {
        config.value.font_key = recommended.id
      }
    }

    if (applyDefaultStyleFontMappings(availableFonts.value)) {
      saveStoredConfig(config.value)
      status.value = '已按你预设的字体样式映射自动带出默认字体。'
    }
  } catch (error) {
    availableFonts.value = []
  }
}

function onFileChange(event) {
  const [file] = event.target.files || []
  selectedFile.value = file || null
  errorMessage.value = ''
}

async function submitFile() {
  if (!selectedFile.value) {
    errorMessage.value = '请先选择一个 zip/cbz 或单张图片文件。'
    return
  }

  uploading.value = true
  errorMessage.value = ''
  originalImages.value = []
  translatedImages.value = []
  sessionId.value = ''
  currentProject.value = null
  projectTitleDraft.value = ''
  projectNoteDraft.value = ''
  downloadUrl.value = ''
  downloadPath.value = ''
  translatedDirPath.value = ''
  maskDebugDirPath.value = ''
  workflowStage.value = 'idle'
  progress.value = { current: 0, total: 0 }
  resetTranslationReview()
  resetStyleInspector()
  closeSocket()

  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)
    formData.append('review_mode', config.value.default_review_mode)

    const response = await fetch(toApiUrl('/api/upload'), {
      method: 'POST',
      body: formData
    })

    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '上传失败')
    }

    applySessionPayload(payload, { resetInspectors: true })
    await loadProjectHistory({ silent: true })
    status.value = config.value.pause_after_detection
      ? `上传完成，共解析 ${payload.total_images} 张图片。现在可以先识别文本框，再逐框确认。`
      : `上传完成，共解析 ${payload.total_images} 张图片。现在可以开始翻译。`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '上传失败'
    status.value = '上传未完成。'
  } finally {
    uploading.value = false
  }
}

function startTranslation(action = 'translate') {
  if (!sessionId.value || translating.value) {
    return
  }

  activeAction.value = action
  const rerenderTargetStoredName = action === 'rerender'
    ? (selectedEditPage.value?.stored_name || '')
    : ''
  manualDrawDraft.value = null
  renderNonce.value = Date.now()
  if (action === 'translate' || action === 'detect') {
    translatedImages.value = []
    downloadUrl.value = ''
    downloadPath.value = ''
    translatedDirPath.value = ''
    maskDebugDirPath.value = ''
  }
  errorMessage.value = ''
  translating.value = true
  progress.value = { current: 0, total: 0 }
  status.value = action === 'rerender'
    ? (rerenderTargetStoredName ? '正在启动当前页重嵌字任务...' : '正在启动重嵌字任务...')
    : action === 'detect'
      ? '正在启动文本框识别任务...'
      : action === 'resume-translate'
        ? '正在继续翻译并嵌字...'
        : '正在启动翻译任务...'
  closeSocket()

  socket = new WebSocket(toWebSocketUrl(`/ws/translate/${sessionId.value}`))

  socket.onopen = () => {
    socket.send(JSON.stringify({
      action,
      config: buildRuntimeConfig(),
      target_stored_name: rerenderTargetStoredName || undefined
    }))
  }

  socket.onmessage = async (event) => {
    const payload = JSON.parse(event.data)

    if (payload.event === 'start') {
      progress.value = { current: 0, total: payload.total_pages }
      status.value = activeAction.value === 'rerender'
        ? (rerenderTargetStoredName
          ? `当前页重嵌字已开始。`
          : `重嵌字已开始，共 ${payload.total_pages} 张图片。`)
        : activeAction.value === 'detect'
          ? `文本框识别已开始，共 ${payload.total_pages} 张图片。`
          : activeAction.value === 'resume-translate'
            ? `继续翻译已开始，共 ${payload.total_pages} 张图片。`
        : `翻译已开始，共 ${payload.total_pages} 张图片。`
      return
    }

    if (payload.event === 'progress') {
      progress.value = {
        current: payload.current,
        total: payload.total
      }
      const nextImageUrl = withCacheBust(toApiUrl(payload.image_url))
      translatedImages.value = upsertTranslatedImage(
        translatedImages.value,
        payload,
        nextImageUrl,
        sessionId.value,
      )
      if (payload.stored_name) {
        reviewInspectionPages.value = updatePagePreviewUrl(
          reviewInspectionPages.value,
          payload.stored_name,
          payload.image_url,
        )
        styleInspectionPages.value = updatePagePreviewUrl(
          styleInspectionPages.value,
          payload.stored_name,
          payload.image_url,
        )
      }
      status.value = activeAction.value === 'rerender'
        ? (rerenderTargetStoredName
          ? `当前页重嵌字进行中…`
          : `重嵌字进行中：${payload.current} / ${payload.total}`)
        : activeAction.value === 'detect'
          ? `正在识别并准备校对：${payload.current} / ${payload.total}`
          : activeAction.value === 'resume-translate'
            ? `继续翻译进行中：${payload.current} / ${payload.total}`
        : `翻译进行中：${payload.current} / ${payload.total}`
      return
    }

    if (payload.event === 'status') {
      status.value = payload.message || '正在进行复杂页增强修复...'
      return
    }

    if (payload.event === 'completed') {
      translating.value = false
      applySessionPayload(payload)
      const completedAction = activeAction.value
      if (completedAction === 'rerender') {
        if (rerenderTargetStoredName) {
          clearCanvasPreviewDirty(rerenderTargetStoredName)
        } else {
          clearCanvasPreviewDirty()
        }
      } else if (completedAction === 'resume-translate' || completedAction === 'translate') {
        clearCanvasPreviewDirty()
      }
      status.value = completedAction === 'detect'
        ? '文本框识别完成。现在可以先逐框确认、补框或保留原文，确认后再继续翻译。'
        : completedAction === 'resume-translate'
          ? `翻译完成，共输出 ${translatedImages.value.length} 张图片。`
          : completedAction === 'rerender'
            ? (rerenderTargetStoredName
              ? '当前页重嵌字完成。'
              : `重嵌字完成，共输出 ${progress.value.total} 张图片。`)
            : `翻译完成，共输出 ${translatedImages.value.length} 张图片。`
      await loadEditInspection({ silent: completedAction !== 'detect' })
      if (completedAction === 'detect' && !mergedInspectionPages.value.length) {
        await ensureEditInspectionReadyAfterDetect()
      }
      if (config.value.font_style_mode === 'auto-map') {
        await loadStyleInspection({ silent: true })
      }
      void loadProjectHistory({ silent: true })
      closeSocket()
      return
    }

    if (payload.event === 'error') {
      translating.value = false
      errorMessage.value = payload.message || '翻译失败'
      status.value = activeAction.value === 'rerender'
        ? '重嵌字失败。'
        : activeAction.value === 'detect'
          ? '文本框识别失败。'
          : activeAction.value === 'resume-translate'
            ? '继续翻译失败。'
            : '翻译失败。'
      closeSocket()
    }
  }

  socket.onerror = () => {
    errorMessage.value = '翻译连接中断，请查看后端控制台日志。'
    status.value = activeAction.value === 'rerender'
      ? '重嵌字连接中断。'
      : activeAction.value === 'detect'
        ? '识别连接中断。'
        : activeAction.value === 'resume-translate'
          ? '继续翻译连接中断。'
          : '翻译连接中断。'
    translating.value = false
    closeSocket()
  }

  socket.onclose = () => {
    if (translating.value) {
      errorMessage.value = '翻译任务意外断开，请查看后端日志。'
      status.value = activeAction.value === 'rerender'
        ? '重嵌字未完成。'
        : activeAction.value === 'detect'
          ? '识别未完成。'
          : activeAction.value === 'resume-translate'
            ? '继续翻译未完成。'
            : '翻译未完成。'
      translating.value = false
    }
  }
}

onMounted(() => {
  checkBackendStatus()
  loadFonts()
  loadProjectHistory()
  window.addEventListener('resize', refreshTranslatedPreviewScale)
  window.addEventListener('pointermove', updateCanvasRegionTransform)
  window.addEventListener('pointerup', finishCanvasRegionTransform)
  window.addEventListener('pointercancel', cancelCanvasRegionTransform)
  window.addEventListener('keydown', handleGlobalCanvasKeydown)
})

onBeforeUnmount(() => {
  closeSocket()
  window.removeEventListener('resize', refreshTranslatedPreviewScale)
  window.removeEventListener('pointermove', updateCanvasRegionTransform)
  window.removeEventListener('pointerup', finishCanvasRegionTransform)
  window.removeEventListener('pointercancel', cancelCanvasRegionTransform)
  window.removeEventListener('keydown', handleGlobalCanvasKeydown)
})

async function warmPreviewFonts() {
  if (typeof document === 'undefined' || !document.fonts) {
    return
  }

  const fontIds = new Set()
  for (const font of availableFonts.value) {
    if (font?.id) {
      fontIds.add(String(font.id))
    }
  }
  if (config.value.font_key) {
    fontIds.add(String(config.value.font_key))
  }
  for (const styleBucket of styleBucketOptions.map((option) => option.value)) {
    const fontId = getConfiguredStyleFontId(styleBucket)
    if (fontId) {
      fontIds.add(fontId)
    }
  }
  for (const override of Object.values(translationRegionLayoutOverrides.value || {})) {
    const fontId = String(override?.font_key || '').trim()
    if (fontId) {
      fontIds.add(fontId)
    }
  }

  const nextState = { ...previewFontLoadState.value }
  await Promise.all(
    Array.from(fontIds).map(async (fontId) => {
      try {
        await document.fonts.load(`16px "${getPreviewFontAlias(fontId)}"`, '測試漢字ABC')
        const loaded = document.fonts.check(`16px "${getPreviewFontAlias(fontId)}"`, '測試漢字ABC')
        nextState[fontId] = {
          status: loaded ? 'loaded' : 'unsupported',
          error: loaded ? '' : 'document.fonts.check returned false'
        }
      } catch (error) {
        nextState[fontId] = {
          status: 'unsupported',
          error: error instanceof Error ? error.message : String(error || '')
        }
      }
    })
  )
  previewFontLoadState.value = nextState
  renderNonce.value = Date.now()
}

watch(
  config,
  (nextValue) => {
    saveStoredConfig(nextValue)
  },
  { deep: true }
)

watch(
  [
    previewFontFaceCss,
    () => availableFonts.value.length,
    () => config.value.font_key,
    () => config.value.font_style_mode,
    () => config.value.style_font_gothic_key,
    () => config.value.style_font_mincho_key,
    () => config.value.style_font_rounded_key,
    () => config.value.style_font_cartoon_key,
    () => config.value.style_font_handwritten_key,
    () => config.value.style_font_sfx_key,
    () => JSON.stringify(translationRegionLayoutOverrides.value)
  ],
  () => {
    void warmPreviewFonts()
  },
  { immediate: true }
)

watch(
  () => config.value.translator,
  (nextTranslator) => {
    if (!isValidTranslatorModel(nextTranslator, config.value.translator_model)) {
      config.value.translator_model = getDefaultTranslatorModel(nextTranslator)
    }
    if (nextTranslator !== 'doubao-ark') {
      config.value.translator_model_custom = ''
    }
  }
)

watch(
  () => config.value.image_cleanup_mode,
  (nextMode) => {
    if (!isValidImageCleanupModel(nextMode, config.value.image_cleanup_model)) {
      config.value.image_cleanup_model = getDefaultImageCleanupModel(nextMode)
    }
  }
)

watch(
  () => config.value.font_style_mode,
  () => {
    if (sessionId.value && (downloadUrl.value || translatedImages.value.length)) {
      void loadEditInspection({ silent: true })
      return
    }
    syncEditSelection()
  }
)

watch(selectedEditRegionKey, () => {
  if (selectedEditRegionKey.value) {
    void scrollSelectedRegionCardIntoView()
  }
  void refreshSelectedRegionPreviewDebug()
})

watch(
  () => [
    selectedEditPage.value?.stored_name || '',
    selectedEditPage.value?.translated_image_url || '',
    selectedEditPage.value?.base_image_url || '',
    isCanvasReviewMode.value
  ],
  () => {
    void syncTranslatedPreviewScale()
    void refreshSelectedRegionPreviewDebug()
  }
)

watch(
  () => [
    selectedEditRegion.value?.id || '',
    JSON.stringify(translationRegionLayoutOverrides.value),
    JSON.stringify(translationRegionSkipOverrides.value),
    JSON.stringify(translationRegionDisabledOverrides.value),
    JSON.stringify(styleRegionOverrides.value),
    previewFontFaceCss.value,
    renderNonce.value,
  ],
  () => {
    void refreshSelectedRegionPreviewDebug()
  },
  { deep: false }
)
</script>

<template>
  <component :is="'style'">{{ previewFontFaceCss }}</component>
  <main class="page-shell">
    <section class="hero-card">
      <p class="eyebrow">Manga Auto-Translator</p>
      <h1>本地漫画翻译工作台</h1>
      <p class="hero-copy">
        先上传压缩包或单张图片，再点击开始翻译。翻译过程中会逐张推送结果，并在完成后提供压缩包下载。
      </p>

      <div class="status-row">
        <span :class="['status-dot', backendOnline ? 'online' : 'offline']"></span>
        <span>{{ status }}</span>
      </div>

      <div class="upload-panel">
        <label class="file-picker">
          <span>{{ selectedFile ? selectedFile.name : '选择 zip / cbz / 图片文件' }}</span>
          <input
            :accept="acceptValue"
            type="file"
            @change="onFileChange"
          />
        </label>

        <button
          class="primary-button"
          :disabled="!canUpload"
          @click="submitFile"
        >
          {{ uploading ? '正在上传...' : '上传文件' }}
        </button>
      </div>

      <div class="action-row action-row-primary">
        <button
          class="primary-button"
          :disabled="!canTranslate"
          @click="startTranslation(primaryTranslateAction)"
        >
          {{ primaryTranslateLabel }}
        </button>

        <button
          v-if="downloadUrl"
          class="secondary-button"
          :disabled="!canRerender"
          type="button"
          @click="startTranslation('rerender')"
        >
          仅重新嵌字
        </button>

        <a
          v-if="downloadUrl"
          class="secondary-button"
          :href="downloadUrl"
        >
          下载翻译结果
        </a>

        <button
          class="secondary-button settings-toggle-button"
          type="button"
          @click="showAdvancedSettings = !showAdvancedSettings"
        >
          {{ showAdvancedSettings ? '收起设置' : '展开设置' }}
        </button>
      </div>

      <div class="settings-summary">
        <span class="settings-summary-label">当前配置</span>
        <span class="settings-summary-text">{{ compactConfigSummary }}</span>
      </div>

      <div v-if="showAdvancedSettings" class="config-grid">
        <label class="field">
          <span>翻译器</span>
          <select v-model="config.translator">
            <option value="sugoi">sugoi</option>
            <option value="gemini">gemini (推荐)</option>
            <option value="doubao-ark">Doubao (Ark / 火山方舟)</option>
            <option value="chatgpt">chatgpt</option>
            <option value="youdao">有道 (youdao)</option>
            <option value="baidu">百度 (baidu)</option>
            <option value="offline">offline</option>
            <option value="none">none</option>
          </select>
        </label>

        <label v-if="config.translator === 'doubao-ark'" class="field">
          <span>Doubao 模型</span>
          <select v-model="config.translator_model">
            <option
              v-for="model in doubaoModelOptions"
              :key="model.value"
              :value="model.value"
            >
              {{ model.label }}
            </option>
          </select>
          <input
            v-model="config.translator_model_custom"
            type="text"
            placeholder="可选：直接输入任意 Ark 模型 ID，留空则使用上面的常用模型"
            style="width: 100%; padding: 8px; border-radius: 4px; border: 1px solid var(--border); margin-top: 8px;"
          />
          <small class="field-hint">
            已内置常用官方模型；如果控制台里有更新或白名单模型，也可以直接填模型 ID，输入框优先级更高。漫画 OCR 翻译优先推荐 `doubao-seed-translation-250915`，其余 2.0 系列更适合作为实验选项。
          </small>
        </label>

        <label class="field">
          <span>目标语言</span>
          <select v-model="config.target_lang">
            <option value="CHS">简体中文</option>
            <option value="CHT">繁体中文</option>
            <option value="ENG">英语</option>
            <option value="JPN">日语</option>
            <option value="KOR">韩语</option>
          </select>
        </label>

        <label v-if="config.translator === 'gemini' || config.translator === 'chatgpt' || config.translator === 'doubao-ark'" class="field" style="grid-column: span 2;">
          <span>{{ config.translator === 'doubao-ark' ? 'Ark API Key (可选，留空则使用默认配置)' : 'API Key (可选，留空则使用默认配置)' }}</span>
          <input
            v-model="config.api_key"
            type="password"
            :placeholder="config.translator === 'doubao-ark' ? '输入火山方舟 Ark API Key' : '输入你的 API Key'"
            style="width: 100%; padding: 8px; border-radius: 4px; border: 1px solid var(--border);"
          />
          <small class="field-hint field-hint-row">
            <span>
              {{
                config.translator === 'doubao-ark'
                  ? '会保存在当前浏览器本地，并按火山方舟官方兼容接口调用；翻译增强模型会自动切到 Responses API。'
                  : '会保存在当前浏览器本地，下次打开页面会自动带出。'
              }}
            </span>
            <button
              v-if="config.api_key"
              class="inline-button"
              type="button"
              @click="clearStoredApiKey"
            >
              清除已保存 Key
            </button>
          </small>
        </label>

        <label class="field" style="grid-column: span 2;">
          <span>翻译字体</span>
          <select v-model="config.font_key">
            <option value="">默认字体</option>
            <option
              v-for="font in availableFonts"
              :key="font.id"
              :value="font.id"
            >
              {{ font.label }}
            </option>
          </select>
          <small class="field-hint">
            想换自定义字体时，把 `.ttf` / `.ttc` / `.otf` 放到项目根目录 `fonts` 文件夹后重启即可。
          </small>
        </label>

        <label class="field">
          <span>字体风格模式</span>
          <select v-model="config.font_style_mode">
            <option value="single">单字体</option>
            <option value="auto-map">自动风格映射</option>
          </select>
          <small class="field-hint">
            当前版本按字体样式来做启发式识别，不再按对白或说明框场景分类。建议改完后直接点“仅重新嵌字”快速比对效果。
          </small>
        </label>

        <template v-if="config.font_style_mode === 'auto-map'">
          <label class="field">
            <span>黑体映射</span>
            <div class="field-static-value">
              {{ getFontById(config.font_key)?.label || '跟随主字体' }}
            </div>
            <small class="field-hint">
              黑体会直接跟随上面的“翻译字体”，修改主字体后会同步影响标准对白。
            </small>
          </label>

          <label class="field">
            <span>宋体 / 明体映射</span>
            <select v-model="config.style_font_mincho_key">
              <option value="">跟随翻译字体</option>
              <option
                v-for="font in availableFonts"
                :key="`mincho-${font.id}`"
                :value="font.id"
              >
                {{ font.label }}
              </option>
            </select>
          </label>

          <label class="field">
            <span>圆体映射</span>
            <select v-model="config.style_font_rounded_key">
              <option value="">跟随翻译字体</option>
              <option
                v-for="font in availableFonts"
                :key="`rounded-${font.id}`"
                :value="font.id"
              >
                {{ font.label }}
              </option>
            </select>
          </label>

          <label class="field">
            <span>卡通映射</span>
            <select v-model="config.style_font_cartoon_key">
              <option value="">跟随翻译字体</option>
              <option
                v-for="font in availableFonts"
                :key="`cartoon-${font.id}`"
                :value="font.id"
              >
                {{ font.label }}
              </option>
            </select>
            <small class="field-hint">
              可以把更粗、更圆、更夸张的中文字体放在这里，适合显眼的强调字。
            </small>
          </label>

          <label class="field">
            <span>手写映射</span>
            <select v-model="config.style_font_handwritten_key">
              <option value="">跟随翻译字体</option>
              <option
                v-for="font in availableFonts"
                :key="`handwritten-${font.id}`"
                :value="font.id"
              >
                {{ font.label }}
              </option>
            </select>
            <small class="field-hint">
              适合映射到更轻、更松、更像手写笔触的字体。
            </small>
          </label>

          <label class="field">
            <span>拟声映射</span>
            <select v-model="config.style_font_sfx_key">
              <option value="">跟随翻译字体</option>
              <option
                v-for="font in availableFonts"
                :key="`sfx-${font.id}`"
                :value="font.id"
              >
                {{ font.label }}
              </option>
            </select>
            <small class="field-hint">
              适合放最有表现力、最夸张、最重的字体，用来处理拟声和大字效果。
            </small>
          </label>
        </template>

        <label class="field">
          <span>排版对齐</span>
          <select v-model="config.render_alignment">
            <option value="left">靠左 / 靠上</option>
            <option value="center">居中</option>
            <option value="right">靠右 / 靠下</option>
            <option value="auto">自动</option>
          </select>
          <small class="field-hint">
            现在默认会优先靠左 / 靠上。修改后可直接点“仅重新嵌字”快速比较版式。
          </small>
        </label>

        <label class="field">
          <span>字间距倍率</span>
          <input
            v-model.number="config.render_letter_spacing"
            type="number"
            min="0.85"
            max="1.35"
            step="0.02"
            style="width: 100%; padding: 8px; border-radius: 4px; border: 1px solid var(--border);"
          />
          <small class="field-hint">
            默认 `1.08`，会比之前稍微舒展一点。改完后也建议用“仅重新嵌字”比较效果。
          </small>
        </label>

        <label class="field">
          <span>重嵌字输出</span>
          <select v-model="config.rerender_output_format">
            <option value="png">PNG（更清晰，推荐）</option>
            <option value="source">跟随原图格式</option>
          </select>
          <small class="field-hint">
            只影响“仅重新嵌字 / 审校后重嵌字”，不会改动首次翻译主流程。想优先保清晰度时，推荐保持 `PNG`。
          </small>
        </label>

        <label class="field">
          <span>默认审校模式</span>
          <select v-model="config.default_review_mode">
            <option value="classic">经典审校</option>
            <option value="canvas_beta">画布审校（Beta）</option>
          </select>
          <small class="field-hint">
            只影响新上传项目的默认审校工作台；历史项目会继续沿用它们创建时的模式。
          </small>
        </label>

        <label class="toggle-field">
          <input v-model="config.pause_after_detection" type="checkbox" />
          <span>识别后先进入逐框校对</span>
        </label>

        <label class="field">
          <span>擦字强度</span>
          <select v-model="config.mask_cleanup_strength">
            <option value="standard">标准</option>
            <option value="clean">更干净</option>
            <option value="aggressive">激进</option>
          </select>
          <small class="field-hint">
            只影响擦字和修复阶段；“更干净 / 激进”会更积极地抹掉文字边缘，但也更可能吃掉靠得很近的细节。
          </small>
        </label>

        <label class="toggle-field">
          <input v-model="config.export_mask_debug" type="checkbox" />
          <span>导出擦字调试图</span>
        </label>

        <label class="field">
          <span>复杂嵌字增强</span>
          <select v-model="config.advanced_text_repair">
            <option value="auto">自动识别后增强</option>
            <option value="off">关闭，始终走稳定流程</option>
            <option value="force">强制开启实验增强</option>
          </select>
          <small class="field-hint">
            只会对疑似框外嵌字或彩色复杂页尝试第二次修复；普通页面默认仍走当前稳定流程。
          </small>
        </label>

        <label class="field">
          <span>AI 去字模型</span>
          <select v-model="config.image_cleanup_mode">
            <option value="off">关闭</option>
            <option value="gemini-image">Gemini 图像编辑</option>
            <option value="seedream-image">Seedream 5.0 Lite</option>
          </select>
          <small class="field-hint">
            只会命中复杂嵌字页；普通页仍走稳定流程。单页等待过久时会自动超时并回退，不会卡住整本。
          </small>
        </label>

        <label v-if="config.image_cleanup_mode === 'gemini-image'" class="field">
          <span>AI 去字模型版本</span>
          <select v-model="config.image_cleanup_model">
            <option value="gemini-2.5-flash-image">gemini-2.5-flash-image (Nano Banana / 官方稳定)</option>
            <option value="gemini-3.1-flash-image-preview">gemini-3.1-flash-image-preview (更强编辑 / 预览版)</option>
            <option value="gemini-3-pro-image-preview">gemini-3-pro-image-preview (Nano Banana Pro / 最慢)</option>
          </select>
          <small class="field-hint">
            Google 官方公开可用的是这些模型名；如果你说的 “Nano Banana 2” 是更强编辑版，这里更接近的是 `gemini-3.1-flash-image-preview`。
          </small>
        </label>

        <label v-if="config.image_cleanup_mode === 'seedream-image'" class="field">
          <span>AI 去字模型版本</span>
          <select v-model="config.image_cleanup_model">
            <option value="doubao-seedream-5-0-lite-260128">doubao-seedream-5-0-lite-260128 (Seedream 5.0 Lite)</option>
          </select>
          <small class="field-hint">
            这条链路会把原图裁剪和红色引导图一起送到火山方舟图片生成接口，prompt 固定为“去除覆盖在图片上的文字”。
          </small>
        </label>

        <label v-if="config.image_cleanup_mode !== 'off'" class="field" style="grid-column: span 2;">
          <span>AI 去字 API Key</span>
          <input
            v-model="config.image_cleanup_api_key"
            type="password"
            :placeholder="config.image_cleanup_mode === 'seedream-image' ? '输入火山方舟 Ark API Key' : '输入图像编辑 API Key'"
            style="width: 100%; padding: 8px; border-radius: 4px; border: 1px solid var(--border);"
          />
          <small class="field-hint field-hint-row">
            <span>
              {{
                config.image_cleanup_mode === 'seedream-image'
                  ? '会保存在当前浏览器本地；Seedream 不会复用上面的 Gemini 翻译 Key。'
                  : '会保存在当前浏览器本地；如果留空，会优先复用当前页面里的 Gemini 翻译 Key。'
              }}
            </span>
            <button
              v-if="config.image_cleanup_api_key"
              class="inline-button"
              type="button"
              @click="clearStoredImageApiKey"
            >
              清除已保存 Key
            </button>
          </small>
        </label>

        <label class="toggle-field">
          <input v-model="config.use_gpu" type="checkbox" />
          <span>使用 GPU</span>
        </label>
      </div>

      <div v-if="downloadPath || translatedDirPath || maskDebugDirPath" class="artifact-panel">
        <div class="artifact-row" v-if="downloadPath">
          <div class="artifact-copy">
            <span class="artifact-label">压缩包路径</span>
            <code class="artifact-path">{{ downloadPath }}</code>
          </div>
          <button
            class="secondary-button artifact-button"
            type="button"
            @click="copyText(downloadPath, '已复制压缩包路径。')"
          >
            复制压缩包路径
          </button>
        </div>

        <div class="artifact-row" v-if="translatedDirPath">
          <div class="artifact-copy">
            <span class="artifact-label">输出目录</span>
            <code class="artifact-path">{{ translatedDirPath }}</code>
          </div>
          <button
            class="secondary-button artifact-button"
            type="button"
            @click="copyText(translatedDirPath, '已复制输出目录路径。')"
          >
            复制输出目录
          </button>
        </div>

        <div class="artifact-row" v-if="maskDebugDirPath">
          <div class="artifact-copy">
            <span class="artifact-label">擦字调试目录</span>
            <code class="artifact-path">{{ maskDebugDirPath }}</code>
          </div>
          <button
            class="secondary-button artifact-button"
            type="button"
            @click="copyText(maskDebugDirPath, '已复制擦字调试目录路径。')"
          >
            复制调试目录
          </button>
        </div>
      </div>

      <div v-if="progress.total" class="progress-panel">
        <div class="progress-meta">
          <span>翻译进度</span>
          <strong>{{ progress.current }} / {{ progress.total }}</strong>
        </div>
        <div class="progress-track">
          <div class="progress-fill" :style="{ width: `${progressPercent}%` }"></div>
        </div>
      </div>

      <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
      <p v-if="sessionId" class="session-text">Session: {{ sessionId }}</p>
    </section>

    <section v-if="currentProject" class="gallery-card project-meta-card">
      <div class="section-head">
        <div>
          <p class="eyebrow">Current Project</p>
          <h2>当前项目</h2>
        </div>
        <div class="project-meta-badges">
          <span class="project-badge">{{ workflowStageLabelMap[currentProject.workflow_stage] || currentProject.workflow_stage }}</span>
          <span class="project-badge">{{ currentProject.page_count }} 页</span>
        </div>
      </div>

      <div class="project-meta-grid">
        <label class="field">
          <span>项目名称</span>
          <input
            v-model="projectTitleDraft"
            type="text"
            placeholder="给这个项目起个名字"
          />
        </label>

        <label class="field project-note-field">
          <span>项目备注</span>
          <textarea
            v-model="projectNoteDraft"
            rows="3"
            placeholder="可选：记录这本漫画的说明、当前进度或注意事项"
          ></textarea>
        </label>
      </div>

      <div class="project-meta-actions">
        <button
          class="secondary-button"
          type="button"
          :disabled="savingProjectMeta || !projectMetaDirty"
          @click="saveProjectMetadata"
        >
          {{ savingProjectMeta ? '正在保存...' : '保存项目名称 / 备注' }}
        </button>
        <span class="project-meta-hint">
          历史翻译列表会显示这里的名称和备注，后续恢复项目时也会一起带回。
        </span>
      </div>
    </section>

    <section class="gallery-card project-history-card">
      <div class="section-head">
        <div>
          <p class="eyebrow">History</p>
          <h2>历史翻译</h2>
        </div>
        <div class="action-row">
          <button
            class="secondary-button"
            type="button"
            :disabled="historyLoading"
            @click="loadProjectHistory"
          >
            {{ historyLoading ? '正在读取...' : '刷新历史列表' }}
          </button>
        </div>
      </div>

      <div v-if="!projectHistory.length && historyLoading" class="empty-state">
        正在读取历史项目…
      </div>

      <div v-else-if="!projectHistory.length" class="empty-state">
        还没有历史翻译项目。上传并完成一次识别或翻译后，这里会自动保存下来。
      </div>

      <div v-else class="project-history-list">
        <article
          v-for="project in projectHistory"
          :key="project.project_id"
          class="project-history-item"
        >
          <div class="project-history-cover">
            <img
              v-if="project.cover_image"
              :src="withCacheBust(project.cover_image)"
              :alt="`${project.title} 封面`"
            />
            <div v-else class="project-history-cover-fallback">
              {{ project.page_count }} 页
            </div>
          </div>

          <div class="project-history-body">
            <div class="project-history-head">
              <strong>{{ project.title }}</strong>
              <div class="project-history-meta">
                <span>{{ workflowStageLabelMap[project.workflow_stage] || project.workflow_stage }}</span>
                <span>{{ project.page_count }} 页</span>
              </div>
            </div>

            <p v-if="project.note" class="project-history-note">{{ project.note }}</p>
            <p class="project-history-time">最近更新：{{ project.updated_at || project.created_at }}</p>

            <div class="action-row">
              <button
                class="secondary-button"
                type="button"
                :disabled="Boolean(restoringProjectId) && restoringProjectId !== project.project_id"
                @click="restoreProject(project.project_id)"
              >
                {{ restoringProjectId === project.project_id ? '正在恢复...' : '继续编辑' }}
              </button>

              <button
                class="secondary-button"
                type="button"
                :disabled="snapshotLoadingProjectId === project.project_id"
                @click="toggleProjectSnapshots(project.project_id)"
              >
                {{
                  expandedProjectId === project.project_id
                    ? '收起快照'
                    : `查看快照（${project.snapshot_count || 0}）`
                }}
              </button>

              <a
                v-if="sessionId === project.project_id && downloadUrl"
                class="secondary-button"
                :href="downloadUrl"
              >
                下载当前结果
              </a>

              <button
                class="secondary-button danger-button"
                type="button"
                @click="deleteProject(project)"
              >
                删除项目
              </button>
            </div>

            <div
              v-if="expandedProjectId === project.project_id"
              class="snapshot-panel"
            >
              <div
                v-if="snapshotLoadingProjectId === project.project_id && !(projectSnapshots[project.project_id] || []).length"
                class="snapshot-empty"
              >
                正在读取快照…
              </div>

              <div
                v-else-if="!(projectSnapshots[project.project_id] || []).length"
                class="snapshot-empty"
              >
                这个项目还没有可恢复的快照。
              </div>

              <div v-else class="snapshot-list">
                <article
                  v-for="snapshot in projectSnapshots[project.project_id]"
                  :key="snapshot.snapshot_id"
                  class="snapshot-item"
                >
                  <div class="snapshot-copy">
                    <strong>{{ snapshot.summary || '未命名快照' }}</strong>
                    <div class="snapshot-meta">
                      <span>{{ snapshot.created_at }}</span>
                      <span>{{ workflowStageLabelMap[snapshot.workflow_stage] || snapshot.workflow_stage }}</span>
                      <span>{{ snapshot.kind }}</span>
                      <span v-if="snapshot.pinned">已固定</span>
                    </div>
                  </div>

                  <div class="snapshot-actions">
                    <button
                      class="secondary-button snapshot-button"
                      type="button"
                      @click="toggleSnapshotPin(project.project_id, snapshot)"
                    >
                      {{ snapshot.pinned ? '取消固定' : '固定快照' }}
                    </button>

                    <button
                      class="secondary-button snapshot-button"
                      type="button"
                      :disabled="Boolean(restoringSnapshotId) && restoringSnapshotId !== snapshot.snapshot_id"
                      @click="restoreSnapshot(project.project_id, snapshot.snapshot_id)"
                    >
                      {{ restoringSnapshotId === snapshot.snapshot_id ? '正在恢复...' : '恢复为新项目' }}
                    </button>
                  </div>
                </article>
              </div>
            </div>
          </div>
        </article>
      </div>
    </section>

    <section v-if="canInspectEditor" class="gallery-card">
      <div class="section-head">
        <div>
          <p class="eyebrow">Region Editor</p>
          <h2>逐框校对</h2>
        </div>
        <div class="action-row">
          <button
            v-if="isCanvasReviewMode"
            class="secondary-button"
            type="button"
            :disabled="!canUndoCanvasEdit"
            @click="undoCanvasEdit"
          >
            撤销
          </button>

          <button
            v-if="isCanvasReviewMode"
            class="secondary-button"
            type="button"
            :disabled="!canRedoCanvasEdit"
            @click="redoCanvasEdit"
          >
            重做
          </button>

          <button
            class="secondary-button"
            type="button"
            :disabled="editInspectionLoading"
            @click="loadEditInspection"
          >
            {{ editInspectionLoading ? '正在读取...' : '刷新逐框列表' }}
          </button>

          <button
            class="secondary-button"
            type="button"
            :disabled="!canCreateManualRegion"
            @click="manualDrawMode = !manualDrawMode; adjustingRegionId = ''; manualDrawDraft = null"
          >
            {{ manualDrawMode ? '结束补漏框绘制' : '手动补漏框' }}
          </button>

          <button
            class="secondary-button"
            type="button"
            :disabled="!selectedEditPage || creatingManualRegion"
            @click="toggleMergeMode"
          >
            {{ mergeMode ? '取消合并选择' : '合并文本框' }}
          </button>

          <button
            v-if="mergeSelectionCount >= 2"
            class="secondary-button"
            type="button"
            :disabled="creatingManualRegion"
            @click="mergeSelectedRegions"
          >
            合并选中框（{{ mergeSelectionCount }}）
          </button>

          <button
            v-if="disabledRegionCountForSelectedPage > 0"
            class="secondary-button"
            type="button"
            @click="restoreDisabledRegionsForPage(selectedEditPage)"
          >
            恢复本页禁用框（{{ disabledRegionCountForSelectedPage }}）
          </button>

          <button
            v-if="hasTranslationOverrides"
            class="secondary-button"
            type="button"
            @click="clearTranslationOverrides"
          >
            清空人工修改
          </button>

          <button
            v-if="config.font_style_mode === 'auto-map' && hasStyleOverrides"
            class="secondary-button"
            type="button"
            @click="clearStyleOverrides"
          >
            清空字体覆盖
          </button>

          <button
            v-if="canContinueSegmentedTranslation"
            class="secondary-button"
            type="button"
            @click="startTranslation('resume-translate')"
          >
            确认框后继续翻译
          </button>

          <button
            v-else-if="canRerender"
            class="secondary-button"
            type="button"
            @click="startTranslation('rerender')"
          >
            保存修改并重新嵌字
          </button>
        </div>
      </div>

      <div v-if="!selectedEditPage && editInspectionLoading" class="empty-state">
        正在读取当前会话的逐框校对数据…
      </div>

      <div v-else-if="!selectedEditPage" class="empty-state">
        <template v-if="workflowStage === 'detected'">
          文本框识别已完成，但逐框校对数据尚未载入。可以先点击“刷新逐框列表”再继续确认。
        </template>
        <template v-else>
          当前还没有可编辑的逐框数据。你可以先执行识别或翻译，再回到这里继续校对。
        </template>
      </div>

      <div v-else-if="selectedEditPage" class="style-inspector">
        <div class="style-toolbar">
          <label class="field style-page-field">
            <span>校对页面</span>
            <div class="style-page-nav-controls">
              <button
                class="inline-button style-page-jump-button"
                type="button"
                :disabled="!canSelectPreviousEditPage"
                title="上一页"
                @click="selectAdjacentEditPage(-1)"
              >
                ↑
              </button>
              <select v-model="selectedEditPageKey" class="style-page-select">
                <option
                  v-for="page in mergedInspectionPages"
                  :key="page.stored_name"
                  :value="page.stored_name"
                >
                  {{ page.name }}
                </option>
              </select>
              <button
                class="inline-button style-page-jump-button"
                type="button"
                :disabled="!canSelectNextEditPage"
                title="下一页"
                @click="selectAdjacentEditPage(1)"
              >
                ↓
              </button>
            </div>
            <small class="field-hint">{{ selectedEditPageSummary }}</small>
          </label>

          <div class="field style-summary">
            <span>使用说明</span>
            <small class="field-hint">
              这里把译文、字体、禁用框和框体调整放在同一个列表里处理。开启“识别后先进入逐框校对”时，可以先补框、合并框、禁用误识别框，再点“确认框后继续翻译”。
            </small>
          </div>

          <div class="style-toolbar-actions">
            <button
              class="inline-button"
              type="button"
              :disabled="!selectedEditPage || exportingOcrDebug"
              @click="exportCurrentPageOcrDebug"
            >
              {{ exportingOcrDebug ? '导出中…' : '导出 OCR 调试' }}
            </button>
            <button
              class="inline-button"
              type="button"
              :disabled="!selectedEditPage || exportingTranslationInputDebug"
              @click="exportCurrentPageTranslationInputDebug"
            >
              {{ exportingTranslationInputDebug ? '导出中…' : '导出翻译输入调试' }}
            </button>
            <button
              class="inline-button"
              type="button"
              :disabled="!sessionId || exportingTranslationRequestDebug"
              @click="exportCurrentProjectTranslationRequestDebug"
            >
              {{ exportingTranslationRequestDebug ? '导出中…' : '导出翻译请求调试' }}
            </button>
          </div>
        </div>

        <div v-if="isCanvasReviewMode && selectedEditRegion" class="style-runtime-debug">
          <span><strong>选中框：</strong>#{{ selectedEditRegion.index + 1 }}</span>
          <span><strong>期望字体：</strong>{{ selectedRegionPreviewDebug.requestedFont || getEffectiveRegionFontLabel(selectedEditRegion) }}</span>
          <span><strong>预览别名：</strong>{{ selectedRegionPreviewDebug.requestedAlias || '（无）' }}</span>
          <span><strong>实际命中：</strong>{{ selectedRegionPreviewDebug.computedFontFamily || '（未读取到）' }}</span>
          <span><strong>字重：</strong>{{ selectedRegionPreviewDebug.computedFontWeight || '（未读取到）' }}</span>
          <span><strong>当前层：</strong>{{ selectedRegionPreviewDebug.previewLayer || '（未知）' }}</span>
        </div>

        <div class="style-workbench">
          <div class="style-preview">
            <div class="style-preview-grid">
              <div class="style-preview-panel">
                <div class="style-preview-label">原图</div>
                <div
                  :class="['style-preview-canvas', (manualDrawMode || isAdjustingRegionBBox) ? 'draw-mode' : '']"
                  @pointerdown="startManualDraw($event, selectedEditPage)"
                  @pointermove="updateManualDraw($event, selectedEditPage)"
                  @pointerup="finishManualDraw($event, selectedEditPage)"
                  @pointercancel="clearManualDraft({ keepMode: true })"
                >
                  <img
                    :alt="`${selectedEditPage.name} 原图`"
                    :src="withCacheBust(toApiUrl(selectedEditPage.source_image_url || selectedEditPage.image_url))"
                  />

                  <div v-if="manualDrawMode" class="style-preview-tip">
                    在原图上拖拽框出漏掉的文字区域
                  </div>

                  <div v-else-if="isAdjustingRegionBBox" class="style-preview-tip">
                    在原图上拖一个新框，替换当前文本框范围
                  </div>

                  <div v-else-if="isCanvasReviewMode" class="style-preview-tip">
                    画布模式：拖动文本框可改位置，拖拽控制点可改大小
                  </div>

                  <button
                    v-for="region in selectedEditPage.regions"
                    :key="`source-${region.id}`"
                    type="button"
                    :class="[
                      'style-box',
                      isManualRegion(region) ? 'manual' : '',
                      mergeMode && isRegionSelectedForMerge(region) ? 'merge-selected' : '',
                      selectedEditRegionKey === region.id ? 'active' : '',
                      canDirectManipulateCanvas ? 'canvas-editable' : '',
                      getStyleRegionLabelClass(region, selectedEditPage)
                    ]"
                    :style="getStyleRegionBoxStyle(region, selectedEditPage)"
                    @click="mergeMode ? toggleMergeSelection(region) : selectedEditRegionKey = region.id"
                    @pointerdown.stop="startCanvasRegionTransform($event, region, selectedEditPage, 'move')"
                  >
                    <span class="style-box-label">{{ region.index + 1 }}</span>
                    <span
                      v-if="canDirectManipulateCanvas && selectedEditRegionKey === region.id"
                      v-for="handle in canvasHandleOptions"
                      :key="`${region.id}-${handle}`"
                      :class="['style-box-handle', `handle-${handle}`]"
                      :style="{ cursor: getCanvasHandleCursor(handle) }"
                      @pointerdown.stop="startCanvasRegionTransform($event, region, selectedEditPage, 'resize', handle)"
                    ></span>
                  </button>

                  <div
                    v-if="manualDrawDraft && manualDrawDraft.stored_name === selectedEditPage.stored_name"
                    :class="['style-box', 'style-box-draft', 'active', 'manual', getStyleRegionLabelClass({ bbox: getManualDraftBBox(selectedEditPage) || [0, 0, 0, 0] }, selectedEditPage)]"
                    :style="getManualDraftStyle(selectedEditPage)"
                  >
                    <span class="style-box-label">新框</span>
                  </div>
                </div>
              </div>

              <div class="style-preview-panel">
                <div class="style-preview-label">当前译图</div>
                <div class="style-preview-canvas" ref="translatedPreviewCanvasRef">
                  <img
                    :alt="`${selectedEditPage.name} 译图`"
                    :src="getCanvasPreviewImageUrl(selectedEditPage)"
                    @load="refreshTranslatedPreviewScale"
                  />

                  <button
                    v-for="region in selectedEditPage.regions"
                    :key="`translated-${region.id}`"
                    type="button"
                    :class="[
                      'style-box',
                      isManualRegion(region) ? 'manual' : '',
                      mergeMode && isRegionSelectedForMerge(region) ? 'merge-selected' : '',
                      selectedEditRegionKey === region.id ? 'active' : '',
                      canDirectManipulateCanvas ? 'canvas-editable' : '',
                      getStyleRegionLabelClass(region, selectedEditPage)
                    ]"
                    :style="getStyleRegionBoxStyle(region, selectedEditPage)"
                    @click="mergeMode ? toggleMergeSelection(region) : selectedEditRegionKey = region.id"
                    @pointerdown.stop="startCanvasRegionTransform($event, region, selectedEditPage, 'move')"
                  >
                    <span class="style-box-label">{{ region.index + 1 }}</span>
                    <span
                      v-if="shouldShowSourceCropPreview(region, selectedEditPage)"
                      class="style-box-source-crop"
                    >
                      <img
                        :src="withCacheBust(toApiUrl(selectedEditPage.source_image_url || selectedEditPage.image_url))"
                        :style="getSourceCropImageStyle(region, selectedEditPage)"
                        alt=""
                      />
                    </span>
                    <span
                      v-else-if="shouldShowCanvasTextOverlay(region, selectedEditPage)"
                      class="style-box-preview-text"
                      :class="{ vertical: isVerticalRegion(region) }"
                      :style="getCanvasPreviewTextContainerStyle(region)"
                    >
                      <span
                        class="style-box-preview-text-content"
                        :class="{ vertical: isVerticalRegion(region) }"
                        :data-region-id="region.id"
                        :style="getCanvasPreviewTextStyle(region)"
                      >
                        {{ getCanvasPreviewText(region) }}
                      </span>
                    </span>
                    <span
                      v-if="canDirectManipulateCanvas && selectedEditRegionKey === region.id"
                      v-for="handle in canvasHandleOptions"
                      :key="`translated-${region.id}-${handle}`"
                      :class="['style-box-handle', `handle-${handle}`]"
                      :style="{ cursor: getCanvasHandleCursor(handle) }"
                      @pointerdown.stop="startCanvasRegionTransform($event, region, selectedEditPage, 'resize', handle)"
                    ></span>
                  </button>
                </div>
              </div>

              <div class="style-preview-panel">
                <div class="style-preview-label">已嵌字结果</div>
                <div class="style-preview-canvas">
                  <img
                    v-if="getSavedTranslatedImageUrl(selectedEditPage)"
                    :alt="`${selectedEditPage.name} 已嵌字结果`"
                    :src="getSavedTranslatedImageUrl(selectedEditPage)"
                  />
                  <div v-else class="style-preview-empty">
                    当前还没有可用的已嵌字结果
                  </div>
                </div>
              </div>
            </div>
          </div>

          <div class="style-region-list">
            <article
              v-for="region in selectedEditPage.regions"
              :key="`edit-${region.id}`"
              :data-region-id="region.id"
              :class="[
                'style-region-card',
                selectedEditRegionKey === region.id ? 'active' : '',
                mergeMode && isRegionSelectedForMerge(region) ? 'merge-selected' : ''
              ]"
              @click="mergeMode ? toggleMergeSelection(region) : selectedEditRegionKey = region.id"
            >
              <div class="style-region-row">
                  <div class="style-region-copy">
                    <strong class="style-region-index">#{{ region.index + 1 }}</strong>
                    <div class="style-region-texts">
                      <p class="style-source-text">{{ region.source_text || '（没有识别到可用原文）' }}</p>
                    </div>
                    <span v-if="isManualRegion(region)" class="style-badge style-badge-manual">手动补框</span>
                  </div>

                <div class="style-region-inline-controls" @click.stop @mousedown.stop>
                  <label class="region-inline-toggle">
                    <input
                      :checked="isRegionSkipEnabled(region)"
                      type="checkbox"
                      @click.stop
                      @mousedown.stop
                      @change="updateTranslationSkipOverride(region, $event.target.checked)"
                    />
                    <span>保留原文</span>
                  </label>

                  <button
                    v-if="!isManualRegion(region)"
                    class="inline-button"
                    type="button"
                    @click.stop="updateRegionDisabledOverride(region, true)"
                  >
                    禁用
                  </button>

                  <button
                    v-if="!isCanvasReviewMode"
                    class="inline-button"
                    type="button"
                    @click.stop="startRegionBBoxAdjustment(region)"
                  >
                    调框
                  </button>

                  <label class="compact-select-wrap compact-select-wrap-font">
                    <select
                      :value="getRegionFontOverrideId(region)"
                      @click.stop
                      @mousedown.stop
                      @change="updateRegionFontOverride(region, $event.target.value)"
                    >
                      <option value="">{{ getEffectiveRegionFontLabel(region) }}</option>
                      <option
                        v-for="font in availableFonts"
                        :key="`${region.id}-${font.id}`"
                        :value="font.id"
                      >
                        {{ getPreviewFontOptionLabel(font) }}
                      </option>
                    </select>
                  </label>

                  <label class="compact-number-wrap compact-number-wrap-font-size" @click.stop @mousedown.stop>
                    <input
                      :value="getRegionFontSize(region)"
                      type="number"
                      min="8"
                      max="240"
                      step="1"
                      inputmode="numeric"
                      @click.stop
                      @mousedown.stop
                      @input="handleRegionFontSizeInput(region, $event.target.value)"
                      @keydown.enter.prevent="commitRegionFontSize(region)"
                      @blur="commitRegionFontSize(region)"
                    />
                  </label>

                  <label class="compact-select-wrap compact-select-wrap-direction">
                    <select
                      :value="getRegionDirectionValue(region)"
                      @click.stop
                      @mousedown.stop
                      @change="updateRegionTextDirection(region, $event.target.value)"
                    >
                      <option
                        v-for="option in textDirectionOptions"
                        :key="`${region.id}-${option.value}`"
                        :value="option.value"
                      >
                        {{ option.label }}
                      </option>
                    </select>
                  </label>

                  <button
                    v-if="isManualRegion(region)"
                    class="inline-button inline-button-danger"
                    type="button"
                    @click.stop="deleteManualRegion(region)"
                  >
                    删除
                  </button>
                </div>
              </div>

              <div class="region-card-controls region-card-controls-single">
                <label
                  class="field style-override-field compact-field compact-field-grow"
                  @click.stop
                  @mousedown.stop
                >
                  <input
                    :class="['translation-review-input', isRegionSkipEnabled(region) ? 'disabled' : '']"
                    :value="getEditRegionText(region)"
                    type="text"
                    :disabled="isRegionSkipEnabled(region)"
                    @click.stop
                    @mousedown.stop
                    @input="handleRegionTextInput(region, $event.target.value)"
                    @change="commitRegionTextDraft(region)"
                  />
                </label>
              </div>
            </article>
          </div>
        </div>
      </div>

      <div v-else class="empty-state">
        当前会话还没有可编辑的文本框缓存。先完整翻译一次，再回来逐框改译文和字体会更稳。
      </div>
    </section>

    <section class="gallery-card">
      <div class="section-head">
        <div>
          <p class="eyebrow">Compare</p>
          <h2>结果对照</h2>
        </div>
        <p>{{ comparisonImages.length }} 组页面</p>
      </div>

      <div v-if="comparisonImages.length" class="compare-grid">
        <article
          v-for="pair in comparisonImages"
          :key="pair.key"
          class="compare-card"
        >
          <div class="compare-card-head">{{ pair.name }}</div>
          <div class="compare-card-grid">
            <div class="compare-panel">
              <div class="compare-panel-label">原图</div>
              <div class="compare-image-shell">
                <img
                  v-if="pair.original"
                  :alt="`${pair.name} 原图`"
                  :src="pair.original.url"
                  loading="lazy"
                  class="compare-image"
                />
                <div v-else class="compare-image-empty">暂无原图</div>
              </div>
            </div>

            <div class="compare-panel">
              <div class="compare-panel-label">译图</div>
              <div class="compare-image-shell">
                <img
                  v-if="pair.translated"
                  :alt="`${pair.name} 译图`"
                  :src="pair.translated.url"
                  loading="lazy"
                  class="compare-image"
                />
                <div v-else class="compare-image-empty">尚未生成</div>
              </div>
            </div>
          </div>
        </article>
      </div>

      <div v-else class="empty-state">
        上传并开始翻译后，这里会按页面把原图和译图左右对照展示。
      </div>
    </section>

  </main>
</template>
