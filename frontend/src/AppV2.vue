<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import reviewJaPlaceholder from './assets/v2/review-ja.jpg'
import reviewZhPlaceholder from './assets/v2/review-zh.png'
import thumbAPlaceholder from './assets/v2/thumb-a.jpg'
import thumbBPlaceholder from './assets/v2/thumb-b.jpg'
import { usePageCommandState } from './composables/usePageCommandState.js'

const desktopBridge = typeof window !== 'undefined' && window.mangaDesktop && typeof window.mangaDesktop === 'object'
  ? window.mangaDesktop
  : null
const runtimeApiBaseUrl = String(desktopBridge?.runtime?.apiBaseUrl || '').trim()
const defaultApiBaseUrl = (runtimeApiBaseUrl || import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const configStorageKey = 'manga-translator.ui-config'
const reviewWorkspaceStorageKey = 'manga-translator.review-workspace'
const IMAGE_THUMBNAIL_MAX_SIDE = 480
const IMAGE_REVIEW_MAX_SIDE = 2400
const browserConfigKeys = [
  'translator',
  'translator_model',
  'translator_model_custom',
  'target_lang',
  'use_gpu',
  'font_key',
  'font_style_mode',
  'style_font_gothic_key',
  'style_font_mincho_key',
  'style_font_rounded_key',
  'style_font_cartoon_key',
  'style_font_handwritten_key',
  'style_font_sfx_key',
  'render_alignment',
  'render_letter_spacing',
  'rerender_output_format',
  'default_review_mode',
  'workspace_width_mode',
  'pause_after_detection',
  'mask_cleanup_strength',
  'export_mask_debug',
  'advanced_text_repair',
  'image_cleanup_mode',
  'image_cleanup_model',
  'advanced_erase_provider',
  'advanced_erase_base_url',
  'advanced_erase_model',
  'advanced_erase_timeout_seconds',
  'advanced_erase_selection_prompt'
]
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
const advancedEraseDefaultConfig = {
  provider: 'volcengine-ark',
  baseUrl: 'https://ark.cn-beijing.volces.com/api/v3/images/generations',
  model: 'doubao-seedream-5-0-lite-260128',
  timeoutSeconds: 120
}
const advancedEraseSelectionDefaultPrompt = [
  "Edit this manga page. The white blank area is outside the user's selection and should stay blank.",
  'In the visible selected areas, remove all text, letters, handwriting, and sound-effect characters.',
  'Fill the removed text with the surrounding background.',
  'Keep non-text artwork, character lines, speech bubbles, caption boxes, sound-effect borders, panels, tones, and layout unchanged.',
  'Do not add or translate text. Do not crop, rotate, or resize. Return only the cleaned image.'
].join('\n')
const advancedEraseProviderOptions = [
  { value: 'volcengine-ark', label: '火山引擎 Ark / Seedream' }
]
const styleBucketOptions = [
  { value: 'gothic', label: '黑体' },
  { value: 'mincho', label: '宋体 / 明体' },
  { value: 'rounded', label: '圆体' },
  { value: 'cartoon', label: '卡通' },
  { value: 'handwritten', label: '手写' },
  { value: 'sfx', label: '拟声' }
]
const defaultBuiltinFontId = 'builtin:NotoSansCJKtc-Regular.otf'
const projectGlossaryCategoryOptions = ['人名', '组织/团体', '地点', '作品/道具/技能', '行业术语', '其他']
const textDirectionOptions = [
  { value: 'auto', label: '自动（中文默认竖排）' },
  { value: 'vertical', label: '竖排' },
  { value: 'horizontal', label: '横排' }
]
const canvasHandleOptions = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']
const canvasZoomMin = 0.2
const canvasZoomMax = 6
const maxCanvasHistoryEntries = 50
const renderFontSizeOffset = -6
const renderTextPaddingRatio = 0.12
const defaultStrokeStrength = 0.2
const styleColorSwatches = ['#000000', '#ffffff', '#152234', '#ef4444', '#f59e0b', '#2563eb']
const strokeStrengthOptions = [0, 0.1, 0.2, 0.35, 0.5, 0.75, 1]
const reviewComparePaneOptions = [
  { key: 'final', label: '嵌后', shortLabel: '嵌后' },
  { key: 'source', label: '原图', shortLabel: '原图' },
  { key: 'blank', label: '空页', shortLabel: '空页' },
  { key: 'frame', label: '框页', shortLabel: '框页' }
]
const defaultReviewComparePaneModes = ['final', 'frame']
const reviewComparePaneOrder = reviewComparePaneOptions.map((option) => option.key)
const styleBucketLabelMap = Object.fromEntries(styleBucketOptions.map((option) => [option.value, option.label]))
const defaultStyleFontNameMap = {
  gothic: ['NotoSansCJKtc-Regular.otf', 'NotoSansCJKtc-Regular'],
  mincho: ['NotoSansCJKtc-Regular.otf', 'NotoSansCJKtc-Regular'],
  rounded: ['NotoSansCJKtc-Regular.otf', 'NotoSansCJKtc-Regular'],
  cartoon: ['NotoSansCJKtc-Regular.otf', 'NotoSansCJKtc-Regular'],
  handwritten: ['NotoSansCJKtc-Regular.otf', 'NotoSansCJKtc-Regular'],
  sfx: ['NotoSansCJKtc-Regular.otf', 'NotoSansCJKtc-Regular']
}
const previewFallbackFontNameMap = {
  gothic: ['NotoSansCJKtc-Regular.otf', 'SourceHanSansSC-Regular-2.otf', 'NotoSansSC-Bold.otf'],
  mincho: ['NotoSansCJKtc-Regular.otf', 'SourceHanSansSC-Regular-2.otf', 'NotoSansSC-Bold.otf'],
  rounded: ['NotoSansCJKtc-Regular.otf', 'SourceHanSansSC-Medium-2.otf', 'NotoSansSC-Bold.otf'],
  cartoon: ['NotoSansCJKtc-Regular.otf', 'SourceHanSansSC-Bold.otf', 'NotoSansSC-Bold.otf'],
  handwritten: ['NotoSansCJKtc-Regular.otf', 'SourceHanSansSC-Regular-2.otf', 'NotoSansSC-Bold.otf'],
  sfx: ['NotoSansCJKtc-Regular.otf', 'SourceHanSansSC-Bold.otf', 'NotoSansSC-Bold.otf']
}
const previewFontDecodeDenyList = new Set([
  '華康方圓體.ttf',
  '華康布丁體.ttf',
  '華康竹風體.ttf'
])
const styleFontConfigKeyMap = {
  gothic: 'style_font_gothic_key',
  mincho: 'style_font_mincho_key',
  rounded: 'style_font_rounded_key',
  cartoon: 'style_font_cartoon_key',
  handwritten: 'style_font_handwritten_key',
  sfx: 'style_font_sfx_key'
}
const v2PlaceholderThumbs = [
  reviewJaPlaceholder,
  reviewZhPlaceholder,
  thumbAPlaceholder,
  thumbBPlaceholder
]
const emptyRuntimeInfo = {
  desktop_mode: false,
  app_version: 'web',
  backend_base_url: defaultApiBaseUrl,
  data_dir: '',
  models_dir: '',
  output_dir: '',
  logs_dir: '',
  settings_path: '',
  settings_exists: false,
  migration: {
    needed: false,
    status: 'pending',
    target: {},
    summary: {}
  }
}
const emptyDiagnosticsInfo = {
  platform: '',
  python_version: '',
  disk: {
    total_bytes: 0,
    used_bytes: 0,
    free_bytes: 0
  },
  gpu: {
    available: false,
    device_count: 0,
    devices: [],
    cuda_version: ''
  }
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

function normalizeAdvancedEraseProvider(value) {
  const normalized = String(value || advancedEraseDefaultConfig.provider).trim().toLowerCase()
  return advancedEraseProviderOptions.some((option) => option.value === normalized)
    ? normalized
    : advancedEraseDefaultConfig.provider
}

function normalizeAdvancedEraseTimeoutSeconds(value) {
  const numericValue = Number(value)
  const rounded = Number.isFinite(numericValue)
    ? Math.round(numericValue)
    : advancedEraseDefaultConfig.timeoutSeconds
  return Math.min(300, Math.max(30, rounded))
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

function isValidWorkspaceWidthMode(value) {
  return ['auto', 'fixed'].includes(value)
}

function isValidComparePaneMode(value) {
  return ['saved', 'preview', 'source', 'blank', 'frame', 'final'].includes(value)
}

function isValidReviewComparePaneMode(value) {
  return reviewComparePaneOrder.includes(value)
}

function normalizeLegacyComparePaneMode(value) {
  if (value === 'source') {
    return 'source'
  }
  if (value === 'blank') {
    return 'blank'
  }
  if (value === 'frame') {
    return 'frame'
  }
  return 'final'
}

function normalizeReviewComparePaneModes(value, legacyMode = '') {
  const sourceModes = Array.isArray(value)
    ? value
    : (legacyMode ? [normalizeLegacyComparePaneMode(legacyMode), 'frame'] : defaultReviewComparePaneModes)
  const uniqueModes = []
  for (const mode of sourceModes) {
    const normalized = normalizeLegacyComparePaneMode(String(mode || '').trim())
    if (!isValidReviewComparePaneMode(normalized) || uniqueModes.includes(normalized)) {
      continue
    }
    uniqueModes.push(normalized)
  }

  for (const fallbackMode of defaultReviewComparePaneModes) {
    if (uniqueModes.length >= 2) {
      break
    }
    if (!uniqueModes.includes(fallbackMode)) {
      uniqueModes.push(fallbackMode)
    }
  }

  return reviewComparePaneOrder
    .filter((mode) => uniqueModes.includes(mode))
    .slice(0, 3)
}

function isValidInspectorTab(value) {
  return ['inspector', 'regions'].includes(value)
}

function createDefaultConfig() {
  return {
    translator: 'gemini',
    translator_model: '',
    translator_model_custom: '',
    target_lang: 'CHT',
    use_gpu: true,
    api_key: '',
    openai_base_url: '',
    openai_model: '',
    font_key: defaultBuiltinFontId,
    font_style_mode: 'auto-map',
    style_font_gothic_key: defaultBuiltinFontId,
    style_font_mincho_key: defaultBuiltinFontId,
    style_font_rounded_key: defaultBuiltinFontId,
    style_font_cartoon_key: defaultBuiltinFontId,
    style_font_handwritten_key: defaultBuiltinFontId,
    style_font_sfx_key: defaultBuiltinFontId,
    render_alignment: 'center',
    render_letter_spacing: 1.08,
    rerender_output_format: 'png',
    default_review_mode: 'canvas_beta',
    workspace_width_mode: 'fixed',
    pause_after_detection: false,
    mask_cleanup_strength: 'standard',
    export_mask_debug: false,
    advanced_text_repair: 'auto',
    image_cleanup_mode: 'off',
    image_cleanup_model: 'gemini-2.5-flash-image',
    image_cleanup_api_key: '',
    advanced_erase_provider: advancedEraseDefaultConfig.provider,
    advanced_erase_base_url: advancedEraseDefaultConfig.baseUrl,
    advanced_erase_model: advancedEraseDefaultConfig.model,
    advanced_erase_api_key: '',
    advanced_erase_timeout_seconds: advancedEraseDefaultConfig.timeoutSeconds,
    advanced_erase_selection_prompt: advancedEraseSelectionDefaultPrompt
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
  const advancedEraseProvider = normalizeAdvancedEraseProvider(rawValue.advanced_erase_provider)
  const advancedEraseBaseUrl = typeof rawValue.advanced_erase_base_url === 'string' && rawValue.advanced_erase_base_url.trim()
    ? rawValue.advanced_erase_base_url.trim()
    : defaults.advanced_erase_base_url
  const advancedEraseModel = typeof rawValue.advanced_erase_model === 'string' && rawValue.advanced_erase_model.trim()
    ? rawValue.advanced_erase_model.trim()
    : defaults.advanced_erase_model
  const advancedEraseTimeoutSeconds = normalizeAdvancedEraseTimeoutSeconds(rawValue.advanced_erase_timeout_seconds)
  const advancedEraseSelectionPrompt = typeof rawValue.advanced_erase_selection_prompt === 'string'
    && rawValue.advanced_erase_selection_prompt.trim()
    ? rawValue.advanced_erase_selection_prompt.slice(0, 4000)
    : defaults.advanced_erase_selection_prompt
  const maskCleanupStrength = typeof rawValue.mask_cleanup_strength === 'string' && isValidMaskCleanupStrength(rawValue.mask_cleanup_strength)
    ? rawValue.mask_cleanup_strength
    : defaults.mask_cleanup_strength
  const fontStyleMode = defaults.font_style_mode
  const rerenderOutputFormat = typeof rawValue.rerender_output_format === 'string' && isValidRerenderOutputFormat(rawValue.rerender_output_format)
    ? rawValue.rerender_output_format
    : defaults.rerender_output_format
  const defaultReviewMode = typeof rawValue.default_review_mode === 'string' && isValidReviewMode(rawValue.default_review_mode)
    ? rawValue.default_review_mode
    : defaults.default_review_mode
  const workspaceWidthMode = typeof rawValue.workspace_width_mode === 'string' && isValidWorkspaceWidthMode(rawValue.workspace_width_mode)
    ? rawValue.workspace_width_mode
    : defaults.workspace_width_mode
  const renderAlignment = normalizeRenderAlignmentValue(rawValue.render_alignment, defaults.render_alignment)

  return {
    translator,
    translator_model: translatorModel,
    translator_model_custom: translatorModelCustom,
    target_lang: typeof rawValue.target_lang === 'string' ? rawValue.target_lang : defaults.target_lang,
    use_gpu: typeof rawValue.use_gpu === 'boolean' ? rawValue.use_gpu : defaults.use_gpu,
    api_key: typeof rawValue.api_key === 'string' ? rawValue.api_key : defaults.api_key,
    openai_base_url: typeof rawValue.openai_base_url === 'string' ? rawValue.openai_base_url : defaults.openai_base_url,
    openai_model: typeof rawValue.openai_model === 'string' ? rawValue.openai_model : defaults.openai_model,
    font_key: typeof rawValue.font_key === 'string' && rawValue.font_key.trim()
      ? rawValue.font_key
      : defaults.font_key,
    font_style_mode: fontStyleMode,
    style_font_gothic_key: typeof rawValue.style_font_gothic_key === 'string' && rawValue.style_font_gothic_key.trim()
      ? rawValue.style_font_gothic_key
      : defaults.style_font_gothic_key,
    style_font_mincho_key: typeof rawValue.style_font_mincho_key === 'string'
      && rawValue.style_font_mincho_key.trim()
      ? rawValue.style_font_mincho_key
      : defaults.style_font_mincho_key,
    style_font_rounded_key: typeof rawValue.style_font_rounded_key === 'string'
      && rawValue.style_font_rounded_key.trim()
      ? rawValue.style_font_rounded_key
      : defaults.style_font_rounded_key,
    style_font_cartoon_key: typeof rawValue.style_font_cartoon_key === 'string'
      && rawValue.style_font_cartoon_key.trim()
      ? rawValue.style_font_cartoon_key
      : defaults.style_font_cartoon_key,
    style_font_handwritten_key: typeof rawValue.style_font_handwritten_key === 'string'
      && rawValue.style_font_handwritten_key.trim()
      ? rawValue.style_font_handwritten_key
      : defaults.style_font_handwritten_key,
    style_font_sfx_key: typeof rawValue.style_font_sfx_key === 'string'
      && rawValue.style_font_sfx_key.trim()
      ? rawValue.style_font_sfx_key
      : defaults.style_font_sfx_key,
    render_alignment: renderAlignment,
    render_letter_spacing: typeof rawValue.render_letter_spacing === 'number'
      ? Math.min(1.35, Math.max(0.85, rawValue.render_letter_spacing))
      : defaults.render_letter_spacing,
    rerender_output_format: rerenderOutputFormat,
    default_review_mode: defaultReviewMode,
    workspace_width_mode: workspaceWidthMode,
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
      : defaults.image_cleanup_api_key,
    advanced_erase_provider: advancedEraseProvider,
    advanced_erase_base_url: advancedEraseBaseUrl,
    advanced_erase_model: advancedEraseModel,
    advanced_erase_api_key: typeof rawValue.advanced_erase_api_key === 'string'
      ? rawValue.advanced_erase_api_key
      : defaults.advanced_erase_api_key,
    advanced_erase_timeout_seconds: advancedEraseTimeoutSeconds,
    advanced_erase_selection_prompt: advancedEraseSelectionPrompt
  }
}

function normalizeRenderAlignmentValue(rawValue, fallback = 'center') {
  const normalized = String(rawValue || fallback)
    .trim()
    .toLowerCase()
  if (normalized === 'right') {
    return 'right'
  }
  return 'center'
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

function pickConfigKeys(value, keys) {
  return Object.fromEntries(
    keys
      .filter((key) => Object.prototype.hasOwnProperty.call(value || {}, key))
      .map((key) => [key, value[key]])
  )
}

function createBrowserConfigCache(value) {
  const normalized = normalizeStoredConfig(value)
  return pickConfigKeys(normalized, browserConfigKeys)
}

function saveStoredConfig(value) {
  if (typeof window === 'undefined') {
    return
  }

  try {
    const nextValue = isDesktopRuntime.value
      ? createBrowserConfigCache(value)
      : normalizeStoredConfig(value)
    window.localStorage.setItem(configStorageKey, JSON.stringify(nextValue))
  } catch (error) {
    console.warn('Failed to persist UI config locally.', error)
  }
}

function mergeProjectConfigWithLocalPreferences(projectConfig, localConfig) {
  return normalizeStoredConfig({
    ...(projectConfig && typeof projectConfig === 'object' ? projectConfig : {}),
    ...(localConfig && typeof localConfig === 'object' ? localConfig : {})
  })
}

function createDefaultReviewWorkspacePrefs() {
  return {
    split_ratio: 65,
    compare_pane_mode: 'final',
    compare_pane_modes: [...defaultReviewComparePaneModes],
    compare_sync_enabled: true,
    inspector_tab: 'inspector',
    show_debug: false,
    page_rail_width: 128,
    inspector_width: 420,
    show_settings_panel: false,
    show_project_meta_panel: false,
    show_project_history_panel: false,
    show_compare_gallery_panel: false
  }
}

function normalizeStoredReviewWorkspacePrefs(rawValue) {
  const defaults = createDefaultReviewWorkspacePrefs()
  if (!rawValue || typeof rawValue !== 'object') {
    return defaults
  }

  const splitRatio = Number(rawValue.split_ratio)
  const pageRailWidth = Number(rawValue.page_rail_width)
  const inspectorWidth = Number(rawValue.inspector_width)
  return {
    split_ratio: Number.isFinite(splitRatio) ? Math.min(80, Math.max(50, Math.round(splitRatio))) : defaults.split_ratio,
    compare_pane_mode: isValidComparePaneMode(rawValue.compare_pane_mode)
      ? normalizeLegacyComparePaneMode(rawValue.compare_pane_mode)
      : defaults.compare_pane_mode,
    compare_pane_modes: normalizeReviewComparePaneModes(rawValue.compare_pane_modes, rawValue.compare_pane_mode),
    compare_sync_enabled: typeof rawValue.compare_sync_enabled === 'boolean'
      ? rawValue.compare_sync_enabled
      : defaults.compare_sync_enabled,
    inspector_tab: isValidInspectorTab(rawValue.inspector_tab) ? rawValue.inspector_tab : defaults.inspector_tab,
    show_debug: typeof rawValue.show_debug === 'boolean'
      ? rawValue.show_debug
      : defaults.show_debug,
    page_rail_width: Number.isFinite(pageRailWidth)
      ? Math.min(180, Math.max(96, Math.round(pageRailWidth)))
      : defaults.page_rail_width,
    inspector_width: Number.isFinite(inspectorWidth)
      ? Math.min(920, Math.max(300, Math.round(inspectorWidth)))
      : defaults.inspector_width,
    show_settings_panel: typeof rawValue.show_settings_panel === 'boolean'
      ? rawValue.show_settings_panel
      : defaults.show_settings_panel,
    show_project_meta_panel: typeof rawValue.show_project_meta_panel === 'boolean'
      ? rawValue.show_project_meta_panel
      : defaults.show_project_meta_panel,
    show_project_history_panel: typeof rawValue.show_project_history_panel === 'boolean'
      ? rawValue.show_project_history_panel
      : defaults.show_project_history_panel,
    show_compare_gallery_panel: typeof rawValue.show_compare_gallery_panel === 'boolean'
      ? rawValue.show_compare_gallery_panel
      : defaults.show_compare_gallery_panel
  }
}

function getReviewWorkspaceStorageKey(projectId = '') {
  const normalizedProjectId = String(projectId || '').trim()
  return normalizedProjectId
    ? `${reviewWorkspaceStorageKey}.${normalizedProjectId}`
    : reviewWorkspaceStorageKey
}

function loadStoredReviewWorkspacePrefs(projectId = '') {
  if (typeof window === 'undefined') {
    return createDefaultReviewWorkspacePrefs()
  }

  try {
    const rawValue = window.localStorage.getItem(getReviewWorkspaceStorageKey(projectId))
      || (!projectId ? '' : window.localStorage.getItem(reviewWorkspaceStorageKey))
    if (!rawValue) {
      return createDefaultReviewWorkspacePrefs()
    }
    return normalizeStoredReviewWorkspacePrefs(JSON.parse(rawValue))
  } catch (_error) {
    return createDefaultReviewWorkspacePrefs()
  }
}

function saveStoredReviewWorkspacePrefs(value, projectId = '') {
  if (typeof window === 'undefined') {
    return
  }

  try {
    window.localStorage.setItem(
      getReviewWorkspaceStorageKey(projectId),
      JSON.stringify(normalizeStoredReviewWorkspacePrefs(value))
    )
  } catch (error) {
    console.warn('Failed to persist review workspace prefs locally.', error)
  }
}

function createDefaultPerPageUiState() {
  return {
    selectedRegionId: '',
    main: {
      zoom: 1,
      panX: 0,
      panY: 0,
    },
    compare: {
      zoom: 1,
      panX: 0,
      panY: 0,
    }
  }
}

const selectedFile = ref(null)
const status = ref('正在检查后端状态...')
const backendOnline = ref(false)
const uploading = ref(false)
const translating = ref(false)
const advancedEraseBusy = ref(false)
const selectionEraseModalOpen = ref(false)
const selectionEraseRects = ref([])
const selectionEraseDraft = ref(null)
const historyLoading = ref(false)
const deletingProjectId = ref('')
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
const fontSizeDraftOriginOverrides = ref({})
const styleInspectionPages = ref([])
const styleInspectionLoading = ref(false)
const styleRegionOverrides = ref({})
const pageEditHistory = ref({})
const pageCommandRevisions = ref({})
const canvasPreviewDirtyPages = ref({})
const pageImageNonces = ref({})
const creatingManualRegion = ref(false)
const manualDrawMode = ref(false)
const adjustingRegionId = ref('')
const manualDrawDraft = ref(null)
const canvasTransformState = ref(null)
const canvasRegionSelection = ref({})
const canvasMarqueeState = ref(null)
const advancedStylePopover = ref({ pageId: '', regionId: '' })
const mergeMode = ref(false)
const mergeRegionSelection = ref({})
const selectedEditPageKey = ref('')
const selectedEditRegionKey = ref('')
const workflowStage = ref('idle')
const projectHistory = ref([])
const projectSnapshots = ref({})
const snapshotLoadingProjectId = ref('')
const expandedProjectId = ref('')
const currentProject = ref(null)
const projectTitleDraft = ref('')
const projectNoteDraft = ref('')
const translatedPreviewCanvasRef = ref(null)
const canvasShellMetrics = ref({
  main: { width: 0, height: 0 },
  compare: { width: 0, height: 0 }
})
const appRuntime = ref({ ...emptyRuntimeInfo, ...(desktopBridge?.runtime || {}) })
const appDiagnostics = ref({ ...emptyDiagnosticsInfo })
const apiBaseUrl = ref(defaultApiBaseUrl)
const appSettingsLoading = ref(false)
const appSettingsSaving = ref(false)
const appSettingsLoaded = ref(false)
const appSettingsValidation = ref({ ok: null, message: '', preview: '' })
const onboardingOpen = ref(false)
const migrationModalOpen = ref(false)
const translatedPreviewScale = ref(1)
const exportingOcrDebug = ref(false)
const exportingTranslationInputDebug = ref(false)
const exportingTranslationRequestDebug = ref(false)
const projectGlossary = ref({ version: 1, entries: [] })
const glossaryDrawerOpen = ref(false)
const glossaryLoading = ref(false)
const glossarySaving = ref(false)
const glossaryExtracting = ref(false)
const glossaryPreviewing = ref(false)
const glossaryApplying = ref(false)
const glossaryOccurrencesLoading = ref(false)
const glossaryDraftEntries = ref([])
const glossaryPreview = ref({ changes: [], change_count: 0, affected_pages: [], affected_page_count: 0 })
const glossaryError = ref('')
const topbarTaskProgress = ref({
  label: '',
  current: 0,
  total: 0
})

const config = ref(loadStoredConfig())
const reviewWorkspacePrefs = ref(loadStoredReviewWorkspacePrefs())
const showAdvancedSettings = computed({
  get() {
    return Boolean(reviewWorkspacePrefs.value.show_settings_panel)
  },
  set(value) {
    reviewWorkspacePrefs.value = {
      ...reviewWorkspacePrefs.value,
      show_settings_panel: Boolean(value)
    }
  }
})
const perPageUiState = ref({})
const regionListSearch = ref('')
const regionListFilter = ref('all')
const reviewCanvasZoneRef = ref(null)
const reviewWorkspaceLayoutRef = ref(null)
const selectionEraseStageRef = ref(null)
const mainCanvasShellRef = ref(null)
const compareCanvasShellRef = ref(null)
const viewportPanState = ref(null)
const compareSplitterState = ref(null)
const workspaceSplitterState = ref(null)
const spacePanPressed = ref(false)
const v2View = ref('home')
const v2HistoryModalOpen = ref(false)
const v2SettingsModalOpen = ref(false)
const v2UploadDragOver = ref(false)
const v2SupplementUploading = ref(false)
const v2PageSearch = ref('')
const v2HistorySearch = ref('')
const v2HistorySort = ref('recent')
const v2UploadInputRef = ref(null)
const v2SupplementInputRef = ref(null)

let socket = null
const expectedClosingSockets = new WeakSet()
let pendingCanvasNudge = null
let canvasNudgeCommitTimer = null
let suppressCanvasRegionClickUntil = 0
let topbarTaskProgressTimer = null
let reviewInspectionRequestToken = 0
let styleInspectionRequestToken = 0
let autoFitCanvasPageIds = new Set()
const pageCommandExecutionQueue = new Map()
const preloadedImageUrls = new Set()
let canvasLayoutFrame = null
const TRANSLATION_COMPLETION_RECOVERY_DELAY_MS = 15000
const TRANSLATION_COMPLETION_RECOVERY_RETRY_MS = 15000
const TRANSLATION_COMPLETION_RECOVERY_MAX_ATTEMPTS = 20
let translationCompletionRecoveryTimer = null
let translationCompletionRecoveryToken = 0

const acceptValue = '.zip,.cbz,.jpg,.jpeg,.png,.webp'
const {
  pageCommandPendingCounts,
  regionCommitStates,
  hasPendingPageCommands,
  setPageCommandPending,
  isPageCommandPending,
  getCommandRegionIds,
  setRegionCommitState,
  clearRegionCommitState,
  getRegionCommitStatusLabel,
  getRegionCommitStatusClass,
} = usePageCommandState()

const canUpload = computed(() => Boolean(selectedFile.value) && !uploading.value)
const canTranslate = computed(() => Boolean(sessionId.value) && !translating.value)
const canUseV2SupplementUpload = computed(() => (
  Boolean(sessionId.value)
  && !uploading.value
  && !translating.value
  && !v2SupplementUploading.value
))
const canStartNewProject = computed(() => !uploading.value && !translating.value && !v2SupplementUploading.value)
const canContinueSegmentedTranslation = computed(
  () => Boolean(sessionId.value) && !translating.value && workflowStage.value === 'detected'
)
const selectedEditPageHasTranslatedResult = computed(() => {
  const storedName = String(selectedEditPage.value?.stored_name || '').trim()
  if (!storedName) {
    return false
  }
  return Boolean(latestTranslatedImageUrlByPage.value[storedName])
})
const canTranslateCurrentPage = computed(
  () => Boolean(
    sessionId.value
    && selectedEditPage.value
    && !translating.value
    && (workflowStage.value === 'detected' || !selectedEditPageHasTranslatedResult.value)
  )
)
const canRerender = computed(
  () => Boolean(sessionId.value) && !translating.value && workflowStage.value === 'translated' && Boolean(downloadUrl.value || translatedImages.value.length)
)
const canRetranslate = computed(
  () => Boolean(sessionId.value) && !translating.value && (workflowStage.value === 'translated' || Boolean(translatedImages.value.length))
)
const canRunAdvancedErase = computed(
  () => Boolean(sessionId.value && selectedEditPage.value && !translating.value && !advancedEraseBusy.value)
)
const canUseProjectGlossary = computed(() => Boolean(sessionId.value) && !translating.value)
const projectGlossaryEntryCount = computed(() => glossaryDraftEntries.value.length)
const projectGlossaryOccurrencesLoaded = computed(() => Boolean(projectGlossary.value?.occurrences_loaded))
const projectGlossaryOccurrenceCount = computed(() => (
  projectGlossaryOccurrencesLoaded.value
    ? glossaryDraftEntries.value.reduce((total, entry) => total + Number(entry.occurrence_count || 0), 0)
    : '未扫描'
))
const glossaryBusy = computed(() => (
  glossaryLoading.value
  || glossarySaving.value
  || glossaryExtracting.value
  || glossaryPreviewing.value
  || glossaryApplying.value
  || glossaryOccurrencesLoading.value
))
const canSaveProjectGlossary = computed(() => canUseProjectGlossary.value && !glossaryBusy.value)
const canApplyProjectGlossary = computed(() => canUseProjectGlossary.value && !glossaryBusy.value)
const canRefreshProjectGlossaryOccurrences = computed(() => canUseProjectGlossary.value && !glossaryBusy.value)
const v2ExportArchiveUrl = computed(() => {
  const activeSessionId = String(sessionId.value || '').trim()
  if (!activeSessionId) {
    return ''
  }
  const currentDownloadUrl = String(downloadUrl.value || '').trim()
  if (currentDownloadUrl) {
    return currentDownloadUrl
  }
  if (workflowStage.value === 'translated' || translatedImages.value.length) {
    return withCacheBust(toApiUrl(`/api/download/${activeSessionId}`))
  }
  return ''
})
const canExportTranslatedResults = computed(
  () => Boolean(v2ExportArchiveUrl.value) && !translating.value
)
const activeTaskProjectId = computed(() => (translating.value ? String(sessionId.value || '').trim() : ''))
const canInspectEditor = computed(() => Boolean(sessionId.value))
const canCreateManualRegion = computed(() => Boolean(sessionId.value) && !translating.value && !creatingManualRegion.value)
const canActivateManualDraw = computed(() => Boolean(selectedEditPage.value) && canCreateManualRegion.value)
const activeReviewMode = computed(() => {
  const mode = currentProject.value?.review_mode || config.value.default_review_mode || 'canvas_beta'
  return mode === 'classic' ? 'canvas_beta' : mode
})
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
const canvasInteractionLockReason = computed(() => {
  if (!isCanvasReviewMode.value) {
    return '当前不是画布审校模式'
  }
  if (!selectedEditPage.value) {
    return '当前没有可编辑页面'
  }
  if (manualDrawMode.value) {
    return '正在手动添加框模式'
  }
  if (mergeMode.value) {
    return '正在合并文本框模式'
  }
  if (isAdjustingRegionBBox.value) {
    return '正在替换文本框范围'
  }
  return ''
})
const isCanvasInteractionLocked = computed(
  () => Boolean(canvasInteractionLockReason.value)
)
const canDirectManipulateCanvas = computed(() => !isCanvasInteractionLocked.value)
const pageShellWidthClass = computed(() => (
  config.value.workspace_width_mode === 'auto'
    ? 'page-shell-auto'
    : 'page-shell-fixed'
))
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
const selectedCanvasRegionIds = computed(() => {
  const page = selectedEditPage.value
  if (!page?.regions?.length) {
    return []
  }
  const explicitSelection = new Set(
    Object.keys(canvasRegionSelection.value || {})
      .filter((regionId) => canvasRegionSelection.value[regionId])
  )
  if (!explicitSelection.size && selectedEditRegionKey.value) {
    explicitSelection.add(selectedEditRegionKey.value)
  }
  return page.regions
    .map((region) => region.id)
    .filter((regionId) => explicitSelection.has(regionId))
})
const selectedCanvasRegions = computed(() => {
  const selectedIds = new Set(selectedCanvasRegionIds.value)
  return (selectedEditPage.value?.regions || []).filter((region) => selectedIds.has(region.id))
})
const selectedCanvasRegionCount = computed(() => selectedCanvasRegionIds.value.length)
const hasCanvasMultiSelection = computed(() => selectedCanvasRegionCount.value > 1)
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
const reviewCanvasGridStyle = computed(() => {
  const mainRatio = Math.min(80, Math.max(50, Number(reviewWorkspacePrefs.value.split_ratio || 65)))
  return {
    gridTemplateColumns: `minmax(0, ${mainRatio}fr) 12px minmax(300px, ${100 - mainRatio}fr)`
  }
})
const reviewWorkspaceLayoutStyle = computed(() => ({
  '--page-rail-width': `${Math.round(reviewWorkspacePrefs.value.page_rail_width || 128)}px`,
  '--inspector-width': `${Math.round(reviewWorkspacePrefs.value.inspector_width || 420)}px`
}))
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
const v2SelectedPagePositionLabel = computed(() => {
  if (selectedEditPageIndex.value < 0) {
    return '未选择'
  }
  return `${selectedEditPageIndex.value + 1} / ${Math.max(v2PageEntries.value.length, mergedInspectionPages.value.length, 1)}`
})
const selectedEditPageSummary = computed(() => {
  const page = selectedEditPage.value
  if (!page) {
    return ''
  }
  const pageNumber = selectedEditPageIndex.value >= 0 ? selectedEditPageIndex.value + 1 : 1
  return `第 ${pageNumber} / ${mergedInspectionPages.value.length} 页`
})
const selectedEditPageThumbnailUrl = computed(() => {
  const page = selectedEditPage.value
  if (!page) {
    return ''
  }
  return getThumbnailPageImageUrl(
    page.source_image_url || page.image_url || page.base_image_url || '',
    page.stored_name
  )
})
const selectedEditPageMainImageUrl = computed(() => {
  const page = selectedEditPage.value
  if (!page) {
    return ''
  }
  return getReviewPageImageUrl(
    page.base_image_url || page.source_image_url || page.image_url || '',
    page.stored_name
  )
})
const selectionEraseImageUrl = computed(() => selectedEditPageMainImageUrl.value)
const selectedEditRegion = computed(() => {
  const page = selectedEditPage.value
  if (!page) {
    return null
  }
  return page.regions.find((region) => region.id === selectedEditRegionKey.value) || null
})
const selectedEditRegionVisibleIndex = computed(() => (
  filteredEditRegions.value.findIndex((region) => region.id === selectedEditRegionKey.value)
))
const canSelectPreviousEditRegion = computed(() => selectedEditRegionVisibleIndex.value > 0)
const canSelectNextEditRegion = computed(() => (
  selectedEditRegionVisibleIndex.value >= 0
  && selectedEditRegionVisibleIndex.value < filteredEditRegions.value.length - 1
))
const selectedEditRegionIndexLabel = computed(() => {
  if (!selectedEditRegion.value) {
    return '未选中文本框'
  }
  return `#${selectedEditRegion.value.index + 1}`
})
const v2RegionSidebarCompact = computed(() => filteredEditRegions.value.length <= 4)
const pageRailItems = computed(() => (
  mergedInspectionPages.value.map((page, index) => ({
    ...page,
    pageNumber: index + 1,
    thumbnailUrl: getThumbnailPageImageUrl(
      page.source_image_url || page.image_url || page.base_image_url || '',
      page.stored_name
    ),
    selected: page.stored_name === selectedEditPageKey.value
  }))
))
const filteredEditRegions = computed(() => {
  const page = selectedEditPage.value
  if (!page) {
    return []
  }
  const search = String(regionListSearch.value || '').trim().toLowerCase()
  const filter = String(regionListFilter.value || 'all')

  return page.regions.filter((region) => {
    if (search) {
      const haystack = [
        region.source_text,
        getEditRegionText(region),
        getEffectiveRegionFontLabel(region),
        region.index + 1
      ]
        .map((item) => String(item || '').toLowerCase())
        .join(' ')
      if (!haystack.includes(search)) {
        return false
      }
    }

    if (filter === 'manual') {
      return isManualRegion(region)
    }
    if (filter === 'keep-original') {
      return isRegionSkipEnabled(region)
    }
    if (filter === 'untranslated') {
      return !String(getEditRegionText(region) || '').trim()
    }
    if (filter === 'font-override') {
      return Boolean(getRegionFontOverrideId(region))
    }
    if (filter === 'warning') {
      return hasRegionWarning(region)
    }
    return true
  })
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
const topbarTaskProgressActive = computed(() => (
  Number(topbarTaskProgress.value.total) > 0
  && Number(topbarTaskProgress.value.current) > 0
))
const topbarTaskProgressPercent = computed(() => {
  if (!topbarTaskProgressActive.value) {
    return 0
  }
  const total = Math.max(Number(topbarTaskProgress.value.total) || 0, 1)
  const current = Math.max(Number(topbarTaskProgress.value.current) || 0, 0)
  return Math.min(100, Math.round((current / total) * 100))
})
const translatorLabelMap = {
  gemini: 'Gemini',
  'doubao-ark': 'Doubao',
  'openai-compatible': 'OpenAI Compatible'
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
  const styleMode = '字体映射'
  const cleanup = config.value.image_cleanup_mode === 'off' ? '稳定流程' : 'AI 去字'
  const workflow = config.value.pause_after_detection ? '先校对再翻译' : '直接翻译'
  const reviewMode = reviewModeLabelMap[config.value.default_review_mode] || config.value.default_review_mode
  let translatorModel = ''
  if (config.value.translator === 'doubao-ark') {
    translatorModel = ` / ${getResolvedTranslatorModel(config.value)}`
  } else if (config.value.translator === 'openai-compatible') {
    translatorModel = config.value.openai_model ? ` / ${config.value.openai_model}` : ''
  }
  return `${translator}${translatorModel} / ${targetLang} / ${styleMode} / ${cleanup} / ${workflow} / 默认${reviewMode}`
})
const showTranslatorApiKeyField = computed(() => ['gemini', 'doubao-ark', 'openai-compatible'].includes(config.value.translator))
const translatorApiKeyLabel = computed(() => (
  config.value.translator === 'doubao-ark'
    ? 'Doubao Ark API Key'
    : config.value.translator === 'openai-compatible'
    ? 'OpenAI Compatible API Key'
    : 'Gemini API Key'
))
const translatorApiKeyPlaceholder = computed(() => (
  config.value.translator === 'doubao-ark'
    ? '输入火山方舟 Ark API Key'
    : config.value.translator === 'openai-compatible'
    ? '输入 OpenAI Compatible API Key'
    : '输入 Gemini API Key'
))
const showImageCleanupApiKeyField = computed(() => config.value.image_cleanup_mode !== 'off')
const imageCleanupApiKeyLabel = computed(() => (
  config.value.image_cleanup_mode === 'seedream-image'
    ? 'Seedream / Ark API Key'
    : 'Gemini Image API Key'
))
const imageCleanupApiKeyPlaceholder = computed(() => (
  config.value.image_cleanup_mode === 'seedream-image'
    ? '输入火山方舟 Ark API Key'
    : '输入 Gemini Image API Key'
))
const advancedEraseProviderLabel = computed(() => (
  advancedEraseProviderOptions.find((option) => option.value === config.value.advanced_erase_provider)?.label
  || advancedEraseProviderOptions[0].label
))
const v2HasProject = computed(() => Boolean(sessionId.value || currentProject.value))
const isDesktopRuntime = computed(() => Boolean(appRuntime.value?.desktop_mode))
const settingsStatusLabel = computed(() => {
  if (appSettingsLoading.value) {
    return '正在读取设置…'
  }
  if (appSettingsSaving.value) {
    return '正在保存设置…'
  }
  if (appSettingsValidation.value.ok === true) {
    return '连接已验证'
  }
  if (appSettingsValidation.value.ok === false) {
    return '连接待修复'
  }
  return appSettingsLoaded.value ? '设置已载入' : '尚未完成初始化'
})
const appRuntimeGpuLabel = computed(() => {
  const gpu = appDiagnostics.value?.gpu || {}
  if (!gpu.available) {
    return gpu.error ? `未启用 (${gpu.error})` : '未检测到可用 GPU'
  }
  const devices = Array.isArray(gpu.devices) ? gpu.devices : []
  const primary = devices[0]?.name || `${gpu.device_count || 1} 块设备`
  return gpu.cuda_version ? `${primary} / CUDA ${gpu.cuda_version}` : primary
})
const appRuntimeDiskLabel = computed(() => formatBytes(appDiagnostics.value?.disk?.free_bytes || 0))
const v2ProjectTitle = computed(() => (
  String(currentProject.value?.title || '').trim()
  || String(sessionId.value || '').trim()
  || '未命名项目'
))
const v2ProjectSubtitle = computed(() => {
  const pageCount = v2PageEntries.value.length
  const stage = workflowStageLabelMap[workflowStage.value] || workflowStage.value || '未开始'
  return `${stage} · ${pageCount || 0} 页`
})
const v2ReviewSavedLabel = computed(() => {
  const selectedPageId = selectedEditPage.value?.stored_name || ''
  if (selectedPageId && isPageCommandPending(selectedPageId)) {
    return '页面保存中…'
  }
  if (hasPendingPageCommands.value) {
    return '保存队列处理中…'
  }
  const updatedAt = currentProject.value?.updated_at || currentProject.value?.created_at || ''
  return `已保存 ${formatV2TimeOnly(updatedAt)}`
})
const v2ReviewSaveLabel = computed(() => (
  translating.value ? '处理中…' : '保存'
))
const v2TopbarStatusText = computed(() => (
  String(errorMessage.value || topbarTaskProgress.value.label || status.value || '').trim()
))
const v2TopbarStatusVisible = computed(
  () => v2View.value !== 'home' && Boolean(v2TopbarStatusText.value)
)
const v2TopbarProgressVisible = computed(
  () => (translating.value && progress.value.total > 0) || topbarTaskProgressActive.value
)
const v2TopbarProgressCurrent = computed(() => (
  translating.value && progress.value.total > 0
    ? Number(progress.value.current || 0)
    : Number(topbarTaskProgress.value.current || 0)
))
const v2TopbarProgressTotal = computed(() => (
  translating.value && progress.value.total > 0
    ? Number(progress.value.total || 0)
    : Number(topbarTaskProgress.value.total || 0)
))
const v2TopbarProgressPercent = computed(() => (
  translating.value && progress.value.total > 0
    ? progressPercent.value
    : topbarTaskProgressPercent.value
))
const canRunProjectPrimaryAction = computed(
  () => Boolean(sessionId.value) && !translating.value
)
const v2SettingsOpen = computed({
  get() {
    return v2View.value === 'review' ? Boolean(reviewWorkspacePrefs.value.show_settings_panel) : v2SettingsModalOpen.value
  },
  set(value) {
    if (v2View.value === 'review') {
      reviewWorkspacePrefs.value = {
        ...reviewWorkspacePrefs.value,
        show_settings_panel: Boolean(value)
      }
      return
    }
    v2SettingsModalOpen.value = Boolean(value)
  }
})
const v2PageEntries = computed(() => {
  const order = []
  const seen = new Set()
  const mapped = new Map()

  const ensureEntry = (key, payload = {}) => {
    const normalizedKey = String(key || '').trim()
    if (!normalizedKey) {
      return null
    }
    if (!mapped.has(normalizedKey)) {
      order.push(normalizedKey)
      mapped.set(normalizedKey, {
        stored_name: normalizedKey,
        name: payload.name || normalizedKey,
        sourceUrl: payload.sourceUrl || '',
        blankUrl: payload.blankUrl || '',
        finalUrl: payload.finalUrl || '',
        previewUrl: payload.previewUrl || '',
        sourceThumbUrl: payload.sourceThumbUrl || '',
        blankThumbUrl: payload.blankThumbUrl || '',
        finalThumbUrl: payload.finalThumbUrl || '',
        previewThumbUrl: payload.previewThumbUrl || '',
        pageNumber: payload.pageNumber || order.length,
        regionCount: Number(payload.regionCount || 0),
        reviewReady: Boolean(payload.reviewReady)
      })
    }
    const entry = mapped.get(normalizedKey)
    Object.assign(entry, payload)
    return entry
  }

  for (const image of originalImages.value) {
    const key = String(image?.stored_name || image?.name || '').trim()
    if (!key || seen.has(key)) {
      continue
    }
    seen.add(key)
    const sourceUrl = getReviewPageImageUrl(image?.url || '', key)
    const sourceThumbUrl = getThumbnailPageImageUrl(image?.url || '', key)
    ensureEntry(key, {
      name: image?.name || key,
      sourceUrl,
      blankUrl: sourceUrl,
      sourceThumbUrl,
      blankThumbUrl: sourceThumbUrl,
      pageNumber: order.length + 1
    })
  }

  for (const page of mergedInspectionPages.value) {
    const sourceImagePath = page?.source_image_url || page?.image_url || page?.base_image_url || ''
    const blankImagePath = page?.base_image_url || page?.source_image_url || page?.image_url || ''
    const translatedImagePath = page?.translated_image_url || page?.image_url || ''
    const finalUrl = getSavedTranslatedImageUrl(page) || getReviewPageImageUrl(translatedImagePath, page?.stored_name)
    const previewUrl = getCanvasPreviewImageUrl(page) || getSavedTranslatedImageUrl(page) || getReviewPageImageUrl(translatedImagePath, page?.stored_name)
    ensureEntry(page?.stored_name, {
      name: page?.name || page?.stored_name || '未命名页面',
      sourceUrl: getReviewPageImageUrl(sourceImagePath, page?.stored_name),
      blankUrl: getReviewPageImageUrl(blankImagePath, page?.stored_name),
      finalUrl,
      previewUrl,
      sourceThumbUrl: getThumbnailPageImageUrl(sourceImagePath, page?.stored_name),
      blankThumbUrl: getThumbnailPageImageUrl(blankImagePath, page?.stored_name),
      finalThumbUrl: getSavedTranslatedImageUrl(page, IMAGE_THUMBNAIL_MAX_SIDE) || getThumbnailPageImageUrl(translatedImagePath, page?.stored_name),
      previewThumbUrl: getCanvasPreviewImageUrl(page, IMAGE_THUMBNAIL_MAX_SIDE) || getSavedTranslatedImageUrl(page, IMAGE_THUMBNAIL_MAX_SIDE) || getThumbnailPageImageUrl(translatedImagePath, page?.stored_name),
      regionCount: Number(page?.regions?.length || 0),
      reviewReady: true
    })
  }

  for (const image of translatedImages.value) {
    const key = String(image?.stored_name || image?.name || '').trim()
    const finalUrl = getReviewPageImageUrl(image?.url || '', key)
    const finalThumbUrl = getThumbnailPageImageUrl(image?.url || '', key)
    const entry = ensureEntry(key, {
      name: image?.name || key,
      finalUrl,
      previewUrl: finalUrl,
      finalThumbUrl,
      previewThumbUrl: finalThumbUrl
    })
    if (entry && !entry.blankUrl) {
      entry.blankUrl = entry.sourceUrl || entry.finalUrl
    }
    if (entry && !entry.blankThumbUrl) {
      entry.blankThumbUrl = entry.sourceThumbUrl || entry.finalThumbUrl
    }
  }

  return order.map((key) => {
    const entry = mapped.get(key)
    const fallback = (
      entry?.sourceThumbUrl
      || entry?.blankThumbUrl
      || entry?.finalThumbUrl
      || entry?.previewThumbUrl
      || entry?.sourceUrl
      || entry?.blankUrl
      || entry?.finalUrl
      || entry?.previewUrl
      || ''
    )
    const status = entry?.finalUrl
      ? '已完成'
      : entry?.reviewReady
        ? '可审校'
        : workflowStage.value === 'detecting' || workflowStage.value === 'translating'
          ? '处理中'
          : '待处理'
    return {
      ...entry,
      coverUrl: fallback,
      status,
      selected: String(selectedEditPageKey.value || '').trim() === key
    }
  })
})
const v2FilteredPageEntries = computed(() => {
  const search = String(v2PageSearch.value || '').trim().toLowerCase()
  if (!search) {
    return v2PageEntries.value
  }
  return v2PageEntries.value.filter((page) => (
    [page.name, page.stored_name, page.status]
      .map((item) => String(item || '').toLowerCase())
      .join(' ')
      .includes(search)
  ))
})
const v2SelectedPageEntry = computed(() => {
  const preferredKey = String(selectedEditPageKey.value || '').trim()
  if (preferredKey) {
    const matched = v2PageEntries.value.find((page) => page.stored_name === preferredKey)
    if (matched) {
      return matched
    }
  }
  return v2PageEntries.value[0] || null
})
const v2EditorPaneImageUrl = computed(() => {
  const entry = v2SelectedPageEntry.value
  return (
    entry?.blankUrl
    || selectedEditPageMainImageUrl.value
    || entry?.sourceUrl
    || selectedEditPageThumbnailUrl.value
    || ''
  )
})
const selectedReviewComparePanes = computed(() => {
  const selectedModes = normalizeReviewComparePaneModes(reviewWorkspacePrefs.value.compare_pane_modes, reviewWorkspacePrefs.value.compare_pane_mode)
  return reviewComparePaneOptions.filter((option) => selectedModes.includes(option.key))
})
const selectedReviewComparePaneCount = computed(() => selectedReviewComparePanes.value.length)
const v2ReviewPaneStripStyle = computed(() => ({
  '--review-pane-count': Math.max(2, selectedReviewComparePaneCount.value)
}))
const firstReadonlyReviewComparePaneKey = computed(() => (
  selectedReviewComparePanes.value.find((pane) => pane.key !== 'frame')?.key || ''
))
const v2SelectedPageSummary = computed(() => {
  const entry = v2SelectedPageEntry.value
  if (!entry) {
    return '尚未选择页面'
  }
  return `第 ${entry.pageNumber} 页`
})
const v2ReviewPrimaryLabel = computed(() => {
  if (translating.value) {
    return '处理中…'
  }
  if (canContinueSegmentedTranslation.value) {
    return '继续翻译'
  }
  if (canTranslateCurrentPage.value) {
    return workflowStage.value === 'detected' ? '翻译本页' : '开始识别'
  }
  if (canRerender.value) {
    return '保存并重渲染'
  }
  return '开始翻译'
})
const v2FilteredProjectHistory = computed(() => {
  const search = String(v2HistorySearch.value || '').trim().toLowerCase()
  const sorter = String(v2HistorySort.value || 'recent')
  const filtered = projectHistory.value.filter((project) => {
    if (!search) {
      return true
    }
    return [
      project.title,
      project.project_id,
      project.note,
      workflowStageLabelMap[project.workflow_stage] || project.workflow_stage
    ]
      .map((item) => String(item || '').toLowerCase())
      .join(' ')
      .includes(search)
  })

  const ranked = [...filtered].sort((left, right) => {
    if (sorter === 'title') {
      return String(left.title || '').localeCompare(String(right.title || ''), 'zh-Hans-CN')
    }
    return Date.parse(right.updated_at || right.created_at || 0) - Date.parse(left.updated_at || left.created_at || 0)
  })

  return ranked
})
const v2HomePreviewPages = computed(() => {
  if (v2PageEntries.value.length) {
    return v2PageEntries.value.slice(0, 4)
  }
  return v2PlaceholderThumbs.map((coverUrl, index) => ({
    stored_name: `placeholder-${index}`,
    name: `示例 ${index + 1}`,
    coverUrl,
    status: index < 2 ? '审校示意' : '页面示意',
    pageNumber: index + 1,
    regionCount: index < 2 ? 8 + index : 0
  }))
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
    if (isPreviewFontDecodeDenied(font)) {
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
    if (activeAction.value === 'translate-page') {
      return '本页翻译中...'
    }
    if (activeAction.value === 'resume-translate') {
      return '翻译进行中...'
    }
    return '翻译进行中...'
  }

  if (workflowStage.value === 'detected') {
    return '继续翻译'
  }
  if (config.value.pause_after_detection) {
    return workflowStage.value === 'translated' ? '重新识别' : '开始识别'
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

  return `${apiBaseUrl.value}${path.startsWith('/') ? path : `/${path}`}`
}

async function readApiJson(response, fallbackMessage) {
  const rawText = await response.text()
  if (!rawText) {
    return {}
  }

  try {
    return JSON.parse(rawText)
  } catch (_error) {
    if (!response.ok) {
      throw new Error(rawText.slice(0, 240) || fallbackMessage)
    }
    throw new Error(`${fallbackMessage}：后端返回了无法解析的响应。`)
  }
}

function toWebSocketUrl(path) {
  const url = new URL(toApiUrl(path))
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  return url.toString()
}

function withUrlQueryParam(url, key, value) {
  if (!url || value === undefined || value === null || value === '') {
    return url || ''
  }

  try {
    const parsedUrl = new URL(url, typeof window !== 'undefined' ? window.location.origin : 'http://localhost')
    parsedUrl.searchParams.set(key, String(value))
    return parsedUrl.toString()
  } catch (_error) {
    const [pathAndQuery, hash = ''] = String(url).split('#')
    const [path, query = ''] = pathAndQuery.split('?')
    const params = new URLSearchParams(query)
    params.set(key, String(value))
    const nextQuery = params.toString()
    return `${path}${nextQuery ? `?${nextQuery}` : ''}${hash ? `#${hash}` : ''}`
  }
}

function withImagePreviewSize(url, maxSide) {
  const normalizedMaxSide = Number(maxSide || 0)
  if (!url || !Number.isFinite(normalizedMaxSide) || normalizedMaxSide <= 0) {
    return url || ''
  }
  return withUrlQueryParam(url, 'max_side', Math.round(normalizedMaxSide))
}

function withCacheBust(url) {
  if (!url) {
    return ''
  }
  return withUrlQueryParam(url, 't', renderNonce.value)
}

function withExplicitCacheBust(url, nonce) {
  if (!url || !nonce) {
    return url || ''
  }
  return withUrlQueryParam(url, 't', nonce)
}

function getPageImageNonce(pageId) {
  const normalizedPageId = String(pageId || '').trim()
  return normalizedPageId ? pageImageNonces.value[normalizedPageId] : ''
}

function getVersionedPageImageUrl(path, pageId, options = {}) {
  const maxSide = typeof options === 'number' ? options : options?.maxSide
  const url = withImagePreviewSize(toApiUrl(String(path || '').trim()), maxSide)
  if (!url) {
    return ''
  }
  return withExplicitCacheBust(url, getPageImageNonce(pageId))
}

function getThumbnailPageImageUrl(path, pageId) {
  return getVersionedPageImageUrl(path, pageId, { maxSide: IMAGE_THUMBNAIL_MAX_SIDE })
}

function getReviewPageImageUrl(path, pageId) {
  return getVersionedPageImageUrl(path, pageId, { maxSide: IMAGE_REVIEW_MAX_SIDE })
}

function markPageImageUpdated(pageId, timestamp = Date.now()) {
  const normalizedPageId = String(pageId || '').trim()
  if (!normalizedPageId) {
    return
  }
  pageImageNonces.value = {
    ...pageImageNonces.value,
    [normalizedPageId]: timestamp
  }
}

function markTranslatedPayloadImagesUpdated(images = [], options = {}) {
  if (!Array.isArray(images) || !images.length) {
    return
  }
  const onlyPageId = String(options.onlyPageId || '').trim()
  const shouldRefreshAll = Boolean(options.refreshAll)
  const timestamp = Date.now()
  const nextNonces = { ...pageImageNonces.value }
  let changed = false

  for (const image of images) {
    const storedName = String(image?.stored_name || '').trim()
    if (!storedName) {
      continue
    }
    if (onlyPageId && storedName !== onlyPageId) {
      continue
    }
    if (!shouldRefreshAll && !onlyPageId && nextNonces[storedName]) {
      continue
    }
    nextNonces[storedName] = timestamp
    changed = true
  }

  if (changed) {
    pageImageNonces.value = nextNonces
  }
}

function preloadImageUrl(url) {
  const normalizedUrl = String(url || '').trim()
  if (!normalizedUrl || preloadedImageUrls.has(normalizedUrl) || typeof window === 'undefined') {
    return
  }
  preloadedImageUrls.add(normalizedUrl)
  const image = new window.Image()
  image.src = normalizedUrl
}

function preloadReviewImagesAroundPage(pageKey = '') {
  const entries = v2PageEntries.value
  if (!entries.length) {
    return
  }
  const normalizedPageKey = String(pageKey || selectedEditPageKey.value || '').trim()
  const currentIndex = Math.max(0, entries.findIndex((entry) => entry.stored_name === normalizedPageKey))
  const preloadIndexes = new Set([
    currentIndex,
    Math.max(0, currentIndex - 1),
    Math.min(entries.length - 1, currentIndex + 1)
  ])

  for (const index of preloadIndexes) {
    const entry = entries[index]
    if (!entry) {
      continue
    }
    preloadImageUrl(entry.sourceUrl)
    preloadImageUrl(entry.blankUrl)
    preloadImageUrl(entry.finalUrl)
    preloadImageUrl(entry.previewUrl)
  }
}

async function loadAppRuntime() {
  try {
    if (desktopBridge && typeof desktopBridge.getRuntime === 'function') {
      const bridgeRuntime = await desktopBridge.getRuntime()
      const resolvedBaseUrl = String(bridgeRuntime?.apiBaseUrl || bridgeRuntime?.backend_base_url || '').trim()
      if (resolvedBaseUrl) {
        apiBaseUrl.value = resolvedBaseUrl.replace(/\/$/, '')
      }
      appRuntime.value = {
        ...appRuntime.value,
        ...(bridgeRuntime || {}),
        backend_base_url: apiBaseUrl.value,
      }
    }
  } catch (error) {
    console.warn('Failed to read desktop runtime bridge.', error)
  }

  try {
    const response = await fetch(toApiUrl('/api/app/runtime'))
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '读取应用运行时信息失败')
    }
    appRuntime.value = {
      ...emptyRuntimeInfo,
      ...(desktopBridge?.runtime || {}),
      ...(payload.runtime || {})
    }
    const resolvedBaseUrl = String(
      payload?.runtime?.backend_base_url
      || payload?.runtime?.apiBaseUrl
      || appRuntime.value?.backend_base_url
      || apiBaseUrl.value
      || ''
    ).trim()
    if (resolvedBaseUrl) {
      apiBaseUrl.value = resolvedBaseUrl.replace(/\/$/, '')
    }
    appRuntime.value.backend_base_url = apiBaseUrl.value
    migrationModalOpen.value = Boolean(isDesktopRuntime.value && appRuntime.value?.migration?.needed)
  } catch (error) {
    console.warn('Failed to load app runtime.', error)
  }
}

async function loadAppDiagnostics() {
  try {
    const response = await fetch(toApiUrl('/api/app/diagnostics'))
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '读取运行时诊断失败')
    }
    appDiagnostics.value = {
      ...emptyDiagnosticsInfo,
      ...(payload.diagnostics || {})
    }
  } catch (error) {
    console.warn('Failed to load app diagnostics.', error)
  }
}

async function loadPersistedAppSettings() {
  appSettingsLoading.value = true
  try {
    const response = await fetch(toApiUrl('/api/app/settings'))
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '读取设置失败')
    }
    const nextSettings = payload.settings || {}
    config.value = mergeProjectConfigWithLocalPreferences(nextSettings, config.value)
    appSettingsLoaded.value = true
    const translatorNeedsKey = ['gemini', 'doubao-ark'].includes(String(nextSettings?.translator || config.value.translator || ''))
    const hasPrimaryKey = Boolean(String(nextSettings?.api_key || config.value.api_key || '').trim())
    onboardingOpen.value = isDesktopRuntime.value && (
      !Boolean(appRuntime.value?.settings_exists)
      || (translatorNeedsKey && !hasPrimaryKey)
    )
  } catch (error) {
    console.warn('Failed to load persisted settings.', error)
  } finally {
    appSettingsLoading.value = false
  }
}

let persistAppSettingsTimer = null
async function persistAppSettings(value) {
  try {
    const response = await fetch(toApiUrl('/api/app/settings'), {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(value)
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '保存设置失败')
    }
    appRuntime.value = {
      ...appRuntime.value,
      settings_exists: true
    }
    appSettingsLoaded.value = true
  } catch (error) {
    console.warn('Failed to persist app settings.', error)
  }
}

function queuePersistAppSettings(value) {
  if (!appSettingsLoaded.value) {
    return
  }
  if (persistAppSettingsTimer) {
    window.clearTimeout(persistAppSettingsTimer)
  }
  appSettingsSaving.value = true
  persistAppSettingsTimer = window.setTimeout(async () => {
    persistAppSettingsTimer = null
    await persistAppSettings(normalizeStoredConfig(value))
    appSettingsSaving.value = false
  }, 250)
}

async function validateCurrentSettings() {
  appSettingsValidation.value = { ok: null, message: '正在验证…', preview: '' }
  try {
    const response = await fetch(toApiUrl('/api/app/settings/validate'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(config.value)
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || payload.message || '验证失败')
    }
    appSettingsValidation.value = {
      ok: Boolean(payload.ok),
      message: String(payload.message || (payload.ok ? '连接正常。' : '验证失败')),
      preview: String(payload.preview || '')
    }
    if (payload.ok) {
      onboardingOpen.value = false
      status.value = '设置验证成功，已经可以开始使用。'
    }
  } catch (error) {
    appSettingsValidation.value = {
      ok: false,
      message: error instanceof Error ? error.message : '验证失败',
      preview: ''
    }
  }
}

async function handleLegacyMigration(action) {
  try {
    const response = await fetch(toApiUrl('/api/app/migrate-legacy'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action })
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '处理旧数据失败')
    }
    appRuntime.value = {
      ...appRuntime.value,
      migration: payload.migration || appRuntime.value.migration
    }
    migrationModalOpen.value = false
    status.value = action === 'migrate' ? '旧项目数据已迁移到应用数据目录。' : '已跳过旧数据迁移。'
    if (action === 'migrate') {
      await loadProjectHistory({ silent: true })
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '处理旧数据失败'
  }
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
    const socketToClose = socket
    socket = null
    expectedClosingSockets.add(socketToClose)
    socketToClose.close()
  }
}

