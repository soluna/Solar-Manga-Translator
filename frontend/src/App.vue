<script setup>
import { computed, onMounted, ref } from 'vue'

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')

const selectedFile = ref(null)
const status = ref('正在检查后端状态...')
const backendOnline = ref(false)
const uploading = ref(false)
const sessionId = ref('')
const images = ref([])
const errorMessage = ref('')

const acceptValue = '.zip,.cbz,.jpg,.jpeg,.png,.webp'

const canUpload = computed(() => Boolean(selectedFile.value) && !uploading.value)

function toApiUrl(path) {
  if (!path) {
    return ''
  }

  if (path.startsWith('http://') || path.startsWith('https://')) {
    return path
  }

  return `${apiBaseUrl}${path.startsWith('/') ? path : `/${path}`}`
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
  images.value = []
  sessionId.value = ''

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
    images.value = (payload.images || []).map((image, index) => ({
      id: `${payload.session_id}-${index}`,
      name: image.name,
      url: toApiUrl(image.url)
    }))
    status.value = `上传完成，共解析 ${payload.total_images} 张图片。`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '上传失败'
    status.value = '上传未完成。'
  } finally {
    uploading.value = false
  }
}

onMounted(() => {
  checkBackendStatus()
})
</script>

<template>
  <main class="page-shell">
    <section class="hero-card">
      <p class="eyebrow">Manga Auto-Translator</p>
      <h1>本地漫画翻译工作台</h1>
      <p class="hero-copy">
        当前版本先打通上传、解包和预览流程，方便在 Windows 上确认前后端都能正常启动。
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
          {{ uploading ? '正在上传...' : '上传并预览' }}
        </button>
      </div>

      <p v-if="errorMessage" class="error-text">{{ errorMessage }}</p>
      <p v-if="sessionId" class="session-text">Session: {{ sessionId }}</p>
    </section>

    <section class="gallery-card">
      <div class="section-head">
        <div>
          <p class="eyebrow">Preview</p>
          <h2>解析结果</h2>
        </div>
        <p>{{ images.length }} 张图片</p>
      </div>

      <div v-if="images.length" class="gallery-grid">
        <article
          v-for="image in images"
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
        上传完成后，这里会显示后端已解析并暴露出来的图片预览。
      </div>
    </section>
  </main>
</template>
