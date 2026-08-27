<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiFetch, apiPostJson, readApiError, toApiUrl, withCacheBust, withImagePreviewSize } from '../api/client.js'
import { useProject } from '../composables/useProject.js'
import { useTaskEvents } from '../composables/useTaskEvents.js'
import { toasts, dismiss, toast, toastError } from '../composables/useToast.js'
import ThemeToggle from '../components/ThemeToggle.vue'

const route = useRoute()
const router = useRouter()
const sessionId = computed(() => String(route.params.sessionId || ''))

const { project, loading, error, loadProject, adoptResponse } = useProject()
const taskEvents = useTaskEvents()
const { taskState } = taskEvents

const search = ref('')
const exportMenuOpen = ref(false)
const baseImageInput = ref(null)

const images = computed(() => project.value?.images || [])
const filteredImages = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return images.value
  return images.value.filter((img) =>
    String(img.name || '').toLowerCase().includes(q)
    || String(img.stored_name || '').toLowerCase().includes(q),
  )
})

const projectTitle = computed(() => project.value?.project?.title || project.value?.project?.project_id || sessionId.value || '项目')
const pageCount = computed(() => project.value?.total_images ?? images.value.length)
const workflowStage = computed(() => String(project.value?.workflow_stage || 'idle'))
const taskBusy = computed(() => Boolean(taskState.value.activeTaskId) && !['completed', 'failed', 'error', 'cancelled', 'interrupted'].includes(taskState.value.eventName))

// ---- 流水线状态 ----
// idle → 未开始；detecting → 识别中；detected → 已识别可翻译；translating → 翻译中；translated → 可审校/重嵌字
const pipeline = computed(() => {
  const stage = workflowStage.value
  const steps = [
    { key: 'detect', no: 1, title: '识别', desc: '文本检测 · OCR · 擦字生成空页' },
    { key: 'translate', no: 2, title: '翻译', desc: 'AI 初稿回填审校工作台' },
    { key: 'rerender', no: 3, title: '审校嵌字', desc: '应用人工调整，重新嵌字' },
  ]
  const stateOf = (key) => {
    if (key === 'detect') {
      if (stage === 'detecting') return 'running'
      if (['detected', 'translating', 'translated'].includes(stage)) return 'done'
      return 'ready'
    }
    if (key === 'translate') {
      if (stage === 'translating') return 'running'
      if (stage === 'translated') return 'done'
      if (stage === 'detected') return 'ready'
      return 'locked'
    }
    if (stage === 'translated') return 'ready'
    return 'locked'
  }
  return steps.map((s) => ({ ...s, state: stateOf(s.key) }))
})

const stageBadgeText = computed(() => {
  const map = {
    idle: '待开始', detecting: '识别中', detected: '已识别',
    translating: '翻译中', translated: '已翻译',
  }
  return map[workflowStage.value] || workflowStage.value
})

function pageBadge(img) {
  const caps = img?.artifact_state?.capabilities || {}
  if (caps.can_export || workflowStage.value === 'translated') return { text: '已嵌字', cls: 'is-ok' }
  if (taskBusy.value && taskState.value.activeAction === 'translate') return { text: '翻译中', cls: 'is-accent' }
  if (workflowStage.value === 'detected') return { text: '已识别', cls: 'is-ok' }
  return { text: '待处理', cls: '' }
}

function thumbUrl(img) {
  const raw = img?.url || `/api/pages/${sessionId.value}/${img.stored_name}/source-image`
  return withImagePreviewSize(toApiUrl(raw), 320)
}

function openReview(img) {
  router.push(`/review/${sessionId.value}/${encodeURIComponent(img.stored_name)}`)
}

function firstPageId() {
  const first = images.value[0]
  return first ? first.stored_name : ''
}

function enterReview() {
  const pid = firstPageId()
  if (!pid) {
    toast('项目还没有页面。', 'warn')
    return
  }
  router.push(`/review/${sessionId.value}/${encodeURIComponent(pid)}`)
}

function gotoGlossary() {
  router.push(`/glossary/${sessionId.value}`)
}

// ---- 任务控制 ----
async function runAction(action) {
  if (!sessionId.value || taskBusy.value) return
  try {
    taskEvents.start(sessionId.value, action, project.value?.config || {})
  } catch (err) {
    toastError(err)
  }
}

async function cancelTask() {
  const taskId = taskState.value.activeTaskId
  if (!taskId) return
  try {
    await apiPostJson(`/api/tasks/${taskId}/cancel`, {}, '取消任务失败')
    toast('已发送取消请求', 'ok')
  } catch (err) {
    toastError(err)
  }
}

