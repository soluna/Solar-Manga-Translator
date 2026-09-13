<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiFetch, apiPostJson, readApiError, toApiUrl, withCacheBust, withImagePreviewSize } from '../api/client.js'
import { useProject } from '../composables/useProject.js'
import { useTaskEvents } from '../composables/useTaskEvents.js'
import { toasts, dismiss, toast, toastError } from '../composables/useToast.js'
import ThemeToggle from '../components/ThemeToggle.vue'
import { loadProcessingConfig } from '../api/processing-config.js'
import { pageStatus, projectReadiness } from '../state/page-status.js'
import { recentPageFor, rememberRecentPage } from '../state/recent-location.js'
import {
  baseImageUploadMessage,
  emptyBaseImageUploadSummary,
  mergeBaseImageUploadFailure,
  mergeBaseImageUploadSummary,
} from '../state/base-image-upload.js'

const route = useRoute()
const router = useRouter()
const sessionId = computed(() => String(route.params.sessionId || ''))

const { project, loading, error, loadProject, adoptResponse } = useProject()
const taskEvents = useTaskEvents()
const { taskState } = taskEvents

const search = ref('')
const exportMenuOpen = ref(false)
const baseImageInput = ref(null)
const baseUploadBusy = ref(false)
const baseUploadProgress = ref({ done: 0, total: 0 })
const baseUploadFeedback = ref(null)

function stablePageNumber(image, index) {
  const explicit = Number(image?.page_number)
  if (Number.isInteger(explicit) && explicit > 0) return explicit
  const zeroBased = Number(image?.page_index)
  if (Number.isInteger(zeroBased) && zeroBased >= 0) return zeroBased + 1
  return index + 1
}

const images = computed(() => (project.value?.images || []).map((image, index) => ({
  ...image,
  page_number: stablePageNumber(image, index),
})))
const readiness = computed(() => projectReadiness(images.value))
const filteredImages = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return images.value
  return images.value.filter((img) =>
    String(img.name || '').toLowerCase().includes(q)
    || String(img.stored_name || '').toLowerCase().includes(q)
    || pageBadge(img).text.includes(q)
    || String(img.page_number) === q,
  )
})

const projectTitle = computed(() => project.value?.project?.title || project.value?.project?.project_id || sessionId.value || '项目')
const pageCount = computed(() => project.value?.total_images ?? images.value.length)
const workflowStage = computed(() => String(project.value?.workflow_stage || 'idle'))
const preparingTask = ref(false)
const taskBusy = computed(() => taskEvents.busy.value || preparingTask.value)
const currentTask = computed(() => taskEvents.sessionId.value === sessionId.value && taskBusy.value ? {
  busy: true, action: taskState.value.activeAction, pageId: taskState.value.activeTaskTargetStoredName,
} : null)

const pipeline = computed(() => {
  const steps = [
    { key: 'detect', no: 1, title: '识别', desc: '文本检测 · OCR · 擦字生成空页', done: readiness.value.recognized, ready: images.value.length > 0 },
    { key: 'translate', no: 2, title: '翻译', desc: 'AI 初稿回填审校工作台', done: readiness.value.translated, ready: readiness.value.canTranslate },
    { key: 'rerender', no: 3, title: '重新嵌字', desc: '把当前译文和排版生成图片', done: readiness.value.rendered, ready: readiness.value.canRender },
  ]
  return steps.map(step => ({ ...step, state: currentTask.value && (currentTask.value.action === step.key
    || (step.key === 'translate' && ['translate-page', 'resume-translate'].includes(currentTask.value.action)))
    ? 'running' : step.done ? 'done' : step.ready ? 'ready' : 'locked' }))
})

const stageBadgeText = computed(() => {
  const map = {
    idle: '待开始', detecting: '识别中', detected: '已识别',
    translating: '翻译中', translated: '已翻译',
  }
  return map[workflowStage.value] || workflowStage.value
})

function pageBadge(img) { return pageStatus(img, currentTask.value) }

