<template>
  <div class="container mx-auto p-4 max-w-5xl">
    <h1 class="text-3xl font-bold mb-6 text-center text-gray-800">自动漫画汉化工具 (GPU 加速)</h1>

    <!-- 配置与上传区域 -->
    <div v-if="!isProcessing" class="mb-8 p-6 border rounded-lg bg-gray-50 shadow-sm">
      <div class="mb-6">
        <label class="block text-gray-700 font-bold mb-2">上传漫画 (ZIP / CBZ / PNG / JPG)</label>
        <div class="border-2 border-dashed border-gray-300 rounded p-10 text-center hover:bg-gray-100 transition relative">
          <input type="file" @change="handleFileUpload" accept=".zip,.cbz,.jpg,.png" class="absolute inset-0 w-full h-full opacity-0 cursor-pointer" />
          <p v-if="!file" class="text-gray-500">拖拽文件到这里，或者点击选择</p>
          <p v-else class="text-blue-600 font-semibold">{{ file.name }}</p>
        </div>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
        <div>
          <label class="block text-gray-700 font-bold mb-2">目标语言</label>
          <select v-model="config.target_lang" class="w-full border rounded p-2 bg-white">
            <option value="CHS">简体中文 (CHS)</option>
            <option value="CHT">繁体中文 (CHT)</option>
            <option value="ENG">English</option>
          </select>
        </div>
        <div>
          <label class="block text-gray-700 font-bold mb-2">翻译引擎</label>
          <select v-model="config.translator" class="w-full border rounded p-2 bg-white">
            <option value="google">Google Translate</option>
            <option value="deepl">DeepL</option>
            <option value="youdao">网易有道</option>
            <option value="gpt4">GPT-4 (需要配置 Key)</option>
          </select>
        </div>
      </div>

      <div class="mt-6 flex justify-end">
        <button @click="startTranslation" :disabled="!file" class="bg-blue-600 text-white px-6 py-3 rounded-lg font-bold hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed transition shadow">
          🚀 开始翻译
        </button>
      </div>
    </div>

    <!-- 进度显示与画廊 -->
    <div v-else>
      <div class="mb-6 p-4 border rounded-lg bg-blue-50">
        <div class="flex justify-between items-center mb-2">
          <h2 class="text-xl font-bold text-blue-800">翻译进度</h2>
          <span class="text-blue-600 font-semibold">{{ progress.current }} / {{ progress.total }} 页</span>
        </div>
        <div class="w-full bg-blue-200 rounded-full h-4">
          <div class="bg-blue-600 h-4 rounded-full transition-all duration-300" :style="`width: ${(progress.current / progress.total) * 100}%`"></div>
        </div>
        <p v-if="isCompleted" class="mt-4 text-green-600 font-bold text-lg">✅ 全部翻译完成！</p>

        <div v-if="isCompleted" class="mt-4 flex gap-4">
          <button @click="downloadZip" class="bg-green-600 text-white px-4 py-2 rounded font-bold hover:bg-green-700">📥 下载翻译压缩包 (ZIP)</button>
          <button @click="reset" class="bg-gray-500 text-white px-4 py-2 rounded font-bold hover:bg-gray-600">处理新文件</button>
        </div>
      </div>

      <!-- 画廊区域 -->
      <div class="grid grid-cols-1 gap-6">
        <div v-for="(img, idx) in translatedImages" :key="idx" class="border p-2 rounded shadow-md bg-white">
          <div class="flex justify-between text-gray-500 text-sm mb-2 px-1">
            <span>第 {{ idx + 1 }} 页</span>
            <a :href="img" target="_blank" class="text-blue-500 hover:underline">查看大图</a>
          </div>
          <img :src="img" class="w-full h-auto object-contain" loading="lazy" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'

const file = ref(null)
const config = ref({ translator: 'google', target_lang: 'CHS' })
const isProcessing = ref(false)
const isCompleted = ref(false)
const progress = ref({ current: 0, total: 100 })
const translatedImages = ref([])
const currentSession = ref('')

const handleFileUpload = (e) => {
  file.value = e.target.files[0]
}

const downloadZip = () => {
  if (currentSession.value) {
    window.location.href = `http://localhost:8000/api/download/${currentSession.value}`
  }
}

const reset = () => {
  file.value = null
  isProcessing.value = false
  isCompleted.value = false
  translatedImages.value = []
  progress.value = { current: 0, total: 100 }
}

const startTranslation = async () => {
  if (!file.value) return
  isProcessing.value = true
  isCompleted.value = false
  translatedImages.value = []

  try {
    // 1. 上传文件
    const formData = new FormData()
    formData.append('file', file.value)
    const res = await fetch('http://localhost:8000/api/upload', { method: 'POST', body: formData })
    const data = await res.json()
    currentSession.value = data.session_id

    // 2. 建立 WebSocket 长连接
    const ws = new WebSocket(`ws://localhost:8000/ws/translate/${data.session_id}`)

    ws.onopen = () => {
      ws.send(JSON.stringify({ images: data.source_images, config: config.value }))
    }

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data)
      if (msg.event === 'start') {
        progress.value.total = msg.total_pages
        progress.value.current = 0
      } else if (msg.event === 'progress') {
        progress.value.current = msg.current
        // 添加时间戳防止浏览器缓存图片
        translatedImages.value.push(`http://localhost:8000${msg.image_url}?t=${Date.now()}`)
      } else if (msg.event === 'completed') {
        isCompleted.value = true
        ws.close()
      } else if (msg.event === 'error') {
        console.error("Translation Error:", msg.message)
      }
    }

    ws.onerror = (e) => {
      console.error("WebSocket Error:", e)
      alert("连接出错，请检查后端是否运行。")
    }
  } catch (error) {
    console.error("Upload Error:", error)
    alert("上传失败！")
    isProcessing.value = false
  }
}
</script>