function clearTranslationCompletionRecoveryTimer() {
  if (translationCompletionRecoveryTimer != null) {
    window.clearTimeout(translationCompletionRecoveryTimer)
    translationCompletionRecoveryTimer = null
  }
}

function resetTranslationCompletionRecovery() {
  translationCompletionRecoveryToken += 1
  clearTranslationCompletionRecoveryTimer()
}

function getCompletedTranslationStatus(completedAction, pageTargetStoredName = '') {
  if (completedAction === 'detect') {
    return '文本框识别完成。现在可以先逐框确认、手动加框或保留原文，确认后再继续翻译。'
  }
  if (completedAction === 'translate-page') {
    return '当前页翻译完成，结果已回填到工作台。'
  }
  if (completedAction === 'resume-translate') {
    return `翻译完成，共输出 ${translatedImages.value.length} 张图片。`
  }
  if (completedAction === 'rerender') {
    return pageTargetStoredName
      ? '当前页重嵌字完成。'
      : `重嵌字完成，共输出 ${progress.value.total} 张图片。`
  }
  return `翻译完成，共输出 ${translatedImages.value.length} 张图片。`
}

function markCompletedTranslationAction(completedAction, pageTargetStoredName = '') {
  if (completedAction === 'rerender') {
    if (pageTargetStoredName) {
      clearCanvasPreviewDirty(pageTargetStoredName)
    } else {
      clearCanvasPreviewDirty()
    }
  } else if (completedAction === 'translate-page') {
    if (pageTargetStoredName) {
      clearCanvasPreviewDirty(pageTargetStoredName)
    }
  } else if (completedAction === 'resume-translate' || completedAction === 'translate') {
    clearCanvasPreviewDirty()
  }
}

function scheduleTranslationCompletionRecovery(context, delayMs = TRANSLATION_COMPLETION_RECOVERY_DELAY_MS) {
  if (!translating.value) {
    return
  }
  clearTranslationCompletionRecoveryTimer()
  const token = translationCompletionRecoveryToken
  translationCompletionRecoveryTimer = window.setTimeout(() => {
    translationCompletionRecoveryTimer = null
    void recoverCompletedTranslationIfIdle(context, token)
  }, delayMs)
}