function thumbUrl(img) {
  const raw = img?.url || `/api/pages/${sessionId.value}/${img.stored_name}/source-image`
  return withImagePreviewSize(toApiUrl(raw), 320)
}

function openReview(img) {
  if (baseUploadBusy.value) return
  rememberRecentPage(sessionId.value, img.stored_name)
  router.push(`/review/${sessionId.value}/${encodeURIComponent(img.stored_name)}`)
}

function firstPageId() {
  const remembered = recentPageFor(sessionId.value, images.value)
  if (remembered) return remembered
  const first = images.value[0]
  return first ? first.stored_name : ''
}

function enterReview() {
  if (baseUploadBusy.value) return
  const pid = firstPageId()
  if (!pid) {
    toast('项目还没有页面。', 'warn')
    return
  }
  router.push(`/review/${sessionId.value}/${encodeURIComponent(pid)}`)
}

function gotoGlossary() {
  if (baseUploadBusy.value) return
  const page = recentPageFor(sessionId.value, images.value)
  const target = { path: `/glossary/${encodeURIComponent(sessionId.value)}` }
  if (page) target.query = { page }
  router.push(target)
}

// ---- 任务控制 ----
async function runAction(action) {
  if (!sessionId.value || taskBusy.value || baseUploadBusy.value) return
  preparingTask.value = true
  const id = sessionId.value
  try {
    const config = await loadProcessingConfig(project.value?.config || {})
    if (id !== sessionId.value) return
    const nextAction = action === 'translate' && !readiness.value.translated ? 'resume-translate' : action
    taskEvents.start(id, nextAction, config)
  } catch (err) {
    toastError(err)
  } finally { preparingTask.value = false }
}

async function cancelTask() {
  try {
    const requested = await taskEvents.cancel()
    toast(requested ? '已请求取消，等待后台停止。' : '任务已发送，正在确认后台任务；确认后可取消。', requested ? 'ok' : 'warn')
  } catch (err) {
    toastError(err)
  }
}

async function reloadProject(id = sessionId.value) {
  if (!id) return
  try {
    await loadProject(id)
  } catch (err) {
    toastError(err)
  }
}

