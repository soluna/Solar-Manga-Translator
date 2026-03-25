<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const configStorageKey = 'manga-translator.ui-config'
const translatorDefaultModels = {
  'doubao-ark': 'doubao-seed-translation-250915'
}
const translatorAllowedModels = {
  'doubao-ark': new Set([
    'doubao-seed-translation-250915',
    'doubao-seed-2-0-mini-260215'
  ])
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
const styleBucketLabelMap = Object.fromEntries(styleBucketOptions.map((option) => [option.value, option.label]))
const defaultStyleFontNameMap = {
  gothic: ['華康儷中黑.ttf', '華康儷中黑'],
  rounded: ['華康儷粗圓.ttf', '華康儷粗圓'],
  mincho: ['華康儷粗宋.ttf', '華康儷粗宋'],
  cartoon: ['華康布丁體.ttf', '華康布丁體'],
  handwritten: ['華康竹風體.ttf', '華康竹風體'],
  sfx: ['方正剪紙GBK.ttf', '方正剪紙GBK']
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

function createDefaultConfig() {
  return {
    translator: 'gemini',
    translator_model: '',
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

  const translator = typeof rawValue.translator === 'string'
    ? rawValue.translator
    : defaults.translator
  const storedTranslatorModel = typeof rawValue.translator_model === 'string'
    ? rawValue.translator_model
    : defaults.translator_model
  const translatorModel = isValidTranslatorModel(translator, storedTranslatorModel)
    ? storedTranslatorModel
    : getDefaultTranslatorModel(translator)
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

  return {
    translator,
    translator_model: translatorModel,
    target_lang: typeof rawValue.target_lang === 'string' ? rawValue.target_lang : defaults.target_lang,
    use_gpu: typeof rawValue.use_gpu === 'boolean' ? rawValue.use_gpu : defaults.use_gpu,
    api_key: typeof rawValue.api_key === 'string' ? rawValue.api_key : defaults.api_key,
    font_key: typeof rawValue.font_key === 'string' ? rawValue.font_key : defaults.font_key,
    font_style_mode: fontStyleMode,
    style_font_gothic_key: typeof rawValue.style_font_gothic_key === 'string'
      ? rawValue.style_font_gothic_key
      : defaults.style_font_gothic_key,
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
const reviewInspectionPages = ref([])
const reviewInspectionLoading = ref(false)
const translationRegionOverrides = ref({})
const styleInspectionPages = ref([])
const styleInspectionLoading = ref(false)
const styleRegionOverrides = ref({})
const selectedEditPageKey = ref('')
const selectedEditRegionKey = ref('')

const config = ref(loadStoredConfig())

let socket = null

const acceptValue = '.zip,.cbz,.jpg,.jpeg,.png,.webp'

const canUpload = computed(() => Boolean(selectedFile.value) && !uploading.value)
const canTranslate = computed(() => Boolean(sessionId.value) && !translating.value)
const canRerender = computed(() => Boolean(sessionId.value) && !translating.value && Boolean(downloadUrl.value || translatedImages.value.length))
const canInspectEditor = computed(() => Boolean(sessionId.value))
const hasTranslationOverrides = computed(() => Object.keys(translationRegionOverrides.value).length > 0)
const hasStyleOverrides = computed(() => Object.keys(styleRegionOverrides.value).length > 0)
const editInspectionLoading = computed(() => reviewInspectionLoading.value || styleInspectionLoading.value)
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
    for (const region of page.regions || []) {
      const existing = mergedPage.regions.get(region.id) || { id: region.id, index: region.index, bbox: region.bbox }
      mergedPage.regions.set(region.id, {
        ...existing,
        ...region
      })
    }
  }

  return pageOrder.map((storedName) => {
    const page = pageMap.get(storedName)
    return {
      ...page,
      regions: Array.from(page.regions.values()).sort((left, right) => left.index - right.index)
    }
  })
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
const progressPercent = computed(() => {
  if (!progress.value.total) {
    return 0
  }

  return Math.min(100, Math.round((progress.value.current / progress.value.total) * 100))
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
}

function resetEditInspectorSelection() {
  selectedEditPageKey.value = ''
  selectedEditRegionKey.value = ''
}

function buildRuntimeConfig() {
  return {
    ...config.value,
    style_region_overrides: { ...styleRegionOverrides.value },
    translation_region_overrides: { ...translationRegionOverrides.value }
  }
}

function getStyleLabel(style) {
  return styleBucketLabelMap[style] || '未分类'
}

function getRegionOverrideValue(region) {
  return styleRegionOverrides.value[region.id] || ''
}

function getEditRegionText(region) {
  return translationRegionOverrides.value[region.id] || region.current_translation || region.machine_translation || ''
}

function getResolvedStyle(region) {
  return styleRegionOverrides.value[region.id] || region.resolved_style || region.auto_style || ''
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

function getStyleRegionBoxStyle(region, page) {
  const [x1, y1, x2, y2] = region?.bbox || [0, 0, 0, 0]
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

function applyReviewInspectionPayload(payload) {
  reviewInspectionPages.value = payload.pages || []
  const nextOverrides = {}
  for (const page of reviewInspectionPages.value) {
    for (const region of page.regions || []) {
      if (region.override_translation) {
        nextOverrides[region.id] = region.override_translation
      }
    }
  }
  translationRegionOverrides.value = nextOverrides
}

function applyStyleInspectionPayload(payload) {
  styleInspectionPages.value = payload.pages || []
}

async function loadReviewInspection() {
  if (!sessionId.value) {
    reviewInspectionPages.value = []
    syncEditSelection()
    return
  }

  reviewInspectionLoading.value = true
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
    reviewInspectionLoading.value = false
  }
}

async function loadStyleInspection() {
  if (!sessionId.value || config.value.font_style_mode !== 'auto-map') {
    styleInspectionPages.value = []
    syncEditSelection()
    return
  }

  styleInspectionLoading.value = true
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
    styleInspectionLoading.value = false
  }
}

async function loadEditInspection() {
  if (!sessionId.value) {
    resetTranslationReview()
    resetStyleInspector()
    resetEditInspectorSelection()
    return
  }

  const tasks = [loadReviewInspection()]
  if (config.value.font_style_mode === 'auto-map') {
    tasks.push(loadStyleInspection())
  } else {
    resetStyleInspector()
  }

  await Promise.all(tasks)
  syncEditSelection()
}

function updateStyleOverride(region, nextStyle) {
  const normalizedStyle = styleBucketOptions.some((option) => option.value === nextStyle) ? nextStyle : ''
  const nextOverrides = { ...styleRegionOverrides.value }
  if (!normalizedStyle || normalizedStyle === region.auto_style) {
    delete nextOverrides[region.id]
  } else {
    nextOverrides[region.id] = normalizedStyle
  }
  styleRegionOverrides.value = nextOverrides
  selectedEditRegionKey.value = region.id
  status.value = '已更新当前文本框的样式覆盖。点“仅重新嵌字”即可查看新效果。'
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
  status.value = '已更新当前文本框的译文。点“仅重新嵌字”即可查看新效果。'
}

function clearTranslationOverrides() {
  translationRegionOverrides.value = {}
  status.value = '已清空当前会话里的人工译文修改。'
}

function clearStyleOverrides() {
  styleRegionOverrides.value = {}
  status.value = '已清空当前会话里的手动样式覆盖。'
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
  downloadUrl.value = ''
  downloadPath.value = ''
  translatedDirPath.value = ''
  maskDebugDirPath.value = ''
  progress.value = { current: 0, total: 0 }
  resetTranslationReview()
  resetStyleInspector()
  closeSocket()

  try {
    const formData = new FormData()
    formData.append('file', selectedFile.value)

    const response = await fetch(toApiUrl('/api/upload'), {
      method: 'POST',
      body: formData
    })

    const payload = await response.json()
    if (!response.ok) {
      throw new Error(payload.detail || '上传失败')
    }

    sessionId.value = payload.session_id
    originalImages.value = (payload.images || []).map((image, index) => ({
      id: `${payload.session_id}-${index}`,
      name: image.name,
      url: toApiUrl(image.url)
    }))
    status.value = `上传完成，共解析 ${payload.total_images} 张图片。现在可以开始翻译。`
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
  renderNonce.value = Date.now()
  translatedImages.value = []
  downloadUrl.value = ''
  downloadPath.value = ''
  translatedDirPath.value = ''
  maskDebugDirPath.value = ''
  errorMessage.value = ''
  translating.value = true
  progress.value = { current: 0, total: 0 }
  status.value = action === 'rerender' ? '正在启动重嵌字任务...' : '正在启动翻译任务...'
  closeSocket()

  socket = new WebSocket(toWebSocketUrl(`/ws/translate/${sessionId.value}`))

  socket.onopen = () => {
    socket.send(JSON.stringify({ action, config: buildRuntimeConfig() }))
  }

  socket.onmessage = (event) => {
    const payload = JSON.parse(event.data)

    if (payload.event === 'start') {
      progress.value = { current: 0, total: payload.total_pages }
      status.value = activeAction.value === 'rerender'
        ? `重嵌字已开始，共 ${payload.total_pages} 张图片。`
        : `翻译已开始，共 ${payload.total_pages} 张图片。`
      return
    }

    if (payload.event === 'progress') {
      progress.value = {
        current: payload.current,
        total: payload.total
      }
      translatedImages.value.push({
        id: `${sessionId.value}-translated-${payload.current}`,
        name: payload.name || `第 ${payload.current} 张`,
        url: withCacheBust(toApiUrl(payload.image_url))
      })
      status.value = activeAction.value === 'rerender'
        ? `重嵌字进行中：${payload.current} / ${payload.total}`
        : `翻译进行中：${payload.current} / ${payload.total}`
      return
    }

    if (payload.event === 'status') {
      status.value = payload.message || '正在进行复杂页增强修复...'
      return
    }

    if (payload.event === 'completed') {
      translating.value = false
      downloadUrl.value = withCacheBust(toApiUrl(payload.download_url))
      downloadPath.value = payload.download_path || ''
      translatedDirPath.value = payload.translated_dir || ''
      maskDebugDirPath.value = payload.mask_debug_dir || ''
      status.value = activeAction.value === 'rerender'
        ? `重嵌字完成，共输出 ${translatedImages.value.length} 张图片。`
        : `翻译完成，共输出 ${translatedImages.value.length} 张图片。`
      void loadEditInspection()
      closeSocket()
      return
    }

    if (payload.event === 'error') {
      translating.value = false
      errorMessage.value = payload.message || '翻译失败'
      status.value = activeAction.value === 'rerender' ? '重嵌字失败。' : '翻译失败。'
      closeSocket()
    }
  }

  socket.onerror = () => {
    errorMessage.value = '翻译连接中断，请查看后端控制台日志。'
    status.value = activeAction.value === 'rerender' ? '重嵌字连接中断。' : '翻译连接中断。'
    translating.value = false
    closeSocket()
  }

  socket.onclose = () => {
    if (translating.value) {
      errorMessage.value = '翻译任务意外断开，请查看后端日志。'
      status.value = activeAction.value === 'rerender' ? '重嵌字未完成。' : '翻译未完成。'
      translating.value = false
    }
  }
}

onMounted(() => {
  checkBackendStatus()
  loadFonts()
})

onBeforeUnmount(() => {
  closeSocket()
})

watch(
  config,
  (nextValue) => {
    saveStoredConfig(nextValue)
  },
  { deep: true }
)

watch(
  () => config.value.translator,
  (nextTranslator) => {
    if (!isValidTranslatorModel(nextTranslator, config.value.translator_model)) {
      config.value.translator_model = getDefaultTranslatorModel(nextTranslator)
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
      void loadEditInspection()
      return
    }
    syncEditSelection()
  }
)
</script>

<template>
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

      <div class="config-grid">
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
            <option value="doubao-seed-translation-250915">doubao-seed-translation-250915 (翻译增强 / 推荐)</option>
            <option value="doubao-seed-2-0-mini-260215">doubao-seed-2-0-mini-260215 (通用文本 / 多模态)</option>
          </select>
          <small class="field-hint">
            按火山方舟官方模型列表接入；当前只开放你指定的这两个模型。
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
                  ? '会保存在当前浏览器本地，并按火山方舟兼容 OpenAI SDK 的方式调用 Chat API。'
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
            <select v-model="config.style_font_gothic_key">
              <option value="">跟随翻译字体</option>
              <option
                v-for="font in availableFonts"
                :key="`gothic-${font.id}`"
                :value="font.id"
              >
                {{ font.label }}
              </option>
            </select>
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

      <div class="action-row">
        <button
          class="primary-button"
          :disabled="!canTranslate"
          @click="startTranslation('translate')"
        >
          {{ translating ? '翻译进行中...' : '开始翻译' }}
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

    <section class="gallery-card">
      <div class="section-head">
        <div>
          <p class="eyebrow">Original</p>
          <h2>上传预览</h2>
        </div>
        <p>{{ originalImages.length }} 张图片</p>
      </div>

      <div v-if="originalImages.length" class="gallery-grid">
        <article
          v-for="image in originalImages"
          :key="image.id"
          class="gallery-item"
        >
          <img
            :alt="image.name"
            :src="image.url"
            loading="lazy"
          />
          <p>{{ image.name }}</p>
        </article>
      </div>

      <div v-else class="empty-state">
        上传完成后，这里会显示原图预览。
      </div>
    </section>

    <section class="gallery-card">
      <div class="section-head">
        <div>
          <p class="eyebrow">Translated</p>
          <h2>翻译结果</h2>
        </div>
        <p>{{ translatedImages.length }} 张图片</p>
      </div>

      <div v-if="translatedImages.length" class="gallery-grid">
        <article
          v-for="image in translatedImages"
          :key="image.id"
          class="gallery-item"
        >
          <img
            :alt="image.name"
            :src="image.url"
            loading="lazy"
          />
          <p>{{ image.name }}</p>
        </article>
      </div>

      <div v-else class="empty-state">
        点击“开始翻译”后，这里会随着后端处理逐张显示翻译后的页面。
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
            class="secondary-button"
            type="button"
            :disabled="editInspectionLoading"
            @click="loadEditInspection"
          >
            {{ editInspectionLoading ? '正在读取...' : '刷新逐框列表' }}
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
            v-if="canRerender"
            class="secondary-button"
            type="button"
            @click="startTranslation('rerender')"
          >
            保存修改并重新嵌字
          </button>
        </div>
      </div>

      <div v-if="editInspectionLoading" class="empty-state">
        正在读取当前会话的逐框校对数据…
      </div>

      <div v-else-if="selectedEditPage" class="style-inspector">
        <div class="style-toolbar">
          <label class="field">
            <span>校对页面</span>
            <select v-model="selectedEditPageKey">
              <option
                v-for="page in mergedInspectionPages"
                :key="page.stored_name"
                :value="page.stored_name"
              >
                {{ page.name }}（{{ page.regions.length }} 个文本框）
              </option>
            </select>
          </label>

          <div class="field style-summary">
            <span>使用说明</span>
            <small class="field-hint">
              这里把译文和字体样式放在同一个列表里处理。改完后直接点“保存修改并重新嵌字”即可，复用缓存重新出图，不需要重跑 OCR 和擦字。
            </small>
          </div>
        </div>

        <div class="style-workbench">
          <div class="style-preview">
            <div class="style-preview-canvas">
              <img
                :alt="selectedEditPage.name"
                :src="withCacheBust(toApiUrl(selectedEditPage.image_url))"
              />

              <button
                v-for="region in selectedEditPage.regions"
                :key="region.id"
                type="button"
                :class="['style-box', selectedEditRegionKey === region.id ? 'active' : '']"
                :style="getStyleRegionBoxStyle(region, selectedEditPage)"
                @click="selectedEditRegionKey = region.id"
              >
                <span>{{ region.index + 1 }}</span>
              </button>
            </div>
          </div>

          <div class="style-region-list">
            <article
              v-for="region in selectedEditPage.regions"
              :key="`edit-${region.id}`"
              :class="['style-region-card', selectedEditRegionKey === region.id ? 'active' : '']"
              @click="selectedEditRegionKey = region.id"
            >
              <div class="style-region-head">
                <strong>文本框 #{{ region.index + 1 }}</strong>
                <div class="style-badges">
                  <span
                    v-if="config.font_style_mode === 'auto-map'"
                    class="style-badge"
                  >
                    自动：{{ getStyleLabel(region.auto_style) }}
                  </span>
                  <span
                    v-if="config.font_style_mode === 'auto-map'"
                    class="style-badge style-badge-strong"
                  >
                    当前：{{ getStyleLabel(getResolvedStyle(region)) }}
                  </span>
                  <span v-if="translationRegionOverrides[region.id]" class="style-badge style-badge-strong">已改译文</span>
                </div>
              </div>

              <p class="style-source-text">{{ region.source_text || '（没有识别到可用原文）' }}</p>

              <div class="region-card-controls" :class="{ compact: config.font_style_mode !== 'auto-map' }">
                <label v-if="config.font_style_mode === 'auto-map'" class="field style-override-field compact-field">
                  <span>字体样式</span>
                  <select
                    :value="getRegionOverrideValue(region)"
                    @change="updateStyleOverride(region, $event.target.value)"
                  >
                    <option value="">跟随自动识别（{{ getStyleLabel(region.auto_style) }}）</option>
                    <option
                      v-for="option in styleBucketOptions"
                      :key="`${region.id}-${option.value}`"
                      :value="option.value"
                    >
                      {{ option.label }}
                    </option>
                  </select>
                </label>

                <label class="field style-override-field compact-field compact-field-grow">
                  <span>译文</span>
                  <textarea
                    class="translation-review-textarea compact-textarea"
                    :value="getEditRegionText(region)"
                    rows="2"
                    @input="updateTranslationOverride(region, $event.target.value)"
                  ></textarea>
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
  </main>
</template>
