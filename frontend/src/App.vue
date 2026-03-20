<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

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

const config = ref({
  translator: 'sugoi',
  target_lang: 'CHS',
  use_gpu: true
})

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
})

onBeforeUnmount(() => {
  closeSocket()
})
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
            <option value="offline">offline</option>
            <option value="original">original</option>
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