watch(
  () => taskState.value.eventName,
  (name) => {
    if (taskEvents.sessionId.value !== sessionId.value) return
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
  if (!url || !readiness.value.rendered || taskBusy.value || baseUploadBusy.value) {
    toast('还没有可导出的结果。', 'warn')
    return
  }
  window.open(withCacheBust(toApiUrl(url)), '_blank')
  exportMenuOpen.value = false
}

function exportBlank() {
  if (!readiness.value.blankReady || taskBusy.value || baseUploadBusy.value) return
  window.open(withCacheBust(toApiUrl(`/api/download/${sessionId.value}/blank`)), '_blank')
  exportMenuOpen.value = false
}

// ---- 补充无字图 ----
function pickBaseImages() {
  if (baseUploadBusy.value || taskBusy.value) return
  baseImageInput.value?.click()
}

async function onBaseImagePicked(event) {
  const files = Array.from(event.target.files || [])
  event.target.value = ''
  if (!files.length || baseUploadBusy.value || taskBusy.value) return
  const uploadProjectId = sessionId.value
  if (!uploadProjectId) return
  baseUploadBusy.value = true
  baseUploadProgress.value = { done: 0, total: files.length }
  let summary = emptyBaseImageUploadSummary()
  try {
    // The backend endpoint deliberately accepts one image/archive per request;
    // serialize the batch so a project write lease cannot race itself.
    for (let index = 0; index < files.length; index += 1) {
      const file = files[index]
      if (sessionId.value !== uploadProjectId || taskBusy.value) {
        const reason = sessionId.value === uploadProjectId ? '后台任务已开始，未继续上传' : '项目已切换，未上传'
        for (let rest = index; rest < files.length; rest += 1) {
          summary = mergeBaseImageUploadFailure(summary, files[rest].name, reason)
          baseUploadProgress.value = { done: rest + 1, total: files.length }
        }
        break
      }
      try {
        const fd = new FormData()
        fd.append('file', file)
        const res = await apiFetch(`/api/projects/${encodeURIComponent(uploadProjectId)}/base-images`, { method: 'POST', body: fd })
        if (!res.ok) throw new Error(await readApiError(res, '补充无字图失败'))
        const data = await res.json().catch(() => null)
        if (!data?.base_image_upload || typeof data.base_image_upload !== 'object') {
          throw new Error('后端未返回无字图匹配结果。')
        }
        summary = mergeBaseImageUploadSummary(summary, data)
        if (sessionId.value === uploadProjectId) adoptResponse(data)
      } catch (error) {
        summary = mergeBaseImageUploadFailure(summary, file.name, error.message)
      } finally {
        baseUploadProgress.value = { done: baseUploadProgress.value.done + 1, total: files.length }
      }
    }
    if (sessionId.value === uploadProjectId) {
      baseUploadFeedback.value = summary
      toast(baseImageUploadMessage(summary), summary.matched ? 'ok' : 'warn', 7000)
      await reloadProject(uploadProjectId)
    }
  } finally {
    baseUploadBusy.value = false
  }
}

watch(sessionId, async id => {
  baseUploadFeedback.value = null
  try {
    await loadProject(id)
    if (id === sessionId.value && route.query.resume === '1') {
      const remembered = recentPageFor(id, project.value?.images || [])
      if (remembered) {
        await router.replace(`/review/${encodeURIComponent(id)}/${encodeURIComponent(remembered)}`)
      }
    }
  } catch { /* The view exposes the load error. */ }
}, { immediate: true })
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
        <button class="btn btn-ghost" type="button" :disabled="baseUploadBusy || taskBusy" @click="pickBaseImages">{{ baseUploadBusy ? '补充中…' : '补充无字图' }}</button>
        <div class="export-menu-wrap">
          <button class="btn btn-secondary" type="button" data-tip="导出结果或无字页" aria-haspopup="menu" :aria-expanded="exportMenuOpen" @click="exportMenuOpen = !exportMenuOpen">
            导出
            <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m4 6 4 4 4-4"/></svg>
          </button>
          <div v-if="exportMenuOpen" class="export-menu" role="menu">
            <button type="button" role="menuitem" :disabled="!readiness.rendered || taskBusy || baseUploadBusy" @click="exportResult">导出结果（.zip）</button>
            <button type="button" role="menuitem" :disabled="!readiness.blankReady || taskBusy || baseUploadBusy" @click="exportBlank">导出空页（.zip）</button>
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
            <button class="btn btn-secondary" type="button" :disabled="baseUploadBusy" @click="gotoGlossary">专有名词库</button>
            <button class="btn btn-primary" type="button" :disabled="baseUploadBusy" @click="enterReview">进入审校</button>
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
              :aria-label="`${step.title}：${step.state === 'done' ? '已完成' : step.state === 'running' ? '进行中' : step.state === 'ready' ? '可执行' : '待前一步'}`"
              :class="{ 'is-done': step.state === 'done', 'is-running': step.state === 'running' }"
              :disabled="step.state === 'locked' || step.state === 'running' || taskBusy || baseUploadBusy"
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
            第 1 步生成可编辑空页；第 2 步把译文回填到审校工作台并生成初稿；第 3 步应用调整重新嵌字，生成图片不代表人工审校完成。翻译进行中仍可进入审校查看已完成页。
            <button v-if="taskBusy" class="btn btn-ghost btn-sm" type="button" @click="cancelTask">取消任务</button>
          </p>

          <div class="pages-toolbar">
            <div class="search-box">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
              <input v-model="search" type="search" aria-label="搜索页名、状态或页码" placeholder="搜索页名 / 状态 / 页码" />
            </div>
            <div class="meta">
              <span class="num">{{ pageCount }} 页</span>
              <span class="badge is-accent">{{ stageBadgeText }}</span>
            </div>
          </div>

          <div v-if="baseUploadBusy" class="base-upload-progress card" role="status" aria-live="polite">
            <span class="spin" />
            <span>正在处理无字图 {{ baseUploadProgress.done }} / {{ baseUploadProgress.total }}…</span>
          </div>
          <div v-else-if="baseUploadFeedback" class="base-upload-feedback card" role="status" aria-live="polite">
            <div class="base-upload-feedback-head">
              <strong>{{ baseImageUploadMessage(baseUploadFeedback) }}</strong>
              <button class="icon-btn" type="button" data-tip="关闭反馈" aria-label="关闭无字图上传反馈" @click="baseUploadFeedback = null">×</button>
            </div>
            <div class="base-upload-counts">
              <span class="badge is-ok">匹配 {{ baseUploadFeedback.matched }}</span>
              <span class="badge is-warn">未匹配 {{ baseUploadFeedback.unmatched }}</span>
              <span class="badge is-danger">无效 {{ baseUploadFeedback.invalid }}</span>
              <span v-if="baseUploadFeedback.failed" class="badge is-danger">失败 {{ baseUploadFeedback.failed }}</span>
            </div>
            <details v-if="baseUploadFeedback.unmatchedFiles.length || baseUploadFeedback.invalidFiles.length || baseUploadFeedback.failedFiles.length">
              <summary>查看文件明细</summary>
              <p v-if="baseUploadFeedback.unmatchedFiles.length" class="inline-note">未匹配：{{ baseUploadFeedback.unmatchedFiles.join('、') }}</p>
              <p v-if="baseUploadFeedback.invalidFiles.length" class="inline-note is-error">无效：{{ baseUploadFeedback.invalidFiles.join('、') }}</p>
              <p v-if="baseUploadFeedback.failedFiles.length" class="inline-note is-error">上传失败：{{ baseUploadFeedback.failedFiles.join('、') }}</p>
              <p v-for="(error, errorIndex) in baseUploadFeedback.errors" :key="`${error.file}-${errorIndex}`" class="inline-note is-error">{{ error.file }}：{{ error.message }}</p>
            </details>
          </div>

          <div class="page-grid">
            <a
              v-for="img in filteredImages"
              :key="img.stored_name"
              class="page-card"
              :href="`#/review/${sessionId}/${encodeURIComponent(img.stored_name)}`"
              :title="`第 ${img.page_number} 页 · ${img.name || img.stored_name}`"
              :aria-label="`打开第 ${img.page_number} 页：${img.name || img.stored_name}`"
              @click.prevent="openReview(img)"
            >
              <div class="page-card-media">
                <img :src="thumbUrl(img)" :alt="`第 ${img.page_number} 页`" loading="lazy" />
                <span class="page-no">P{{ img.page_number }}</span>
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

    <input ref="baseImageInput" type="file" accept=".zip,.cbz,.png,.jpg,.jpeg,.webp,.bmp" multiple hidden @change="onBaseImagePicked" />

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
  background: var(--bg-elevated);
  border: 1px solid var(--border-strong);
  border-radius: 10px;
  padding: 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  z-index: 50;
  box-shadow: var(--shadow-pop);
}
.export-menu button {
  text-align: left;
  padding: 8px 12px;
  border: none;
  background: transparent;
  color: var(--text-1);
  font-size: 13px;
  border-radius: 7px;
  cursor: pointer;
}
.export-menu button:hover:not(:disabled) { background: var(--bg-hover); }
.pages-loading, .pages-error {
  padding: 60px 0;
  text-align: center;
  color: var(--text-2);
}
.pages-error { color: var(--danger); }
.pages-error .btn { margin-top: 12px; }
.pages-empty { grid-column: 1 / -1; padding: 40px; text-align: center; color: var(--text-2); }
.toast-stack { position: fixed; right: 20px; bottom: 20px; display: flex; flex-direction: column; gap: 8px; z-index: 100; }
.toast { padding: 10px 16px; border-radius: 10px; background: var(--bg-elevated); border: 1px solid var(--border-strong); color: var(--text-1); font-size: 13px; cursor: pointer; max-width: 360px; }
.toast.is-error { border-color: var(--danger); }
.toast.is-warn { border-color: var(--warn); }
.toast.is-ok { border-color: var(--accent); }
</style>
