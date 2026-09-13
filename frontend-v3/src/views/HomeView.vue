<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiFetch, apiGetJson, readApiError } from '../api/client.js'
import { toasts, dismiss, toast, toastError } from '../composables/useToast.js'
import ThemeToggle from '../components/ThemeToggle.vue'
import { buildImportForm, readDroppedFiles } from '../state/project-import.js'
import { recentPageFor } from '../state/recent-location.js'

const router = useRouter()
const uploading = ref(false)
const readingDrop = ref(false)
const importBusy = computed(() => uploading.value || readingDrop.value)
const backendOnline = ref(null) // null=未知 true/false
const recentProjects = ref([])
const fileInput = ref(null)
const folderInput = ref(null)


function pickFiles() {
  if (importBusy.value) return
  fileInput.value?.click()
}

function pickFolder() {
  if (importBusy.value) return
  folderInput.value?.click()
}

async function checkBackend() {
  try {
    const res = await apiFetch('/api/status')
    backendOnline.value = res.ok
  } catch {
    backendOnline.value = false
  }
}

async function loadRecent() {
  try {
    const data = await apiGetJson('/api/projects', '获取项目列表失败')
    recentProjects.value = Array.isArray(data?.projects) ? data.projects.slice(0, 2) : []
  } catch {
    recentProjects.value = []
  }
}

function relativeTime(isoString) {
  const t = Date.parse(isoString || '')
  if (!Number.isFinite(t)) return ''
  const diff = Date.now() - t
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days} 天前`
  return new Date(t).toLocaleDateString('zh-CN')
}

function stageBadge(stage) {
  const map = {
    idle: { text: '待开始', cls: '' },
    detecting: { text: '识别中', cls: 'is-accent' },
    detected: { text: '已识别', cls: 'is-ok' },
    translating: { text: '翻译中', cls: 'is-accent' },
    translated: { text: '可审校', cls: 'is-ok' },
  }
  return map[stage] || { text: stage || '未知', cls: '' }
}

function recentProjectHref(project) {
  const projectId = String(project?.project_id || '')
  const recentPage = recentPageFor(projectId)
  return recentPage
    ? `#/pages/${encodeURIComponent(projectId)}?resume=1`
    : `#/pages/${encodeURIComponent(projectId)}`
}

function recentProjectMeta(project) {
  const recentPage = recentPageFor(project?.project_id)
  return recentPage ? `继续上次 · ${recentPage}` : `${project?.page_count ?? 0} 页 · ${relativeTime(project?.updated_at)}`
}

async function uploadAndEnter(files, options = {}) {
  if (uploading.value) return
  uploading.value = true
  try {
    const formData = buildImportForm(files, options)
    const res = await apiFetch('/api/upload', { method: 'POST', body: formData })
    if (!res.ok) {
      throw new Error(await readApiError(res, '上传失败'))
    }
    const view = await res.json()
    const sessionId = view?.session_id
    if (!sessionId) {
      throw new Error('后端未返回项目 ID')
    }
    toast('项目创建成功，正在进入…', 'ok')
    router.push(`/pages/${encodeURIComponent(sessionId)}`)
  } catch (err) {
    toastError(err)
    if (!backendOnline.value) {
      toast('后端未连接：请先启动后端（8000 端口）再上传。', 'warn', 6000)
    }
  } finally {
    uploading.value = false
  }
}

async function onFilesPicked(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  if (files.length) await uploadAndEnter(files)
}

async function onFolderPicked(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  if (!files.length) return
  const folderName = files[0]?.webkitRelativePath?.split('/')[0] || '图片文件夹'
  await uploadAndEnter(files, { folderName })
}

async function onDrop(event) {
  event.preventDefault()
  if (importBusy.value) return
  readingDrop.value = true
  try {
    const result = await readDroppedFiles(event.dataTransfer)
    if (result.files.length) await uploadAndEnter(result.files, result)
  } catch (error) {
    toastError(error)
  } finally {
    readingDrop.value = false
  }
}

function onDragOver(event) {
  event.preventDefault()
}

onMounted(() => {
  checkBackend()
  loadRecent()
})
</script>