async function reloadProject() {
  try {
    await loadProject(sessionId.value)
  } catch (err) {
    toastError(err)
  }
}

watch(
  () => taskState.value.eventName,
  (name) => {
    if (name === 'completed') {
      toast('任务完成', 'ok')
      reloadProject()
    } else if (name === 'error' || name === 'failed') {
      toast(taskState.value.statusMessage || '任务失败', 'error')
      reloadProject()
    }
  },
)

// ---- 导出 ----
function exportResult() {
  const url = project.value?.download_url
  if (!url) {
    toast('还没有可导出的结果。', 'warn')
    return
  }
  window.open(withCacheBust(toApiUrl(url)), '_blank')
  exportMenuOpen.value = false
}

function exportBlank() {
  window.open(withCacheBust(toApiUrl(`/api/download/${sessionId.value}/blank`)), '_blank')
  exportMenuOpen.value = false
}

// ---- 补充无字图 ----
function pickBaseImages() {
  baseImageInput.value?.click()
}

async function onBaseImagePicked(event) {
  const file = (event.target.files || [])[0]
  event.target.value = ''
  if (!file) return
  try {
    const fd = new FormData()
    fd.append('file', file)
    const res = await apiFetch(`/api/projects/${sessionId.value}/base-images`, { method: 'POST', body: fd })
    if (!res.ok) throw new Error(await readApiError(res, '补充无字图失败'))
    const data = await res.json().catch(() => ({}))
    adoptResponse(data)
    toast('无字图已补充。', 'ok')
    reloadProject()
  } catch (err) {
    toastError(err)
  }
}

onMounted(async () => {
  try {
    await loadProject(sessionId.value)
  } catch {
    /* error ref 已承载 */
  }
})

onUnmounted(() => {
  taskEvents.disconnect()
})
</script>

