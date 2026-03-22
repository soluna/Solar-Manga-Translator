<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const configStorageKey = 'manga-translator.ui-config'

function createDefaultConfig() {
  return {
    translator: 'gemini',
    target_lang: 'CHS',
    use_gpu: true,
    api_key: '',
    font_key: ''
  }
}

function normalizeStoredConfig(rawValue) {
  const defaults = createDefaultConfig()
  if (!rawValue || typeof rawValue !== 'object') {
    return defaults
  }

  return {
    translator: typeof rawValue.translator === 'string' ? rawValue.translator : defaults.translator,
    target_lang: typeof rawValue.target_lang === 'string' ? rawValue.target_lang : defaults.target_lang,
    use_gpu: typeof rawValue.use_gpu === 'boolean' ? rawValue.use_gpu : defaults.use_gpu,
    api_key: typeof rawValue.api_key === 'string' ? rawValue.api_key : defaults.api_key,
    font_key: typeof rawValue.font_key === 'string' ? rawValue.font_key : defaults.font_key
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
const sessionId = ref('')
const originalImages = ref([])
const translatedImages = ref([])
const errorMessage = ref('')
const downloadUrl = ref('')
const progress = ref({ current: 0, total: 0 })
const availableFonts = ref([])

const config = ref(loadStoredConfig())

let socket = null

const acceptValue = '.zip,.cbz,.jpg,.jpeg,.png,.webp'

const canUpload = computed(() => Boolean(selectedFile.value) && !uploading.value)
const canTranslate = computed(() => Boolean(sessionId.value) && !translating.value)
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

function closeSocket() {
  if (socket) {
    socket.close()
    socket = null
  }
}

function clearStoredApiKey() {
  config.value.api_key = ''
  saveStoredConfig(config.value)
  status.value = '已清除本机浏览器里保存的 API Key。'
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
  progress.value = { current: 0, total: 0 }
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

function startTranslation() {
  if (!sessionId.value || translating.value) {
    return
  }

  translatedImages.value = []
  downloadUrl.value = ''
  errorMessage.value = ''
  translating.value = true
  progress.value = { current: 0, total: 0 }
  status.value = '正在启动翻译任务...'
  closeSocket()

  socket = new WebSocket(toWebSocketUrl(`/ws/translate/${sessionId.value}`))

  socket.onopen = () => {
    socket.send(JSON.stringify({ config: config.value }))
  }

  socket.onmessage = (event) => {
    const payload = JSON.parse(event.data)

    if (payload.event === 'start') {
      progress.value = { current: 0, total: payload.total_pages }
      status.value = `翻译已开始，共 ${payload.total_pages} 张图片。`
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
        url: toApiUrl(payload.image_url)
      })
      status.value = `翻译进行中：${payload.current} / ${payload.total}`
      return
    }

    if (payload.event === 'completed') {
      translating.value = false
      downloadUrl.value = toApiUrl(payload.download_url)
      status.value = `翻译完成，共输出 ${translatedImages.value.length} 张图片。`
      closeSocket()
      return
    }

    if (payload.event === 'error') {
      translating.value = false
      errorMessage.value = payload.message || '翻译失败'
      status.value = '翻译失败。'
      closeSocket()
    }
  }

  socket.onerror = () => {
    errorMessage.value = '翻译连接中断，请查看后端控制台日志。'
    status.value = '翻译连接中断。'
    translating.value = false
    closeSocket()
  }

  socket.onclose = () => {
    if (translating.value) {
      errorMessage.value = '翻译任务意外断开，请查看后端日志。'
      status.value = '翻译未完成。'
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
            <option value="chatgpt">chatgpt</option>
            <option value="youdao">有道 (youdao)</option>
            <option value="baidu">百度 (baidu)</option>
            <option value="offline">offline</option>
            <option value="none">none</option>
          </select>
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

        <label v-if="config.translator === 'gemini' || config.translator === 'chatgpt'" class="field" style="grid-column: span 2;">
          <span>API Key (可选，留空则使用默认配置)</span>
          <input v-model="config.api_key" type="password" placeholder="输入你的 API Key" style="width: 100%; padding: 8px; border-radius: 4px; border: 1px solid var(--border);" />
          <small class="field-hint field-hint-row">
            <span>会保存在当前浏览器本地，下次打开页面会自动带出。</span>
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

        <label class="toggle-field">
          <input v-model="config.use_gpu" type="checkbox" />
          <span>使用 GPU</span>
        </label>
      </div>

      <div class="action-row">
        <button
          class="primary-button"
          :disabled="!canTranslate"
          @click="startTranslation"
        >
          {{ translating ? '翻译进行中...' : '开始翻译' }}
        </button>

        <a
          v-if="downloadUrl"
          class="secondary-button"
          :href="downloadUrl"
        >
          下载翻译结果
        </a>
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
  </main>
</template>