<template>
  <div class="app">
    <header class="topbar">
      <a class="topbar-brand" href="#/">
        <span class="brand-mark">S</span>
        <span class="brand-word">Solar<small>MANGA&nbsp;STUDIO</small></span>
      </a>
      <div class="topbar-spacer"></div>
      <div class="topbar-actions">
        <span class="conn" :class="{ 'is-down': backendOnline === false }">
          <span class="dot"></span>{{ backendOnline === false ? '后端离线' : backendOnline ? '后端在线' : '检测中…' }}
        </span>
        <a class="btn btn-ghost" href="#/projects">项目管理</a>
        <ThemeToggle />
        <a class="icon-btn" href="#/settings" data-tip="设置" aria-label="设置">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
        </a>
      </div>
    </header>

    <main class="home">
      <div class="home-inner">
        <div class="home-hero">
          <span class="kicker">Local Manga Translation Workbench</span>
          <h1>把生肉放上<em>审片台</em>，<br/>翻译到嵌字，一步一检。</h1>
          <p>在本机管理漫画项目，逐页完成识别、翻译、修图与审校。在线翻译和修图会将所需文本或图片发送给你选择的服务商。</p>
          <div class="home-flow">
            <b>导入</b><i>→</i><b>识别</b><i>→</i><b>翻译</b><i>→</i><b>审校</b><i>→</i><b>导出</b>
          </div>
        </div>

        <section class="dropzone" :class="{ 'is-busy': uploading }" @click="pickFiles" @drop="onDrop" @dragover="onDragOver">
          <span class="dropzone-icon">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/><line x1="12" y1="3" x2="12" y2="15"/></svg>
          </span>
          <strong>{{ importBusy ? (readingDrop ? '正在读取文件夹…' : '正在上传并创建项目…') : '拖入图片 / 图片包，开始新项目' }}</strong>
          <p>支持多张图片、图片文件夹或单个 ZIP / CBZ。导入的素材与项目进度保存在本机。</p>
          <div class="dropzone-formats">
            <span class="tag">ZIP</span>
            <span class="tag">CBZ</span>
            <span class="tag">PNG</span>
            <span class="tag">JPG</span>
            <span class="tag">WEBP</span>
            <span class="tag">文件夹</span>
          </div>
          <div class="dropzone-actions">
            <button class="btn btn-primary btn-lg" type="button" :disabled="importBusy" @click.stop="pickFiles">选择文件</button>
            <button class="btn btn-secondary btn-lg" type="button" :disabled="importBusy" @click.stop="pickFolder">选择文件夹</button>
          </div>
          <input ref="fileInput" type="file" accept=".zip,.cbz,.png,.jpg,.jpeg,.webp,.bmp,.gif" multiple hidden @change="onFilesPicked" />
          <input ref="folderInput" type="file" webkitdirectory hidden @change="onFolderPicked" />
        </section>

        <section class="home-recent">
          <div class="home-recent-head">
            <span class="kicker">继续上次</span>
            <a href="#/projects" class="btn btn-ghost btn-sm">全部项目 →</a>
          </div>
          <div v-if="recentProjects.length" class="recent-cards">
            <a
              v-for="item in recentProjects"
              :key="item.project_id"
              class="recent-card"
              :href="recentProjectHref(item)"
              :aria-label="`继续项目 ${item.title || item.project_id}`"
            >
              <img v-if="item.cover_image" :src="item.cover_image" alt="项目封面" />
              <span v-else class="recent-card-cover-placeholder">{{ (item.title || '?').slice(0, 1) }}</span>
              <div class="recent-card-info">
                <strong>{{ item.title || item.project_id }}</strong>
                <span>{{ recentProjectMeta(item) }}</span>
              </div>
              <span class="badge" :class="stageBadge(item.workflow_stage).cls">{{ stageBadge(item.workflow_stage).text }}</span>
            </a>
          </div>
          <div v-else class="recent-empty">
            <p>还没有项目。从上面拖入第一批图片开始吧。</p>
          </div>
        </section>

        <p class="home-foot">项目存储在本机 · 在线处理范围由所选服务决定</p>
      </div>
    </main>

    <div class="toast-stack">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="`is-${t.kind}`" @click="dismiss(t.id)">{{ t.message }}</div>
    </div>
  </div>
</template>

<style scoped>
.recent-card-cover-placeholder {
  width: 44px;
  height: 60px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  font-weight: 700;
  background: var(--bg-elevated);
  color: var(--text-2);
  border: 1px solid var(--border);
  border-radius: 4px;
}
.recent-empty {
  padding: 24px;
  border: 1px dashed var(--border-strong);
  border-radius: 12px;
  color: var(--text-2);
  text-align: center;
}
.toast-stack {
  position: fixed;
  right: 20px;
  bottom: 20px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  z-index: 100;
}
.toast {
  padding: 10px 16px;
  border-radius: 10px;
  background: var(--bg-elevated);
  border: 1px solid var(--border-strong);
  color: var(--text-1);
  font-size: 13px;
  cursor: pointer;
  max-width: 360px;
}
.toast.is-error { border-color: var(--danger); }
.toast.is-warn { border-color: var(--warn); }
.toast.is-ok { border-color: var(--accent); }
</style>