<template>
  <div class="app">
    <header class="topbar">
      <button class="btn btn-ghost btn-sm" type="button" @click="router.back()">‹ 返回</button>
      <a class="topbar-brand" href="#/">
        <span class="brand-mark">S</span>
        <span class="brand-word">Solar<small>MANGA&nbsp;STUDIO</small></span>
      </a>
      <div class="topbar-divider"></div>
      <div class="topbar-title">
        <strong>{{ projectTitle }}</strong>
        <span>{{ pageCount }} 页 · {{ stageBadgeText }}</span>
      </div>
      <div class="topbar-spacer"></div>
      <div class="topbar-actions">
        <span v-if="taskBusy" class="task-pill is-busy">
          <span class="dot"></span>
          <span class="task-text">{{ taskState.statusMessage || '任务进行中…' }}</span>
          <progress
            v-if="taskState.progress && taskState.progress.total"
            :value="taskState.progress.current"
            :max="taskState.progress.total"
          ></progress>
        </span>
        <a class="btn btn-ghost" href="#/projects">项目管理</a>
        <button class="btn btn-ghost" type="button" @click="pickBaseImages">补充无字图</button>
        <div class="export-menu-wrap">
          <button class="btn btn-secondary" type="button" @click="exportMenuOpen = !exportMenuOpen">
            导出
            <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m4 6 4 4 4-4"/></svg>
          </button>
          <div v-if="exportMenuOpen" class="export-menu">
            <button type="button" @click="exportResult">导出结果（.zip）</button>
            <button type="button" @click="exportBlank">导出空页（.zip）</button>
          </div>
        </div>
        <ThemeToggle />
        <a class="icon-btn" href="#/settings" data-tip="设置" aria-label="设置">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
        </a>
      </div>
    </header>

    <main class="pages-view">
      <div class="pages-inner">
        <div class="pages-head">
          <div>
            <span class="kicker">Project Pages</span>
            <h2>页面列表</h2>
            <p class="sub">确认识别结果，执行翻译，再逐页进入审校。</p>
          </div>
          <div class="pages-head-actions">
            <button class="btn btn-secondary" type="button" @click="gotoGlossary">专有名词库</button>
            <button class="btn btn-primary" type="button" @click="enterReview">进入审校</button>
          </div>
        </div>

        <div v-if="loading && !project" class="pages-loading">正在加载项目…</div>
        <div v-else-if="error && !project" class="pages-error">
          <p>{{ error }}</p>
          <button class="btn btn-secondary" type="button" @click="router.push('/')">返回首页</button>
        </div>

        <template v-else>
          <div class="pipeline">
            <button
              v-for="step in pipeline"
              :key="step.key"
              class="pipe-step"
              :class="{ 'is-done': step.state === 'done', 'is-running': step.state === 'running' }"
              :disabled="step.state === 'locked' || step.state === 'running' || taskBusy"
              @click="step.state === 'ready' || step.state === 'done' ? runAction(step.key) : null"
            >
              <span class="step-no">{{ step.state === 'done' ? '✓' : step.no }}</span>
              <span class="step-copy">
                <strong>{{ step.title }}</strong>
                <span>{{ step.desc }}</span>
              </span>
              <span class="step-state badge" :class="{ 'is-ok': step.state === 'done', 'is-accent': step.state === 'running' }">
                {{ step.state === 'done' ? '已完成' : step.state === 'running' ? '进行中' : step.state === 'ready' ? '可执行' : '待前一步' }}
              </span>
            </button>
          </div>
          <p class="pipeline-note">
            第 1 步生成可编辑空页；第 2 步把译文回填到审校工作台并生成初稿；第 3 步应用审校调整重新嵌字。翻译进行中仍可进入审校查看已完成页。
            <button v-if="taskBusy" class="btn btn-ghost btn-sm" type="button" @click="cancelTask">取消任务</button>
          </p>

          <div class="pages-toolbar">
            <div class="search-box">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
              <input v-model="search" type="search" placeholder="搜索页名 / 状态" />
            </div>
            <div class="meta">
              <span class="num">{{ pageCount }} 页</span>
              <span class="badge is-accent">{{ stageBadgeText }}</span>
            </div>
          </div>

          <div class="page-grid">
            <a
              v-for="(img, index) in filteredImages"
              :key="img.stored_name"
              class="page-card"
              href="javascript:void(0)"
              @click="openReview(img)"
            >
              <div class="page-card-media">
                <img :src="thumbUrl(img)" :alt="`第 ${index + 1} 页`" loading="lazy" />
                <span class="page-no">P{{ index + 1 }}</span>
                <span class="page-status badge" :class="pageBadge(img).cls">{{ pageBadge(img).text }}</span>
              </div>
              <div class="page-card-body">
                <strong>{{ img.name || img.stored_name }}</strong>
                <div class="meta"><span>{{ img.region_count ?? 0 }} 框</span><span>{{ img.stored_name }}</span></div>
              </div>
            </a>
            <div v-if="!filteredImages.length" class="pages-empty">
              <p>{{ search ? '没有匹配的页面。' : '项目还没有页面。' }}</p>
            </div>
          </div>
        </template>
      </div>
    </main>

    <input ref="baseImageInput" type="file" accept=".png,.jpg,.jpeg,.webp,.bmp" hidden @change="onBaseImagePicked" />

    <div class="toast-stack">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="`is-${t.kind}`" @click="dismiss(t.id)">{{ t.message }}</div>
    </div>
  </div>
</template>

<style scoped>
.export-menu-wrap { position: relative; }
.export-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 6px);
  min-width: 180px;
  background: var(--surface-2, #1c2130);
  border: 1px solid var(--line, rgba(255, 255, 255, 0.1));
  border-radius: 10px;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  z-index: 50;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35);
}
.export-menu button {
  text-align: left;
  padding: 8px 12px;
  border: none;
  background: transparent;
  color: var(--text-1, #e8eaf0);
  font-size: 13px;
  border-radius: 7px;
  cursor: pointer;
}
.export-menu button:hover { background: var(--surface-3, #262c3d); }
.pages-loading, .pages-error {
  padding: 60px 0;
  text-align: center;
  color: var(--text-2, #8a8f9e);
}
.pages-error { color: #e05656; }
.pages-error .btn { margin-top: 12px; }
.pages-empty { grid-column: 1 / -1; padding: 40px; text-align: center; color: var(--text-2, #8a8f9e); }
.toast-stack { position: fixed; right: 20px; bottom: 20px; display: flex; flex-direction: column; gap: 8px; z-index: 100; }
.toast { padding: 10px 16px; border-radius: 10px; background: var(--surface-3, #232838); border: 1px solid var(--line, rgba(255,255,255,.1)); color: var(--text-1, #e8eaf0); font-size: 13px; cursor: pointer; max-width: 360px; }
.toast.is-error { border-color: #e05656; }
.toast.is-warn { border-color: #e8a33d; }
.toast.is-ok { border-color: #3ecfc0; }
</style>