async function recoverCompletedTranslationIfIdle(context = {}, token = translationCompletionRecoveryToken) {
  if (token !== translationCompletionRecoveryToken || !translating.value) {
    return
  }

  const projectId = String(context.sessionId || sessionId.value || '').trim()
  if (!projectId) {
    return
  }

  const attempt = Number(context.attempt || 1)
  try {
    const projectsResponse = await fetch(toApiUrl('/api/projects'))
    const projectsPayload = await projectsResponse.json()
    if (projectsResponse.ok) {
      const project = (projectsPayload.projects || []).find((item) => String(item?.project_id || '') === projectId)
      if (project && project.is_busy) {
        if (attempt < TRANSLATION_COMPLETION_RECOVERY_MAX_ATTEMPTS) {
          scheduleTranslationCompletionRecovery({ ...context, attempt: attempt + 1 }, TRANSLATION_COMPLETION_RECOVERY_RETRY_MS)
        }
        return
      }
    }

    const restoreResponse = await fetch(toApiUrl(`/api/projects/${encodeURIComponent(projectId)}/restore`), {
      method: 'POST'
    })
    const payload = await restoreResponse.json()
    if (!restoreResponse.ok) {
      throw new Error(payload.detail || '恢复任务完成状态失败')
    }
    if (payload?.project?.is_busy) {
      if (attempt < TRANSLATION_COMPLETION_RECOVERY_MAX_ATTEMPTS) {
        scheduleTranslationCompletionRecovery({ ...context, attempt: attempt + 1 }, TRANSLATION_COMPLETION_RECOVERY_RETRY_MS)
      }
      return
    }

    if (token !== translationCompletionRecoveryToken || !translating.value) {
      return
    }

    const completedAction = context.action || activeAction.value
    const pageTargetStoredName = String(context.pageTargetStoredName || '').trim()
    resetTranslationCompletionRecovery()
    translating.value = false
    errorMessage.value = ''
    applySessionPayload(payload, {
      refreshTranslatedPageId: (completedAction === 'rerender' || completedAction === 'translate-page') ? pageTargetStoredName : '',
      refreshAllTranslatedImages: completedAction === 'translate' || completedAction === 'resume-translate' || (completedAction === 'rerender' && !pageTargetStoredName)
    })
    markCompletedTranslationAction(completedAction, pageTargetStoredName)
    status.value = getCompletedTranslationStatus(completedAction, pageTargetStoredName)
    void loadEditInspection({ silent: completedAction !== 'detect' }).catch((error) => {
      console.warn('[TranslationRecovery] review inspector refresh failed', error)
    })
    if (config.value.font_style_mode === 'auto-map') {
      void loadStyleInspection({ silent: true }).catch((error) => {
        console.warn('[TranslationRecovery] style inspector refresh failed', error)
      })
    }
    void loadProjectHistory({ silent: true })
    closeSocket()
  } catch (error) {
    console.warn('[TranslationRecovery] completion recovery failed', error)
    if (token === translationCompletionRecoveryToken && translating.value && attempt < TRANSLATION_COMPLETION_RECOVERY_MAX_ATTEMPTS) {
      scheduleTranslationCompletionRecovery({ ...context, attempt: attempt + 1 }, TRANSLATION_COMPLETION_RECOVERY_RETRY_MS)
    }
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
  canvasRegionSelection.value = {}
  canvasMarqueeState.value = null
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
  canvasRegionSelection.value = {}
  canvasMarqueeState.value = null
  viewportPanState.value = null
  compareSplitterState.value = null
  mergeRegionSelection.value = {}
  mergeMode.value = false
  translationInputDrafts.value = {}
  fontSizeInputDrafts.value = {}
  fontSizeDraftOriginOverrides.value = {}
  pageEditHistory.value = {}
  perPageUiState.value = {}
  autoFitCanvasPageIds = new Set()
  regionListSearch.value = ''
  regionListFilter.value = 'all'
}

function normalizeHistoryProject(project) {
  return {
    ...project,
    cover_image: project?.cover_image ? toApiUrl(project.cover_image) : '',
    updated_at: project?.updated_at || '',
    created_at: project?.created_at || '',
    title: project?.title || project?.project_id || '未命名项目',
    note: project?.note || '',
    review_mode: 'canvas_beta',
    workflow_stage: project?.workflow_stage || 'idle',
    page_count: Number(project?.page_count || 0),
    is_busy: Boolean(project?.is_busy),
    busy_action: project?.busy_action || ''
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

function getV2PlaceholderThumb(index = 0) {
  const safeIndex = Math.abs(Number(index || 0)) % v2PlaceholderThumbs.length
  return v2PlaceholderThumbs[safeIndex]
}

function getV2ProjectCover(project, index = 0) {
  return String(project?.cover_image || '').trim() || getV2PlaceholderThumb(index)
}

function getV2PageCover(page, index = 0) {
  return String(page?.coverUrl || '').trim() || getV2PlaceholderThumb(index)
}

function handleV2ImageError(event, index = 0) {
  const target = event?.target
  if (!target || typeof target.src !== 'string') {
    return
  }
  const fallback = getV2PlaceholderThumb(index)
  if (target.src !== fallback) {
    target.src = fallback
  }
}

function formatV2Timestamp(value) {
  const timestamp = Date.parse(String(value || ''))
  if (!Number.isFinite(timestamp)) {
    return '刚刚更新'
  }
  const date = new Date(timestamp)
  const delta = Date.now() - timestamp
  const minute = 60 * 1000
  const hour = 60 * minute
  const day = 24 * hour
  if (delta < hour) {
    return `${Math.max(1, Math.round(delta / minute))} 分钟前`
  }
  if (delta < day) {
    return `${Math.max(1, Math.round(delta / hour))} 小时前`
  }
  const formatter = new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  })
  return formatter.format(date)
}

function formatV2TimeOnly(value) {
  const timestamp = Date.parse(String(value || ''))
  if (!Number.isFinite(timestamp)) {
    return '刚刚'
  }
  return new Intl.DateTimeFormat('zh-CN', {
    hour: '2-digit',
    minute: '2-digit'
  }).format(new Date(timestamp))
}

function formatBytes(value) {
  const bytes = Number(value) || 0
  if (bytes <= 0) {
    return '0 B'
  }
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const exponent = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const formatted = bytes / (1024 ** exponent)
  return `${formatted >= 100 || exponent === 0 ? Math.round(formatted) : formatted.toFixed(1)} ${units[exponent]}`
}

function normalizeProjectGlossaryCategory(value) {
  const normalized = String(value || '').trim()
  return projectGlossaryCategoryOptions.includes(normalized) ? normalized : '其他'
}

function createEmptyGlossaryEntry() {
  return {
    id: `local-term-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    source: '',
    translation: '',
    category: '其他',
    replacement: '',
    note: '',
    source_kind: 'user',
    occurrence_count: 0,
    occurrences: []
  }
}

function normalizeProjectGlossaryEntry(entry = {}) {
  return {
    id: String(entry.id || `local-term-${Date.now()}-${Math.random().toString(16).slice(2)}`).trim(),
    source: String(entry.source || entry.term || '').trim(),
    translation: String(entry.translation || entry.target || '').trim(),
    category: normalizeProjectGlossaryCategory(entry.category),
    replacement: String(entry.replacement || entry.replace_text || '').trim(),
    note: String(entry.note || '').trim(),
    source_kind: String(entry.source_kind || entry.kind || 'user').trim() || 'user',
    occurrence_count: entry.occurrence_count === null || typeof entry.occurrence_count === 'undefined'
      ? null
      : Number(entry.occurrence_count || 0),
    occurrences: Array.isArray(entry.occurrences) ? entry.occurrences : []
  }
}

function normalizeProjectGlossary(value = {}) {
  const entries = Array.isArray(value?.entries)
    ? value.entries.map((entry) => normalizeProjectGlossaryEntry(entry)).filter((entry) => entry.source || entry.translation)
    : []
  return {
    version: Number(value?.version || 1),
    updated_at: value?.updated_at || '',
    occurrences_loaded: Boolean(value?.occurrences_loaded),
    entries
  }
}

function syncGlossaryDraftFromProject(nextGlossary = projectGlossary.value) {
  glossaryDraftEntries.value = normalizeProjectGlossary(nextGlossary).entries.map((entry) => ({ ...entry }))
}

function getGlossaryRequestEntries() {
  return glossaryDraftEntries.value
    .map((entry) => normalizeProjectGlossaryEntry(entry))
    .filter((entry) => entry.source && entry.translation)
}

function getGlossaryEntrySourceKindLabel(entry) {
  return entry?.source_kind === 'system' ? '系统提取' : '用户维护'
}

function getGlossaryOccurrenceLabel(entry) {
  if (!projectGlossaryOccurrencesLoaded.value) {
    return '未扫描'
  }
  const count = Number(entry?.occurrence_count || 0)
  return count > 0 ? `${count} 处` : '未匹配'
}

function getGlossaryPreviewSummary() {
  const changeCount = Number(glossaryPreview.value?.change_count || 0)
  const pageCount = Number(glossaryPreview.value?.affected_page_count || 0)
  if (changeCount <= 0) {
    return '当前没有可应用的明确替换。'
  }
  return `将修改 ${changeCount} 个文本框，影响 ${pageCount} 页。`
}

function isProjectBusy(project) {
  const projectId = String(project?.project_id || '').trim()
  if (!projectId) {
    return false
  }
  return Boolean(project?.is_busy) || (Boolean(activeTaskProjectId.value) && activeTaskProjectId.value === projectId)
}

function getBusyActionLabel(project) {
  const action = String(project?.busy_action || '').trim().toLowerCase()
  if (action === 'detect') {
    return '识别中'
  }
  if (action === 'rerender') {
    return '重嵌中'
  }
  if (action === 'translate-page') {
    return '本页翻译中'
  }
  if (action === 'resume-translate') {
    return '继续翻译中'
  }
  if (action === 'glossary') {
    return '名词库处理中'
  }
  return '翻译中'
}

function canRestoreHistoryProject(project) {
  return !translating.value && !isProjectBusy(project) && (!restoringProjectId.value || restoringProjectId.value === project?.project_id)
}

function canDeleteHistoryProject(project) {
  return !isProjectBusy(project) && (!deletingProjectId.value || deletingProjectId.value === project?.project_id)
}

function canRestoreHistorySnapshot(project, snapshot) {
  return !translating.value && !isProjectBusy(project) && (!restoringSnapshotId.value || restoringSnapshotId.value === snapshot?.snapshot_id)
}

function applySessionPayload(payload, options = {}) {
  const resetInspectors = Boolean(options.resetInspectors)
  const nextSessionId = payload?.session_id || ''
  const sessionChanged = String(nextSessionId || '') !== String(sessionId.value || '')
  const resetEditingState = options.resetEditingState ?? (sessionChanged || resetInspectors)
  const forceRegionIds = Array.isArray(options.forceRegionIds)
    ? options.forceRegionIds.map((regionId) => String(regionId || '').trim()).filter(Boolean)
    : []
  renderNonce.value = Date.now()
  sessionId.value = nextSessionId
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
  markTranslatedPayloadImagesUpdated(payload?.translated_images || [], {
    onlyPageId: options.refreshTranslatedPageId || '',
    refreshAll: sessionChanged || resetInspectors || Boolean(options.refreshAllTranslatedImages)
  })
  translatedImages.value = (payload?.translated_images || []).map((image, index) => ({
    id: image.id || `${sessionId.value || 'session'}-translated-${index}`,
    name: image.name,
    url: getVersionedPageImageUrl(image.url, image.stored_name),
    stored_name: image.stored_name
  }))
  projectGlossary.value = normalizeProjectGlossary(payload?.glossary || {})
  if (!glossaryDrawerOpen.value || resetEditingState) {
    syncGlossaryDraftFromProject(projectGlossary.value)
    glossaryPreview.value = { changes: [], change_count: 0, affected_pages: [], affected_page_count: 0 }
    glossaryError.value = ''
  }

  const payloadConfig = payload?.config && typeof payload.config === 'object' ? payload.config : {}
  config.value = mergeProjectConfigWithLocalPreferences(payloadConfig, config.value)
  if (sessionChanged || resetInspectors) {
    reviewWorkspacePrefs.value = loadStoredReviewWorkspacePrefs(nextSessionId)
  }

  const overrides = payload?.overrides || {}
  if (resetEditingState) {
    translationRegionOverrides.value = { ...(overrides.translation_region_overrides || {}) }
    translationRegionSkipOverrides.value = { ...(overrides.translation_region_skip_overrides || {}) }
    translationRegionDisabledOverrides.value = { ...(overrides.translation_region_disabled_overrides || {}) }
    translationRegionLayoutOverrides.value = { ...(overrides.translation_region_layout_overrides || {}) }
    styleRegionOverrides.value = { ...(overrides.style_region_overrides || {}) }
    translationInputDrafts.value = {}
    fontSizeInputDrafts.value = {}
    fontSizeDraftOriginOverrides.value = {}
    pageEditHistory.value = {}
    pageCommandPendingCounts.value = {}
    regionCommitStates.value = {}
    canvasPreviewDirtyPages.value = {}
    perPageUiState.value = {}
    canvasRegionSelection.value = {}
    canvasMarqueeState.value = null
    autoFitCanvasPageIds = new Set()
    regionListSearch.value = ''
    regionListFilter.value = 'all'
  } else {
    if (forceRegionIds.length) {
      translationInputDrafts.value = omitRegionDrafts(translationInputDrafts.value, forceRegionIds)
      fontSizeInputDrafts.value = omitRegionDrafts(fontSizeInputDrafts.value, forceRegionIds)
      fontSizeDraftOriginOverrides.value = omitRegionDrafts(fontSizeDraftOriginOverrides.value, forceRegionIds)
    }
    applyInspectionOverrides(overrides, { forceRegionIds })
  }

  currentProject.value = payload?.project ? normalizeHistoryProject(payload.project) : null
  projectTitleDraft.value = currentProject.value?.title || ''
  projectNoteDraft.value = currentProject.value?.note || ''

  if (resetInspectors) {
    resetTranslationReview()
    resetStyleInspector()
    resetEditInspectorSelection()
    if (resetEditingState) {
      translationRegionOverrides.value = { ...(overrides.translation_region_overrides || {}) }
      translationRegionSkipOverrides.value = { ...(overrides.translation_region_skip_overrides || {}) }
      translationRegionDisabledOverrides.value = { ...(overrides.translation_region_disabled_overrides || {}) }
      translationRegionLayoutOverrides.value = { ...(overrides.translation_region_layout_overrides || {}) }
      styleRegionOverrides.value = { ...(overrides.style_region_overrides || {}) }
    }
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
  if (translating.value) {
    errorMessage.value = '当前有识别/翻译任务在运行，暂时不能切换到别的项目。'
    status.value = '请等待当前任务完成后，再恢复历史项目。'
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
    try {
      await loadEditInspection({ silent: true })
    } catch (inspectionError) {
      console.warn('[HistoryRestore] review inspector preload failed', inspectionError)
    }
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
  if (translating.value) {
    errorMessage.value = '当前有识别/翻译任务在运行，暂时不能恢复快照。'
    status.value = '请等待当前任务完成后，再从快照恢复项目。'
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
    try {
      await loadEditInspection({ silent: true })
    } catch (inspectionError) {
      console.warn('[HistoryRestore] snapshot inspector preload failed', inspectionError)
    }
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
  if (isProjectBusy(project)) {
    errorMessage.value = '该项目仍有任务在运行，暂时不能删除。'
    status.value = '请等待当前识别/翻译任务完成后，再删除这个项目。'
    return
  }
  const confirmed = typeof window === 'undefined'
    ? true
    : window.confirm(`确定要删除项目「${project.title}」吗？相关历史记录和输出文件会一起移除。`)
  if (!confirmed) {
    return
  }

  errorMessage.value = ''
  deletingProjectId.value = project.project_id
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
  } finally {
    deletingProjectId.value = ''
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

async function loadProjectGlossary(options = {}) {
  if (!sessionId.value) {
    projectGlossary.value = { version: 1, entries: [] }
    syncGlossaryDraftFromProject(projectGlossary.value)
    return
  }
  const silent = Boolean(options.silent)
  const includeOccurrences = Boolean(options.includeOccurrences)
  const occurrenceRefresh = Boolean(options.occurrenceRefresh)
  if (occurrenceRefresh) {
    glossaryOccurrencesLoading.value = true
  } else if (!silent) {
    glossaryLoading.value = true
  }
  glossaryError.value = ''
  try {
    const suffix = includeOccurrences ? '?include_occurrences=1' : '?include_occurrences=0'
    const response = await fetch(toApiUrl(`/api/projects/${sessionId.value}/glossary${suffix}`))
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '读取专有名词库失败')
    }
    projectGlossary.value = normalizeProjectGlossary(payload.glossary || {})
    syncGlossaryDraftFromProject(projectGlossary.value)
  } catch (error) {
    glossaryError.value = error instanceof Error ? error.message : '读取专有名词库失败'
  } finally {
    if (occurrenceRefresh) {
      glossaryOccurrencesLoading.value = false
    } else if (!silent) {
      glossaryLoading.value = false
    }
  }
}

async function refreshProjectGlossaryOccurrences() {
  await loadProjectGlossary({
    includeOccurrences: true,
    occurrenceRefresh: true
  })
}

async function openProjectGlossaryDrawer() {
  if (!sessionId.value) {
    return
  }
  glossaryDrawerOpen.value = true
  syncGlossaryDraftFromProject(projectGlossary.value)
  glossaryPreview.value = { changes: [], change_count: 0, affected_pages: [], affected_page_count: 0 }
  await loadProjectGlossary()
}

function closeProjectGlossaryDrawer() {
  glossaryDrawerOpen.value = false
  glossaryError.value = ''
}

function addGlossaryEntry() {
  glossaryDraftEntries.value = [...glossaryDraftEntries.value, createEmptyGlossaryEntry()]
}

function removeGlossaryEntry(entryId) {
  glossaryDraftEntries.value = glossaryDraftEntries.value.filter((entry) => entry.id !== entryId)
  glossaryPreview.value = { changes: [], change_count: 0, affected_pages: [], affected_page_count: 0 }
}

async function saveProjectGlossaryDraft(options = {}) {
  if (!sessionId.value) {
    return null
  }
  const silent = Boolean(options.silent)
  if (!silent) {
    glossarySaving.value = true
  }
  glossaryError.value = ''
  try {
    const response = await fetch(toApiUrl(`/api/projects/${sessionId.value}/glossary`), {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ entries: getGlossaryRequestEntries() })
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '保存专有名词库失败')
    }
    projectGlossary.value = normalizeProjectGlossary(payload.glossary || {})
    syncGlossaryDraftFromProject(projectGlossary.value)
    if (!silent) {
      status.value = '已保存专有名词库。'
    }
    return projectGlossary.value
  } catch (error) {
    glossaryError.value = error instanceof Error ? error.message : '保存专有名词库失败'
    return null
  } finally {
    if (!silent) {
      glossarySaving.value = false
    }
  }
}

async function extractProjectGlossary() {
  if (!sessionId.value || glossaryExtracting.value) {
    return
  }
  glossaryExtracting.value = true
  glossaryError.value = ''
  try {
    const response = await fetch(toApiUrl(`/api/projects/${sessionId.value}/glossary/extract`), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ config: buildRuntimeConfig() })
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '提取专有名词失败')
    }
    projectGlossary.value = normalizeProjectGlossary(payload.glossary || {})
    syncGlossaryDraftFromProject(projectGlossary.value)
    glossaryPreview.value = { changes: [], change_count: 0, affected_pages: [], affected_page_count: 0 }
    status.value = `已更新专有名词库，共 ${projectGlossary.value.entries.length} 个词条。`
  } catch (error) {
    glossaryError.value = error instanceof Error ? error.message : '提取专有名词失败'
  } finally {
    glossaryExtracting.value = false
  }
}

async function previewProjectGlossaryApplication() {
  if (!sessionId.value || glossaryPreviewing.value) {
    return
  }
  glossaryPreviewing.value = true
  glossaryError.value = ''
  try {
    const response = await fetch(toApiUrl(`/api/projects/${sessionId.value}/glossary/preview`), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ entries: getGlossaryRequestEntries() })
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '预览专有名词应用失败')
    }
    glossaryPreview.value = {
      changes: Array.isArray(payload.changes) ? payload.changes : [],
      change_count: Number(payload.change_count || 0),
      affected_pages: Array.isArray(payload.affected_pages) ? payload.affected_pages : [],
      affected_page_count: Number(payload.affected_page_count || 0)
    }
    status.value = getGlossaryPreviewSummary()
  } catch (error) {
    glossaryError.value = error instanceof Error ? error.message : '预览专有名词应用失败'
  } finally {
    glossaryPreviewing.value = false
  }
}

async function applyProjectGlossary() {
  if (!sessionId.value || glossaryApplying.value || translating.value) {
    return
  }
  glossaryApplying.value = true
  glossaryError.value = ''
  status.value = '正在应用专有名词库并重新嵌字…'
  try {
    const response = await fetch(toApiUrl(`/api/projects/${sessionId.value}/glossary/apply`), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ entries: getGlossaryRequestEntries() })
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '应用专有名词库失败')
    }
    glossaryPreview.value = {
      changes: Array.isArray(payload.changes) ? payload.changes : [],
      change_count: Number(payload.change_count || 0),
      affected_pages: Array.isArray(payload.affected_pages) ? payload.affected_pages : [],
      affected_page_count: Number(payload.affected_page_count || 0)
    }
    const changedRegionIds = glossaryPreview.value.changes
      .map((change) => String(change?.region_id || '').trim())
      .filter(Boolean)
    applySessionPayload(payload, {
      refreshAllTranslatedImages: true,
      resetEditingState: false,
      forceRegionIds: changedRegionIds
    })
    syncGlossaryDraftFromProject(projectGlossary.value)
    await loadEditInspection({ silent: true })
    void loadProjectHistory({ silent: true })
    status.value = Number(payload.change_count || 0) > 0
      ? `已应用专有名词库并重新嵌字，更新 ${payload.change_count} 个文本框。`
      : '已保存专有名词库，没有发现需要替换的文本框。'
  } catch (error) {
    glossaryError.value = error instanceof Error ? error.message : '应用专有名词库失败'
    status.value = glossaryError.value
  } finally {
    glossaryApplying.value = false
  }
}

async function jumpToGlossaryOccurrence(occurrence) {
  const pageId = String(occurrence?.page_id || '').trim()
  if (!pageId) {
    return
  }
  await selectEditPageForReview(pageId)
  const regionId = String(occurrence?.region_id || '').trim()
  if (regionId) {
    selectedEditRegionKey.value = regionId
    void scrollSelectedRegionCardIntoView()
  }
  closeProjectGlossaryDrawer()
  await nextTick()
  scheduleCanvasLayoutRefresh()
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

function isPreviewFontDecodeDenied(fontOrId) {
  const values = typeof fontOrId === 'object' && fontOrId
    ? [fontOrId.id, fontOrId.name, fontOrId.label, fontOrId.filename]
    : [fontOrId]
  return values.some((value) => previewFontDecodeDenyList.has(String(value || '').trim()))
}

function isPreviewFontIdDecodeDenied(fontId) {
  return isPreviewFontDecodeDenied(fontId) || isPreviewFontDecodeDenied(getFontById(fontId))
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
  const compatibleFonts = availableFonts.value.filter((font) => !isPreviewFontUnsupported(font.id) && !isPreviewFontDecodeDenied(font))
  const matchedFont = pickMappedStyleFont(compatibleFonts, preferredNames)
  return matchedFont?.id || ''
}

function getResolvedPreviewFontId(region) {
  const effectiveFontId = getEffectiveRegionFontId(region)
  if (effectiveFontId && !isPreviewFontUnsupported(effectiveFontId) && !isPreviewFontIdDecodeDenied(effectiveFontId)) {
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

function hasRegionWarning(region) {
  const regionText = String(getEditRegionText(region) || '').trim()
  const confidence = Number(region?.ocr_confidence || 0)
  return Boolean(
    (getEffectiveRegionFontId(region) && (
      isPreviewFontUnsupported(getEffectiveRegionFontId(region))
      || isPreviewFontIdDecodeDenied(getEffectiveRegionFontId(region))
    ))
    || !regionText
    || (Number.isFinite(confidence) && confidence > 0 && confidence < 0.72)
  )
}

function getPreviewFontOptionLabel(font) {
  if (!font) {
    return ''
  }
  return isPreviewFontUnsupported(font.id) || isPreviewFontDecodeDenied(font)
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

function getPageUiState(pageId) {
  const normalizedPageId = String(pageId || '').trim()
  if (!normalizedPageId) {
    return createDefaultPerPageUiState()
  }
  return perPageUiState.value[normalizedPageId] || createDefaultPerPageUiState()
}

function updatePageUiState(pageId, updater) {
  const normalizedPageId = String(pageId || '').trim()
  if (!normalizedPageId) {
    return
  }
  const current = getPageUiState(normalizedPageId)
  const nextState = typeof updater === 'function'
    ? updater({
        selectedRegionId: current.selectedRegionId || '',
        main: { ...current.main },
        compare: { ...current.compare }
      })
    : updater
  perPageUiState.value = {
    ...perPageUiState.value,
    [normalizedPageId]: nextState
  }
}

function prunePerPageUiState(pages) {
  const validPageIds = new Set((pages || []).map((page) => String(page?.stored_name || '').trim()).filter(Boolean))
  const nextState = {}
  for (const [pageId, value] of Object.entries(perPageUiState.value || {})) {
    if (validPageIds.has(pageId)) {
      nextState[pageId] = value
    }
  }
  perPageUiState.value = nextState
}

function getViewportState(pageId, pane = 'main') {
  const pageState = getPageUiState(pageId)
  return pageState[pane] || createDefaultPerPageUiState()[pane]
}

function clampCanvasZoom(value) {
  return Math.min(canvasZoomMax, Math.max(canvasZoomMin, Number(value) || 1))
}

function updateViewportState(pageId, pane, patch, options = {}) {
  const normalizedPane = pane === 'compare' ? 'compare' : 'main'
  updatePageUiState(pageId, (current) => {
    const currentViewport = current[normalizedPane] || createDefaultPerPageUiState()[normalizedPane]
    const nextViewport = {
      zoom: clampCanvasZoom(patch.zoom ?? currentViewport.zoom ?? 1),
      panX: Number(patch.panX ?? currentViewport.panX ?? 0),
      panY: Number(patch.panY ?? currentViewport.panY ?? 0)
    }
    const nextState = {
      ...current,
      [normalizedPane]: nextViewport
    }
    if (
      normalizedPane === 'main'
      && reviewWorkspacePrefs.value.compare_sync_enabled
      && options.syncCompare !== false
    ) {
      nextState.compare = { ...nextViewport }
    }
    return nextState
  })
}

function resetViewportStateForPage(page) {
  const pageId = String(page?.stored_name || '').trim()
  if (!pageId) {
    return
  }
  updatePageUiState(pageId, (current) => ({
    ...current,
    main: { zoom: 1, panX: 0, panY: 0 },
    compare: reviewWorkspacePrefs.value.compare_sync_enabled
      ? { zoom: 1, panX: 0, panY: 0 }
      : { ...(current.compare || createDefaultPerPageUiState().compare), zoom: 1, panX: 0, panY: 0 }
  }))
}

function focusSelectedRegionInViewport(page, pane = 'main') {
  const region = selectedEditRegion.value
  if (!page || !region) {
    return
  }
  const shell = getCanvasShellForPane(pane)
  if (!shell) {
    return
  }
  const geometry = getCanvasGeometryFromSurface(shell, page, pane)
  const safeWidth = Math.max(canvasShellMetrics.value[pane]?.width || geometry.stageWidth || 0, 1)
  const safeHeight = Math.max(canvasShellMetrics.value[pane]?.height || geometry.stageHeight || 0, 1)
  const [x1, y1, x2, y2] = getEffectiveRegionBBox(region)
  const regionWidth = Math.max(8, x2 - x1)
  const regionHeight = Math.max(8, y2 - y1)
  const imageWidth = Math.max(page.image_width || 1, 1)
  const imageHeight = Math.max(page.image_height || 1, 1)
  const fitZoom = Math.min(
    4,
    Math.max(
      1,
      Math.min(
        (safeWidth * 0.72) / ((regionWidth / imageWidth) * safeWidth),
        (safeHeight * 0.72) / ((regionHeight / imageHeight) * safeHeight),
      )
    )
  )
  const centerX = (x1 + x2) / 2
  const centerY = (y1 + y2) / 2
  const shellCenterX = (geometry.shellWidth / 2) - geometry.stageOffsetLeft
  const shellCenterY = (geometry.shellHeight / 2) - geometry.stageOffsetTop
  updateViewportState(page.stored_name, pane, {
    zoom: fitZoom,
    panX: shellCenterX - ((centerX / imageWidth) * geometry.stageWidth * fitZoom),
    panY: shellCenterY - ((centerY / imageHeight) * geometry.stageHeight * fitZoom)
  })
}

function getCanvasStageBaseStyle(page, pane = 'main') {
  const imageWidth = Math.max(page?.image_width || 1, 1)
  const imageHeight = Math.max(page?.image_height || 1, 1)
  const metrics = canvasShellMetrics.value[pane] || {}
  const availableWidth = Math.max(Number(metrics.width || 0), 0)
  const availableHeight = Math.max(Number(metrics.height || 0), 0)
  if (!availableWidth || !availableHeight) {
    return {
      aspectRatio: `${imageWidth} / ${imageHeight}`
    }
  }

  const baseScale = Math.min(availableWidth / imageWidth, availableHeight / imageHeight)
  return {
    width: `${Math.max(1, Math.floor(imageWidth * baseScale))}px`,
    height: `${Math.max(1, Math.floor(imageHeight * baseScale))}px`,
    aspectRatio: `${imageWidth} / ${imageHeight}`
  }
}

function getCanvasViewportStyle(page, pane = 'main') {
  const pageId = String(page?.stored_name || '').trim()
  const viewport = getViewportState(pageId, pane)
  return {
    transform: `translate(${viewport.panX}px, ${viewport.panY}px) scale(${viewport.zoom})`,
    transformOrigin: 'top left'
  }
}

function getCanvasStageStyle(page, pane = 'main') {
  return {
    ...getCanvasStageBaseStyle(page, pane),
    ...getCanvasViewportStyle(page, pane)
  }
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
  if (!isVerticalRegion(region)) {
    return 'left'
  }
  const normalized = String(region?.alignment || config.value.render_alignment || 'left')
    .trim()
    .toLowerCase()
  if (normalized === 'center' || normalized === 'right' || normalized === 'left') {
    return normalized
  }
  return 'left'
}

function colorTripletToCss(value, fallback = '#152234') {
  if (!Array.isArray(value) || value.length < 3) {
    return fallback
  }
  const channels = value.slice(0, 3).map((channel) => {
    const parsed = Number(channel)
    if (!Number.isFinite(parsed)) {
      return 0
    }
    return Math.max(0, Math.min(255, Math.round(parsed)))
  })
  return `rgb(${channels[0]}, ${channels[1]}, ${channels[2]})`
}

function clampNumber(value, min, max, fallback = min) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) {
    return fallback
  }
  return Math.min(max, Math.max(min, parsed))
}

function normalizeHexColor(value, fallback = '#000000') {
  let normalized = String(value || '').trim()
  if (!normalized) {
    return fallback
  }
  if (!normalized.startsWith('#')) {
    normalized = `#${normalized}`
  }
  if (/^#[0-9a-fA-F]{3}$/.test(normalized)) {
    normalized = `#${normalized.slice(1).split('').map((char) => char + char).join('')}`
  }
  if (!/^#[0-9a-fA-F]{6}$/.test(normalized)) {
    return fallback
  }
  return normalized.toLowerCase()
}

function colorTripletToHex(value, fallback = '#000000') {
  if (!Array.isArray(value) || value.length < 3) {
    return fallback
  }
  const channels = value.slice(0, 3).map((channel) => {
    const parsed = Number(channel)
    const normalized = Number.isFinite(parsed) ? Math.max(0, Math.min(255, Math.round(parsed))) : 0
    return normalized.toString(16).padStart(2, '0')
  })
  return `#${channels.join('')}`
}

function hexToColorTriplet(value, fallback = [0, 0, 0]) {
  if (Array.isArray(value) && value.length >= 3) {
    return value.slice(0, 3).map((channel) => {
      const parsed = Number(channel)
      return Number.isFinite(parsed) ? Math.max(0, Math.min(255, Math.round(parsed))) : 0
    })
  }
  const normalized = normalizeHexColor(value, colorTripletToHex(fallback))
  return [
    Number.parseInt(normalized.slice(1, 3), 16),
    Number.parseInt(normalized.slice(3, 5), 16),
    Number.parseInt(normalized.slice(5, 7), 16)
  ]
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

function getRegionLayoutOverride(region) {
  return translationRegionLayoutOverrides.value[region?.id] || {}
}

function getRegionRotation(region) {
  const override = getRegionLayoutOverride(region)
  const rawValue = Object.prototype.hasOwnProperty.call(override, 'rotation')
    ? override.rotation
    : region?.rotation
  return Math.round(clampNumber(rawValue, -180, 180, 0) * 100) / 100
}

function getRegionStrokeStrength(region) {
  const override = getRegionLayoutOverride(region)
  if (!Object.prototype.hasOwnProperty.call(override, 'stroke_width')) {
    return 0
  }
  const rawValue = override.stroke_width
  return Math.round(clampNumber(rawValue, 0, 1, defaultStrokeStrength) * 1000) / 1000
}

function getRegionLetterSpacing(region) {
  const override = getRegionLayoutOverride(region)
  const rawValue = Object.prototype.hasOwnProperty.call(override, 'letter_spacing')
    ? override.letter_spacing
    : region?.letter_spacing
  return Math.round(clampNumber(rawValue, 0.5, 2.5, 1) * 1000) / 1000
}

function getRegionLineSpacing(region) {
  const override = getRegionLayoutOverride(region)
  const rawValue = Object.prototype.hasOwnProperty.call(override, 'line_spacing')
    ? override.line_spacing
    : region?.line_spacing
  return Math.round(clampNumber(rawValue, 0.5, 2.5, 1.08) * 1000) / 1000
}

function getRegionTextColorHex(region) {
  const override = getRegionLayoutOverride(region)
  return colorTripletToHex(
    Object.prototype.hasOwnProperty.call(override, 'fg_color') ? override.fg_color : region?.fg_color,
    '#152234'
  )
}

function getRegionStrokeColorHex(region) {
  const override = getRegionLayoutOverride(region)
  return colorTripletToHex(
    Object.prototype.hasOwnProperty.call(override, 'bg_color') ? override.bg_color : region?.bg_color,
    '#ffffff'
  )
}

function shouldPreserveRegionBackground(region) {
  const override = getRegionLayoutOverride(region)
  if (Object.prototype.hasOwnProperty.call(override, 'preserve_background')) {
    return Boolean(override.preserve_background)
  }
  return Boolean(region?.preserve_background)
}

function hasRegionExplicitFontSize(region) {
  return Object.prototype.hasOwnProperty.call(fontSizeInputDrafts.value, region.id)
    || getRegionExplicitFontSizeOverride(region.id) !== null
    || Number(region?.font_size_override || 0) > 0
}

function getCanvasPreviewRenderFontSize(region) {
  const baseFontSize = Number(getRegionFontSize(region) || region?.font_size || 12)
  if (!Number.isFinite(baseFontSize)) {
    return 12
  }
  return Math.max(8, Math.round(hasRegionExplicitFontSize(region)
    ? baseFontSize
    : baseFontSize + renderFontSizeOffset))
}

function getRegionExplicitFontSizeOverride(regionId) {
  const override = translationRegionLayoutOverrides.value[String(regionId || '')]
  return typeof override?.font_size === 'number' ? override.font_size : null
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

function getPageCommandRevision(pageId) {
  return Number(pageCommandRevisions.value[pageId] || 0)
}

function bumpPageCommandRevision(pageId) {
  const normalizedPageId = String(pageId || '').trim()
  if (!normalizedPageId) {
    return 0
  }
  const nextRevision = getPageCommandRevision(normalizedPageId) + 1
  pageCommandRevisions.value = {
    ...pageCommandRevisions.value,
    [normalizedPageId]: nextRevision
  }
  return nextRevision
}

function getProtectedRegionIds(forceRegionIds = []) {
  const forceSet = new Set((forceRegionIds || []).map((regionId) => String(regionId || '').trim()))
  const protectedIds = new Set()
  for (const [regionId, commitState] of Object.entries(regionCommitStates.value || {})) {
    const normalizedRegionId = String(regionId || '').trim()
    if (normalizedRegionId && !forceSet.has(normalizedRegionId) && ['dirty', 'saving', 'failed'].includes(commitState?.status)) {
      protectedIds.add(normalizedRegionId)
    }
  }
  for (const regionId of Object.keys(translationInputDrafts.value || {})) {
    if (!forceSet.has(regionId)) {
      protectedIds.add(regionId)
    }
  }
  for (const regionId of Object.keys(fontSizeInputDrafts.value || {})) {
    if (!forceSet.has(regionId)) {
      protectedIds.add(regionId)
    }
  }
  return protectedIds
}

function mergeOverrideMapPreservingLocal(currentMap, incomingMap, protectedRegionIds) {
  const nextMap = { ...(incomingMap || {}) }
  for (const regionId of protectedRegionIds) {
    if (Object.prototype.hasOwnProperty.call(currentMap || {}, regionId)) {
      nextMap[regionId] = currentMap[regionId]
    }
  }
  return nextMap
}

function replaceInspectionPage(pages, nextPage) {
  if (!nextPage?.stored_name) {
    return pages
  }
  let found = false
  const nextPages = (pages || []).map((page) => {
    if (page?.stored_name !== nextPage.stored_name) {
      return page
    }
    found = true
    return {
      ...nextPage
    }
  })
  if (found) {
    return nextPages
  }
  return [...nextPages, { ...nextPage }]
}

function applyInspectionOverrides(overrides, options = {}) {
  if (!overrides || typeof overrides !== 'object') {
    return
  }
  const protectedRegionIds = getProtectedRegionIds(options.forceRegionIds || [])
  if (Object.prototype.hasOwnProperty.call(overrides, 'translation_region_overrides')) {
    translationRegionOverrides.value = mergeOverrideMapPreservingLocal(
      translationRegionOverrides.value,
      overrides.translation_region_overrides,
      protectedRegionIds,
    )
  }
  if (Object.prototype.hasOwnProperty.call(overrides, 'translation_region_skip_overrides')) {
    translationRegionSkipOverrides.value = mergeOverrideMapPreservingLocal(
      translationRegionSkipOverrides.value,
      overrides.translation_region_skip_overrides,
      protectedRegionIds,
    )
  }
  if (Object.prototype.hasOwnProperty.call(overrides, 'translation_region_disabled_overrides')) {
    translationRegionDisabledOverrides.value = mergeOverrideMapPreservingLocal(
      translationRegionDisabledOverrides.value,
      overrides.translation_region_disabled_overrides,
      protectedRegionIds,
    )
  }
  if (Object.prototype.hasOwnProperty.call(overrides, 'translation_region_layout_overrides')) {
    translationRegionLayoutOverrides.value = mergeOverrideMapPreservingLocal(
      translationRegionLayoutOverrides.value,
      overrides.translation_region_layout_overrides,
      protectedRegionIds,
    )
  }
  if (Object.prototype.hasOwnProperty.call(overrides, 'style_region_overrides')) {
    styleRegionOverrides.value = mergeOverrideMapPreservingLocal(
      styleRegionOverrides.value,
      overrides.style_region_overrides,
      protectedRegionIds,
    )
  }
}

function applyPageCommandPayload(payload) {
  if (payload?.translation_page) {
    reviewInspectionPages.value = replaceInspectionPage(reviewInspectionPages.value, payload.translation_page)
  }
  if (payload?.style_page) {
    styleInspectionPages.value = replaceInspectionPage(styleInspectionPages.value, payload.style_page)
  }
  const forceRegionIds = [
    ...(payload?.updated_region_ids || []),
    payload?.created_region_id,
    payload?.deleted_region_id,
  ].filter(Boolean)
  applyInspectionOverrides(payload?.overrides, { forceRegionIds })
  translationInputDrafts.value = pruneRegionDraftMap(translationInputDrafts.value, mergedInspectionPages.value)
  fontSizeInputDrafts.value = pruneRegionDraftMap(fontSizeInputDrafts.value, mergedInspectionPages.value)
  fontSizeDraftOriginOverrides.value = pruneRegionDraftMap(fontSizeDraftOriginOverrides.value, mergedInspectionPages.value)
  syncEditSelection()
}

function queuePageCommandExecution(pageId, executor) {
  const normalizedPageId = String(pageId || '').trim()
  if (!normalizedPageId) {
    return Promise.resolve(null)
  }
  const previous = pageCommandExecutionQueue.get(normalizedPageId) || Promise.resolve()
  let trackedPromise = null
  trackedPromise = previous
    .catch(() => {})
    .then(executor)
    .finally(() => {
      if (pageCommandExecutionQueue.get(normalizedPageId) === trackedPromise) {
        pageCommandExecutionQueue.delete(normalizedPageId)
      }
    })
  pageCommandExecutionQueue.set(normalizedPageId, trackedPromise)
  return trackedPromise
}

function invalidateInspectionRequests() {
  reviewInspectionRequestToken += 1
  styleInspectionRequestToken += 1
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

function isRegionSelectedOnCanvas(region) {
  if (!region?.id) {
    return false
  }
  if (canvasRegionSelection.value[region.id]) {
    return true
  }
  return !Object.keys(canvasRegionSelection.value || {}).length && selectedEditRegionKey.value === region.id
}

function setCanvasRegionSelection(regionIds, primaryRegionId = '') {
  const pageRegionIds = new Set((selectedEditPage.value?.regions || []).map((region) => region.id))
  const nextSelection = {}
  for (const regionId of regionIds || []) {
    if (pageRegionIds.has(regionId)) {
      nextSelection[regionId] = true
    }
  }
  canvasRegionSelection.value = nextSelection
  const normalizedPrimary = String(primaryRegionId || '').trim()
  if (normalizedPrimary && pageRegionIds.has(normalizedPrimary)) {
    selectedEditRegionKey.value = normalizedPrimary
  } else if (Object.keys(nextSelection).length) {
    selectedEditRegionKey.value = Object.keys(nextSelection)[0]
  } else {
    selectedEditRegionKey.value = ''
  }
}

function selectSingleCanvasRegion(region) {
  canvasRegionSelection.value = {}
  selectedEditRegionKey.value = region?.id || ''
}

function toggleCanvasRegionSelection(region) {
  if (!region?.id) {
    return
  }
  const nextSelection = { ...canvasRegionSelection.value }
  if (!Object.keys(nextSelection).length && selectedEditRegionKey.value) {
    nextSelection[selectedEditRegionKey.value] = true
  }
  if (nextSelection[region.id]) {
    delete nextSelection[region.id]
  } else {
    nextSelection[region.id] = true
  }
  setCanvasRegionSelection(Object.keys(nextSelection), region.id)
}

function clearCanvasRegionSelection() {
  canvasRegionSelection.value = {}
  selectedEditRegionKey.value = ''
}

function handleCanvasRegionClick(event, region) {
  if (!region) {
    return
  }
  if (Date.now() < suppressCanvasRegionClickUntil) {
    return
  }
  if (mergeMode.value) {
    toggleMergeSelection(region)
    return
  }
  if (event?.shiftKey || event?.metaKey || event?.ctrlKey) {
    toggleCanvasRegionSelection(region)
    return
  }
  selectSingleCanvasRegion(region)
}

function handleRegionCardClick(event, region) {
  if (event?.shiftKey || event?.metaKey || event?.ctrlKey) {
    toggleCanvasRegionSelection(region)
    return
  }
  selectSingleCanvasRegion(region)
}

function syncEditSelection() {
  if (!mergedInspectionPages.value.length) {
    resetEditInspectorSelection()
    return
  }

  prunePerPageUiState(mergedInspectionPages.value)

  if (!mergedInspectionPages.value.some((page) => page.stored_name === selectedEditPageKey.value)) {
    selectedEditPageKey.value = mergedInspectionPages.value[0].stored_name
  }

  const currentPage = mergedInspectionPages.value.find((page) => page.stored_name === selectedEditPageKey.value)
  if (!currentPage) {
    selectedEditRegionKey.value = ''
    return
  }

  const currentState = getPageUiState(currentPage.stored_name)
  const hadStoredState = Boolean(perPageUiState.value[currentPage.stored_name])
  let nextSelectedRegionId = String(currentState.selectedRegionId || '')
  if (nextSelectedRegionId && !currentPage.regions.some((region) => region.id === nextSelectedRegionId)) {
    nextSelectedRegionId = ''
  }
  if (!hadStoredState) {
    nextSelectedRegionId = currentPage.regions[0]?.id || ''
  }
  updatePageUiState(currentPage.stored_name, (state) => ({
    ...state,
    selectedRegionId: nextSelectedRegionId
  }))
  selectedEditRegionKey.value = nextSelectedRegionId
  reconcileCanvasInteractionState()
}

function reconcileCanvasInteractionState() {
  const currentPage = selectedEditPage.value
  const validRegionIds = new Set((currentPage?.regions || []).map((region) => String(region?.id || '')).filter(Boolean))

  if (adjustingRegionId.value && !validRegionIds.has(adjustingRegionId.value)) {
    adjustingRegionId.value = ''
    manualDrawDraft.value = null
  }

  const nextCanvasSelection = {}
  for (const regionId of Object.keys(canvasRegionSelection.value || {})) {
    if (validRegionIds.has(regionId)) {
      nextCanvasSelection[regionId] = true
    }
  }
  canvasRegionSelection.value = nextCanvasSelection
  if (selectedEditRegionKey.value && !validRegionIds.has(selectedEditRegionKey.value)) {
    selectedEditRegionKey.value = Object.keys(nextCanvasSelection)[0] || ''
  }

  if (mergeMode.value) {
    const nextSelection = {}
    for (const regionId of Object.keys(mergeRegionSelection.value || {})) {
      if (validRegionIds.has(regionId)) {
        nextSelection[regionId] = true
      }
    }
    mergeRegionSelection.value = nextSelection
    if (!Object.keys(nextSelection).length) {
      mergeMode.value = false
    }
  }

  if (!manualDrawMode.value && !adjustingRegionId.value && manualDrawDraft.value) {
    manualDrawDraft.value = null
  }
}

function clearCanvasInteractionLocks() {
  mergeMode.value = false
  mergeRegionSelection.value = {}
  canvasMarqueeState.value = null
  clearManualDraft()
  status.value = '已解除画布交互锁，可以继续拖框、缩框和方向键微调。'
}

function pruneRegionDraftMap(draftMap, pages) {
  const validRegionIds = new Set()
  for (const page of pages || []) {
    for (const region of page?.regions || []) {
      if (region?.id) {
        validRegionIds.add(region.id)
      }
    }
  }

  const nextDrafts = {}
  for (const [regionId, value] of Object.entries(draftMap || {})) {
    if (validRegionIds.has(regionId)) {
      nextDrafts[regionId] = value
    }
  }
  return nextDrafts
}

function omitRegionDrafts(draftMap, regionIds = []) {
  const removeIds = new Set((regionIds || []).map((regionId) => String(regionId || '').trim()).filter(Boolean))
  if (!removeIds.size) {
    return draftMap || {}
  }
  const nextDrafts = {}
  for (const [regionId, value] of Object.entries(draftMap || {})) {
    if (!removeIds.has(regionId)) {
      nextDrafts[regionId] = value
    }
  }
  return nextDrafts
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
  const rotation = getRegionRotation(region)
  return {
    left: `${left}%`,
    top: `${top}%`,
    width: `${boxWidth}%`,
    height: `${boxHeight}%`,
    '--region-rotation': `${rotation}deg`,
    transform: rotation ? `rotate(${rotation}deg)` : 'none',
    transformOrigin: 'center center'
  }
}

function getAdvancedStylePopoverStyle(region, page) {
  const [x1, y1, x2] = getEffectiveRegionBBox(region)
  const imageWidth = Math.max(page?.image_width || 1, 1)
  const imageHeight = Math.max(page?.image_height || 1, 1)
  const shell = getCanvasShellForPane('main')
  const stage = shell?.querySelector?.('.v2-canvas-stage') || null
  const stageRect = stage?.getBoundingClientRect?.()
  const viewportWidth = Math.max(typeof window !== 'undefined' ? window.innerWidth : 0, 1)
  const viewportHeight = Math.max(typeof window !== 'undefined' ? window.innerHeight : 0, 1)
  const popoverWidth = Math.min(340, Math.max(260, viewportWidth - 24))
  const popoverMaxHeight = Math.max(220, viewportHeight - 24)
  if (!stageRect) {
    return {
      left: '12px',
      top: '12px',
      '--popover-max-height': `${popoverMaxHeight}px`
    }
  }
  const anchorRight = stageRect.left + (x2 / imageWidth) * stageRect.width
  const anchorLeft = stageRect.left + (x1 / imageWidth) * stageRect.width
  const anchorTop = stageRect.top + (y1 / imageHeight) * stageRect.height
  let left = anchorRight + 12
  if (left + popoverWidth > viewportWidth - 12) {
    left = anchorLeft - popoverWidth - 12
  }
  left = Math.min(Math.max(12, left), Math.max(12, viewportWidth - popoverWidth - 12))
  const top = Math.min(
    Math.max(12, anchorTop),
    Math.max(12, viewportHeight - Math.min(popoverMaxHeight, 520) - 12)
  )
  return {
    left: `${Math.round(left)}px`,
    top: `${Math.round(top)}px`,
    width: `${popoverWidth}px`,
    '--popover-max-height': `${popoverMaxHeight}px`
  }
}

function getStyleRegionSettingsButtonStyle(region, page) {
  const [, y1, x2] = getEffectiveRegionBBox(region)
  const width = Math.max(page?.image_width || 1, 1)
  const height = Math.max(page?.image_height || 1, 1)
  return {
    left: `${Math.max(0, Math.min(100, (x2 / width) * 100))}%`,
    top: `${Math.max(0, Math.min(100, (y1 / height) * 100))}%`
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
  const canvas = resolveCanvasInteractionSurface(event.currentTarget) || event.currentTarget
  const geometry = getCanvasGeometryFromSurface(canvas, page, 'main')
  return getCanvasPointFromGeometry(geometry, event.clientX, event.clientY, page)
}

function resolveCanvasInteractionSurface(target) {
  if (!target || typeof target.closest !== 'function') {
    return null
  }
  return (
    target.closest('.v2-canvas-shell')
    || target.closest('.v2-canvas-stage')
    || target.closest('.review-canvas-shell')
    || target.closest('.style-preview-canvas')
    || target.closest('.review-canvas-stage')
    || null
  )
}

function getCanvasShellForPane(pane = 'main') {
  return pane === 'compare' ? compareCanvasShellRef.value : translatedPreviewCanvasRef.value
}

function setCompareCanvasShellElement(element) {
  if (element) {
    compareCanvasShellRef.value = element
  }
}

function setTranslatedPreviewCanvasElement(element) {
  translatedPreviewCanvasRef.value = element || null
}

function setReviewPaneCanvasElement(element, mode) {
  if (isReviewFramePane(mode)) {
    setTranslatedPreviewCanvasElement(element)
    return
  }
  if (element && mode === firstReadonlyReviewComparePaneKey.value) {
    setCompareCanvasShellElement(element)
  }
}

function readCanvasShellMetric(shell) {
  if (!shell || typeof window === 'undefined') {
    return { width: 0, height: 0 }
  }
  const computedStyle = window.getComputedStyle(shell)
  const paddingX = Number.parseFloat(computedStyle.paddingLeft || '0') + Number.parseFloat(computedStyle.paddingRight || '0')
  const paddingY = Number.parseFloat(computedStyle.paddingTop || '0') + Number.parseFloat(computedStyle.paddingBottom || '0')
  return {
    width: Math.max(1, Math.round((shell.clientWidth || 0) - paddingX)),
    height: Math.max(1, Math.round((shell.clientHeight || 0) - paddingY))
  }
}

function refreshCanvasShellMetrics() {
  canvasShellMetrics.value = {
    main: readCanvasShellMetric(translatedPreviewCanvasRef.value),
    compare: readCanvasShellMetric(compareCanvasShellRef.value)
  }
}

function scheduleCanvasLayoutRefresh() {
  if (typeof window === 'undefined') {
    refreshCanvasShellMetrics()
    refreshTranslatedPreviewScale()
    return
  }
  if (canvasLayoutFrame != null) {
    window.cancelAnimationFrame(canvasLayoutFrame)
  }
  canvasLayoutFrame = window.requestAnimationFrame(() => {
    canvasLayoutFrame = null
    refreshCanvasShellMetrics()
    refreshTranslatedPreviewScale()
  })
}

function getCanvasGeometryFromSurface(surface, page, pane = 'main') {
  const shell = resolveCanvasInteractionSurface(surface) || getCanvasShellForPane(pane)
  const shellRect = shell?.getBoundingClientRect?.() || { left: 0, top: 0, width: 1, height: 1 }
  const stage = shell?.querySelector?.('.v2-canvas-stage') || null
  const stageWidth = Math.max(stage?.offsetWidth || shell?.clientWidth || shellRect.width || 1, 1)
  const stageHeight = Math.max(stage?.offsetHeight || shell?.clientHeight || shellRect.height || 1, 1)
  const stageOffsetLeft = Number(stage?.offsetLeft || 0)
  const stageOffsetTop = Number(stage?.offsetTop || 0)
  const viewport = getViewportState(page?.stored_name || '', pane)
  return {
    shell,
    shellRect,
    shellWidth: Math.max(shell?.clientWidth || shellRect.width || 1, 1),
    shellHeight: Math.max(shell?.clientHeight || shellRect.height || 1, 1),
    stageWidth,
    stageHeight,
    stageOffsetLeft,
    stageOffsetTop,
    viewport
  }
}

function getCanvasPointFromGeometry(geometry, clientX, clientY, page) {
  const imageWidth = Math.max(page?.image_width || 1, 1)
  const imageHeight = Math.max(page?.image_height || 1, 1)
  const safeWidth = Math.max(geometry?.stageWidth || 1, 1)
  const safeHeight = Math.max(geometry?.stageHeight || 1, 1)
  const viewport = geometry?.viewport || getViewportState(page?.stored_name || '', 'main')
  const zoom = Math.max(viewport.zoom || 1, 0.001)
  const shellRect = geometry?.shellRect || { left: 0, top: 0 }
  const stageOffsetLeft = Number(geometry?.stageOffsetLeft || 0)
  const stageOffsetTop = Number(geometry?.stageOffsetTop || 0)
  const x = Math.min(
    imageWidth,
    Math.max(0, (((clientX - shellRect.left - stageOffsetLeft - viewport.panX) / (safeWidth * zoom)) * imageWidth))
  )
  const y = Math.min(
    imageHeight,
    Math.max(0, (((clientY - shellRect.top - stageOffsetTop - viewport.panY) / (safeHeight * zoom)) * imageHeight))
  )
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

function clearCanvasNudgeCommitTimer() {
  if (canvasNudgeCommitTimer != null) {
    window.clearTimeout(canvasNudgeCommitTimer)
    canvasNudgeCommitTimer = null
  }
}

function scheduleCanvasNudgeCommit() {
  clearCanvasNudgeCommitTimer()
  canvasNudgeCommitTimer = window.setTimeout(() => {
    void flushPendingCanvasNudge()
  }, 90)
}

function haveSameRegionIdSet(first = [], second = []) {
  if (!Array.isArray(first) || !Array.isArray(second) || first.length !== second.length) {
    return false
  }
  const firstSet = new Set(first.map((item) => String(item || '')))
  return second.every((item) => firstSet.has(String(item || '')))
}

async function flushPendingCanvasNudge() {
  const pending = pendingCanvasNudge
  if (!pending) {
    return
  }

  pendingCanvasNudge = null
  clearCanvasNudgeCommitTimer()

  const page = mergedInspectionPages.value.find((item) => item.stored_name === pending.pageId)
  if (!page) {
    return
  }

  const regionIds = Array.isArray(pending.regionIds) && pending.regionIds.length
    ? pending.regionIds
    : [pending.regionId].filter(Boolean)
  const redoCommands = []
  const undoCommands = []
  for (const regionId of regionIds) {
    const region = page.regions?.find((item) => item.id === regionId)
    const originBBox = pending.originBBoxes?.[regionId] || pending.originBBox
    const currentOverride = translationRegionLayoutOverrides.value[regionId]
    const nextBBox = Array.isArray(currentOverride?.bbox) && currentOverride.bbox.length === 4
      ? currentOverride.bbox
      : region
        ? getEffectiveRegionBBox(region)
        : null
    if (!originBBox || !nextBBox) {
      continue
    }
    const changed = nextBBox.some((value, index) => value !== originBBox[index])
    if (!changed) {
      continue
    }
    redoCommands.push({
      type: 'update_region_bbox',
      region_id: regionId,
      bbox: nextBBox
    })
    undoCommands.push({
      type: 'update_region_bbox',
      region_id: regionId,
      bbox: originBBox
    })
  }

  if (!redoCommands.length) {
    return
  }

  await runCanvasCommand(page, {
    label: redoCommands.length > 1 ? `微调 ${redoCommands.length} 个文本框` : '微调文本框位置',
    pendingMessage: '位置保存中…',
    redoCommands,
    undoCommands,
    focusRegionId: pending.regionId || regionIds[0],
    rollback: () => {
      for (const regionId of regionIds) {
        const originBBox = pending.originBBoxes?.[regionId] || pending.originBBox
        if (originBBox) {
          updateRegionLayoutOverride(regionId, { bbox: originBBox })
        }
      }
    }
  })
}

function nudgeSelectedRegion(deltaX, deltaY) {
  const page = selectedEditPage.value
  const region = selectedEditRegion.value
  if (!page || !region || !canDirectManipulateCanvas.value) {
    return
  }

  const regionIds = selectedCanvasRegionIds.value.length > 1 && selectedCanvasRegionIds.value.includes(region.id)
    ? selectedCanvasRegionIds.value
    : [region.id]
  const pendingRegionIds = pendingCanvasNudge
    ? (Array.isArray(pendingCanvasNudge.regionIds) && pendingCanvasNudge.regionIds.length
        ? pendingCanvasNudge.regionIds
        : [pendingCanvasNudge.regionId].filter(Boolean))
    : []
  const hasPendingForSameSelection = Boolean(
    pendingCanvasNudge
    && pendingCanvasNudge.pageId === page.stored_name
    && haveSameRegionIdSet(pendingRegionIds, regionIds)
  )
  if (pendingCanvasNudge && !hasPendingForSameSelection) {
    void flushPendingCanvasNudge()
  }

  if (!hasPendingForSameSelection) {
    bumpPageCommandRevision(page.stored_name)
    const originBBoxes = {}
    for (const regionId of regionIds) {
      const targetRegion = page.regions?.find((item) => item.id === regionId)
      if (targetRegion) {
        originBBoxes[regionId] = getEffectiveRegionBBox(targetRegion)
      }
    }
    pendingCanvasNudge = {
      pageId: page.stored_name,
      regionId: region.id,
      regionIds,
      originBBox: originBBoxes[region.id],
      originBBoxes
    }
  }

  let changedAny = false
  for (const regionId of regionIds) {
    const targetRegion = page.regions?.find((item) => item.id === regionId)
    if (!targetRegion) {
      continue
    }
    const currentBBox = getEffectiveRegionBBox(targetRegion)
    const nextBBox = translateBBoxWithinPage(currentBBox, deltaX, deltaY, page)
    const changed = nextBBox.some((value, index) => value !== currentBBox[index])
    if (!changed) {
      continue
    }
    changedAny = true
    updateRegionLayoutOverride(regionId, { bbox: nextBBox })
  }
  if (!changedAny) {
    return
  }
  selectedEditRegionKey.value = region.id
  setRegionCommitState(regionIds, 'dirty', '位置草稿未保存')
  scheduleCanvasNudgeCommit()
}

function resizeBBoxWithinPage(originBBox, handle, deltaX, deltaY, page, options = {}) {
  let [x1, y1, x2, y2] = originBBox
  if (handle.includes('n')) {
    y1 += deltaY
    if (options.fromCenter) {
      y2 -= deltaY
    }
  }
  if (handle.includes('s')) {
    y2 += deltaY
    if (options.fromCenter) {
      y1 -= deltaY
    }
  }
  if (handle.includes('w')) {
    x1 += deltaX
    if (options.fromCenter) {
      x2 -= deltaX
    }
  }
  if (handle.includes('e')) {
    x2 += deltaX
    if (options.fromCenter) {
      x1 -= deltaX
    }
  }

  if (options.proportional && handle.length === 2) {
    const originWidth = Math.max(8, originBBox[2] - originBBox[0])
    const originHeight = Math.max(8, originBBox[3] - originBBox[1])
    const ratio = originWidth / originHeight
    let nextWidth = Math.max(8, Math.abs(x2 - x1))
    let nextHeight = Math.max(8, Math.abs(y2 - y1))
    const widthScale = nextWidth / originWidth
    const heightScale = nextHeight / originHeight

    if (widthScale >= heightScale) {
      nextHeight = nextWidth / ratio
    } else {
      nextWidth = nextHeight * ratio
    }

    if (options.fromCenter) {
      const centerX = (originBBox[0] + originBBox[2]) / 2
      const centerY = (originBBox[1] + originBBox[3]) / 2
      x1 = centerX - (nextWidth / 2)
      x2 = centerX + (nextWidth / 2)
      y1 = centerY - (nextHeight / 2)
      y2 = centerY + (nextHeight / 2)
    } else if (handle === 'se') {
      x1 = originBBox[0]
      y1 = originBBox[1]
      x2 = x1 + nextWidth
      y2 = y1 + nextHeight
    } else if (handle === 'ne') {
      x1 = originBBox[0]
      y2 = originBBox[3]
      x2 = x1 + nextWidth
      y1 = y2 - nextHeight
    } else if (handle === 'sw') {
      x2 = originBBox[2]
      y1 = originBBox[1]
      x1 = x2 - nextWidth
      y2 = y1 + nextHeight
    } else if (handle === 'nw') {
      x2 = originBBox[2]
      y2 = originBBox[3]
      x1 = x2 - nextWidth
      y1 = y2 - nextHeight
    }
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

function getCanvasPreviewImageUrl(page, maxSide = IMAGE_REVIEW_MAX_SIDE) {
  const dirty = Boolean(page?.stored_name && canvasPreviewDirtyPages.value[page.stored_name])
  if (isCanvasReviewMode.value && dirty) {
    const baseImagePath = page?.base_image_url || page?.translated_image_url || page?.image_url || ''
    return getVersionedPageImageUrl(baseImagePath, page?.stored_name, { maxSide })
  }

  const latestTranslatedUrl = page?.stored_name
    ? String(latestTranslatedImageUrlByPage.value[page.stored_name] || '').trim()
    : ''
  if (latestTranslatedUrl) {
    return getVersionedPageImageUrl(latestTranslatedUrl, page?.stored_name, { maxSide })
  }

  const imagePath = page?.translated_image_url || page?.image_url || page?.base_image_url || ''
  return getVersionedPageImageUrl(imagePath, page?.stored_name, { maxSide })
}

function getSavedTranslatedImageUrl(page, maxSide = IMAGE_REVIEW_MAX_SIDE) {
  const latestTranslatedUrl = page?.stored_name
    ? String(latestTranslatedImageUrlByPage.value[page.stored_name] || '').trim()
    : ''
  if (latestTranslatedUrl) {
    return getVersionedPageImageUrl(latestTranslatedUrl, page?.stored_name, { maxSide })
  }

  const translatedPath = String(page?.translated_image_url || '').trim()
  if (translatedPath) {
    return getVersionedPageImageUrl(translatedPath, page?.stored_name, { maxSide })
  }

  return ''
}

function getReviewComparePaneOption(mode) {
  return reviewComparePaneOptions.find((option) => option.key === mode) || reviewComparePaneOptions[0]
}

function isReviewFramePane(mode) {
  return mode === 'frame'
}

function getReviewComparePaneCanvasRole(mode) {
  return isReviewFramePane(mode) ? 'main' : 'compare'
}

function getReviewComparePaneImageUrl(mode) {
  const page = selectedEditPage.value
  const entry = v2SelectedPageEntry.value
  if (mode === 'final') {
    return (
      getSavedTranslatedImageUrl(page, IMAGE_REVIEW_MAX_SIDE)
      || getReviewPageImageUrl(page?.translated_image_url || page?.image_url || '', page?.stored_name)
      || entry?.finalUrl
      || getCanvasPreviewImageUrl(page, IMAGE_REVIEW_MAX_SIDE)
      || entry?.previewUrl
      || entry?.blankUrl
      || entry?.sourceUrl
      || ''
    )
  }
  if (mode === 'source') {
    return (
      getReviewPageImageUrl(page?.source_image_url || page?.image_url || page?.base_image_url || '', page?.stored_name)
      || entry?.sourceUrl
      || selectedEditPageMainImageUrl.value
      || selectedEditPageThumbnailUrl.value
      || ''
    )
  }
  if (mode === 'blank') {
    return (
      getReviewPageImageUrl(page?.base_image_url || page?.source_image_url || page?.image_url || '', page?.stored_name)
      || entry?.blankUrl
      || selectedEditPageMainImageUrl.value
      || entry?.sourceUrl
      || ''
    )
  }
  return getReviewPageImageUrl(page?.base_image_url || page?.source_image_url || page?.image_url || '', page?.stored_name) || v2EditorPaneImageUrl.value
}

function getReviewComparePaneLabel(mode) {
  return getReviewComparePaneOption(mode).label
}

function getReviewComparePaneAlt(mode) {
  return `${v2SelectedPageEntry.value?.name || '页面'} ${getReviewComparePaneLabel(mode)}`
}

function isReviewComparePaneSelected(mode) {
  return normalizeReviewComparePaneModes(reviewWorkspacePrefs.value.compare_pane_modes, reviewWorkspacePrefs.value.compare_pane_mode).includes(mode)
}

function isReviewComparePaneToggleDisabled(mode) {
  const currentModes = normalizeReviewComparePaneModes(reviewWorkspacePrefs.value.compare_pane_modes, reviewWorkspacePrefs.value.compare_pane_mode)
  const hasMode = currentModes.includes(mode)
  return (hasMode && currentModes.length <= 2) || (!hasMode && currentModes.length >= 3)
}

function toggleReviewComparePaneMode(mode) {
  if (!isValidReviewComparePaneMode(mode)) {
    return
  }
  const currentModes = normalizeReviewComparePaneModes(reviewWorkspacePrefs.value.compare_pane_modes, reviewWorkspacePrefs.value.compare_pane_mode)
  const hasMode = currentModes.includes(mode)
  if (hasMode && currentModes.length <= 2) {
    status.value = '审校对比至少保留两个页面。'
    return
  }
  if (!hasMode && currentModes.length >= 3) {
    status.value = '审校对比最多同时显示三个页面。'
    return
  }
  const nextModes = hasMode
    ? currentModes.filter((item) => item !== mode)
    : [...currentModes, mode]
  const normalizedModes = normalizeReviewComparePaneModes(nextModes)
  reviewWorkspacePrefs.value = {
    ...reviewWorkspacePrefs.value,
    compare_pane_mode: normalizedModes[0] || 'final',
    compare_pane_modes: normalizedModes
  }
}

function setInspectorTab(tab) {
  if (!isValidInspectorTab(tab)) {
    return
  }
  reviewWorkspacePrefs.value = {
    ...reviewWorkspacePrefs.value,
    inspector_tab: tab
  }
}

function toggleCompareSync() {
  const nextEnabled = !reviewWorkspacePrefs.value.compare_sync_enabled
  reviewWorkspacePrefs.value = {
    ...reviewWorkspacePrefs.value,
    compare_sync_enabled: nextEnabled
  }
  if (nextEnabled && selectedEditPage.value?.stored_name) {
    const pageId = selectedEditPage.value.stored_name
    const mainViewport = getViewportState(pageId, 'main')
    updateViewportState(pageId, 'compare', mainViewport, { syncCompare: false })
  }
}

function toggleWorkspaceDebug() {
  reviewWorkspacePrefs.value = {
    ...reviewWorkspacePrefs.value,
    show_debug: !reviewWorkspacePrefs.value.show_debug
  }
}

function toggleWorkspacePanel(panelKey) {
  if (!panelKey) {
    return
  }
  const nextValue = !reviewWorkspacePrefs.value[panelKey]
  reviewWorkspacePrefs.value = {
    ...reviewWorkspacePrefs.value,
    [panelKey]: nextValue
  }
}

function startWorkspaceSplitterDrag(kind, event) {
  if (event.button != null && event.button !== 0) {
    return
  }
  const shell = reviewWorkspaceLayoutRef.value
  if (!shell) {
    return
  }
  const rect = shell.getBoundingClientRect()
  workspaceSplitterState.value = {
    kind,
    pointerId: event.pointerId,
    shellLeft: rect.left,
    shellRight: rect.right,
    originRailWidth: Number(reviewWorkspacePrefs.value.page_rail_width || 128),
    originInspectorWidth: Number(reviewWorkspacePrefs.value.inspector_width || 420)
  }
  event.preventDefault()
}

function updateWorkspaceSplitterDrag(event) {
  const draft = workspaceSplitterState.value
  if (!draft) {
    return
  }
  if (draft.pointerId != null && event.pointerId != null && draft.pointerId !== event.pointerId) {
    return
  }

  if (draft.kind === 'rail') {
    const nextWidth = Math.min(180, Math.max(96, Math.round(event.clientX - draft.shellLeft)))
    reviewWorkspacePrefs.value = {
      ...reviewWorkspacePrefs.value,
      page_rail_width: nextWidth
    }
    return
  }

  if (draft.kind === 'inspector') {
    const nextWidth = Math.min(920, Math.max(300, Math.round(draft.shellRight - event.clientX)))
    reviewWorkspacePrefs.value = {
      ...reviewWorkspacePrefs.value,
      inspector_width: nextWidth
    }
  }
}

function finishWorkspaceSplitterDrag() {
  if (!workspaceSplitterState.value) {
    return
  }
  workspaceSplitterState.value = null
}

function startCompareSplitterDrag(event) {
  if (event.button != null && event.button !== 0) {
    return
  }
  const zone = reviewCanvasZoneRef.value
  if (!zone) {
    return
  }
  const rect = zone.getBoundingClientRect()
  compareSplitterState.value = {
    pointerId: event.pointerId,
    zoneLeft: rect.left,
    zoneWidth: Math.max(rect.width, 1),
    originRatio: reviewWorkspacePrefs.value.split_ratio
  }
  event.preventDefault()
}

function updateCompareSplitterDrag(event) {
  const draft = compareSplitterState.value
  if (!draft) {
    return
  }
  if (draft.pointerId != null && event.pointerId != null && draft.pointerId !== event.pointerId) {
    return
  }
  const relative = ((event.clientX - draft.zoneLeft) / draft.zoneWidth) * 100
  reviewWorkspacePrefs.value = {
    ...reviewWorkspacePrefs.value,
    split_ratio: Math.min(80, Math.max(50, Math.round(relative)))
  }
}

function finishCompareSplitterDrag() {
  if (!compareSplitterState.value) {
    return
  }
  compareSplitterState.value = null
}

function startCanvasViewportPan(event, page, pane = 'main') {
  if (!page || manualDrawMode.value || mergeMode.value || isAdjustingRegionBBox.value) {
    return
  }
  const isMiddleMouse = event.button === 1
  const allowPan = spacePanPressed.value || isMiddleMouse
  if (!allowPan) {
    return
  }
  const shell = resolveCanvasInteractionSurface(event.currentTarget) || getCanvasShellForPane(pane)
  if (!shell) {
    return
  }
  const viewport = getViewportState(page.stored_name, pane)
  viewportPanState.value = {
    pointerId: event.pointerId,
    pageId: page.stored_name,
    pane,
    startClientX: event.clientX,
    startClientY: event.clientY,
    originPanX: viewport.panX,
    originPanY: viewport.panY
  }
  event.preventDefault()
}

function updateCanvasViewportPan(event) {
  const draft = viewportPanState.value
  if (!draft) {
    return
  }
  if (draft.pointerId != null && event.pointerId != null && draft.pointerId !== event.pointerId) {
    return
  }
  updateViewportState(draft.pageId, draft.pane, {
    panX: draft.originPanX + (event.clientX - draft.startClientX),
    panY: draft.originPanY + (event.clientY - draft.startClientY)
  })
}

function finishCanvasViewportPan() {
  viewportPanState.value = null
}

function cancelCanvasViewportPan() {
  viewportPanState.value = null
}

function handleCanvasWheel(event, page, pane = 'main') {
  if (!page) {
    return
  }
  event.preventDefault()
  const shell = resolveCanvasInteractionSurface(event.currentTarget) || getCanvasShellForPane(pane)
  if (!shell) {
    return
  }
  const geometry = getCanvasGeometryFromSurface(shell, page, pane)
  const viewport = getViewportState(page.stored_name, pane)
  const shouldZoom = event.ctrlKey || event.metaKey

  if (!shouldZoom) {
    const horizontalDelta = event.shiftKey ? event.deltaY : event.deltaX
    const verticalDelta = event.shiftKey ? 0 : event.deltaY
    updateViewportState(page.stored_name, pane, {
      panX: viewport.panX - horizontalDelta,
      panY: viewport.panY - verticalDelta
    })
    return
  }

  const pointerX = event.clientX - geometry.shellRect.left - geometry.stageOffsetLeft
  const pointerY = event.clientY - geometry.shellRect.top - geometry.stageOffsetTop
  const delta = event.deltaY < 0 ? 1.1 : 0.92
  const nextZoom = clampCanvasZoom(Number((viewport.zoom * delta).toFixed(3)))
  const ratio = nextZoom / Math.max(viewport.zoom || 1, 0.001)
  updateViewportState(page.stored_name, pane, {
    zoom: nextZoom,
    panX: pointerX - ((pointerX - viewport.panX) * ratio),
    panY: pointerY - ((pointerY - viewport.panY) * ratio)
  })
}

function stepCurrentPageZoom(step) {
  const page = selectedEditPage.value
  if (!page) {
    return
  }

  const panes = [
    { key: 'main', shell: translatedPreviewCanvasRef.value },
    { key: 'compare', shell: compareCanvasShellRef.value }
  ]
  const delta = step > 0 ? 1.12 : 0.9

  panes.forEach(({ key, shell }) => {
    if (!shell) {
      return
    }
    const geometry = getCanvasGeometryFromSurface(shell, page, key)
    const viewport = getViewportState(page.stored_name, key)
    const pointerX = Math.max((geometry.shellWidth - geometry.stageOffsetLeft * 2) / 2, 1)
    const pointerY = Math.max((geometry.shellHeight - geometry.stageOffsetTop) / 2, 1)
    const nextZoom = clampCanvasZoom(Number((viewport.zoom * delta).toFixed(3)))
    const ratio = nextZoom / Math.max(viewport.zoom || 1, 0.001)
    updateViewportState(page.stored_name, key, {
      zoom: nextZoom,
      panX: pointerX - ((pointerX - viewport.panX) * ratio),
      panY: pointerY - ((pointerY - viewport.panY) * ratio)
    }, { syncCompare: false })
  })
}

function setCanvasViewportPreset(page, pane = 'main', preset = 'fit') {
  const shell = getCanvasShellForPane(pane)
  if (!page || !shell) {
    return
  }
  refreshCanvasShellMetrics()
  const geometry = getCanvasGeometryFromSurface(shell, page, pane)
  const metrics = canvasShellMetrics.value[pane] || {}
  const availableWidth = Math.max(metrics.width || geometry.stageWidth, 1)
  const availableHeight = Math.max(metrics.height || geometry.stageHeight, 1)
  let zoom = 1

  if (preset === 'fit') {
    zoom = Math.min(1, availableWidth / geometry.stageWidth, availableHeight / geometry.stageHeight)
  } else if (preset === 'width') {
    zoom = availableWidth / geometry.stageWidth
  }

  zoom = clampCanvasZoom(zoom)
  updateViewportState(page.stored_name, pane, {
    zoom,
    panX: Math.round((geometry.stageWidth * (1 - zoom)) / 2),
    panY: preset === 'fit' ? 0 : Math.round((availableHeight - (geometry.stageHeight * zoom)) / 2)
  }, { syncCompare: pane !== 'compare' })
}

function setCurrentPageViewportPreset(preset = 'fit', pane = 'main') {
  if (!selectedEditPage.value) {
    return
  }
  setCanvasViewportPreset(selectedEditPage.value, pane, preset)
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
    void selectEditPageForReview(nextPage.stored_name)
  }
}

async function selectEditPageForReview(pageKey) {
  const normalizedPageKey = String(pageKey || '').trim()
  if (!normalizedPageKey) {
    return
  }
  if (pendingCanvasNudge) {
    await flushPendingCanvasNudge()
  }
  canvasRegionSelection.value = {}
  canvasMarqueeState.value = null
  closeAdvancedStylePopover()
  selectedEditPageKey.value = normalizedPageKey
}

function selectAdjacentEditRegion(offset) {
  if (!filteredEditRegions.value.length) {
    return
  }

  const currentIndex = selectedEditRegionVisibleIndex.value
  if (currentIndex < 0) {
    const fallbackIndex = offset >= 0 ? 0 : filteredEditRegions.value.length - 1
    selectSingleCanvasRegion(filteredEditRegions.value[fallbackIndex])
    return
  }

  const nextIndex = Math.min(
    filteredEditRegions.value.length - 1,
    Math.max(0, currentIndex + offset),
  )
  selectSingleCanvasRegion(filteredEditRegions.value[nextIndex] || selectedEditRegion.value)
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
    && (isRegionSkipEnabled(region) || isRegionDisabled(region))
  )
}

function shouldShowCanvasTextOverlay(region, page) {
  return Boolean(
    isCanvasReviewMode.value
    && page?.stored_name
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
  const rawFontSize = getCanvasPreviewRenderFontSize(region)
  const scaledFontSize = Math.max(8, Math.round(rawFontSize * Math.max(translatedPreviewScale.value || 1, 0.1)))
  const spacingMultiplier = getRegionLetterSpacing(region)
  const normalizedLineSpacing = getRegionLineSpacing(region)
  const letterSpacing = Math.max(0, (spacingMultiplier - 1) * scaledFontSize)
  const lineHeight = Math.max(0.5, Math.min(2.5, normalizedLineSpacing || 1.08))
  const resolvedAlignment = getResolvedRegionAlignment(region)
  const fgColor = getRegionTextColorHex(region)
  const strokeColor = getRegionStrokeColorHex(region)
  const strokeWidth = Math.max(0, scaledFontSize * getRegionStrokeStrength(region) * 0.35)
  return {
    fontFamily: getRegionPreviewFontFamily(region),
    fontSize: `${scaledFontSize}px`,
    letterSpacing: `${letterSpacing.toFixed(2)}px`,
    lineHeight: String(lineHeight),
    color: fgColor,
    WebkitTextStroke: strokeWidth > 0 ? `${strokeWidth.toFixed(2)}px ${strokeColor}` : '0 transparent',
    paintOrder: 'stroke fill',
    fontSynthesis: 'none',
    writingMode: isVerticalRegion(region) ? 'vertical-rl' : 'horizontal-tb',
    textOrientation: isVerticalRegion(region) ? 'mixed' : 'initial',
    textAlign: isVerticalRegion(region)
      ? 'start'
      : resolvedAlignment === 'center'
        ? 'center'
        : resolvedAlignment === 'right'
          ? 'right'
          : 'left'
  }
}

function getCanvasPreviewTextPadding(region) {
  const [x1, y1, x2, y2] = getEffectiveRegionBBox(region)
  const minSide = Math.max(0, Math.min(x2 - x1, y2 - y1))
  if (minSide <= 3) {
    return 0
  }
  const rawFontSize = getCanvasPreviewRenderFontSize(region)
  const fontPadding = Math.max(1, Math.round(rawFontSize * renderTextPaddingRatio))
  const sideLimit = Math.max(0, Math.floor(minSide / 8))
  return Math.max(0, Math.min(fontPadding, sideLimit)) * Math.max(translatedPreviewScale.value || 1, 0.1)
}

function getCanvasPreviewTextContainerStyle(region) {
  const padding = getCanvasPreviewTextPadding(region)
  if (isVerticalRegion(region)) {
    return {
      padding: `${padding.toFixed(2)}px`,
      justifyContent: 'center',
      alignItems: 'flex-start'
    }
  }

  const resolvedAlignment = getResolvedRegionAlignment(region)
  return {
    padding: `${padding.toFixed(2)}px`,
    justifyContent: resolvedAlignment === 'right' ? 'flex-end'
      : resolvedAlignment === 'center' ? 'center'
      : 'flex-start',
    alignItems: 'center'
  }
}

function refreshTranslatedPreviewScale() {
  const page = selectedEditPage.value
  const canvasElement = translatedPreviewCanvasRef.value
  if (!page?.image_width || !canvasElement) {
    translatedPreviewScale.value = 1
    return
  }
  const stageElement = canvasElement.querySelector?.('.v2-canvas-stage')
  const safeWidth = Math.max(stageElement?.clientWidth || canvasElement.clientWidth || 0, 1)
  const zoom = getViewportState(page.stored_name, 'main').zoom || 1
  translatedPreviewScale.value = (safeWidth / Math.max(page.image_width || 1, 1)) * zoom
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

function getCanvasMarqueeBBox(page) {
  const draft = canvasMarqueeState.value
  if (!draft || !page || draft.pageId !== page.stored_name) {
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

function getCanvasMarqueeStyle(page) {
  const bbox = getCanvasMarqueeBBox(page)
  if (!bbox) {
    return {}
  }
  return getStyleRegionBoxStyle({ bbox }, page)
}

function doBBoxesIntersect(first, second) {
  if (!Array.isArray(first) || !Array.isArray(second)) {
    return false
  }
  return first[0] <= second[2]
    && first[2] >= second[0]
    && first[1] <= second[3]
    && first[3] >= second[1]
}

function getCanvasMeasurementOverlay(page) {
  if (!page?.stored_name) {
    return null
  }

  const transformDraft = canvasTransformState.value
  if (transformDraft?.pageId === page.stored_name && Array.isArray(transformDraft.currentBBox)) {
    return {
      bbox: transformDraft.currentBBox,
      label: transformDraft.mode === 'move' ? '移动' : '缩放',
      modifiers: [
        transformDraft.axisLocked ? '轴锁定' : '',
        transformDraft.proportional ? '等比' : '',
        transformDraft.fromCenter ? '中心' : ''
      ].filter(Boolean)
    }
  }

  const manualBBox = getManualDraftBBox(page)
  if (manualBBox) {
    return {
      bbox: manualBBox,
      label: manualDrawDraft.value?.action === 'adjust' ? '替换范围' : '新框',
      modifiers: []
    }
  }

  return null
}

function getCanvasMeasurementStyle(page) {
  const overlay = getCanvasMeasurementOverlay(page)
  if (!overlay) {
    return {}
  }
  const [x1, y1] = overlay.bbox
  const imageWidth = Math.max(page?.image_width || 1, 1)
  const imageHeight = Math.max(page?.image_height || 1, 1)
  const viewport = getViewportState(page?.stored_name || '', 'main')
  const inverseScale = 1 / Math.max(viewport.zoom || 1, 0.001)
  return {
    left: `${(x1 / imageWidth) * 100}%`,
    top: `${(y1 / imageHeight) * 100}%`,
    '--measure-scale': inverseScale
  }
}

function getCanvasMeasurementText(page) {
  const overlay = getCanvasMeasurementOverlay(page)
  if (!overlay) {
    return ''
  }
  const [x1, y1, x2, y2] = overlay.bbox.map((value) => Math.round(Number(value) || 0))
  const suffix = overlay.modifiers?.length ? ` · ${overlay.modifiers.join(' · ')}` : ''
  return `${overlay.label} · x ${x1} y ${y1} · ${Math.max(0, x2 - x1)} × ${Math.max(0, y2 - y1)}${suffix}`
}

function getCanvasViewportPercent(page, pane = 'main') {
  const viewport = getViewportState(page?.stored_name || '', pane)
  return `${Math.round((viewport.zoom || 1) * 100)}%`
}

function getCanvasHudDetail(page, pane = 'main') {
  if (!page) {
    return ''
  }
  return pane === 'compare' || reviewWorkspacePrefs.value.compare_sync_enabled ? '联动画布' : '独立画布'
}

function getCanvasSelectionToolbarStyle(page) {
  const regions = hasCanvasMultiSelection.value ? selectedCanvasRegions.value : (selectedEditRegion.value ? [selectedEditRegion.value] : [])
  if (!page?.stored_name || !regions.length || canvasTransformState.value?.pageId === page.stored_name) {
    return {}
  }
  const bboxes = regions.map((region) => getEffectiveRegionBBox(region))
  const x1 = Math.min(...bboxes.map((bbox) => bbox[0]))
  const y1 = Math.min(...bboxes.map((bbox) => bbox[1]))
  const y2 = Math.max(...bboxes.map((bbox) => bbox[3]))
  const imageWidth = Math.max(page?.image_width || 1, 1)
  const imageHeight = Math.max(page?.image_height || 1, 1)
  const viewport = getViewportState(page.stored_name, 'main')
  const placeBelow = (y1 / imageHeight) < 0.08
  return {
    left: `${(x1 / imageWidth) * 100}%`,
    top: `${((placeBelow ? y2 : y1) / imageHeight) * 100}%`,
    '--toolbar-scale': 1 / Math.max(viewport.zoom || 1, 0.001),
    '--toolbar-offset-y': placeBelow ? '10px' : 'calc(-100% - 10px)',
    '--toolbar-origin-y': placeBelow ? 'top' : 'bottom'
  }
}

function getCanvasSelectionToolbarText(page) {
  const region = selectedEditRegion.value
  if (hasCanvasMultiSelection.value) {
    return `${selectedCanvasRegionCount.value} 个框已选`
  }
  if (!page || !region) {
    return ''
  }
  const [x1, y1, x2, y2] = getEffectiveRegionBBox(region).map((value) => Math.round(Number(value) || 0))
  return `x ${x1} y ${y1} · ${Math.max(0, x2 - x1)} × ${Math.max(0, y2 - y1)}`
}

function isCanvasViewportPanning(pane = 'main') {
  return Boolean(viewportPanState.value && viewportPanState.value.pane === pane)
}

function clearTopbarTaskProgress() {
  if (topbarTaskProgressTimer != null) {
    window.clearTimeout(topbarTaskProgressTimer)
    topbarTaskProgressTimer = null
  }
  topbarTaskProgress.value = {
    label: '',
    current: 0,
    total: 0
  }
}

function setTopbarTaskProgress(label, current, total) {
  if (topbarTaskProgressTimer != null) {
    window.clearTimeout(topbarTaskProgressTimer)
    topbarTaskProgressTimer = null
  }
  topbarTaskProgress.value = {
    label: String(label || '').trim(),
    current: Math.max(0, Number(current) || 0),
    total: Math.max(0, Number(total) || 0)
  }
}

function scheduleClearTopbarTaskProgress(delay = 900) {
  if (typeof window === 'undefined') {
    clearTopbarTaskProgress()
    return
  }
  if (topbarTaskProgressTimer != null) {
    window.clearTimeout(topbarTaskProgressTimer)
  }
  topbarTaskProgressTimer = window.setTimeout(() => {
    clearTopbarTaskProgress()
  }, delay)
}

async function flushUiFrame() {
  await nextTick()
  if (typeof window !== 'undefined' && typeof window.requestAnimationFrame === 'function') {
    await new Promise((resolve) => window.requestAnimationFrame(() => resolve()))
  }
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
    setTopbarTaskProgress('已完成画框，正在准备手动框…', 1, 3)
    await flushUiFrame()
    setTopbarTaskProgress('正在识别新框内的文字…', 2, 3)
    if (isCanvasReviewMode.value) {
      await runCanvasStructuredAction(page, {
        kind: 'create_region',
        bbox
      })
    } else {
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
        throw new Error(payload.detail || '新增手动框失败')
      }
      await loadEditInspection({ silent: true })
      selectedEditPageKey.value = page.stored_name
      selectedEditRegionKey.value = payload.region?.id || selectedEditRegionKey.value
      status.value = '已新增手动框，并自动尝试 OCR / 翻译。'
    }

    setTopbarTaskProgress('正在生成新的可编辑文本框…', 3, 3)
    await flushUiFrame()
    scheduleClearTopbarTaskProgress()
  } catch (error) {
    clearTopbarTaskProgress()
    errorMessage.value = error instanceof Error ? error.message : '新增手动框失败'
  } finally {
    creatingManualRegion.value = false
  }
}

function toggleManualDrawMode() {
  if (!selectedEditPage.value || !canCreateManualRegion.value) {
    return
  }
  const nextEnabled = !manualDrawMode.value
  clearManualDraft({ keepMode: true })
  mergeMode.value = false
  mergeRegionSelection.value = {}
  adjustingRegionId.value = ''
  manualDrawMode.value = nextEnabled
  status.value = nextEnabled
    ? '请在右侧带框页上拖出一个新框，松手后会自动识别并生成文本框。'
    : '已退出手动添加框。'
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

function startCanvasMarqueeSelection(event, page) {
  if (!page || !canDirectManipulateCanvas.value || manualDrawMode.value || adjustingRegionId.value || mergeMode.value) {
    return
  }
  if (spacePanPressed.value || event.button !== 0) {
    return
  }

  const point = getCanvasPoint(event, page)
  canvasMarqueeState.value = {
    pageId: page.stored_name,
    pointerId: event.pointerId,
    startX: point.x,
    startY: point.y,
    currentX: point.x,
    currentY: point.y,
    additive: Boolean(event.shiftKey || event.metaKey || event.ctrlKey),
    moved: false
  }
  event.currentTarget.setPointerCapture?.(event.pointerId)
  event.preventDefault()
}

function updateCanvasMarqueeSelection(event, page) {
  const draft = canvasMarqueeState.value
  if (!draft || !page || draft.pageId !== page.stored_name) {
    return
  }
  if (draft.pointerId != null && event.pointerId != null && draft.pointerId !== event.pointerId) {
    return
  }
  const point = getCanvasPoint(event, page)
  draft.currentX = point.x
  draft.currentY = point.y
  draft.moved = draft.moved || Math.abs(draft.currentX - draft.startX) >= 4 || Math.abs(draft.currentY - draft.startY) >= 4
}

function finishCanvasMarqueeSelection(event, page) {
  const draft = canvasMarqueeState.value
  if (!draft || !page || draft.pageId !== page.stored_name) {
    return
  }
  if (draft.pointerId != null && event.pointerId != null && draft.pointerId !== event.pointerId) {
    return
  }
  if (event.currentTarget.hasPointerCapture?.(draft.pointerId)) {
    event.currentTarget.releasePointerCapture?.(draft.pointerId)
  }

  const marqueeBBox = getCanvasMarqueeBBox(page)
  canvasMarqueeState.value = null
  if (!draft.moved || !marqueeBBox) {
    clearCanvasRegionSelection()
    return
  }

  const selectedIds = (page.regions || [])
    .filter((region) => doBBoxesIntersect(marqueeBBox, getEffectiveRegionBBox(region)))
    .map((region) => region.id)
  const nextIds = draft.additive
    ? Array.from(new Set([...selectedCanvasRegionIds.value, ...selectedIds]))
    : selectedIds
  setCanvasRegionSelection(nextIds, nextIds[0] || '')
}

function handleCanvasStagePointerDown(event, page) {
  if (manualDrawMode.value || adjustingRegionId.value) {
    startManualDraw(event, page)
    return
  }
  startCanvasMarqueeSelection(event, page)
}

function handleCanvasStagePointerMove(event, page) {
  updateManualDraw(event, page)
  updateCanvasMarqueeSelection(event, page)
}

function handleCanvasStagePointerUp(event, page) {
  if (manualDrawDraft.value) {
    void finishManualDraw(event, page)
    return
  }
  finishCanvasMarqueeSelection(event, page)
}

function cancelCanvasStagePointerInteraction() {
  clearManualDraft({ keepMode: true })
  canvasMarqueeState.value = null
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
    status.value = draft.action === 'adjust'
      ? '请继续拖出新的框范围，松手后会自动替换当前文本框。'
      : '请拖出一个足够大的新框，松手后会自动补充并识别。'
    event.preventDefault()
    return
  }

  const bbox = getManualDraftBBox(page)
  if (!bbox || bbox[2] - bbox[0] < 8 || bbox[3] - bbox[1] < 8) {
    clearManualDraft({ keepMode: true })
    status.value = adjustingRegionId.value ? '调整后的框太小了，拖大一点会更稳。' : '手动框太小了，拖大一点会更稳。'
    return
  }

  const draftAction = draft.action
  const draftRegionId = draft.regionId
  clearManualDraft({ keepMode: draftAction === 'adjust' })
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
      throw new Error(payload.detail || '删除手动框失败')
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
    status.value = '已删除这个手动框。'
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '删除手动框失败'
  } finally {
    creatingManualRegion.value = false
  }
}

function applyReviewInspectionPayload(payload) {
  reviewInspectionPages.value = payload.pages || []
  if (payload.workflow_stage) {
    workflowStage.value = payload.workflow_stage
  }
  const sessionOverrides = payload?.overrides && typeof payload.overrides === 'object' ? payload.overrides : {}
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
  applyInspectionOverrides({
    translation_region_overrides: nextOverrides,
    translation_region_skip_overrides: nextSkipOverrides,
    translation_region_disabled_overrides: nextDisabledOverrides,
    translation_region_layout_overrides: nextLayoutOverrides,
  })
}

function applyStyleInspectionPayload(payload) {
  styleInspectionPages.value = payload.pages || []
  if (payload.workflow_stage) {
    workflowStage.value = payload.workflow_stage
  }
  applyInspectionOverrides(payload?.overrides)
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
    reviewInspectionRequestToken += 1
    reviewInspectionPages.value = []
    syncEditSelection()
    return
  }

  const requestToken = ++reviewInspectionRequestToken
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

    if (requestToken !== reviewInspectionRequestToken) {
      return
    }
    applyReviewInspectionPayload(payload)
    syncEditSelection()
  } catch (error) {
    if (requestToken !== reviewInspectionRequestToken) {
      return
    }
    errorMessage.value = error instanceof Error ? error.message : '读取翻译审校结果失败'
  } finally {
    if (!silent && requestToken === reviewInspectionRequestToken) {
      reviewInspectionLoading.value = false
    }
  }
}

async function loadStyleInspection(options = {}) {
  const silent = Boolean(options.silent)
  if (!sessionId.value || config.value.font_style_mode !== 'auto-map') {
    styleInspectionRequestToken += 1
    styleInspectionPages.value = []
    syncEditSelection()
    return
  }

  const requestToken = ++styleInspectionRequestToken
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

    if (requestToken !== styleInspectionRequestToken) {
      return
    }
    applyStyleInspectionPayload(payload)
    syncEditSelection()
  } catch (error) {
    if (requestToken !== styleInspectionRequestToken) {
      return
    }
    errorMessage.value = error instanceof Error ? error.message : '读取字体样式识别结果失败'
  } finally {
    if (!silent && requestToken === styleInspectionRequestToken) {
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
  translationInputDrafts.value = pruneRegionDraftMap(translationInputDrafts.value, mergedInspectionPages.value)
  fontSizeInputDrafts.value = pruneRegionDraftMap(fontSizeInputDrafts.value, mergedInspectionPages.value)
  fontSizeDraftOriginOverrides.value = pruneRegionDraftMap(fontSizeDraftOriginOverrides.value, mergedInspectionPages.value)
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
  const normalizedTranslation = normalizeRegionTranslationOverride(nextTranslation)
  const machineTranslation = normalizeRegionTranslationOverride(region.machine_translation)
  const nextOverrides = { ...translationRegionOverrides.value }

  if (!normalizedTranslation || normalizedTranslation === machineTranslation) {
    delete nextOverrides[region.id]
  } else {
    nextOverrides[region.id] = normalizedTranslation
  }

  translationRegionOverrides.value = nextOverrides
  selectedEditRegionKey.value = region.id
}

function normalizeRegionTranslationDraft(nextValue) {
  return String(nextValue ?? '').replace(/\r\n?/g, '\n')
}

function normalizeRegionTranslationOverride(nextValue) {
  return normalizeRegionTranslationDraft(nextValue).trim()
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
  if (Object.prototype.hasOwnProperty.call(patch, 'rotation')) {
    currentOverride.rotation = Math.round(clampNumber(patch.rotation, -180, 180, 0) * 100) / 100
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'stroke_width')) {
    currentOverride.stroke_width = Math.round(clampNumber(patch.stroke_width, 0, 1, defaultStrokeStrength) * 1000) / 1000
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'letter_spacing')) {
    currentOverride.letter_spacing = Math.round(clampNumber(patch.letter_spacing, 0.5, 2.5, 1) * 1000) / 1000
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'line_spacing')) {
    currentOverride.line_spacing = Math.round(clampNumber(patch.line_spacing, 0.5, 2.5, 1.08) * 1000) / 1000
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'fg_color')) {
    currentOverride.fg_color = hexToColorTriplet(patch.fg_color, [21, 34, 52])
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'bg_color')) {
    currentOverride.bg_color = hexToColorTriplet(patch.bg_color, [255, 255, 255])
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'preserve_background')) {
    if (patch.preserve_background) {
      currentOverride.preserve_background = true
    } else {
      delete currentOverride.preserve_background
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

async function requestPageCommands(pageId, commands, runtimeConfig) {
  if (!sessionId.value || !pageId || !Array.isArray(commands) || !commands.length) {
    return null
  }
  const response = await fetch(toApiUrl(`/api/pages/${sessionId.value}/${pageId}/commands`), {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json'
    },
    body: JSON.stringify({
      config: runtimeConfig || buildRuntimeConfig(),
      commands
    })
  })
  const payload = await response.json()
  if (!response.ok) {
    throw new Error(payload.detail || '更新页面编辑状态失败')
  }
  return payload
}

async function applyPageCommands(pageId, commands, options = {}) {
  const normalizedPageId = String(pageId || '').trim()
  if (!normalizedPageId || !Array.isArray(commands) || !commands.length) {
    return { payload: null, isLatest: true, revision: 0 }
  }
  invalidateInspectionRequests()
  const revision = Number(options.revision) || bumpPageCommandRevision(normalizedPageId)
  setPageCommandPending(normalizedPageId, 1)
  return queuePageCommandExecution(normalizedPageId, async () => {
    try {
      const payload = await requestPageCommands(normalizedPageId, commands, buildRuntimeConfig())
      const isLatest = revision === getPageCommandRevision(normalizedPageId)
      if (options.syncPayload !== false && isLatest) {
        applyPageCommandPayload(payload)
      }
      return {
        payload,
        isLatest,
        revision,
      }
    } finally {
      setPageCommandPending(normalizedPageId, -1)
    }
  })
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
    const regionIds = getCommandRegionIds(redoCommands)
    setRegionCommitState(regionIds, 'saving', options?.pendingMessage || '保存中…')
    const result = await applyPageCommands(page.stored_name, redoCommands, { syncPayload: true })
    if (undoCommands.length) {
      pushCanvasHistory(page.stored_name, {
        label: String(options?.label || '编辑文本框'),
        undoCommands,
        redoCommands
      })
    }
    if (options?.successMessage && result?.isLatest !== false) {
      status.value = options.successMessage
    }
    if (options?.focusRegionId) {
      selectedEditPageKey.value = page.stored_name
      selectedEditRegionKey.value = options.focusRegionId
    }
    clearRegionCommitState(regionIds, ['saving'])
  } catch (error) {
    const regionIds = getCommandRegionIds(redoCommands)
    if (typeof options?.rollback === 'function') {
      options.rollback()
    }
    setRegionCommitState(regionIds, 'failed', error instanceof Error ? error.message : '保存失败')
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
      const commandResult = await applyPageCommands(pageId, [{ type: 'create_region', bbox: entry.bbox }])
      const refreshedEntry = {
        ...entry,
        createdRegionId: String(commandResult?.payload?.created_region_id || '')
      }
      replacePageHistoryState(pageId, {
        undo: [...current.undo, refreshedEntry].slice(-maxCanvasHistoryEntries),
        redo: current.redo.slice(0, -1)
      })
      selectedEditRegionKey.value = refreshedEntry.createdRegionId || selectedEditRegionKey.value
    } else if (entry.kind === 'merge_regions') {
      const commandResult = await applyPageCommands(pageId, [{ type: 'merge_regions', region_ids: entry.regionIds || [] }])
      const refreshedEntry = {
        ...entry,
        createdRegionId: String(commandResult?.payload?.created_region_id || '')
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
    const createdRegionId = String(result?.payload?.created_region_id || '')
    const historyEntry = {
      kind: 'create_region',
      label: '新增手动框',
      pageId: page.stored_name,
      bbox,
      createdRegionId
    }
    pushCanvasHistory(page.stored_name, historyEntry)
    if (result?.isLatest !== false) {
      status.value = '已新增手动框，并自动尝试 OCR / 翻译。'
      selectedEditPageKey.value = page.stored_name
      selectedEditRegionKey.value = createdRegionId || selectedEditRegionKey.value
    }
    return
  }

  if (kind === 'merge_regions' && regionIds.length >= 2) {
    const rollbackDisabledOverrides = { ...translationRegionDisabledOverrides.value }
    const nextDisabledOverrides = { ...translationRegionDisabledOverrides.value }
    for (const regionId of regionIds) {
      nextDisabledOverrides[regionId] = true
    }
    translationRegionDisabledOverrides.value = nextDisabledOverrides
    markCanvasPreviewDirty(page.stored_name)
    try {
      result = await applyPageCommands(page.stored_name, [{ type: 'merge_regions', region_ids: regionIds }])
    } catch (error) {
      translationRegionDisabledOverrides.value = rollbackDisabledOverrides
      throw error
    }
    const createdRegionId = String(result?.payload?.created_region_id || '')
    const historyEntry = {
      kind: 'merge_regions',
      label: '合并文本框',
      pageId: page.stored_name,
      regionIds,
      createdRegionId
    }
    pushCanvasHistory(page.stored_name, historyEntry)
    if (result?.isLatest !== false) {
      status.value = '已合并选中的文本框。原框会先隐藏，新的合并框可继续编辑。'
      selectedEditPageKey.value = page.stored_name
      selectedEditRegionKey.value = createdRegionId || selectedEditRegionKey.value
    }
    return
  }

  if (kind === 'delete_manual_region' && regionIds.length === 1) {
    const targetRegionId = regionIds[0]
    markCanvasPreviewDirty(page.stored_name)
    const result = await applyPageCommands(page.stored_name, [{ type: 'delete_manual_region', region_id: targetRegionId }])
    const deletedPayload = result?.payload?.deleted_region_payload || {}
    const historyEntry = {
      kind: 'delete_manual_region',
      label: '删除手动框',
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
    if (result?.isLatest !== false) {
      status.value = '已删除这个手动框。'
      selectedEditPageKey.value = page.stored_name
    }
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

  if ((event.shiftKey || event.metaKey || event.ctrlKey) && mode === 'move') {
    toggleCanvasRegionSelection(region)
    suppressCanvasRegionClickUntil = Date.now() + 180
    event.preventDefault()
    event.stopPropagation()
    return
  }

  if (!isRegionSelectedOnCanvas(region)) {
    selectSingleCanvasRegion(region)
  } else {
    selectedEditRegionKey.value = region.id
  }

  if (!canDirectManipulateCanvas.value) {
    return
  }

  if (event.button != null && event.button !== 0) {
    return
  }

  const canvas = resolveCanvasInteractionSurface(event.currentTarget)
  if (!canvas) {
    return
  }

  const geometry = getCanvasGeometryFromSurface(canvas, page, 'main')
  const captureTarget = event.currentTarget instanceof Element ? event.currentTarget : null
  const point = getCanvasPointFromGeometry(geometry, event.clientX, event.clientY, page)
  const dragRegionIds = mode === 'move' && isRegionSelectedOnCanvas(region)
    ? selectedCanvasRegionIds.value
    : [region.id]
  const originBBoxes = {}
  for (const dragRegionId of dragRegionIds) {
    const dragRegion = page.regions?.find((item) => item.id === dragRegionId)
    if (dragRegion) {
      originBBoxes[dragRegionId] = getEffectiveRegionBBox(dragRegion)
    }
  }
  canvasTransformState.value = {
    pointerId: event.pointerId,
    captureTarget,
    regionId: region.id,
    pageId: page.stored_name,
    mode,
    handle,
    geometry,
    startPoint: point,
    originBBox: getEffectiveRegionBBox(region),
    originBBoxes,
    regionIds: Object.keys(originBBoxes),
    currentBBox: getEffectiveRegionBBox(region),
    currentBBoxes: { ...originBBoxes },
    moved: false,
    startedAt: Date.now()
  }

  if (captureTarget?.setPointerCapture && event.pointerId != null) {
    try {
      captureTarget.setPointerCapture(event.pointerId)
    } catch (_error) {
      // Ignore browsers/elements that don't support explicit pointer capture here.
    }
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

  const point = getCanvasPointFromGeometry(draft.geometry, event.clientX, event.clientY, page)
  let deltaX = point.x - draft.startPoint.x
  let deltaY = point.y - draft.startPoint.y
  let nextBBox = draft.originBBox

  if (draft.mode === 'move') {
    if (event.shiftKey) {
      if (Math.abs(deltaX) >= Math.abs(deltaY)) {
        deltaY = 0
      } else {
        deltaX = 0
      }
    }
    nextBBox = translateBBoxWithinPage(draft.originBBox, deltaX, deltaY, page)
  } else {
    nextBBox = resizeBBoxWithinPage(draft.originBBox, draft.handle, deltaX, deltaY, page, {
      proportional: event.shiftKey,
      fromCenter: event.altKey
    })
  }

  const changed = nextBBox.some((value, index) => value !== draft.originBBox[index])
  const effectiveDeltaX = draft.mode === 'move' ? nextBBox[0] - draft.originBBox[0] : 0
  const effectiveDeltaY = draft.mode === 'move' ? nextBBox[1] - draft.originBBox[1] : 0
  const nextBBoxes = { ...(draft.currentBBoxes || {}) }
  if (draft.mode === 'move' && Array.isArray(draft.regionIds) && draft.regionIds.length > 1) {
    for (const regionId of draft.regionIds) {
      const originBBox = draft.originBBoxes?.[regionId]
      const dragRegion = page.regions?.find((item) => item.id === regionId)
      if (!originBBox || !dragRegion) {
        continue
      }
      const targetBBox = translateBBoxWithinPage(originBBox, effectiveDeltaX, effectiveDeltaY, page)
      nextBBoxes[regionId] = targetBBox
      updateRegionLayoutOverride(regionId, { bbox: targetBBox })
    }
  } else {
    nextBBoxes[draft.regionId] = nextBBox
    updateRegionLayoutOverride(draft.regionId, { bbox: nextBBox })
  }

  draft.moved = draft.moved || changed
  draft.currentBBox = nextBBox
  draft.currentBBoxes = nextBBoxes
  draft.proportional = Boolean(draft.mode !== 'move' && event.shiftKey)
  draft.fromCenter = Boolean(event.altKey)
  draft.axisLocked = Boolean(draft.mode === 'move' && event.shiftKey)
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
  if (draft.captureTarget?.releasePointerCapture && draft.pointerId != null) {
    try {
      draft.captureTarget.releasePointerCapture(draft.pointerId)
    } catch (_error) {
      // Ignore if capture has already been released.
    }
  }
  if (!draft.moved) {
    return
  }

  suppressCanvasRegionClickUntil = Date.now() + 180
  const page = mergedInspectionPages.value.find((item) => item.stored_name === draft.pageId)
  const nextBBox = Array.isArray(draft.currentBBox) ? draft.currentBBox : null
  if (!page || !nextBBox) {
    return
  }
  const movedRegionIds = Array.isArray(draft.regionIds) && draft.regionIds.length
    ? draft.regionIds
    : [draft.regionId]
  const redoCommands = []
  const undoCommands = []
  for (const regionId of movedRegionIds) {
    const originBBox = draft.originBBoxes?.[regionId] || (regionId === draft.regionId ? draft.originBBox : null)
    const currentBBox = draft.currentBBoxes?.[regionId] || (regionId === draft.regionId ? nextBBox : null)
    if (!originBBox || !currentBBox) {
      continue
    }
    const changed = currentBBox.some((value, index) => value !== originBBox[index])
    if (!changed) {
      continue
    }
    redoCommands.push({
      type: 'update_region_bbox',
      region_id: regionId,
      bbox: currentBBox
    })
    undoCommands.push({
      type: 'update_region_bbox',
      region_id: regionId,
      bbox: originBBox
    })
  }
  if (!redoCommands.length) {
    return
  }

  await runCanvasCommand(page, {
    label: draft.mode === 'move'
      ? (redoCommands.length > 1 ? `移动 ${redoCommands.length} 个文本框` : '移动文本框')
      : '调整文本框大小',
    redoCommands,
    undoCommands,
    successMessage: draft.mode === 'move'
      ? (redoCommands.length > 1 ? `已移动 ${redoCommands.length} 个文本框。` : '已移动文本框位置，重新嵌字时会按新位置计算。')
      : '已更新文本框大小，重新嵌字时会按新框重新排版。',
    focusRegionId: draft.regionId,
    rollback: () => {
      for (const regionId of movedRegionIds) {
        const originBBox = draft.originBBoxes?.[regionId]
        if (originBBox) {
          updateRegionLayoutOverride(regionId, { bbox: originBBox })
        }
      }
    }
  })
}

function cancelCanvasRegionTransform() {
  const draft = canvasTransformState.value
  if (!draft) {
    return
  }
  if (draft.captureTarget?.releasePointerCapture && draft.pointerId != null) {
    try {
      draft.captureTarget.releasePointerCapture(draft.pointerId)
    } catch (_error) {
      // Ignore if capture has already been released.
    }
  }
  for (const [regionId, originBBox] of Object.entries(draft.originBBoxes || { [draft.regionId]: draft.originBBox })) {
    updateRegionLayoutOverride(regionId, { bbox: originBBox })
  }
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

function isAdvancedStylePopoverOpen(region, page) {
  return Boolean(
    region?.id
    && page?.stored_name
    && advancedStylePopover.value.regionId === region.id
    && advancedStylePopover.value.pageId === page.stored_name
  )
}

function toggleAdvancedStylePopover(region, page) {
  if (!region?.id || !page?.stored_name) {
    return
  }
  selectedEditRegionKey.value = region.id
  if (isAdvancedStylePopoverOpen(region, page)) {
    advancedStylePopover.value = { pageId: '', regionId: '' }
    return
  }
  advancedStylePopover.value = { pageId: page.stored_name, regionId: region.id }
}

function closeAdvancedStylePopover() {
  advancedStylePopover.value = { pageId: '', regionId: '' }
}

function getRegionAdvancedStyleSnapshot(region) {
  return {
    rotation: getRegionRotation(region),
    stroke_width: getRegionStrokeStrength(region),
    letter_spacing: getRegionLetterSpacing(region),
    line_spacing: getRegionLineSpacing(region),
    fg_color: hexToColorTriplet(getRegionTextColorHex(region), [21, 34, 52]),
    bg_color: hexToColorTriplet(getRegionStrokeColorHex(region), [255, 255, 255]),
    preserve_background: shouldPreserveRegionBackground(region)
  }
}

function normalizeAdvancedStylePatch(patch) {
  const normalized = {}
  if (Object.prototype.hasOwnProperty.call(patch, 'rotation')) {
    normalized.rotation = Math.round(clampNumber(patch.rotation, -180, 180, 0) * 100) / 100
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'stroke_width')) {
    normalized.stroke_width = Math.round(clampNumber(patch.stroke_width, 0, 1, defaultStrokeStrength) * 1000) / 1000
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'letter_spacing')) {
    normalized.letter_spacing = Math.round(clampNumber(patch.letter_spacing, 0.5, 2.5, 1) * 1000) / 1000
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'line_spacing')) {
    normalized.line_spacing = Math.round(clampNumber(patch.line_spacing, 0.5, 2.5, 1.08) * 1000) / 1000
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'fg_color')) {
    normalized.fg_color = hexToColorTriplet(patch.fg_color, [21, 34, 52])
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'bg_color')) {
    normalized.bg_color = hexToColorTriplet(patch.bg_color, [255, 255, 255])
  }
  if (Object.prototype.hasOwnProperty.call(patch, 'preserve_background')) {
    normalized.preserve_background = Boolean(patch.preserve_background)
  }
  return normalized
}

function updateRegionAdvancedStyle(region, patch, label = '修改样式') {
  const page = selectedEditPage.value
  const normalizedPatch = normalizeAdvancedStylePatch(patch)
  if (!region?.id || !Object.keys(normalizedPatch).length) {
    return
  }
  const previousSnapshot = getRegionAdvancedStyleSnapshot(region)
  const undoPatch = Object.fromEntries(
    Object.keys(normalizedPatch).map((key) => [key, previousSnapshot[key]])
  )
  const sameValue = Object.entries(normalizedPatch).every(([key, value]) => {
    const previousValue = previousSnapshot[key]
    return JSON.stringify(value) === JSON.stringify(previousValue)
  })
  if (sameValue) {
    return
  }

  updateRegionLayoutOverride(region.id, normalizedPatch)
  selectedEditRegionKey.value = region.id

  if (!isCanvasReviewMode.value || !page) {
    return
  }

  void runCanvasCommand(page, {
    label,
    redoCommands: [
      {
        type: 'update_region_style',
        region_id: region.id,
        ...normalizedPatch
      }
    ],
    undoCommands: [
      {
        type: 'update_region_style',
        region_id: region.id,
        ...undoPatch
      }
    ],
    successMessage: '已更新文本框样式。',
    focusRegionId: region.id,
    rollback: () => {
      updateRegionLayoutOverride(region.id, undoPatch)
    }
  })
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

function canApplyRegionFontToPage(region) {
  return Boolean(
    selectedEditPage.value
    && getEffectiveRegionFontId(region)
    && selectedEditPage.value.regions?.length
  )
}

function applyRegionFontToPage(region) {
  const page = selectedEditPage.value
  const targetFontId = getEffectiveRegionFontId(region)
  if (!page || !targetFontId) {
    return
  }

  const redoCommands = []
  const undoCommands = []
  const rollbackOverrides = { ...translationRegionLayoutOverrides.value }
  let hasChanges = false

  for (const targetRegion of page.regions || []) {
    const previousFontId = getRegionFontOverrideId(targetRegion)
    if (previousFontId === targetFontId) {
      continue
    }
    hasChanges = true
    updateRegionLayoutOverride(targetRegion.id, { font_key: targetFontId })
    redoCommands.push({
      type: 'update_region_font',
      region_id: targetRegion.id,
      font_key: targetFontId
    })
    undoCommands.push({
      type: 'update_region_font',
      region_id: targetRegion.id,
      font_key: previousFontId
    })
  }

  if (!hasChanges) {
    status.value = '本页文本框已经都是这个字体了。'
    return
  }

  selectedEditRegionKey.value = region.id
  void warmPreviewFonts()

  void runCanvasCommand(page, {
    label: '整页统一字体',
    redoCommands,
    undoCommands,
    successMessage: '已将本页所有文本框设为当前字体。',
    focusRegionId: region.id,
    rollback: () => {
      translationRegionLayoutOverrides.value = rollbackOverrides
      if (selectedEditPage.value?.stored_name) {
        markCanvasPreviewDirty(selectedEditPage.value.stored_name)
      }
      void warmPreviewFonts()
    }
  })
}

function canApplyRegionFontSizeToPage(region) {
  return Boolean(
    selectedEditPage.value
    && Number.isFinite(Number(getRegionFontSize(region)))
    && selectedEditPage.value.regions?.length
  )
}

function applyRegionFontSizeToPage(region) {
  const page = selectedEditPage.value
  const targetFontSize = Math.max(8, Math.min(240, Math.round(Number(getRegionFontSize(region) || 0))))
  if (!page || !Number.isFinite(targetFontSize)) {
    return
  }

  const redoCommands = []
  const undoCommands = []
  const rollbackOverrides = { ...translationRegionLayoutOverrides.value }
  let hasChanges = false

  for (const targetRegion of page.regions || []) {
    const currentOverride = translationRegionLayoutOverrides.value[targetRegion.id] || {}
    const previousExplicit = typeof currentOverride.font_size === 'number' ? currentOverride.font_size : null
    if (previousExplicit === targetFontSize) {
      continue
    }
    hasChanges = true
    updateRegionLayoutOverride(targetRegion.id, { font_size: targetFontSize })
    redoCommands.push({
      type: 'update_font_size',
      region_id: targetRegion.id,
      font_size: targetFontSize
    })
    undoCommands.push(
      previousExplicit == null
        ? {
            type: 'update_font_size',
            region_id: targetRegion.id
          }
        : {
            type: 'update_font_size',
            region_id: targetRegion.id,
            font_size: previousExplicit
          }
    )
  }

  if (!hasChanges) {
    status.value = '本页文本框已经都是这个字号了。'
    return
  }

  selectedEditRegionKey.value = region.id
  void runCanvasCommand(page, {
    label: '整页统一字号',
    redoCommands,
    undoCommands,
    successMessage: '已将本页所有文本框设为当前字号。',
    focusRegionId: region.id,
    rollback: () => {
      translationRegionLayoutOverrides.value = rollbackOverrides
      if (selectedEditPage.value?.stored_name) {
        markCanvasPreviewDirty(selectedEditPage.value.stored_name)
      }
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
  const normalizedDraftValue = normalizeRegionTranslationDraft(nextValue)
  if (isCanvasReviewMode.value) {
    setRegionCommitState(region.id, 'dirty', '草稿未保存')
    translationInputDrafts.value = {
      ...translationInputDrafts.value,
      [region.id]: normalizedDraftValue
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
  const hasDraft = Object.prototype.hasOwnProperty.call(translationInputDrafts.value, region.id)
  if (!hasDraft) {
    return
  }
  const draftValue = normalizeRegionTranslationDraft(
    translationInputDrafts.value[region.id] ?? getEditRegionText(region)
  )
  const normalizedTranslation = normalizeRegionTranslationOverride(draftValue)
  const previousOverride = normalizeRegionTranslationOverride(translationRegionOverrides.value[region.id] || '')
  if (normalizedTranslation === previousOverride) {
    const nextDrafts = { ...translationInputDrafts.value }
    delete nextDrafts[region.id]
    translationInputDrafts.value = nextDrafts
    clearRegionCommitState(region.id, ['dirty', 'failed'])
    return
  }

  const nextOverrides = { ...translationRegionOverrides.value }
  if (!normalizedTranslation || normalizedTranslation === normalizeRegionTranslationOverride(region.machine_translation)) {
    delete nextOverrides[region.id]
  } else {
    nextOverrides[region.id] = normalizedTranslation
  }
  translationRegionOverrides.value = nextOverrides
  const nextDrafts = { ...translationInputDrafts.value }
  delete nextDrafts[region.id]
  translationInputDrafts.value = nextDrafts

  void runCanvasCommand(selectedEditPage.value, {
    label: '修改译文',
    pendingMessage: '译文保存中…',
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
      translationInputDrafts.value = {
        ...translationInputDrafts.value,
        [region.id]: draftValue
      }
    }
  })
}

function handleRegionFontSizeInput(region, nextValue) {
  selectedEditRegionKey.value = region.id
  if (isCanvasReviewMode.value) {
    setRegionCommitState(region.id, 'dirty', '字号草稿未保存')
  }
  const rawValue = String(nextValue ?? '')
  if (!Object.prototype.hasOwnProperty.call(fontSizeDraftOriginOverrides.value, region.id)) {
    fontSizeDraftOriginOverrides.value = {
      ...fontSizeDraftOriginOverrides.value,
      [region.id]: getRegionExplicitFontSizeOverride(region.id)
    }
  }
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
  const hasOriginSnapshot = Object.prototype.hasOwnProperty.call(fontSizeDraftOriginOverrides.value, region.id)
  const previousExplicit = hasOriginSnapshot
    ? fontSizeDraftOriginOverrides.value[region.id]
    : getRegionExplicitFontSizeOverride(region.id)
  const nextOrigins = { ...fontSizeDraftOriginOverrides.value }
  delete nextOrigins[region.id]
  fontSizeDraftOriginOverrides.value = nextOrigins

  if (Number.isNaN(nextValue)) {
    clearRegionCommitState(region.id, ['dirty'])
    return
  }
  const normalizedValue = Math.max(8, Math.min(240, Math.round(nextValue)))

  if (!isCanvasReviewMode.value || !selectedEditPage.value) {
    updateRegionFontSize(region, normalizedValue)
    return
  }

  updateRegionLayoutOverride(region.id, { font_size: normalizedValue })
  void runCanvasCommand(selectedEditPage.value, {
    label: '调整字号',
    pendingMessage: '字号保存中…',
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
      if (Object.keys(current).length) {
        rollbackOverrides[region.id] = current
      } else {
        delete rollbackOverrides[region.id]
      }
      translationRegionLayoutOverrides.value = rollbackOverrides
      if (selectedEditPage.value?.stored_name) {
        markCanvasPreviewDirty(selectedEditPage.value.stored_name)
      }
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

function clearStoredAdvancedEraseApiKey() {
  config.value.advanced_erase_api_key = ''
  saveStoredConfig(config.value)
  status.value = '已清除本机浏览器里保存的高级擦除 API Key。'
}

function clearTranslatorApiKey() {
  clearStoredApiKey()
  appSettingsValidation.value = { ok: null, message: '', preview: '' }
}

function clearImageCleanupApiKey() {
  clearStoredImageApiKey()
  appSettingsValidation.value = { ok: null, message: '', preview: '' }
}

function clearAdvancedEraseApiKey() {
  clearStoredAdvancedEraseApiKey()
  appSettingsValidation.value = { ok: null, message: '', preview: '' }
}

async function scrollSelectedRegionCardIntoView() {
  if (!selectedEditRegionKey.value) {
    return
  }
  await nextTick()
  const card = Array.from(document.querySelectorAll('.style-region-card'))
    .find((element) => element instanceof HTMLElement && element.dataset.regionId === selectedEditRegionKey.value)
  if (card instanceof HTMLElement) {
    scrollElementWithinContainer(card, '.v2-region-list')
  }
}

async function scrollSelectedPageRailItemIntoView() {
  if (!selectedEditPageKey.value) {
    return
  }
  await nextTick()
  const pageButton = Array.from(document.querySelectorAll('.v2-page-rail-item'))
    .find((element) => element instanceof HTMLElement && element.dataset.pageKey === selectedEditPageKey.value)
  if (pageButton instanceof HTMLElement) {
    scrollElementWithinContainer(pageButton, '.v2-page-rail-list')
  }
}

function scrollElementWithinContainer(element, containerSelector, padding = 12) {
  const container = element.closest(containerSelector)
  if (!(container instanceof HTMLElement)) {
    return
  }
  const containerRect = container.getBoundingClientRect()
  const elementRect = element.getBoundingClientRect()
  const topOverflow = elementRect.top - containerRect.top
  const bottomOverflow = elementRect.bottom - containerRect.bottom
  if (topOverflow < padding) {
    container.scrollTop = Math.max(0, container.scrollTop + topOverflow - padding)
    return
  }
  if (bottomOverflow > -padding) {
    container.scrollTop += bottomOverflow + padding
  }
}

async function syncTranslatedPreviewScale() {
  await nextTick()
  refreshTranslatedPreviewScale()
}

function handleGlobalCanvasKeydown(event) {
  if (v2View.value !== 'review' || !selectedEditPage.value) {
    return
  }
  if (isEditableTextTarget(event.target)) {
    return
  }
  if (isCanvasReviewMode.value && event.code === 'Space') {
    spacePanPressed.value = true
    event.preventDefault()
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
  } else if (event.key === 'PageUp' || event.key === '[') {
    event.preventDefault()
    selectAdjacentEditPage(-1)
  } else if (event.key === 'PageDown' || event.key === ']') {
    event.preventDefault()
    selectAdjacentEditPage(1)
  } else if (event.altKey && event.key === 'ArrowUp') {
    event.preventDefault()
    selectAdjacentEditRegion(-1)
  } else if (event.altKey && event.key === 'ArrowDown') {
    event.preventDefault()
    selectAdjacentEditRegion(1)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    clearManualDraft()
    mergeMode.value = false
    mergeRegionSelection.value = {}
    clearCanvasRegionSelection()
    updatePageUiState(selectedEditPage.value.stored_name, (state) => ({
      ...state,
      selectedRegionId: ''
    }))
  } else if (event.shiftKey && event.code === 'Digit1') {
    event.preventDefault()
    setCurrentPageViewportPreset('fit', 'main')
  } else if (event.shiftKey && event.code === 'Digit2' && selectedEditRegion.value) {
    event.preventDefault()
    focusSelectedRegionInViewport(selectedEditPage.value, 'main')
  } else if (event.shiftKey && event.code === 'Digit0') {
    event.preventDefault()
    setCurrentPageViewportPreset('actual', 'main')
  } else if (event.key === 'Enter' && selectedEditRegion.value) {
    event.preventDefault()
    const input = Array.from(document.querySelectorAll('.translation-review-input'))
      .find((element) => element instanceof HTMLElement && element.dataset.regionId === selectedEditRegion.value.id)
    if (input instanceof HTMLElement) {
      input.focus()
    }
  } else if (isCanvasReviewMode.value && canDirectManipulateCanvas.value && selectedEditRegion.value) {
    const nudgeMap = {
      ArrowUp: [0, -1],
      ArrowDown: [0, 1],
      ArrowLeft: [-1, 0],
      ArrowRight: [1, 0]
    }
    const delta = nudgeMap[event.key]
    if (!delta) {
      return
    }
    event.preventDefault()
    const step = event.shiftKey ? 10 : (event.ctrlKey || event.metaKey) ? 5 : 1
    nudgeSelectedRegion(delta[0] * step, delta[1] * step)
  }
}

function handleGlobalCanvasKeyup(event) {
  if (event.code === 'Space') {
    spacePanPressed.value = false
  }
  if (!pendingCanvasNudge) {
    return
  }
  if (!['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) {
    return
  }
  void flushPendingCanvasNudge()
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

function downloadV2TranslatedResults() {
  if (!canExportTranslatedResults.value || !v2ExportArchiveUrl.value) {
    status.value = '当前还没有可导出的翻译结果。'
    return
  }

  const link = document.createElement('a')
  link.href = v2ExportArchiveUrl.value
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  status.value = '已开始导出翻译结果压缩包。'
}

function getBackendLogHint() {
  const logsDir = String(appRuntime.value?.logs_dir || '').trim()
  return logsDir
    ? `后端日志目录：${logsDir}`
    : '请查看设置里的运行时信息，或查看启动目录下的后端日志。'
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
    ? ['NotoSansCJKtc-Regular.otf', 'msgothic.ttc', 'NotoSansMonoCJK-VF.ttf.ttc']
    : ['NotoSansCJKtc-Regular.otf', 'SourceHanSansSC-Regular-2.otf', 'NotoSansSC-Bold.otf', 'msyh.ttc']

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

function triggerV2UploadPicker() {
  v2UploadInputRef.value?.click()
}

async function startV2NewProject() {
  if (!canStartNewProject.value) {
    return
  }
  closeV2HistoryModal()
  closeV2Settings()
  status.value = '选择一组新的图片或图片包后，会创建一个新的项目。'
  await nextTick()
  triggerV2UploadPicker()
}

function triggerV2SupplementPicker() {
  if (!sessionId.value) {
    status.value = '请先上传原图或恢复一个历史项目，再补充对应文件名的无字图。'
    return
  }
  if (!canUseV2SupplementUpload.value) {
    return
  }
  v2SupplementInputRef.value?.click()
}

function openV2HistoryModal() {
  v2HistoryModalOpen.value = true
  if (!projectHistory.value.length) {
    void loadProjectHistory()
  }
}

function closeV2HistoryModal() {
  v2HistoryModalOpen.value = false
}

function openV2Settings() {
  v2SettingsOpen.value = true
}

function closeV2Settings() {
  v2SettingsOpen.value = false
}

function goToV2Home() {
  v2View.value = 'home'
  closeV2HistoryModal()
  closeV2Settings()
}

function goToV2Picker() {
  if (!v2HasProject.value) {
    v2View.value = 'home'
    return
  }
  v2View.value = 'picker'
  closeV2HistoryModal()
  closeV2Settings()
}

async function submitFileV2() {
  await submitFile()
  if (sessionId.value) {
    v2View.value = 'picker'
    closeV2HistoryModal()
    v2SettingsModalOpen.value = false
    v2UploadDragOver.value = false
  }
}

async function handleV2FileChange(event) {
  onFileChange(event)
  if (selectedFile.value) {
    await submitFileV2()
  }
  if (event?.target) {
    event.target.value = ''
  }
}

async function submitV2SupplementFile(file) {
  if (!file) {
    return
  }
  if (!sessionId.value) {
    status.value = '请先上传原图或恢复一个历史项目，再补充对应文件名的无字图。'
    return
  }

  v2SupplementUploading.value = true
  errorMessage.value = ''
  try {
    const formData = new FormData()
    formData.append('file', file)
    const response = await fetch(toApiUrl(`/api/projects/${sessionId.value}/base-images`), {
      method: 'POST',
      body: formData
    })
    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '补充无字图失败')
    }

    applySessionPayload(payload)
    await loadProjectHistory({ silent: true })
    if (sessionId.value) {
      await loadEditInspection({ silent: true })
    }

    const result = payload.base_image_upload || {}
    const matchedCount = Number(result.matched_count || 0)
    const unmatchedCount = Number(result.unmatched_count || 0)
    status.value = matchedCount > 0
      ? `已补充 ${matchedCount} 页无字图${unmatchedCount ? `，另有 ${unmatchedCount} 个文件未匹配到页面。` : '。'}`
      : '没有找到可匹配的页面文件名。'
  } catch (error) {
    const message = error instanceof Error ? error.message : '补充无字图失败'
    errorMessage.value = message
    status.value = message
  } finally {
    v2SupplementUploading.value = false
  }
}

async function handleV2SupplementFileChange(event) {
  const [file] = event?.target?.files || []
  if (file) {
    await submitV2SupplementFile(file)
  }
  if (event?.target) {
    event.target.value = ''
  }
}

function handleV2DragOver(event) {
  event.preventDefault()
  v2UploadDragOver.value = true
}

function handleV2DragLeave(event) {
  if (event?.currentTarget && event?.relatedTarget && event.currentTarget.contains(event.relatedTarget)) {
    return
  }
  v2UploadDragOver.value = false
}

async function handleV2Drop(event) {
  event.preventDefault()
  v2UploadDragOver.value = false
  const [file] = event.dataTransfer?.files || []
  if (!file) {
    return
  }
  selectedFile.value = file
  errorMessage.value = ''
  await submitFileV2()
}

async function restoreProjectV2(projectId) {
  await restoreProject(projectId)
  if (sessionId.value) {
    closeV2HistoryModal()
    v2View.value = 'picker'
  }
}

async function restoreSnapshotV2(projectId, snapshotId) {
  await restoreSnapshot(projectId, snapshotId)
  if (sessionId.value) {
    closeV2HistoryModal()
    v2View.value = 'picker'
  }
}

async function openV2ReviewPage(pageKey = '') {
  const targetKey = String(pageKey || selectedEditPageKey.value || v2PageEntries.value[0]?.stored_name || '').trim()
  if (!targetKey) {
    return
  }
  await selectEditPageForReview(targetKey)
  v2View.value = 'review'
  await nextTick()
  scheduleCanvasLayoutRefresh()
  preloadReviewImagesAroundPage(targetKey)
  void loadEditInspection({ silent: true })
    .then(() => {
      if (!selectedEditRegionKey.value && selectedEditPage.value?.regions?.length) {
        selectedEditRegionKey.value = selectedEditPage.value.regions[0].id
      }
      preloadReviewImagesAroundPage(targetKey)
      scheduleCanvasLayoutRefresh()
    })
    .catch(() => {
      // Picker should still open even when the review payload is not ready yet.
    })
  if (!selectedEditRegionKey.value && selectedEditPage.value?.regions?.length) {
    selectedEditRegionKey.value = selectedEditPage.value.regions[0].id
  }
}

function runV2ReviewPrimaryAction() {
  if (translating.value) {
    return
  }
  if (canContinueSegmentedTranslation.value) {
    startTranslation('resume-translate')
    return
  }
  if (canTranslateCurrentPage.value) {
    startTranslation(workflowStage.value === 'detected' ? 'translate-page' : 'detect')
    return
  }
  if (canRerender.value) {
    startTranslation('rerender')
    return
  }
  startTranslation('translate')
}

function runV2ProjectPrimaryAction() {
  if (!canRunProjectPrimaryAction.value) {
    return
  }
  startTranslation(primaryTranslateAction.value)
}

function runV2RerenderAction() {
  if (!canRerender.value || translating.value) {
    return
  }
  startTranslation('rerender')
}

function runV2RetranslateAction() {
  if (!canRetranslate.value || translating.value) {
    return
  }
  startTranslation('translate')
}

function getAdvancedEraseConfigError() {
  if (config.value.advanced_erase_provider !== 'volcengine-ark') {
    return '高级擦除第一版仅支持火山引擎 Ark / Seedream。'
  }
  if (!String(config.value.advanced_erase_api_key || '').trim()) {
    return '请先在“高级擦除 API”里填写火山引擎 Ark API Key。'
  }
  if (!String(config.value.advanced_erase_base_url || '').trim()) {
    return '请先填写高级擦除接口地址。'
  }
  if (!String(config.value.advanced_erase_model || '').trim()) {
    return '请先填写高级擦除模型名称。'
  }
  return ''
}

function makeSelectionEraseRect(startX, startY, endX, endY) {
  const x1 = clampValue(Math.min(startX, endX), 0, 1)
  const y1 = clampValue(Math.min(startY, endY), 0, 1)
  const x2 = clampValue(Math.max(startX, endX), 0, 1)
  const y2 = clampValue(Math.max(startY, endY), 0, 1)
  return {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    x: x1,
    y: y1,
    width: Math.max(0, x2 - x1),
    height: Math.max(0, y2 - y1)
  }
}

function getSelectionErasePoint(event) {
  const stage = selectionEraseStageRef.value
  if (!stage) {
    return null
  }
  const rect = stage.getBoundingClientRect()
  if (!rect.width || !rect.height) {
    return null
  }
  return {
    x: clampValue((event.clientX - rect.left) / rect.width, 0, 1),
    y: clampValue((event.clientY - rect.top) / rect.height, 0, 1)
  }
}

function getSelectionEraseRectStyle(rect) {
  return {
    left: `${rect.x * 100}%`,
    top: `${rect.y * 100}%`,
    width: `${rect.width * 100}%`,
    height: `${rect.height * 100}%`
  }
}

function openSelectionEraseModal() {
  if (!canRunAdvancedErase.value) {
    return
  }
  const configError = getAdvancedEraseConfigError()
  if (configError) {
    errorMessage.value = configError
    status.value = '选区擦除未启动。'
    return
  }
  selectionEraseRects.value = []
  selectionEraseDraft.value = null
  selectionEraseModalOpen.value = true
}

function closeSelectionEraseModal() {
  selectionEraseModalOpen.value = false
  selectionEraseDraft.value = null
}

function beginSelectionEraseDraw(event) {
  if (!selectionEraseModalOpen.value || advancedEraseBusy.value) {
    return
  }
  const point = getSelectionErasePoint(event)
  if (!point) {
    return
  }
  event.preventDefault()
  event.currentTarget?.setPointerCapture?.(event.pointerId)
  selectionEraseDraft.value = {
    pointerId: event.pointerId,
    startX: point.x,
    startY: point.y,
    ...makeSelectionEraseRect(point.x, point.y, point.x, point.y)
  }
}

function updateSelectionEraseDraw(event) {
  const draft = selectionEraseDraft.value
  if (!draft || draft.pointerId !== event.pointerId) {
    return
  }
  const point = getSelectionErasePoint(event)
  if (!point) {
    return
  }
  selectionEraseDraft.value = {
    ...draft,
    ...makeSelectionEraseRect(draft.startX, draft.startY, point.x, point.y)
  }
}

function finishSelectionEraseDraw(event) {
  const draft = selectionEraseDraft.value
  if (!draft || draft.pointerId !== event.pointerId) {
    return
  }
  event.currentTarget?.releasePointerCapture?.(event.pointerId)
  if (draft.width >= 0.006 && draft.height >= 0.006) {
    const { pointerId: _pointerId, startX: _startX, startY: _startY, ...rect } = draft
    selectionEraseRects.value = [...selectionEraseRects.value, rect]
  }
  selectionEraseDraft.value = null
}

function cancelSelectionEraseDraw(event) {
  if (selectionEraseDraft.value?.pointerId === event.pointerId) {
    selectionEraseDraft.value = null
  }
}

function removeSelectionEraseRect(rectId) {
  selectionEraseRects.value = selectionEraseRects.value.filter((rect) => rect.id !== rectId)
}

function clearSelectionEraseRects() {
  selectionEraseRects.value = []
  selectionEraseDraft.value = null
}

async function confirmSelectionErase() {
  const selections = selectionEraseRects.value
    .filter((rect) => rect.width > 0 && rect.height > 0)
    .map(({ x, y, width, height }) => ({ x, y, width, height }))
  if (!selections.length) {
    errorMessage.value = '请先框选要擦除的区域。'
    return
  }
  closeSelectionEraseModal()
  await runV2AdvancedEraseAction('selection', { selections })
}

async function runV2AdvancedEraseAction(action = 'erase', options = {}) {
  const page = selectedEditPage.value
  if (!sessionId.value || !page || translating.value || advancedEraseBusy.value) {
    return false
  }

  const normalizedAction = String(action || 'erase').trim().toLowerCase()
  if (normalizedAction !== 'restore') {
    const configError = getAdvancedEraseConfigError()
    if (configError) {
      errorMessage.value = configError
      status.value = '高级擦除未启动。'
      return false
    }
  }

  advancedEraseBusy.value = true
  activeAction.value = 'advanced-erase'
  errorMessage.value = ''
  const pageId = page.stored_name
  status.value = normalizedAction === 'restore'
    ? '正在恢复传统空页…'
    : normalizedAction === 'selection'
      ? '正在进行选区擦除，等待 Seedream 返回图片…'
      : '正在进行高级擦除，等待 Seedream 返回图片…'

  try {
    const requestBody = {
      action: normalizedAction,
      config: buildRuntimeConfig()
    }
    if (normalizedAction === 'selection') {
      requestBody.selections = Array.isArray(options.selections) ? options.selections : []
    }
    let response
    try {
      response = await fetch(toApiUrl(`/api/pages/${sessionId.value}/${pageId}/advanced-erase`), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(requestBody)
      })
    } catch (fetchError) {
      const message = fetchError instanceof Error ? fetchError.message : ''
      throw new Error(
        message.toLowerCase().includes('failed to fetch')
          ? '高级擦除请求连接中断：后端可能在处理大图时重启或被系统终止，请重试；如果再次出现，请查看后端日志。'
          : (message || '高级擦除请求连接中断')
      )
    }
    const payload = await readApiJson(response, '高级擦除失败')
    if (!response.ok) {
      throw new Error(payload.detail || '高级擦除失败')
    }

    markPageImageUpdated(pageId)
    applySessionPayload(payload, { refreshTranslatedPageId: pageId })
    await loadEditInspection({ silent: true })
    preloadReviewImagesAroundPage(pageId)
    scheduleCanvasLayoutRefresh()
    void loadProjectHistory({ silent: true })
    status.value = normalizedAction === 'restore'
      ? '已恢复传统空页。'
      : normalizedAction === 'selection'
        ? '选区擦除完成，空页与框页已刷新。'
        : '高级擦除完成，空页与框页已刷新。'
    return true
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '高级擦除失败'
    status.value = normalizedAction === 'restore'
      ? '恢复传统空页失败。'
      : normalizedAction === 'selection'
        ? '选区擦除失败。'
        : '高级擦除失败。'
    return false
  } finally {
    advancedEraseBusy.value = false
  }
}

function toggleRegionEnabledV2(region) {
  updateRegionDisabledOverride(region, !isRegionDisabled(region))
}

function toggleRegionDirectionV2(region) {
  updateRegionTextDirection(region, isVerticalRegion(region) ? 'horizontal' : 'vertical')
}

function getSelectedBatchRegions() {
  const selectedIds = new Set(selectedCanvasRegionIds.value)
  return (selectedEditPage.value?.regions || []).filter((region) => selectedIds.has(region.id))
}

function snapshotRegionOverrideMaps() {
  return {
    disabled: { ...translationRegionDisabledOverrides.value },
    skip: { ...translationRegionSkipOverrides.value },
    translations: { ...translationRegionOverrides.value },
    layout: { ...translationRegionLayoutOverrides.value },
    style: { ...styleRegionOverrides.value }
  }
}

function restoreRegionOverrideMaps(snapshot) {
  if (!snapshot) {
    return
  }
  translationRegionDisabledOverrides.value = { ...(snapshot.disabled || {}) }
  translationRegionSkipOverrides.value = { ...(snapshot.skip || {}) }
  translationRegionOverrides.value = { ...(snapshot.translations || {}) }
  translationRegionLayoutOverrides.value = { ...(snapshot.layout || {}) }
  styleRegionOverrides.value = { ...(snapshot.style || {}) }
  if (selectedEditPage.value?.stored_name) {
    markCanvasPreviewDirty(selectedEditPage.value.stored_name)
  }
}

function applyBatchRegionEnabled(enabled) {
  const page = selectedEditPage.value
  const regions = getSelectedBatchRegions()
  if (!page || regions.length < 2) {
    return
  }
  const snapshot = snapshotRegionOverrideMaps()
  const redoCommands = []
  const undoCommands = []
  const nextDisabled = { ...translationRegionDisabledOverrides.value }
  const nextSkip = { ...translationRegionSkipOverrides.value }
  const nextTranslations = { ...translationRegionOverrides.value }
  const nextLayout = { ...translationRegionLayoutOverrides.value }
  const nextStyle = { ...styleRegionOverrides.value }

  for (const region of regions) {
    const previousValue = isRegionDisabled(region)
    const nextDisabledValue = !enabled
    if (previousValue === nextDisabledValue) {
      continue
    }
    if (nextDisabledValue) {
      nextDisabled[region.id] = true
      delete nextSkip[region.id]
      delete nextTranslations[region.id]
      delete nextLayout[region.id]
      delete nextStyle[region.id]
      redoCommands.push({ type: 'disable_region', region_id: region.id })
    } else {
      delete nextDisabled[region.id]
      redoCommands.push({ type: 'restore_region', region_id: region.id })
    }
    undoCommands.push(previousValue
      ? { type: 'disable_region', region_id: region.id }
      : { type: 'restore_region', region_id: region.id })
  }

  if (!redoCommands.length) {
    return
  }

  translationRegionDisabledOverrides.value = nextDisabled
  translationRegionSkipOverrides.value = nextSkip
  translationRegionOverrides.value = nextTranslations
  translationRegionLayoutOverrides.value = nextLayout
  styleRegionOverrides.value = nextStyle
  markCanvasPreviewDirty(page.stored_name)

  void runCanvasCommand(page, {
    label: enabled ? `批量启用 ${redoCommands.length} 个文本框` : `批量停用 ${redoCommands.length} 个文本框`,
    redoCommands,
    undoCommands,
    successMessage: enabled ? `已启用 ${redoCommands.length} 个文本框。` : `已停用 ${redoCommands.length} 个文本框。`,
    focusRegionId: selectedEditRegionKey.value || regions[0]?.id,
    rollback: () => restoreRegionOverrideMaps(snapshot)
  })
}

function applyBatchDirection(direction) {
  const page = selectedEditPage.value
  const regions = getSelectedBatchRegions()
  const normalizedDirection = normalizeDirectionValue(direction)
  if (!page || regions.length < 2 || normalizedDirection === 'auto') {
    return
  }
  const snapshot = snapshotRegionOverrideMaps()
  const redoCommands = []
  const undoCommands = []
  for (const region of regions) {
    const previousDirection = getRegionDirectionValue(region)
    if (getResolvedRegionDirection(region) === normalizedDirection && previousDirection === normalizedDirection) {
      continue
    }
    updateRegionLayoutOverride(region.id, { direction: normalizedDirection })
    redoCommands.push({
      type: 'update_text_direction',
      region_id: region.id,
      direction: normalizedDirection
    })
    undoCommands.push({
      type: 'update_text_direction',
      region_id: region.id,
      direction: previousDirection || 'auto'
    })
  }
  if (!redoCommands.length) {
    return
  }
  void runCanvasCommand(page, {
    label: normalizedDirection === 'vertical' ? `批量改为纵排` : `批量改为横排`,
    redoCommands,
    undoCommands,
    successMessage: normalizedDirection === 'vertical' ? `已将 ${redoCommands.length} 个文本框改为纵排。` : `已将 ${redoCommands.length} 个文本框改为横排。`,
    focusRegionId: selectedEditRegionKey.value || regions[0]?.id,
    rollback: () => restoreRegionOverrideMaps(snapshot)
  })
}

function applyBatchFontFromActive() {
  const page = selectedEditPage.value
  const sourceRegion = selectedEditRegion.value
  const regions = getSelectedBatchRegions()
  const targetFontId = sourceRegion ? getEffectiveRegionFontId(sourceRegion) : ''
  if (!page || !sourceRegion || regions.length < 2 || !targetFontId) {
    return
  }
  const snapshot = snapshotRegionOverrideMaps()
  const redoCommands = []
  const undoCommands = []
  for (const region of regions) {
    const previousFontId = getRegionFontOverrideId(region)
    updateRegionLayoutOverride(region.id, { font_key: targetFontId })
    redoCommands.push({ type: 'update_region_font', region_id: region.id, font_key: targetFontId })
    undoCommands.push({ type: 'update_region_font', region_id: region.id, font_key: previousFontId })
  }
  void runCanvasCommand(page, {
    label: `批量套用字体`,
    redoCommands,
    undoCommands,
    successMessage: `已给 ${redoCommands.length} 个文本框套用当前字体。`,
    focusRegionId: sourceRegion.id,
    rollback: () => restoreRegionOverrideMaps(snapshot)
  })
}

function applyBatchFontSizeFromActive() {
  const page = selectedEditPage.value
  const sourceRegion = selectedEditRegion.value
  const regions = getSelectedBatchRegions()
  const targetFontSize = Math.max(8, Math.min(240, Math.round(Number(sourceRegion ? getRegionFontSize(sourceRegion) : 0))))
  if (!page || !sourceRegion || regions.length < 2 || !Number.isFinite(targetFontSize)) {
    return
  }
  const snapshot = snapshotRegionOverrideMaps()
  const redoCommands = []
  const undoCommands = []
  for (const region of regions) {
    const previousFontSize = Number(getRegionFontSize(region) || region.font_size || 12)
    updateRegionLayoutOverride(region.id, { font_size: targetFontSize })
    redoCommands.push({ type: 'update_font_size', region_id: region.id, font_size: targetFontSize })
    undoCommands.push({ type: 'update_font_size', region_id: region.id, font_size: previousFontSize })
  }
  void runCanvasCommand(page, {
    label: `批量套用字号`,
    redoCommands,
    undoCommands,
    successMessage: `已给 ${redoCommands.length} 个文本框套用当前字号。`,
    focusRegionId: sourceRegion.id,
    rollback: () => restoreRegionOverrideMaps(snapshot)
  })
}

function adjustRegionFontSizeV2(region, delta) {
  const currentValue = Number(getRegionFontSize(region) || 0)
  const nextValue = Math.max(8, Math.min(240, Math.round(currentValue + delta)))
  handleRegionFontSizeInput(region, String(nextValue))
  commitRegionFontSize(region)
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
  projectGlossary.value = { version: 1, entries: [] }
  glossaryDraftEntries.value = []
  glossaryPreview.value = { changes: [], change_count: 0, affected_pages: [], affected_page_count: 0 }
  glossaryError.value = ''
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

  resetTranslationCompletionRecovery()
  activeAction.value = action
  const pageTargetStoredName = (action === 'rerender' || action === 'translate-page')
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
    ? (pageTargetStoredName ? '正在启动当前页重嵌字任务...' : '正在启动重嵌字任务...')
    : action === 'detect'
      ? '正在启动文本框识别任务...'
      : action === 'translate-page'
        ? '正在启动当前页翻译任务...'
      : action === 'resume-translate'
        ? '正在继续翻译并嵌字...'
        : '正在启动翻译任务...'
  closeSocket()

  const currentSocket = new WebSocket(toWebSocketUrl(`/ws/translate/${sessionId.value}`))
  socket = currentSocket

  currentSocket.onopen = () => {
    currentSocket.send(JSON.stringify({
      action,
      config: buildRuntimeConfig(),
      target_stored_name: pageTargetStoredName || undefined
    }))
  }

  currentSocket.onmessage = async (event) => {
    const payload = JSON.parse(event.data)

    if (payload.event === 'start') {
      progress.value = { current: 0, total: payload.total_pages }
      status.value = activeAction.value === 'rerender'
        ? (pageTargetStoredName
          ? `当前页重嵌字已开始。`
          : `重嵌字已开始，共 ${payload.total_pages} 张图片。`)
        : activeAction.value === 'detect'
          ? `文本框识别已开始，共 ${payload.total_pages} 张图片。`
          : activeAction.value === 'translate-page'
            ? '当前页翻译已开始。'
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
      if (payload.stored_name) {
        markPageImageUpdated(payload.stored_name)
      }
      const nextImageUrl = getVersionedPageImageUrl(payload.image_url, payload.stored_name)
      preloadImageUrl(getReviewPageImageUrl(nextImageUrl, payload.stored_name))
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
        ? (pageTargetStoredName
          ? `当前页重嵌字进行中…`
          : `重嵌字进行中：${payload.current} / ${payload.total}`)
        : activeAction.value === 'detect'
          ? `正在识别并准备校对：${payload.current} / ${payload.total}`
          : activeAction.value === 'translate-page'
            ? '当前页翻译进行中…'
            : activeAction.value === 'resume-translate'
              ? `继续翻译进行中：${payload.current} / ${payload.total}`
              : `翻译进行中：${payload.current} / ${payload.total}`
      if (Number(payload.total || 0) > 0 && Number(payload.current || 0) >= Number(payload.total || 0)) {
        scheduleTranslationCompletionRecovery({
          sessionId: sessionId.value,
          action: activeAction.value,
          pageTargetStoredName,
          attempt: 1
        })
      }
      return
    }

    if (payload.event === 'status') {
      status.value = payload.message || '正在进行复杂页增强修复...'
      return
    }

    if (payload.event === 'completed') {
      resetTranslationCompletionRecovery()
      translating.value = false
      const completedAction = activeAction.value
      applySessionPayload(payload, {
        refreshTranslatedPageId: (completedAction === 'rerender' || completedAction === 'translate-page') ? pageTargetStoredName : '',
        refreshAllTranslatedImages: completedAction === 'translate' || completedAction === 'resume-translate' || (completedAction === 'rerender' && !pageTargetStoredName)
      })
      markCompletedTranslationAction(completedAction, pageTargetStoredName)
      status.value = getCompletedTranslationStatus(completedAction, pageTargetStoredName)
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
      resetTranslationCompletionRecovery()
      translating.value = false
      errorMessage.value = payload.message || '翻译失败'
      status.value = activeAction.value === 'rerender'
        ? '重嵌字失败。'
        : activeAction.value === 'detect'
          ? '文本框识别失败。'
          : activeAction.value === 'translate-page'
            ? '当前页翻译失败。'
          : activeAction.value === 'resume-translate'
            ? '继续翻译失败。'
            : '翻译失败。'
      closeSocket()
    }
  }

  currentSocket.onerror = () => {
    if (currentSocket !== socket || expectedClosingSockets.has(currentSocket)) {
      return
    }
    resetTranslationCompletionRecovery()
    errorMessage.value = `翻译连接中断。${getBackendLogHint()}`
    status.value = activeAction.value === 'rerender'
      ? '重嵌字连接中断。'
      : activeAction.value === 'detect'
        ? '识别连接中断。'
        : activeAction.value === 'translate-page'
          ? '当前页翻译连接中断。'
        : activeAction.value === 'resume-translate'
          ? '继续翻译连接中断。'
          : '翻译连接中断。'
    translating.value = false
    closeSocket()
  }

  currentSocket.onclose = () => {
    if (expectedClosingSockets.has(currentSocket)) {
      expectedClosingSockets.delete(currentSocket)
      return
    }
    if (currentSocket !== socket) {
      return
    }
    if (translating.value) {
      resetTranslationCompletionRecovery()
      errorMessage.value = `翻译任务意外断开。${getBackendLogHint()}`
      status.value = activeAction.value === 'rerender'
        ? '重嵌字未完成。'
        : activeAction.value === 'detect'
          ? '识别未完成。'
          : activeAction.value === 'translate-page'
            ? '当前页翻译未完成。'
          : activeAction.value === 'resume-translate'
            ? '继续翻译未完成。'
            : '翻译未完成。'
      translating.value = false
    }
    if (socket === currentSocket) {
      socket = null
    }
  }
}

onMounted(() => {
  void (async () => {
    await loadAppRuntime()
    await loadAppDiagnostics()
    checkBackendStatus()
    await Promise.all([
      loadFonts(),
      loadProjectHistory(),
      loadPersistedAppSettings(),
    ])
    scheduleCanvasLayoutRefresh()
  })()
  window.addEventListener('resize', scheduleCanvasLayoutRefresh)
  window.addEventListener('pointermove', updateCanvasViewportPan)
  window.addEventListener('pointerup', finishCanvasViewportPan)
  window.addEventListener('pointercancel', cancelCanvasViewportPan)
  window.addEventListener('pointermove', updateCompareSplitterDrag)
  window.addEventListener('pointerup', finishCompareSplitterDrag)
  window.addEventListener('pointercancel', finishCompareSplitterDrag)
  window.addEventListener('pointermove', updateWorkspaceSplitterDrag)
  window.addEventListener('pointerup', finishWorkspaceSplitterDrag)
  window.addEventListener('pointercancel', finishWorkspaceSplitterDrag)
  window.addEventListener('pointermove', updateCanvasRegionTransform)
  window.addEventListener('pointerup', finishCanvasRegionTransform)
  window.addEventListener('pointercancel', cancelCanvasRegionTransform)
  window.addEventListener('keydown', handleGlobalCanvasKeydown)
  window.addEventListener('keyup', handleGlobalCanvasKeyup)
})

onBeforeUnmount(() => {
  resetTranslationCompletionRecovery()
  closeSocket()
  void flushPendingCanvasNudge()
  clearCanvasNudgeCommitTimer()
  clearTopbarTaskProgress()
  if (canvasLayoutFrame != null) {
    window.cancelAnimationFrame(canvasLayoutFrame)
    canvasLayoutFrame = null
  }
  window.removeEventListener('resize', scheduleCanvasLayoutRefresh)
  window.removeEventListener('pointermove', updateCanvasViewportPan)
  window.removeEventListener('pointerup', finishCanvasViewportPan)
  window.removeEventListener('pointercancel', cancelCanvasViewportPan)
  window.removeEventListener('pointermove', updateCompareSplitterDrag)
  window.removeEventListener('pointerup', finishCompareSplitterDrag)
  window.removeEventListener('pointercancel', finishCompareSplitterDrag)
  window.removeEventListener('pointermove', updateWorkspaceSplitterDrag)
  window.removeEventListener('pointerup', finishWorkspaceSplitterDrag)
  window.removeEventListener('pointercancel', finishWorkspaceSplitterDrag)
  window.removeEventListener('pointermove', updateCanvasRegionTransform)
  window.removeEventListener('pointerup', finishCanvasRegionTransform)
  window.removeEventListener('pointercancel', cancelCanvasRegionTransform)
  window.removeEventListener('keydown', handleGlobalCanvasKeydown)
  window.removeEventListener('keyup', handleGlobalCanvasKeyup)
})

async function warmPreviewFonts() {
  if (typeof document === 'undefined' || !document.fonts) {
    return
  }

  const fontIds = new Set()
  for (const font of availableFonts.value) {
    if (font?.id) {
      if (isPreviewFontDecodeDenied(font)) {
        continue
      }
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
      if (isPreviewFontIdDecodeDenied(fontId)) {
        nextState[fontId] = {
          status: 'unsupported',
          error: 'Skipped for browser preview because this font file is not web-decodable.'
        }
        return
      }
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
    queuePersistAppSettings(nextValue)
  },
  { deep: true }
)

watch(
  reviewWorkspacePrefs,
  (nextValue) => {
    saveStoredReviewWorkspacePrefs(nextValue, sessionId.value)
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
  if (selectedEditPage.value?.stored_name) {
    updatePageUiState(selectedEditPage.value.stored_name, (state) => ({
      ...state,
      selectedRegionId: selectedEditRegionKey.value || ''
    }))
  }
  if (selectedEditRegionKey.value) {
    void scrollSelectedRegionCardIntoView()
  }
  void refreshSelectedRegionPreviewDebug()
})

watch(
  () => selectedEditPageKey.value,
  (nextPageId) => {
    const normalizedPageId = String(nextPageId || '').trim()
    if (!normalizedPageId) {
      return
    }
    const nextPage = mergedInspectionPages.value.find((page) => page.stored_name === normalizedPageId)
    if (!nextPage) {
      return
    }
    const hadStoredCanvasState = Boolean(perPageUiState.value[normalizedPageId])
    if (!hadStoredCanvasState) {
      updatePageUiState(normalizedPageId, {
        ...createDefaultPerPageUiState(),
        selectedRegionId: nextPage.regions[0]?.id || ''
      })
    }
    selectedEditRegionKey.value = String(getPageUiState(normalizedPageId).selectedRegionId || '')
    reconcileCanvasInteractionState()
    void scrollSelectedPageRailItemIntoView()
    preloadReviewImagesAroundPage(normalizedPageId)
    scheduleCanvasLayoutRefresh()
    if (!hadStoredCanvasState && isCanvasReviewMode.value && !autoFitCanvasPageIds.has(normalizedPageId)) {
      autoFitCanvasPageIds.add(normalizedPageId)
      void nextTick(() => {
        setCanvasViewportPreset(nextPage, 'main', 'fit')
      })
    }
  }
)

watch(
  () => [
    selectedEditPage.value?.stored_name || '',
    selectedEditPage.value?.translated_image_url || '',
    selectedEditPage.value?.base_image_url || '',
    isCanvasReviewMode.value,
    getViewportState(selectedEditPage.value?.stored_name || '', 'main').zoom
  ],
  () => {
    scheduleCanvasLayoutRefresh()
    void syncTranslatedPreviewScale()
    void refreshSelectedRegionPreviewDebug()
  }
)

watch(
  () => [
    config.value.workspace_width_mode,
    reviewWorkspacePrefs.value.split_ratio,
    reviewWorkspacePrefs.value.page_rail_width,
    reviewWorkspacePrefs.value.inspector_width,
    reviewWorkspacePrefs.value.compare_pane_mode,
    JSON.stringify(reviewWorkspacePrefs.value.compare_pane_modes || []),
    reviewWorkspacePrefs.value.compare_sync_enabled
  ],
  () => {
    void nextTick(() => {
      scheduleCanvasLayoutRefresh()
      if (selectedEditPage.value?.stored_name) {
        updateViewportState(
          selectedEditPage.value.stored_name,
          'compare',
          { zoom: 1, panX: 0, panY: 0 },
          { syncCompare: false }
        )
      }
      void syncTranslatedPreviewScale()
      void refreshSelectedRegionPreviewDebug()
    })
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

watch(
  () => mergedInspectionPages.value,
  () => {
    reconcileCanvasInteractionState()
  },
  { deep: false }
)

watch(
  () => sessionId.value,
  (nextSessionId) => {
    if (!nextSessionId) {
      v2View.value = 'home'
      v2HistoryModalOpen.value = false
      v2SettingsModalOpen.value = false
      return
    }
    if (v2View.value === 'home') {
      v2View.value = 'picker'
    }
  }
)

watch(
  () => v2PageEntries.value.map((page) => page.stored_name).join('|'),
  () => {
    if (!v2PageEntries.value.length) {
      return
    }
    const hasCurrent = v2PageEntries.value.some((page) => page.stored_name === selectedEditPageKey.value)
    if (!hasCurrent) {
      selectedEditPageKey.value = v2PageEntries.value[0].stored_name
    }
  },
  { immediate: true }
)
</script>

<template>
  <component :is="'style'">{{ previewFontFaceCss }}</component>

  <div :class="['v2-app', v2View === 'review' ? 'is-review' : '']">
    <input
      ref="v2UploadInputRef"
      class="v2-hidden-input"
      :accept="acceptValue"
      type="file"
      @change="handleV2FileChange"
    />
    <input
      ref="v2SupplementInputRef"
      class="v2-hidden-input"
      :accept="acceptValue"
      type="file"
      @change="handleV2SupplementFileChange"
    />

    <header :class="['v2-topbar', v2View === 'review' ? 'is-review' : '']">
      <div class="v2-topbar-brand">
        <div class="v2-brand-mark">M</div>
        <div>
          <p class="v2-brand-kicker">MANGAFLOW</p>
          <h1 class="v2-brand-title">{{ v2View === 'review' ? '审校工作台' : '漫画翻译工作台' }}</h1>
        </div>
      </div>

      <div v-if="v2View === 'review'" class="v2-topbar-project">
        <button type="button" class="v2-topbar-button v2-back-button" @click="goToV2Picker">
          ← 返回
        </button>
        <div class="v2-topbar-project-copy">
          <strong>{{ v2ProjectTitle }}</strong>
          <span>{{ v2SelectedPageSummary }}</span>
        </div>
      </div>

      <div class="v2-topbar-actions">
        <template v-if="v2View === 'review'">
          <span class="v2-saved-indicator">{{ v2ReviewSavedLabel }}</span>
          <div
            v-if="v2TopbarStatusVisible"
            :class="['v2-topbar-status', errorMessage ? 'is-error' : '', v2TopbarProgressVisible ? 'is-busy' : '']"
          >
            <div class="v2-topbar-status-copy">
              <span class="v2-topbar-status-dot"></span>
              <span>{{ v2TopbarStatusText }}</span>
            </div>
            <div v-if="v2TopbarProgressVisible" class="v2-topbar-status-progress">
              <div class="v2-topbar-status-track">
                <div class="v2-topbar-status-fill" :style="{ width: `${v2TopbarProgressPercent}%` }"></div>
              </div>
              <span>{{ v2TopbarProgressCurrent }} / {{ v2TopbarProgressTotal }}</span>
            </div>
          </div>
          <button
            type="button"
            class="v2-topbar-button"
            :disabled="!sessionId"
            @click="openProjectGlossaryDrawer"
          >
            专有名词库
          </button>
          <button
            type="button"
            class="v2-topbar-button"
            :disabled="!canExportTranslatedResults"
            @click="downloadV2TranslatedResults"
          >
            导出结果
          </button>
          <button
            type="button"
            class="v2-primary-button"
            :disabled="translating || !sessionId"
            @click="runV2ReviewPrimaryAction"
          >
            {{ v2ReviewSaveLabel }}
          </button>
          <button
            type="button"
            class="v2-icon-button"
            aria-label="打开设置"
            title="打开设置"
            @click="openV2Settings"
          >
            ⚙
          </button>
        </template>

        <template v-else>
          <div class="v2-connection-chip">
            <span :class="['v2-connection-dot', backendOnline ? 'online' : 'offline']"></span>
            <span>{{ backendOnline ? '后端在线' : '后端离线' }}</span>
          </div>
          <div
            v-if="v2TopbarStatusVisible"
            :class="['v2-topbar-status', errorMessage ? 'is-error' : '', v2TopbarProgressVisible ? 'is-busy' : '']"
          >
            <div class="v2-topbar-status-copy">
              <span class="v2-topbar-status-dot"></span>
              <span>{{ v2TopbarStatusText }}</span>
            </div>
            <div v-if="v2TopbarProgressVisible" class="v2-topbar-status-progress">
              <div class="v2-topbar-status-track">
                <div class="v2-topbar-status-fill" :style="{ width: `${v2TopbarProgressPercent}%` }"></div>
              </div>
              <span>{{ v2TopbarProgressCurrent }} / {{ v2TopbarProgressTotal }}</span>
            </div>
          </div>

          <button
            type="button"
            class="v2-topbar-button"
            @click="openV2HistoryModal"
          >
            历史项目
          </button>

          <button
            v-if="v2View === 'picker'"
            type="button"
            class="v2-topbar-button"
            :disabled="!canStartNewProject"
            @click="startV2NewProject"
          >
            新建项目
          </button>

          <button
            v-if="v2HasProject"
            type="button"
            class="v2-topbar-button"
            :disabled="!canExportTranslatedResults"
            @click="downloadV2TranslatedResults"
          >
            导出结果
          </button>

          <button
            v-if="v2HasProject"
            type="button"
            class="v2-topbar-button"
            :disabled="!canUseV2SupplementUpload"
            :title="sessionId ? '上传与原图同名的无字图，用作当前项目底图' : '请先上传原图或恢复项目'"
            @click="triggerV2SupplementPicker"
          >
            {{ v2SupplementUploading ? '补充中…' : '补充无字图' }}
          </button>

          <button
            type="button"
            class="v2-icon-button"
            aria-label="打开设置"
            @click="openV2Settings"
          >
            ⚙
          </button>
        </template>
      </div>
    </header>

    <main class="v2-main">
      <section v-if="v2View === 'home'" class="v2-home-view" data-testid="v2-home-view">
        <section
          :class="['v2-upload-card', 'v2-home-upload-card', v2UploadDragOver ? 'is-dragover' : '']"
          data-testid="v2-upload-card"
          @dragover="handleV2DragOver"
          @dragenter.prevent="v2UploadDragOver = true"
          @dragleave="handleV2DragLeave"
          @drop="handleV2Drop"
        >
          <button type="button" class="v2-upload-surface v2-upload-surface-home" @click="triggerV2UploadPicker">
            <span class="v2-upload-icon">⬆</span>
            <strong>{{ uploading ? '正在导入素材…' : '上传图片 / 图片包' }}</strong>
            <p>{{ selectedFile ? selectedFile.name : '支持 zip、cbz 和单张图片；点击或拖拽到这里后会直接创建新的项目。' }}</p>
          </button>
        </section>
      </section>

      <section v-else-if="v2View === 'picker'" class="v2-picker-view" data-testid="v2-picker-view">
        <div class="v2-section-head">
          <div>
            <p class="v2-section-kicker">项目图片列表</p>
            <h2 class="v2-section-title">{{ v2ProjectTitle }}</h2>
            <p class="v2-section-subtitle">{{ v2ProjectSubtitle }}</p>
          </div>

          <div class="v2-section-actions">
            <button
              type="button"
              class="v2-primary-button"
              :disabled="!canRunProjectPrimaryAction"
              @click="runV2ProjectPrimaryAction"
            >
              {{ primaryTranslateLabel }}
            </button>
            <button
              type="button"
              class="v2-secondary-button"
              :disabled="!v2PageEntries.length"
              @click="openV2ReviewPage(v2SelectedPageEntry?.stored_name || '')"
            >
              进入审校
            </button>
          </div>
        </div>

        <div class="v2-picker-toolbar">
          <input
            v-model="v2PageSearch"
            class="v2-search-input"
            type="search"
            placeholder="搜索页名 / 状态"
          />
          <div class="v2-picker-toolbar-copy">
            <span>{{ v2FilteredPageEntries.length }} / {{ v2PageEntries.length }} 页</span>
            <span>{{ workflowStageLabelMap[workflowStage] || workflowStage }}</span>
          </div>
        </div>

        <div class="v2-page-grid">
          <button
            v-for="(page, index) in v2FilteredPageEntries"
            :key="page.stored_name"
            type="button"
            :class="['v2-page-card', page.selected ? 'is-selected' : '']"
            @click="openV2ReviewPage(page.stored_name)"
          >
            <div class="v2-page-card-media">
              <img :src="getV2PageCover(page, index)" :alt="page.name" @error="handleV2ImageError($event, index)" />
              <span class="v2-page-card-number">P{{ page.pageNumber }}</span>
            </div>
            <div class="v2-page-card-body">
              <div class="v2-page-card-head">
                <strong>{{ page.name }}</strong>
                <span class="v2-page-status">{{ page.status }}</span>
              </div>
              <div class="v2-page-card-meta">
                <span>{{ page.regionCount || 0 }} 个框</span>
                <span>{{ page.finalUrl ? '已生成结果' : '等待处理' }}</span>
              </div>
            </div>
          </button>
        </div>
      </section>

      <section v-else class="v2-review-view" data-testid="v2-review-view">
        <div class="v2-review-toolbar">
          <div class="v2-review-toolbar-left v2-review-toolbar-spacer" aria-hidden="true"></div>

          <div class="v2-review-toolbar-center">
            <div class="v2-compare-selector" role="group" aria-label="审校对比页面">
              <label
                v-for="option in reviewComparePaneOptions"
                :key="option.key"
                :class="[
                  'v2-compare-chip',
                  isReviewComparePaneSelected(option.key) ? 'active' : '',
                  isReviewComparePaneToggleDisabled(option.key) ? 'disabled' : ''
                ]"
              >
                <input
                  type="checkbox"
                  :checked="isReviewComparePaneSelected(option.key)"
                  :disabled="isReviewComparePaneToggleDisabled(option.key)"
                  @change="toggleReviewComparePaneMode(option.key)"
                />
                <span>{{ option.label }}</span>
              </label>
            </div>
          </div>

          <div class="v2-review-toolbar-right">
            <button
              type="button"
              class="v2-icon-button v2-review-tool-button"
              aria-label="上一页"
              title="上一页"
              :disabled="!canSelectPreviousEditPage"
              @click="selectAdjacentEditPage(-1)"
            >
              ‹
            </button>
            <button
              type="button"
              class="v2-icon-button v2-review-tool-button"
              aria-label="下一页"
              title="下一页"
              :disabled="!canSelectNextEditPage"
              @click="selectAdjacentEditPage(1)"
            >
              ›
            </button>
            <button
              type="button"
              class="v2-ghost-button"
              :disabled="!canRerender"
              title="用当前编辑重新生成嵌字结果"
              @click="runV2RerenderAction"
            >
              重新嵌字
            </button>
            <div class="v2-erase-menu">
              <button
                type="button"
                class="v2-ghost-button"
                :disabled="!canRunAdvancedErase"
                title="用 Seedream 对当前页做高级擦除，生成新的空页"
                @click="runV2AdvancedEraseAction('erase')"
              >
                {{ advancedEraseBusy ? '擦除中…' : '高级擦除' }}
              </button>
              <div class="v2-erase-menu-popover">
                <button
                  type="button"
                  :disabled="!canRunAdvancedErase"
                  @click="openSelectionEraseModal"
                >
                  选区擦除
                </button>
                <button
                  type="button"
                  :disabled="!canRunAdvancedErase"
                  title="恢复高级擦除前的传统空页"
                  @click="runV2AdvancedEraseAction('restore')"
                >
                  恢复传统空页
                </button>
              </div>
            </div>
            <button
              type="button"
              class="v2-ghost-button"
              :disabled="!canRetranslate"
              title="使用当前设置和模型重新翻译整本漫画"
              @click="runV2RetranslateAction"
            >
              重新翻译
            </button>
            <button
              type="button"
              class="v2-icon-button v2-review-tool-button"
              aria-label="撤销"
              title="撤销"
              :disabled="!selectedEditPage || !getPageHistoryState(selectedEditPage.stored_name).undo.length"
              @click="undoCanvasEdit"
            >
              ↶
            </button>
            <button
              type="button"
              class="v2-icon-button v2-review-tool-button"
              aria-label="重做"
              title="重做"
              :disabled="!selectedEditPage || !getPageHistoryState(selectedEditPage.stored_name).redo.length"
              @click="redoCanvasEdit"
            >
              ↷
            </button>
          </div>
        </div>

        <div class="v2-review-layout">
          <aside class="v2-page-rail">
            <div class="v2-page-rail-head">
              <div class="v2-page-rail-headline">
                <span class="v2-pane-label">页面</span>
                <strong>{{ v2PageEntries.length }}</strong>
              </div>
              <span class="v2-page-rail-meta">{{ v2SelectedPagePositionLabel }}</span>
            </div>

            <div class="v2-page-rail-list">
              <button
                v-for="(page, index) in v2FilteredPageEntries"
                :key="page.stored_name"
                :data-page-key="page.stored_name"
                type="button"
                :class="['v2-page-rail-item', page.stored_name === (v2SelectedPageEntry?.stored_name || '') ? 'active' : '']"
                @click="selectEditPageForReview(page.stored_name)"
              >
                <img :src="getV2PageCover(page, index)" :alt="page.name" @error="handleV2ImageError($event, index)" />
                <div class="v2-page-rail-copy">
                  <strong>{{ page.pageNumber }}</strong>
                </div>
                <span class="v2-page-rail-status">{{ page.status }}</span>
              </button>
            </div>
          </aside>

          <section class="v2-review-stage">
            <div class="v2-pane-strip" :style="v2ReviewPaneStripStyle">
              <article
                v-for="pane in selectedReviewComparePanes"
                :key="pane.key"
                :class="[
                  'v2-pane-card',
                  isReviewFramePane(pane.key) ? 'v2-pane-card-frame' : 'v2-pane-card-compare'
                ]"
              >
                <header class="v2-pane-head">
                  <template v-if="isReviewFramePane(pane.key)">
                    <div class="v2-pane-head-copy">
                      <span class="v2-pane-label">框页</span>
                      <span class="v2-pane-subtle">拖框后自动识别生成</span>
                    </div>
                    <div class="v2-pane-actions">
                      <button
                        v-if="selectedEditPage"
                        type="button"
                        :class="['v2-ghost-button', manualDrawMode ? 'active' : '']"
                        :disabled="!canActivateManualDraw"
                        @click="toggleManualDrawMode"
                      >
                        {{ manualDrawMode ? '正在画框…' : '手动添加框' }}
                      </button>
                      <div v-if="selectedEditPage" class="v2-view-controls" aria-label="画布视图控制">
                        <button type="button" @click="setCurrentPageViewportPreset('fit', 'main')">适合</button>
                        <button type="button" @click="setCurrentPageViewportPreset('actual', 'main')">100%</button>
                        <button type="button" @click="setCurrentPageViewportPreset('width', 'main')">适宽</button>
                      </div>
                      <button
                        v-if="selectedEditPage"
                        type="button"
                        class="v2-ghost-button"
                        @click="resetViewportStateForPage(selectedEditPage)"
                      >
                        重置视图
                      </button>
                      <button
                        v-if="selectedEditPage && selectedEditRegion"
                        type="button"
                        class="v2-ghost-button"
                        @click="focusSelectedRegionInViewport(selectedEditPage, 'main')"
                      >
                        定位当前框
                      </button>
                    </div>
                  </template>
                  <template v-else>
                    <span class="v2-pane-label">{{ getReviewComparePaneLabel(pane.key) }}</span>
                  </template>
                </header>

                <div
                  :ref="(element) => setReviewPaneCanvasElement(element, pane.key)"
                  :class="[
                    'v2-canvas-shell',
                    isReviewFramePane(pane.key) ? '' : 'v2-canvas-shell-compare',
                    isCanvasViewportPanning(getReviewComparePaneCanvasRole(pane.key)) ? 'is-panning' : '',
                    spacePanPressed ? 'is-space-ready' : ''
                  ]"
                  :style="{ '--page-aspect': `${Math.max(selectedEditPage?.image_width || 720, 1)} / ${Math.max(selectedEditPage?.image_height || 1024, 1)}` }"
                  title="滚轮平移，按住 Space 平移，Shift 锁轴，Ctrl/⌘ 缩放"
                  @wheel="selectedEditPage && handleCanvasWheel($event, selectedEditPage, getReviewComparePaneCanvasRole(pane.key))"
                  @pointerdown="selectedEditPage && startCanvasViewportPan($event, selectedEditPage, getReviewComparePaneCanvasRole(pane.key))"
                >
                  <div
                    :class="[
                      'v2-canvas-stage',
                      isReviewFramePane(pane.key) ? '' : 'v2-canvas-stage-readonly',
                      isReviewFramePane(pane.key) && (manualDrawMode || isAdjustingRegionBBox) ? 'draw-mode' : ''
                    ]"
                    :style="selectedEditPage ? getCanvasStageStyle(selectedEditPage, getReviewComparePaneCanvasRole(pane.key)) : null"
                    @pointerdown="isReviewFramePane(pane.key) && selectedEditPage && handleCanvasStagePointerDown($event, selectedEditPage)"
                    @pointermove="isReviewFramePane(pane.key) && selectedEditPage && handleCanvasStagePointerMove($event, selectedEditPage)"
                    @pointerup="isReviewFramePane(pane.key) && selectedEditPage && handleCanvasStagePointerUp($event, selectedEditPage)"
                    @pointercancel="isReviewFramePane(pane.key) && cancelCanvasStagePointerInteraction()"
                  >
                    <img
                      v-if="getReviewComparePaneImageUrl(pane.key)"
                      :alt="getReviewComparePaneAlt(pane.key)"
                      :src="getReviewComparePaneImageUrl(pane.key)"
                      @load="scheduleCanvasLayoutRefresh"
                    />
                    <div v-else class="v2-canvas-empty">
                      当前没有可展示的页面图像
                    </div>

                    <template v-if="isReviewFramePane(pane.key) && selectedEditPage">
                      <template
                        v-for="region in selectedEditPage.regions"
                        :key="`main-shell-${region.id}`"
                      >
                        <button
                          type="button"
                          :class="[
                            'style-box',
                            isManualRegion(region) ? 'manual' : '',
                            mergeMode && isRegionSelectedForMerge(region) ? 'merge-selected' : '',
                            isRegionSelectedOnCanvas(region) ? 'multi-selected' : '',
                            selectedEditRegionKey === region.id ? 'active' : '',
                            canDirectManipulateCanvas ? 'canvas-editable' : '',
                            getStyleRegionLabelClass(region, selectedEditPage)
                          ]"
                          :style="getStyleRegionBoxStyle(region, selectedEditPage)"
                          @click="handleCanvasRegionClick($event, region)"
                          @pointerdown.stop="startCanvasRegionTransform($event, region, selectedEditPage, 'move')"
                        >
                          <span class="style-box-label">{{ region.index + 1 }}</span>
                          <span
                            v-if="shouldShowSourceCropPreview(region, selectedEditPage)"
                            class="style-box-source-crop"
                          >
                            <img
                              :src="getReviewPageImageUrl(selectedEditPage.source_image_url || selectedEditPage.image_url, selectedEditPage.stored_name)"
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
                            :key="`main-${region.id}-${handle}`"
                            :class="['style-box-handle', `handle-${handle}`]"
                            :style="{ cursor: getCanvasHandleCursor(handle) }"
                            @pointerdown.stop="startCanvasRegionTransform($event, region, selectedEditPage, 'resize', handle)"
                          ></span>
                        </button>
                        <button
                          v-if="canDirectManipulateCanvas"
                          type="button"
                          :class="['style-box-settings-button', isAdvancedStylePopoverOpen(region, selectedEditPage) ? 'active' : '']"
                          :style="getStyleRegionSettingsButtonStyle(region, selectedEditPage)"
                          :aria-label="`打开第 ${region.index + 1} 个文本框样式设置`"
                          @pointerdown.stop
                          @click.stop="toggleAdvancedStylePopover(region, selectedEditPage)"
                        >
                          ⚙
                        </button>
                        <Teleport to="body">
                          <section
                            v-if="isAdvancedStylePopoverOpen(region, selectedEditPage)"
                            class="style-advanced-popover"
                            :style="getAdvancedStylePopoverStyle(region, selectedEditPage)"
                            @pointerdown.stop
                            @click.stop
                          >
                            <header class="style-advanced-popover-head">
                              <strong>#{{ region.index + 1 }} 样式</strong>
                              <button type="button" class="v2-icon-button" aria-label="关闭样式设置" @click="closeAdvancedStylePopover">✕</button>
                            </header>

                            <div class="style-advanced-grid">
                              <label class="v2-field">
                                <span>旋转</span>
                                <input
                                  :value="getRegionRotation(region)"
                                  type="number"
                                  min="-180"
                                  max="180"
                                  step="1"
                                  @change="updateRegionAdvancedStyle(region, { rotation: $event.target.value }, '调整旋转')"
                                />
                              </label>
                              <label class="v2-field">
                                <span>描边</span>
                                <input
                                  :value="getRegionStrokeStrength(region)"
                                  type="number"
                                  min="0"
                                  max="1"
                                  step="0.05"
                                  @change="updateRegionAdvancedStyle(region, { stroke_width: $event.target.value }, '调整描边')"
                                />
                              </label>
                              <label class="v2-field">
                                <span>字距</span>
                                <input
                                  :value="getRegionLetterSpacing(region)"
                                  type="number"
                                  min="0.5"
                                  max="2.5"
                                  step="0.05"
                                  @change="updateRegionAdvancedStyle(region, { letter_spacing: $event.target.value }, '调整字距')"
                                />
                              </label>
                              <label class="v2-field">
                                <span>行距</span>
                                <input
                                  :value="getRegionLineSpacing(region)"
                                  type="number"
                                  min="0.5"
                                  max="2.5"
                                  step="0.05"
                                  @change="updateRegionAdvancedStyle(region, { line_spacing: $event.target.value }, '调整行距')"
                                />
                              </label>
                            </div>

                            <div class="style-stroke-options" role="group" aria-label="描边强度">
                              <button
                                v-for="option in strokeStrengthOptions"
                                :key="`stroke-${region.id}-${option}`"
                                type="button"
                                :class="['style-chip-button', getRegionStrokeStrength(region) === option ? 'active' : '']"
                                @click="updateRegionAdvancedStyle(region, { stroke_width: option }, '调整描边')"
                              >
                                {{ option }}
                              </button>
                            </div>

                            <div class="style-color-editor">
                              <span>文字色</span>
                              <div class="style-color-row">
                                <button
                                  v-for="color in styleColorSwatches"
                                  :key="`fg-${region.id}-${color}`"
                                  type="button"
                                  class="style-color-swatch"
                                  :class="{ active: getRegionTextColorHex(region) === color }"
                                  :style="{ backgroundColor: color }"
                                  :aria-label="`文字色 ${color}`"
                                  @click="updateRegionAdvancedStyle(region, { fg_color: color }, '调整文字色')"
                                ></button>
                                <input
                                  :value="getRegionTextColorHex(region)"
                                  type="color"
                                  aria-label="文字色"
                                  @change="updateRegionAdvancedStyle(region, { fg_color: $event.target.value }, '调整文字色')"
                                />
                                <input
                                  :value="getRegionTextColorHex(region)"
                                  type="text"
                                  inputmode="text"
                                  aria-label="文字色 Hex"
                                  @change="updateRegionAdvancedStyle(region, { fg_color: $event.target.value }, '调整文字色')"
                                />
                              </div>
                            </div>

                            <div class="style-color-editor">
                              <span>底/描边色</span>
                              <div class="style-color-row">
                                <button
                                  v-for="color in styleColorSwatches"
                                  :key="`bg-${region.id}-${color}`"
                                  type="button"
                                  class="style-color-swatch"
                                  :class="{ active: getRegionStrokeColorHex(region) === color }"
                                  :style="{ backgroundColor: color }"
                                  :aria-label="`底/描边色 ${color}`"
                                  @click="updateRegionAdvancedStyle(region, { bg_color: color }, '调整底色')"
                                ></button>
                                <button
                                  type="button"
                                  class="style-chip-button"
                                  @click="updateRegionAdvancedStyle(region, { bg_color: '#ffffff' }, '设为白底')"
                                >
                                  白
                                </button>
                                <button
                                  type="button"
                                  class="style-chip-button"
                                  @click="updateRegionAdvancedStyle(region, { bg_color: '#000000' }, '设为黑底')"
                                >
                                  黑
                                </button>
                                <input
                                  :value="getRegionStrokeColorHex(region)"
                                  type="color"
                                  aria-label="底/描边色"
                                  @change="updateRegionAdvancedStyle(region, { bg_color: $event.target.value }, '调整底色')"
                                />
                                <input
                                  :value="getRegionStrokeColorHex(region)"
                                  type="text"
                                  inputmode="text"
                                  aria-label="底/描边色 Hex"
                                  @change="updateRegionAdvancedStyle(region, { bg_color: $event.target.value }, '调整底色')"
                                />
                              </div>
                            </div>

                            <label class="style-preserve-background-toggle v2-inline-checkbox">
                              <input
                                type="checkbox"
                                :checked="shouldPreserveRegionBackground(region)"
                                @change="updateRegionAdvancedStyle(region, { preserve_background: $event.target.checked }, '切换保留底图')"
                              />
                              <span>保留底图</span>
                            </label>
                          </section>
                        </Teleport>
                      </template>
                      <span
                        v-if="canvasMarqueeState?.pageId === selectedEditPage.stored_name"
                        class="v2-canvas-marquee"
                        :style="getCanvasMarqueeStyle(selectedEditPage)"
                      ></span>
                      <span
                        v-if="getManualDraftBBox(selectedEditPage)"
                        :class="[
                          'style-box',
                          'style-box-draft',
                          'active',
                          'manual',
                          getStyleRegionLabelClass({ bbox: getManualDraftBBox(selectedEditPage) || [0, 0, 0, 0] }, selectedEditPage)
                        ]"
                        :style="getManualDraftStyle(selectedEditPage)"
                      >
                        <span class="style-box-label">新框</span>
                      </span>
                      <div
                        v-if="getCanvasMeasurementOverlay(selectedEditPage)"
                        class="v2-canvas-measure-popover"
                        :style="getCanvasMeasurementStyle(selectedEditPage)"
                      >
                        {{ getCanvasMeasurementText(selectedEditPage) }}
                      </div>
                      <div
                        v-else-if="selectedEditRegion"
                        class="v2-canvas-selection-toolbar"
                        :style="getCanvasSelectionToolbarStyle(selectedEditPage)"
                        @pointerdown.stop
                        @click.stop
                      >
                        <span>{{ getCanvasSelectionToolbarText(selectedEditPage) }}</span>
                        <template v-if="hasCanvasMultiSelection">
                          <button type="button" @click="applyBatchRegionEnabled(true)">启用</button>
                          <button type="button" @click="applyBatchRegionEnabled(false)">停用</button>
                          <button type="button" @click="applyBatchDirection('vertical')">纵排</button>
                          <button type="button" @click="applyBatchDirection('horizontal')">横排</button>
                          <button type="button" @click="applyBatchFontFromActive">套用字体</button>
                          <button type="button" @click="applyBatchFontSizeFromActive">套用字号</button>
                        </template>
                        <template v-else>
                          <button type="button" @click="focusSelectedRegionInViewport(selectedEditPage, 'main')">定位</button>
                          <button type="button" @click="setCurrentPageViewportPreset('actual', 'main')">100%</button>
                        </template>
                      </div>
                    </template>
                  </div>
                  <div v-if="selectedEditPage" class="v2-canvas-hud">
                    <strong>{{ getCanvasViewportPercent(selectedEditPage, getReviewComparePaneCanvasRole(pane.key)) }}</strong>
                    <span>{{ getCanvasHudDetail(selectedEditPage, getReviewComparePaneCanvasRole(pane.key)) }}</span>
                  </div>
                </div>
              </article>
            </div>
          </section>

          <aside :class="['v2-region-sidebar', v2RegionSidebarCompact ? 'is-compact' : '']">
            <div class="v2-region-sidebar-head">
              <div class="v2-region-sidebar-top">
                <div class="v2-region-sidebar-label">
                  <div class="v2-region-sidebar-summary">
                    <span class="v2-pane-label">文本框</span>
                    <strong>{{ hasCanvasMultiSelection ? `${selectedCanvasRegionCount} 个已选` : (selectedEditRegion ? selectedEditRegionIndexLabel : '未选中') }}</strong>
                  </div>
                  <span class="v2-page-rail-count">{{ filteredEditRegions.length }}</span>
                </div>

                <div class="v2-region-sidebar-nav">
                  <button
                    type="button"
                    class="v2-icon-button"
                    aria-label="上一个对白框"
                    :disabled="!canSelectPreviousEditRegion"
                    @click="selectAdjacentEditRegion(-1)"
                  >
                    ↑
                  </button>
                  <button
                    type="button"
                    class="v2-ghost-button"
                    :disabled="!selectedEditPage || !selectedEditRegion"
                    @click="selectedEditPage && focusSelectedRegionInViewport(selectedEditPage, 'main')"
                  >
                    定位
                  </button>
                  <button
                    type="button"
                    class="v2-icon-button"
                    aria-label="下一个对白框"
                    :disabled="!canSelectNextEditRegion"
                    @click="selectAdjacentEditRegion(1)"
                  >
                    ↓
                  </button>
                </div>
              </div>

              <input
                v-model="regionListSearch"
                class="v2-search-input translation-review-input"
                type="search"
                placeholder="搜索对白框"
              />
              <select v-model="regionListFilter" class="v2-filter-select">
                <option value="all">全部</option>
                <option value="manual">手动框</option>
                <option value="keep-original">保留原文</option>
                <option value="untranslated">未翻译</option>
                <option value="font-override">有字体覆盖</option>
                <option value="warning">需留意</option>
              </select>
              <div v-if="hasCanvasMultiSelection" class="v2-region-batch-bar">
                <span>{{ selectedCanvasRegionCount }} 个框</span>
                <button type="button" @click="applyBatchRegionEnabled(true)">启用</button>
                <button type="button" @click="applyBatchRegionEnabled(false)">停用</button>
                <button type="button" @click="applyBatchDirection('vertical')">纵排</button>
                <button type="button" @click="applyBatchDirection('horizontal')">横排</button>
              </div>
            </div>

            <div v-if="selectedEditPage && filteredEditRegions.length" class="v2-region-list">
              <article
                v-for="region in filteredEditRegions"
                :key="region.id"
                :data-region-id="region.id"
                :class="[
                  'style-region-card',
                  'v2-region-card',
                  selectedEditRegionKey === region.id ? 'active' : '',
                  isRegionSelectedOnCanvas(region) ? 'multi-selected' : ''
                ]"
                @click="handleRegionCardClick($event, region)"
              >
                <header class="v2-region-card-head">
                  <div class="v2-region-card-title">
                    <strong>#{{ region.index + 1 }}</strong>
                    <span class="v2-inline-badge style">{{ getResolvedRegionStyleLabel(region) }}</span>
                    <span v-if="hasRegionWarning(region)" class="v2-inline-badge warning">需留意</span>
                    <span v-if="isManualRegion(region)" class="v2-inline-badge">手动框</span>
                  </div>

                  <div class="v2-region-card-actions" @click.stop @mousedown.stop>
                    <button
                      type="button"
                      class="v2-icon-button"
                      :aria-label="`打开第 ${region.index + 1} 个文本框样式设置`"
                      @click.stop="toggleAdvancedStylePopover(region, selectedEditPage)"
                    >
                      ⚙
                    </button>
                    <span
                      :class="[
                        'v2-region-commit-icon',
                        getRegionCommitStatusLabel(region) ? 'is-visible' : '',
                        getRegionCommitStatusClass(region)
                      ]"
                      :title="getRegionCommitStatusLabel(region)"
                      :aria-label="getRegionCommitStatusLabel(region) || undefined"
                      :role="getRegionCommitStatusLabel(region) ? 'status' : undefined"
                    >
                      <span class="v2-region-commit-icon-mark" aria-hidden="true"></span>
                    </span>

                    <div class="v2-region-card-toggles">
                      <button
                        type="button"
                        :class="['v2-toggle-chip', !isRegionDisabled(region) ? 'active' : '']"
                        @click.stop="toggleRegionEnabledV2(region)"
                      >
                        {{ isRegionDisabled(region) ? '已停用' : '已启用' }}
                      </button>
                      <button
                        type="button"
                        :class="['v2-toggle-chip', isVerticalRegion(region) ? 'active' : '']"
                        @click.stop="toggleRegionDirectionV2(region)"
                      >
                        {{ isVerticalRegion(region) ? '纵排' : '横排' }}
                      </button>
                    </div>
                  </div>
                </header>

                <div :class="['v2-region-card-preview', selectedEditRegionKey === region.id ? 'is-expanded' : '']">
                  <div class="v2-region-card-preview-copy">
                    <p class="v2-region-source">{{ region.source_text || '（没有识别到可用原文）' }}</p>
                    <p class="v2-region-translation">{{ getEditRegionText(region) || '未填写译文' }}</p>
                  </div>

                  <label
                    v-if="selectedEditRegionKey === region.id"
                    class="v2-inline-checkbox v2-region-preview-toggle"
                  >
                    <input
                      :checked="isRegionSkipEnabled(region)"
                      type="checkbox"
                      @change="updateTranslationSkipOverride(region, $event.target.checked)"
                    />
                    <span>保留原文</span>
                  </label>
                </div>

                <div v-if="selectedEditRegionKey === region.id" class="v2-region-card-body" @click.stop @mousedown.stop>
                  <label class="v2-field">
                    <span>译文</span>
                    <textarea
                      :class="['translation-review-input', 'translation-review-textarea', isRegionSkipEnabled(region) ? 'disabled' : '']"
                      :value="getEditRegionText(region)"
                      :data-region-id="region.id"
                      rows="4"
                      :disabled="isRegionSkipEnabled(region)"
                      @input="handleRegionTextInput(region, $event.target.value)"
                      @keydown.enter.meta.prevent="commitRegionTextDraft(region)"
                      @keydown.enter.ctrl.prevent="commitRegionTextDraft(region)"
                      @blur="commitRegionTextDraft(region)"
                    ></textarea>
                  </label>

                  <div class="v2-region-setting-stack">
                    <div class="v2-region-setting-row">
                      <span class="v2-region-setting-label">字体</span>
                      <div class="v2-region-setting-controls">
                        <select
                          :value="getRegionFontOverrideId(region)"
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
                        <button
                          type="button"
                          class="v2-secondary-button"
                          :disabled="!canApplyRegionFontToPage(region)"
                          @click="applyRegionFontToPage(region)"
                        >
                          应用全部
                        </button>
                      </div>
                    </div>

                    <div class="v2-region-setting-row">
                      <span class="v2-region-setting-label">字号</span>
                      <div class="v2-region-setting-controls">
                        <div class="v2-stepper">
                          <button type="button" class="v2-stepper-button" @click="adjustRegionFontSizeV2(region, -1)">−</button>
                          <input
                            :value="getRegionFontSize(region)"
                            type="text"
                            inputmode="numeric"
                            pattern="[0-9]*"
                            aria-label="字号"
                            @input="handleRegionFontSizeInput(region, $event.target.value)"
                            @keydown.enter.prevent="commitRegionFontSize(region)"
                            @blur="commitRegionFontSize(region)"
                          />
                          <button type="button" class="v2-stepper-button" @click="adjustRegionFontSizeV2(region, 1)">＋</button>
                        </div>
                        <button
                          type="button"
                          class="v2-secondary-button"
                          :disabled="!canApplyRegionFontSizeToPage(region)"
                          @click="applyRegionFontSizeToPage(region)"
                        >
                          应用全部
                        </button>
                      </div>
                    </div>
                  </div>

                  <div v-if="isManualRegion(region)" class="v2-region-card-footer">
                    <button
                      type="button"
                      class="v2-danger-link"
                      @click="deleteManualRegion(region)"
                    >
                      删除手动框
                    </button>
                  </div>
                </div>
              </article>
            </div>

            <div v-else class="v2-empty-panel">
              <strong>{{ selectedEditPage ? '这一页暂时还没有可编辑的对白框' : '先从左侧选择一页' }}</strong>
              <p>如果刚上传项目，可以先执行一次识别或翻译，让文本框数据进入审校工作台。</p>
              <button
                type="button"
                class="v2-primary-button"
                :disabled="translating || !sessionId"
                @click="runV2ReviewPrimaryAction"
              >
                {{ v2ReviewPrimaryLabel }}
              </button>
            </div>
          </aside>
        </div>
      </section>
    </main>

    <div v-if="selectionEraseModalOpen" class="v2-overlay" @click.self="closeSelectionEraseModal">
      <section class="v2-modal v2-selection-erase-modal">
        <header class="v2-modal-head">
          <div>
            <p class="v2-section-kicker">高级擦除</p>
            <h2 class="v2-section-title">选区擦除</h2>
          </div>
          <button type="button" class="v2-icon-button" aria-label="关闭选区擦除" @click="closeSelectionEraseModal">✕</button>
        </header>
        <div class="v2-selection-erase-layout">
          <div class="v2-selection-erase-stage-wrap">
            <div
              ref="selectionEraseStageRef"
              class="v2-selection-erase-stage"
              @pointerdown="beginSelectionEraseDraw"
              @pointermove="updateSelectionEraseDraw"
              @pointerup="finishSelectionEraseDraw"
              @pointercancel="cancelSelectionEraseDraw"
            >
              <img
                v-if="selectionEraseImageUrl"
                :src="selectionEraseImageUrl"
                alt=""
                draggable="false"
              />
              <div
                v-for="rect in selectionEraseRects"
                :key="rect.id"
                class="v2-selection-erase-rect"
                :style="getSelectionEraseRectStyle(rect)"
              />
              <div
                v-if="selectionEraseDraft"
                class="v2-selection-erase-rect is-draft"
                :style="getSelectionEraseRectStyle(selectionEraseDraft)"
              />
            </div>
          </div>
          <aside class="v2-selection-erase-side">
            <div class="v2-selection-erase-count">
              <strong>{{ selectionEraseRects.length }}</strong>
              <span>个选区</span>
            </div>
            <div v-if="selectionEraseRects.length" class="v2-selection-erase-list">
              <button
                v-for="(rect, index) in selectionEraseRects"
                :key="rect.id"
                type="button"
                @click="removeSelectionEraseRect(rect.id)"
              >
                <span>选区 {{ index + 1 }}</span>
                <small>{{ Math.round(rect.width * 100) }}% × {{ Math.round(rect.height * 100) }}%</small>
              </button>
            </div>
            <div v-else class="v2-selection-erase-empty">在图上拖拽创建选区</div>
          </aside>
        </div>
        <footer class="v2-selection-erase-actions">
          <button type="button" class="v2-ghost-button" :disabled="advancedEraseBusy || !selectionEraseRects.length" @click="clearSelectionEraseRects">
            清空
          </button>
          <button type="button" class="v2-ghost-button" :disabled="advancedEraseBusy" @click="closeSelectionEraseModal">
            取消
          </button>
          <button type="button" class="v2-primary-button" :disabled="advancedEraseBusy || !selectionEraseRects.length" @click="confirmSelectionErase">
            确认擦除
          </button>
        </footer>
      </section>
    </div>

    <div v-if="migrationModalOpen" class="v2-overlay" @click.self="migrationModalOpen = false">
      <section class="v2-modal v2-onboarding-modal">
        <header class="v2-modal-head">
          <div>
            <p class="v2-section-kicker">旧数据迁移</p>
            <h2 class="v2-section-title">检测到旧版项目数据</h2>
          </div>
          <button type="button" class="v2-icon-button" aria-label="关闭迁移提示" @click="migrationModalOpen = false">✕</button>
        </header>
        <div class="v2-settings-content">
          <section class="v2-settings-group">
            <header>
              <strong>建议迁移到新的应用数据目录</strong>
              <span>{{ appRuntime.migration?.target?.app_data || '将迁移到新的用户目录' }}</span>
            </header>
            <p class="v2-onboarding-copy">
              旧版历史项目和输出结果目前还在仓库目录里。迁移后，升级和重新安装都不会覆盖你的项目数据。
            </p>
            <div class="v2-inline-actions">
              <button type="button" class="v2-primary-button" @click="handleLegacyMigration('migrate')">迁移旧数据</button>
              <button type="button" class="v2-secondary-button" @click="handleLegacyMigration('skip')">暂不迁移</button>
            </div>
          </section>
        </div>
      </section>
    </div>

    <div v-if="onboardingOpen" class="v2-overlay" @click.self="onboardingOpen = false">
      <section class="v2-modal v2-onboarding-modal">
        <header class="v2-modal-head">
          <div>
            <p class="v2-section-kicker">首次启动</p>
            <h2 class="v2-section-title">先完成基础设置</h2>
          </div>
          <button type="button" class="v2-icon-button" aria-label="关闭首次设置" @click="onboardingOpen = false">✕</button>
        </header>
        <div class="v2-settings-content">
          <section class="v2-settings-group">
            <header>
              <strong>翻译服务</strong>
              <span>这些设置会保存到桌面应用的配置文件，而不是浏览器缓存里。</span>
            </header>

            <label class="v2-field">
              <span>翻译引擎</span>
              <select v-model="config.translator">
                <option value="gemini">Gemini</option>
                <option value="doubao-ark">Doubao</option>
                <option value="openai-compatible">OpenAI Compatible</option>
              </select>
            </label>

            <label class="v2-field">
              <span>目标语言</span>
              <select v-model="config.target_lang">
                <option value="CHS">简体中文</option>
                <option value="CHT">繁体中文</option>
                <option value="ENG">英语</option>
                <option value="JPN">日语</option>
                <option value="KOR">韩语</option>
              </select>
            </label>

            <label v-if="config.translator === 'doubao-ark'" class="v2-field">
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
            </label>

            <label v-if="config.translator === 'openai-compatible'" class="v2-field">
              <span>API Base URL</span>
              <input
                v-model="config.openai_base_url"
                placeholder="https://api.openai.com/v1"
                type="text"
                autocomplete="off"
              />
            </label>

            <label v-if="config.translator === 'openai-compatible'" class="v2-field">
              <span>模型名称</span>
              <input
                v-model="config.openai_model"
                placeholder="gpt-4o / deepseek-chat / ..."
                type="text"
                autocomplete="off"
              />
            </label>

            <label v-if="showTranslatorApiKeyField" class="v2-field">
              <span>{{ translatorApiKeyLabel }}</span>
              <input
                v-model="config.api_key"
                :placeholder="translatorApiKeyPlaceholder"
                type="password"
                autocomplete="off"
              />
            </label>

            <div class="v2-inline-actions">
              <button type="button" class="v2-secondary-button" @click="validateCurrentSettings">
                测试连接
              </button>
              <button type="button" class="v2-primary-button" @click="validateCurrentSettings">
                保存并开始
              </button>
            </div>
            <p class="v2-onboarding-copy" :class="appSettingsValidation.ok === false ? 'is-error' : ''">
              {{ appSettingsValidation.message || '建议先测试一次连接，确认桌面版可以正常访问你的翻译服务。' }}
            </p>
            <p v-if="appSettingsValidation.preview" class="v2-onboarding-copy">
              测试返回：{{ appSettingsValidation.preview }}
            </p>
          </section>
        </div>
      </section>
    </div>

    <div v-if="v2HistoryModalOpen" class="v2-overlay" @click.self="closeV2HistoryModal">
      <section class="v2-modal v2-history-modal" data-testid="v2-history-modal">
        <header class="v2-modal-head">
          <div>
            <p class="v2-section-kicker">历史项目</p>
            <h2 class="v2-section-title">恢复之前的工作区</h2>
          </div>

          <button type="button" class="v2-icon-button" aria-label="关闭历史项目" @click="closeV2HistoryModal">✕</button>
        </header>

        <div class="v2-modal-toolbar">
          <input
            v-model="v2HistorySearch"
            class="v2-search-input"
            type="search"
            placeholder="搜索项目名称 / 备注 / 状态"
          />
          <select v-model="v2HistorySort" class="v2-filter-select">
            <option value="recent">最近更新</option>
            <option value="title">按标题</option>
          </select>
          <button type="button" class="v2-secondary-button" :disabled="historyLoading" @click="loadProjectHistory">
            {{ historyLoading ? '刷新中…' : '刷新' }}
          </button>
        </div>

        <div v-if="v2FilteredProjectHistory.length" class="v2-history-grid">
          <article
            v-for="(project, index) in v2FilteredProjectHistory"
            :key="project.project_id"
            class="v2-history-card"
          >
            <div class="v2-history-card-cover">
              <img :src="getV2ProjectCover(project, index)" :alt="project.title" @error="handleV2ImageError($event, index)" />
            </div>

            <div class="v2-history-card-body">
              <div class="v2-history-card-head">
                <strong :title="project.title">{{ project.title }}</strong>
                <span class="v2-inline-badge">{{ workflowStageLabelMap[project.workflow_stage] || project.workflow_stage }}</span>
              </div>

              <div class="v2-history-card-meta">
                <span>{{ project.page_count }} 页</span>
                <span>{{ formatV2Timestamp(project.updated_at || project.created_at) }}</span>
              </div>

              <p class="v2-history-card-note" :title="project.note || '没有备注'">{{ project.note || '没有备注' }}</p>

              <div class="v2-history-card-actions">
                <button
                  type="button"
                  class="v2-primary-button"
                  :disabled="!canRestoreHistoryProject(project)"
                  @click="restoreProjectV2(project.project_id)"
                >
                  {{ restoringProjectId === project.project_id ? '恢复中…' : '恢复项目' }}
                </button>
                <button
                  type="button"
                  class="v2-ghost-button"
                  @click="toggleProjectSnapshots(project.project_id)"
                >
                  {{ expandedProjectId === project.project_id ? '收起快照' : '查看快照' }}
                </button>
                <button
                  type="button"
                  class="v2-danger-link"
                  :disabled="!canDeleteHistoryProject(project)"
                  @click="deleteProject(project)"
                >
                  删除
                </button>
              </div>

              <div v-if="expandedProjectId === project.project_id" class="v2-snapshot-list">
                <div
                  v-if="snapshotLoadingProjectId === project.project_id && !(projectSnapshots[project.project_id] || []).length"
                  class="v2-snapshot-empty"
                >
                  正在加载快照…
                </div>
                <template v-else>
                  <article
                    v-for="snapshot in projectSnapshots[project.project_id] || []"
                    :key="snapshot.snapshot_id"
                    class="v2-snapshot-card"
                  >
                    <div class="v2-snapshot-copy">
                      <strong>{{ snapshot.summary || snapshot.kind || '项目快照' }}</strong>
                      <span>{{ formatV2Timestamp(snapshot.created_at) }}</span>
                    </div>
                    <div class="v2-snapshot-actions">
                      <button
                        type="button"
                        class="v2-ghost-button"
                        :disabled="!canRestoreHistorySnapshot(project, snapshot)"
                        @click="restoreSnapshotV2(project.project_id, snapshot.snapshot_id)"
                      >
                        {{ restoringSnapshotId === snapshot.snapshot_id ? '恢复中…' : '恢复此快照' }}
                      </button>
                      <button
                        type="button"
                        class="v2-icon-button"
                        :aria-label="snapshot.pinned ? '取消固定快照' : '固定快照'"
                        @click="toggleSnapshotPin(project.project_id, snapshot)"
                      >
                        {{ snapshot.pinned ? '★' : '☆' }}
                      </button>
                    </div>
                  </article>
                  <div v-if="!(projectSnapshots[project.project_id] || []).length" class="v2-snapshot-empty">
                    这个项目目前还没有快照。
                  </div>
                </template>
              </div>
            </div>
          </article>
        </div>

        <div v-else class="v2-empty-panel">
          <strong>{{ historyLoading ? '正在读取历史项目…' : '暂时没有历史项目' }}</strong>
          <p>先上传一组漫画素材，系统就会开始为你建立可恢复的项目记录。</p>
        </div>
      </section>
    </div>

    <div v-if="glossaryDrawerOpen" class="v2-settings-layer as-drawer v2-glossary-layer">
      <div class="v2-settings-scrim" @click="closeProjectGlossaryDrawer"></div>
      <section class="v2-settings-panel v2-glossary-panel" data-testid="v2-glossary-panel" @click.stop>
        <header class="v2-modal-head">
          <div>
            <p class="v2-section-kicker">项目</p>
            <h2 class="v2-section-title">专有名词库</h2>
          </div>

          <button type="button" class="v2-icon-button" aria-label="关闭专有名词库" @click="closeProjectGlossaryDrawer">✕</button>
        </header>

        <div class="v2-glossary-summary">
          <div class="v2-readonly-field">
            <span>词条</span>
            <strong>{{ projectGlossaryEntryCount }}</strong>
          </div>
          <div class="v2-readonly-field">
            <span>匹配</span>
            <strong>{{ projectGlossaryOccurrenceCount }}</strong>
          </div>
        </div>

        <div class="v2-inline-actions v2-glossary-actions">
          <button type="button" class="v2-secondary-button" :disabled="glossaryBusy" @click="addGlossaryEntry">
            新增
          </button>
          <button type="button" class="v2-secondary-button" :disabled="!canSaveProjectGlossary" @click="saveProjectGlossaryDraft">
            {{ glossarySaving ? '保存中…' : '保存' }}
          </button>
          <button type="button" class="v2-secondary-button" :disabled="!canSaveProjectGlossary" @click="extractProjectGlossary">
            {{ glossaryExtracting ? '提取中…' : '提取/补充' }}
          </button>
          <button type="button" class="v2-secondary-button" :disabled="!canSaveProjectGlossary" @click="previewProjectGlossaryApplication">
            {{ glossaryPreviewing ? '预览中…' : '预览应用' }}
          </button>
          <button type="button" class="v2-secondary-button" :disabled="!canRefreshProjectGlossaryOccurrences" @click="refreshProjectGlossaryOccurrences">
            {{ glossaryOccurrencesLoading ? '刷新中…' : '刷新匹配' }}
          </button>
          <button type="button" class="v2-primary-button" :disabled="!canApplyProjectGlossary" @click="applyProjectGlossary">
            {{ glossaryApplying ? '应用中…' : '应用并重嵌字' }}
          </button>
        </div>

        <p v-if="glossaryError" class="v2-settings-inline-note is-error">{{ glossaryError }}</p>

        <div class="v2-settings-content v2-glossary-content">
          <section class="v2-settings-group">
            <header>
              <strong>词条</strong>
              <span>{{ glossaryLoading ? '读取中…' : `${projectGlossaryEntryCount} 个` }}</span>
            </header>

            <div v-if="!glossaryDraftEntries.length" class="v2-empty-panel v2-glossary-empty">
              <strong>暂无词条</strong>
              <p>可以先提取，也可以直接新增。</p>
            </div>

            <article
              v-for="entry in glossaryDraftEntries"
              :key="entry.id"
              class="v2-glossary-entry"
            >
              <header class="v2-glossary-entry-head">
                <div>
                  <strong>{{ entry.source || '未填写原文' }}</strong>
                  <div class="v2-glossary-entry-meta">
                    <span class="v2-inline-badge">{{ entry.category || '其他' }}</span>
                    <span class="v2-inline-badge style">{{ getGlossaryEntrySourceKindLabel(entry) }}</span>
                    <span class="v2-inline-badge">{{ getGlossaryOccurrenceLabel(entry) }}</span>
                  </div>
                </div>
                <button
                  type="button"
                  class="v2-danger-link"
                  :disabled="glossaryBusy"
                  @click="removeGlossaryEntry(entry.id)"
                >
                  删除
                </button>
              </header>

              <div class="v2-glossary-fields">
                <label class="v2-field">
                  <span>原文</span>
                  <input v-model="entry.source" type="text" autocomplete="off" />
                </label>
                <label class="v2-field">
                  <span>译名</span>
                  <input v-model="entry.translation" type="text" autocomplete="off" />
                </label>
                <label class="v2-field">
                  <span>分类</span>
                  <select v-model="entry.category">
                    <option
                      v-for="category in projectGlossaryCategoryOptions"
                      :key="category"
                      :value="category"
                    >
                      {{ category }}
                    </option>
                  </select>
                </label>
                <label class="v2-field">
                  <span>当前译法</span>
                  <input v-model="entry.replacement" type="text" autocomplete="off" placeholder="可选" />
                </label>
                <label class="v2-field v2-glossary-note-field">
                  <span>备注</span>
                  <input v-model="entry.note" type="text" autocomplete="off" />
                </label>
              </div>

              <details v-if="entry.occurrences && entry.occurrences.length" class="v2-glossary-occurrences">
                <summary>出现位置</summary>
                <button
                  v-for="occurrence in entry.occurrences.slice(0, 8)"
                  :key="`${entry.id}-${occurrence.page_id}-${occurrence.region_id}`"
                  type="button"
                  class="v2-glossary-occurrence"
                  @click="jumpToGlossaryOccurrence(occurrence)"
                >
                  <strong>{{ occurrence.page_name || occurrence.page_id }}</strong>
                  <span>{{ occurrence.source_text }}</span>
                </button>
              </details>
            </article>
          </section>

          <section class="v2-settings-group">
            <header>
              <strong>替换预览</strong>
              <span>{{ getGlossaryPreviewSummary() }}</span>
            </header>

            <div v-if="glossaryPreview.changes.length" class="v2-glossary-preview-list">
              <article
                v-for="change in glossaryPreview.changes.slice(0, 12)"
                :key="`${change.page_id}-${change.region_id}`"
                class="v2-glossary-preview-item"
              >
                <strong>{{ change.page_name || change.page_id }}</strong>
                <p>{{ change.before }}</p>
                <p>{{ change.after }}</p>
              </article>
            </div>
            <p v-else class="v2-settings-inline-note">暂无替换预览。</p>
          </section>
        </div>
      </section>
    </div>

    <div v-if="v2SettingsOpen" :class="['v2-settings-layer', v2View === 'review' ? 'as-drawer' : 'as-modal']">
      <div class="v2-settings-scrim" @click="closeV2Settings"></div>
      <section class="v2-settings-panel" data-testid="v2-settings-panel" @click.stop>
        <header class="v2-modal-head">
          <div>
            <p class="v2-section-kicker">设置</p>
            <h2 class="v2-section-title">{{ v2View === 'review' ? '当前项目与工作流' : '默认工作流设置' }}</h2>
          </div>

          <button type="button" class="v2-icon-button" aria-label="关闭设置" @click="closeV2Settings">✕</button>
        </header>

        <div class="v2-settings-content">
          <section class="v2-settings-group">
            <header>
              <strong>应用运行环境</strong>
              <span>{{ isDesktopRuntime ? '桌面版本地运行时' : '浏览器模式' }}</span>
            </header>

            <div class="v2-readonly-field">
              <span>设置状态</span>
              <strong :title="settingsStatusLabel">{{ settingsStatusLabel }}</strong>
            </div>

            <div v-if="appRuntime.settings_path" class="v2-readonly-field">
              <span>设置文件</span>
              <strong :title="appRuntime.settings_path">{{ appRuntime.settings_path }}</strong>
            </div>

            <div v-if="appRuntime.data_dir" class="v2-readonly-field">
              <span>数据目录</span>
              <strong :title="appRuntime.data_dir">{{ appRuntime.data_dir }}</strong>
            </div>

            <div class="v2-readonly-field">
              <span>GPU / CUDA</span>
              <strong :title="appRuntimeGpuLabel">{{ appRuntimeGpuLabel }}</strong>
            </div>

            <div class="v2-readonly-field">
              <span>可用磁盘空间</span>
              <strong :title="appRuntimeDiskLabel">{{ appRuntimeDiskLabel }}</strong>
            </div>

            <div v-if="appRuntime.logs_dir" class="v2-readonly-field">
              <span>日志目录</span>
              <strong :title="appRuntime.logs_dir">{{ appRuntime.logs_dir }}</strong>
            </div>
          </section>

          <section v-if="currentProject" class="v2-settings-group">
            <header>
              <strong>项目信息</strong>
              <span>给当前工作区一个更清晰的名字</span>
            </header>

            <label class="v2-field">
              <span>项目名称</span>
              <input v-model="projectTitleDraft" type="text" placeholder="未命名项目" />
            </label>

            <label class="v2-field">
              <span>备注</span>
              <textarea v-model="projectNoteDraft" rows="3" placeholder="记录这组素材的来源、进度或注意事项"></textarea>
            </label>

            <button
              type="button"
              class="v2-secondary-button"
              :disabled="!projectMetaDirty || savingProjectMeta"
              @click="saveProjectMetadata"
            >
              {{ savingProjectMeta ? '保存中…' : '保存项目资料' }}
            </button>
          </section>

          <section class="v2-settings-group">
            <header>
              <strong>翻译流程</strong>
              <span>控制识别、翻译与审校的默认方式</span>
            </header>

            <label class="v2-field">
              <span>翻译引擎</span>
              <select v-model="config.translator">
                <option value="gemini">Gemini</option>
                <option value="doubao-ark">Doubao</option>
                <option value="openai-compatible">OpenAI Compatible</option>
              </select>
            </label>

            <label class="v2-field">
              <span>目标语言</span>
              <select v-model="config.target_lang">
                <option value="CHS">简体中文</option>
                <option value="CHT">繁体中文</option>
                <option value="ENG">英语</option>
                <option value="JPN">日语</option>
                <option value="KOR">韩语</option>
              </select>
            </label>

            <label class="v2-field">
              <span>默认审校模式</span>
              <select v-model="config.default_review_mode">
                <option value="canvas_beta">画布审校（Beta）</option>
                <option value="classic">经典审校</option>
              </select>
            </label>

            <label v-if="config.translator === 'doubao-ark'" class="v2-field">
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
            </label>

            <label v-if="config.translator === 'openai-compatible'" class="v2-field">
              <span>API Base URL</span>
              <input
                v-model="config.openai_base_url"
                placeholder="https://api.openai.com/v1"
                type="text"
                autocomplete="off"
              />
            </label>

            <label v-if="config.translator === 'openai-compatible'" class="v2-field">
              <span>模型名称</span>
              <input
                v-model="config.openai_model"
                placeholder="gpt-4o / deepseek-chat / ..."
                type="text"
                autocomplete="off"
              />
            </label>

            <label v-if="showTranslatorApiKeyField" class="v2-field">  <!-- settings panel -->
              <span>{{ translatorApiKeyLabel }}</span>
              <input
                v-model="config.api_key"
                :placeholder="translatorApiKeyPlaceholder"
                type="password"
                autocomplete="off"
              />
            </label>

            <div class="v2-inline-actions">
              <button type="button" class="v2-secondary-button" @click="validateCurrentSettings">
                测试连接
              </button>
              <button
                v-if="showTranslatorApiKeyField"
                type="button"
                class="v2-ghost-button"
                @click="clearTranslatorApiKey"
              >
                清除密钥
              </button>
            </div>
            <p class="v2-settings-inline-note" :class="appSettingsValidation.ok === false ? 'is-error' : ''">
              {{ appSettingsValidation.message || '当前会自动保存到应用配置文件。' }}
            </p>

            <label class="v2-inline-checkbox">
              <input v-model="config.pause_after_detection" type="checkbox" />
              <span>上传后先停在识别阶段，进入逐框审校</span>
            </label>
          </section>

          <section class="v2-settings-group">
            <header>
              <strong>字体与渲染</strong>
              <span>配置识别风格到实际字体的对应关系</span>
            </header>

            <div class="v2-font-map-list">
              <label
                v-for="bucket in styleBucketOptions"
                :key="bucket.value"
                class="v2-font-map-row"
              >
                <span class="v2-font-map-label">{{ bucket.label }}</span>
                <select v-model="config[styleFontConfigKeyMap[bucket.value]]">
                  <option value="">使用内置默认</option>
                  <option v-for="font in availableFonts" :key="font.id" :value="font.id">
                    {{ font.label }}
                  </option>
                </select>
              </label>
            </div>

            <p class="v2-settings-inline-note">
              内置默认暂用 Noto Sans CJK；把自己的字体文件放入 fonts 目录后，可在上方逐项覆盖。
            </p>

            <label class="v2-field">
              <span>结果输出</span>
              <select v-model="config.rerender_output_format">
                <option value="png">独立 PNG</option>
                <option value="source">沿用原格式</option>
              </select>
            </label>
          </section>

          <section class="v2-settings-group">
            <header>
              <strong>图像处理</strong>
              <span>擦字与底图生成相关能力</span>
            </header>

            <label class="v2-field">
              <span>擦字模式</span>
              <select v-model="config.image_cleanup_mode">
                <option value="off">稳定流程</option>
                <option value="gemini-image">Gemini Image</option>
                <option value="seedream-image">Seedream Image</option>
              </select>
            </label>

            <label class="v2-field">
              <span>擦字强度</span>
              <select v-model="config.mask_cleanup_strength">
                <option value="standard">标准</option>
                <option value="clean">更干净</option>
                <option value="aggressive">更激进</option>
              </select>
            </label>

            <label v-if="showImageCleanupApiKeyField" class="v2-field">
              <span>{{ imageCleanupApiKeyLabel }}</span>
              <input
                v-model="config.image_cleanup_api_key"
                :placeholder="imageCleanupApiKeyPlaceholder"
                type="password"
                autocomplete="off"
              />
            </label>

            <div v-if="showImageCleanupApiKeyField" class="v2-inline-actions">
              <button
                type="button"
                class="v2-ghost-button"
                @click="clearImageCleanupApiKey"
              >
                清除图像密钥
              </button>
            </div>

            <label class="v2-inline-checkbox">
              <input v-model="config.export_mask_debug" type="checkbox" />
              <span>输出擦字调试目录</span>
            </label>
          </section>

          <section class="v2-settings-group">
            <header>
              <strong>高级擦除 API</strong>
              <span>单页高级擦除专用配置</span>
            </header>

            <label class="v2-field">
              <span>Provider</span>
              <select v-model="config.advanced_erase_provider">
                <option
                  v-for="provider in advancedEraseProviderOptions"
                  :key="provider.value"
                  :value="provider.value"
                >
                  {{ provider.label }}
                </option>
              </select>
            </label>

            <label class="v2-field">
              <span>API Endpoint</span>
              <input
                v-model="config.advanced_erase_base_url"
                placeholder="https://ark.cn-beijing.volces.com/api/v3/images/generations"
                type="text"
                autocomplete="off"
              />
            </label>

            <label class="v2-field">
              <span>模型名称</span>
              <input
                v-model="config.advanced_erase_model"
                placeholder="doubao-seedream-5-0-lite-260128"
                type="text"
                autocomplete="off"
              />
            </label>

            <label class="v2-field">
              <span>{{ advancedEraseProviderLabel }} API Key</span>
              <input
                v-model="config.advanced_erase_api_key"
                placeholder="输入火山引擎 Ark API Key"
                type="password"
                autocomplete="off"
              />
            </label>

            <label class="v2-field">
              <span>请求超时（秒）</span>
              <input
                v-model.number="config.advanced_erase_timeout_seconds"
                type="number"
                min="30"
                max="300"
                step="10"
                autocomplete="off"
              />
            </label>

            <label class="v2-field">
              <span>选区擦除 Prompt</span>
              <textarea
                v-model="config.advanced_erase_selection_prompt"
                class="v2-prompt-textarea"
                rows="7"
                autocomplete="off"
              ></textarea>
            </label>

            <div class="v2-inline-actions">
              <button
                type="button"
                class="v2-ghost-button"
                @click="clearAdvancedEraseApiKey"
              >
                清除高级擦除密钥
              </button>
              <button
                type="button"
                class="v2-ghost-button"
                @click="config.advanced_erase_selection_prompt = advancedEraseSelectionDefaultPrompt"
              >
                恢复默认 Prompt
              </button>
            </div>

            <p class="v2-settings-inline-note">
              固定参数：size 自动计算，response_format=b64_json，output_format=png，watermark=false。
            </p>
          </section>
        </div>
      </section>
    </div>
  </div>

</template>
