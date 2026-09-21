<script setup>
/**
 * 项目管理 — 项目卡片网格 / 快照恢复与固定 / 重命名备注 / 删除
 * GET  /api/projects                       → { projects: [summary...] }
 * POST  /api/projects/{id}/restore         → 项目视图 → /pages/:id
 * PATCH /api/projects/{id}                 → { title, note }
 * DELETE /api/projects/{id}
 * GET   /api/projects/{id}/snapshots       → { snapshots: [...] }
 * POST  /api/projects/{id}/snapshots/{sid}/restore
 * POST  /api/projects/{id}/snapshots/{sid}/pin  body { pinned }
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  apiDeleteJson,
  apiGetJson,
  apiPatchJson,
  apiPostJson,
  toApiUrl,
  withImagePreviewSize,
} from '../api/client.js'
import { dismiss, toast, toastError, toasts } from '../composables/useToast.js'
import ThemeToggle from '../components/ThemeToggle.vue'
import { workflowStageLabelMap } from '../state/workflow-state.js'
import { useTaskEvents } from '../composables/useTaskEvents.js'
import { canContinueProject, canRestoreProjectSnapshot, projectHasActiveTask } from '../state/project-actions.js'
import p1Blank from '../assets/p1-blank.svg'
import { recentPageFor } from '../state/recent-location.js'

const router = useRouter()
const tasks = useTaskEvents()

// ---- 数据 ----
const projects = ref([])
const loading = ref(true)
const loadError = ref('')
const query = ref('')
const sortBy = ref('updated')

// 快照：按 project_id 缓存列表 + 展开标记
const snapshotsMap = reactive({})
const snapshotsOpenMap = reactive({})
const snapshotLoadingId = ref('')
const snapshotsErrorIds = reactive({})

// 操作中标记（防重入）
const restoringId = ref('')
const deletingId = ref('')
const savingId = ref('')
const pinningKey = ref('')
const restoringSnapshotKey = ref('')

// 弹层
const deleteTarget = ref(null)
const renameTarget = ref(null)
const renameDraft = reactive({ title: '', note: '' })
const snapshotTarget = ref(null) // { project, snapshot }

// ---- 加载 ----
async function loadProjects({ silent = false } = {}) {
  if (!silent) loading.value = true
  loadError.value = ''
  try {
    const payload = await apiGetJson('/api/projects', '读取项目列表失败')
    projects.value = Array.isArray(payload.projects) ? payload.projects : []
  } catch (err) {
    loadError.value = err.message || '读取项目列表失败'
    toastError(err)
  } finally {
    loading.value = false
  }
}

onMounted(() => loadProjects())

// ---- 派生 ----
const workflowLabelMap = workflowStageLabelMap || {}

const filteredProjects = computed(() => {
  const q = query.value.trim().toLowerCase()
  const list = projects.value.filter((p) => {
    if (!q) return true
    const haystack = [p.title, p.note, p.project_id, stageLabel(p.workflow_stage)]
      .map((v) => String(v || '').toLowerCase())
      .join(' ')
    return haystack.includes(q)
  })
  if (sortBy.value === 'title') {
    return [...list].sort((a, b) => String(a.title || '').localeCompare(String(b.title || ''), 'zh-CN'))
  }
  return [...list].sort(
    (a, b) => new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime(),
  )
})

function stageLabel(stage) {
  return workflowLabelMap[stage] || String(stage || 'idle')
}

function stageBadgeClass(stage) {
  if (stage === 'translated' || stage === 'exported' || stage === 'done') return 'is-ok'
  if (stage === 'detecting' || stage === 'translating' || stage === 'rerendering') return 'is-accent'
  if (stage === 'detected') return 'is-warn'
  return ''
}

function relTime(iso) {
  if (!iso) return ''
  const time = new Date(iso).getTime()
  if (!Number.isFinite(time)) return ''
  const minutes = Math.floor((Date.now() - time) / 60000)
  if (minutes < 1) return '刚刚'
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days === 1) return '昨天'
  if (days < 7) return `${days} 天前`
  const d = new Date(time)
  return `${d.getMonth() + 1}月${d.getDate()}日`
}

function coverUrl(project) {
  if (project.cover_image) {
    return withImagePreviewSize(toApiUrl(project.cover_image), 320)
  }
  return p1Blank
}

function isBusy(project) {
  return projectHasActiveTask(project) || restoringId.value === project.project_id
}

function canContinue(project) {
  return canContinueProject(project, {
    restoringId: restoringId.value,
    activeTaskBusy: tasks.busy.value,
    activeSessionId: tasks.sessionId.value,
  })
}

function busyLabel(project) {
  const action = String(project.busy_action || '').toLowerCase()
  const map = { detect: '识别中', rerender: '重嵌中', 'translate-page': '本页翻译中', 'resume-translate': '继续翻译中', glossary: '名词库处理中' }
  return map[action] || '处理中'
}

function snapshotKindLabel(kind) {
  const map = { manual: '手动', auto: '自动备份', initial: '初始', translated: '译文', detected: '识后端' }
  return map[kind] || String(kind || '')
}

// ---- 卡片操作 ----
async function continueProject(project) {
  if (!canContinue(project)) return
  restoringId.value = project.project_id
  try {
    await apiPostJson(`/api/projects/${encodeURIComponent(project.project_id)}/restore`, {}, '恢复项目失败')
    toast(`已恢复「${project.title || project.project_id}」，正在打开…`, 'success')
    const target = { path: `/pages/${encodeURIComponent(project.project_id)}` }
    if (recentPageFor(project.project_id)) target.query = { resume: '1' }
    router.push(target)
  } catch (err) {
    toastError(err)
  } finally {
    restoringId.value = ''
  }
}

async function toggleSnapshots(project) {
  const id = project.project_id
  const open = snapshotsOpenMap[id]
  if (open) {
    snapshotsOpenMap[id] = false
    return
  }
  snapshotsOpenMap[id] = true
  if (snapshotsMap[id] === undefined) {
    snapshotLoadingId.value = id
    try {
      const payload = await apiGetJson(`/api/projects/${encodeURIComponent(id)}/snapshots`, '读取快照失败')
      snapshotsMap[id] = Array.isArray(payload.snapshots) ? payload.snapshots : []
      delete snapshotsErrorIds[id]
    } catch (err) {
      snapshotsMap[id] = []
      snapshotsErrorIds[id] = true
      snapshotsOpenMap[id] = false
      toastError(err)
    } finally {
      snapshotLoadingId.value = ''
    }
  }
}

async function pinSnapshot(project, snapshot) {
  const key = `${project?.project_id}:${snapshot.snapshot_id}`
  if (!project || pinningKey.value) return
  pinningKey.value = key
  try {
    const payload = await apiPostJson(
      `/api/projects/${encodeURIComponent(project.project_id)}/snapshots/${encodeURIComponent(snapshot.snapshot_id)}/pin`,
      { pinned: !snapshot.pinned },
      '更新固定状态失败',
    )
    snapshotsMap[project.project_id] = Array.isArray(payload.snapshots)
      ? payload.snapshots
      : snapshotsMap[project.project_id].map((s) =>
          s.snapshot_id === snapshot.snapshot_id ? { ...s, pinned: !snapshot.pinned } : s,
        )
    toast(snapshot.pinned ? '已取消固定该快照。' : '已固定该快照。', 'success')
  } catch (err) {
    toastError(err)
  } finally {
    pinningKey.value = ''
  }
}

function askRestoreSnapshot(project, snapshot) {
  const key = `${project?.project_id}:${snapshot?.snapshot_id}`
  if (!canRestoreProjectSnapshot(project, { restoringKey: restoringSnapshotKey.value, snapshotKey: key })) {
    toast('项目任务进行中，完成或停止后才能恢复快照。', 'warn')
    return
  }
  snapshotTarget.value = { project, snapshot }
}

async function confirmRestoreSnapshot() {
  const target = snapshotTarget.value
  if (!target) return
  const { project, snapshot } = target
  const key = `${project.project_id}:${snapshot.snapshot_id}`
  snapshotTarget.value = null
  if (!canRestoreProjectSnapshot(project, { restoringKey: restoringSnapshotKey.value, snapshotKey: key })) {
    toast('项目任务进行中，完成或停止后才能恢复快照。', 'warn')
    return
  }
  restoringSnapshotKey.value = key
  try {
    const restored = await apiPostJson(
      `/api/projects/${encodeURIComponent(project.project_id)}/snapshots/${encodeURIComponent(snapshot.snapshot_id)}/restore`,
      {},
      '恢复快照失败',
    )
    if (!restored?.session_id) throw new Error('恢复响应缺少项目 ID，请刷新项目列表查看。')
    toast(`已将快照「${snapshot.summary || '未命名'}」恢复为独立项目，正在打开…`, 'ok')
    router.push(`/pages/${encodeURIComponent(restored.session_id)}`)
  } catch (err) {
    toastError(err)
  } finally {
    restoringSnapshotKey.value = ''
  }
}

function openRename(project) {
  renameTarget.value = project
  renameDraft.title = project.title || ''
  renameDraft.note = project.note || ''
}

async function confirmRename() {
  const project = renameTarget.value
  if (!project) return
  renameTarget.value = null
  savingId.value = project.project_id
  try {
    const payload = await apiPatchJson(
      `/api/projects/${encodeURIComponent(project.project_id)}`,
      { title: renameDraft.title.trim(), note: renameDraft.note.trim() },
      '保存项目信息失败',
    )
    const updated = payload?.project || payload
    if (updated && typeof updated === 'object') {
      const index = projects.value.findIndex((p) => p.project_id === project.project_id)
      if (index >= 0) {
        projects.value[index] = {
          ...projects.value[index],
          title: updated.title ?? projects.value[index].title,
          note: updated.note ?? projects.value[index].note,
        }
      }
    }
    toast('项目名称与备注已保存。', 'success')
  } catch (err) {
    toastError(err)
  } finally {
    savingId.value = ''
  }
}

function askDelete(project) {
  deleteTarget.value = project
}

async function confirmDelete() {
  const project = deleteTarget.value
  if (!project) return
  deleteTarget.value = null
  deletingId.value = project.project_id
  try {
    await apiDeleteJson(`/api/projects/${encodeURIComponent(project.project_id)}`, '删除项目失败')
    projects.value = projects.value.filter((p) => p.project_id !== project.project_id)
    delete snapshotsMap[project.project_id]
    delete snapshotsOpenMap[project.project_id]
    toast(`已删除「${project.title || project.project_id}」。`, 'success')
  } catch (err) {
    toastError(err)
  } finally {
    deletingId.value = ''
  }
}

// ---- 导航 ----
function goHome() {
  router.push('/')
}

function goBack() {
  if (window.history.length > 1 && window.history.state?.back) {
    router.back()
  } else {
    router.push('/')
  }
}
</script>

<template>
  <div class="app">
    <!-- 顶部命令栏 -->
    <header class="topbar">
      <a class="topbar-brand" title="回到首页" @click="goHome">
        <span class="brand-mark">S</span>
        <span class="brand-word">Solar<small>MANGA&nbsp;STUDIO</small></span>
      </a>
      <div class="topbar-divider"></div>
      <button class="btn btn-ghost btn-sm" data-tip="返回上一页" @click="goBack">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
        返回
      </button>
      <div class="topbar-title"><strong>项目管理</strong></div>
      <div class="topbar-spacer"></div>
      <div class="topbar-actions">
        <ThemeToggle />
      </div>
    </header>

    <main class="projects-view">
      <div class="projects-inner">
        <!-- 页头 -->
        <div class="pages-head">
          <div>
            <span class="kicker">Projects</span>
            <h2>项目管理</h2>
            <p class="sub">恢复历史项目、管理快照、继续未完成的工作。</p>
          </div>
          <div class="pages-head-actions">
            <button class="btn btn-primary btn-sm" @click="goHome">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
              新建项目
            </button>
          </div>
        </div>

        <!-- 工具行 -->
        <div class="pages-toolbar">
          <div class="search-box">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
            <input v-model="query" type="search" placeholder="搜索项目名称 / 备注 / 状态" />
          </div>
          <label class="field sort-field">
            <select v-model="sortBy">
              <option value="updated">最近更新</option>
              <option value="title">按标题</option>
            </select>
          </label>
          <button class="btn btn-secondary" @click="loadProjects()">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
            刷新
          </button>
        </div>

        <!-- 加载失败 -->
        <div v-if="loadError" class="card load-error">
          <span class="inline-note is-error">{{ loadError }}</span>
          <button class="btn btn-secondary btn-sm" @click="loadProjects()">重试</button>
        </div>

        <!-- 项目网格 -->
        <div v-if="!loading && filteredProjects.length" class="project-grid">
          <article
            v-for="project in filteredProjects"
            :key="project.project_id"
            class="card project-card"
          >
            <div class="project-card-top">
              <img class="project-card-cover" :src="coverUrl(project)" :alt="`${project.title || '项目'} 封面`" loading="lazy" />
              <div class="project-card-info">
                <strong>{{ project.title || project.project_id || '未命名项目' }}</strong>
                <div class="meta">
                  <span>{{ project.page_count ?? 0 }} 页</span>
                  <span>{{ project.region_count ?? 0 }} 框</span>
                  <span>{{ relTime(project.updated_at) }}</span>
                </div>
                <p v-if="project.note" class="project-card-note">{{ project.note }}</p>
                <p v-else class="project-card-note is-empty">暂无备注</p>
                <div class="card-chips">
                  <span class="badge" :class="stageBadgeClass(project.workflow_stage)">{{ stageLabel(project.workflow_stage) }}</span>
                  <span v-if="isBusy(project)" class="badge is-warn">{{ busyLabel(project) }}</span>
                  <span v-if="project.snapshot_count" class="tag">{{ project.snapshot_count }} 快照</span>
                  <span v-if="project.glossary_count" class="tag">{{ project.glossary_count }} 名词</span>
                </div>
              </div>
            </div>

            <div class="project-card-actions">
              <button class="btn btn-primary btn-sm" :disabled="!canContinue(project)" @click="continueProject(project)">
                <span v-if="restoringId === project.project_id" class="spin" />
                <template v-else>{{ projectHasActiveTask(project) ? '重新连接' : '继续' }}</template>
              </button>
              <button class="btn btn-secondary btn-sm" @click="toggleSnapshots(project)">
                <span v-if="snapshotLoadingId === project.project_id" class="spin" />
                <template v-else>{{ snapshotsOpenMap[project.project_id] ? '收起快照' : '查看快照' }}</template>
              </button>
              <button class="icon-btn" data-tip="重命名 / 备注" aria-label="重命名 / 备注" @click="openRename(project)">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/><path d="m15 5 4 4"/></svg>
              </button>
              <div class="spacer"></div>
              <button class="btn btn-danger btn-sm" :disabled="isBusy(project) || deletingId === project.project_id" @click="askDelete(project)">删除</button>
            </div>

            <!-- 快照列表 -->
            <div v-if="snapshotsOpenMap[project.project_id]" class="snapshot-list">
              <div v-if="snapshotsMap[project.project_id]?.length" class="snapshot-items">
                <div v-for="snapshot in snapshotsMap[project.project_id]" :key="snapshot.snapshot_id" class="snapshot-item">
                  <div class="s-copy">
                    <strong>{{ snapshot.summary || snapshotKindLabel(snapshot.kind) || '未命名快照' }}</strong>
                    <span>
                      {{ snapshotKindLabel(snapshot.kind) ? `${snapshotKindLabel(snapshot.kind)} · ` : '' }}{{ relTime(snapshot.created_at) }}
                    </span>
                  </div>
                  <button
                    class="btn btn-ghost btn-sm"
                    :disabled="!canRestoreProjectSnapshot(project, { restoringKey: restoringSnapshotKey, snapshotKey: `${project.project_id}:${snapshot.snapshot_id}` })"
                    @click="askRestoreSnapshot(project, snapshot)"
                  >恢复此快照</button>
                  <button
                    class="icon-btn"
                    :data-tip="snapshot.pinned ? '已固定' : '固定快照'"
                    :aria-label="snapshot.pinned ? '已固定' : '固定快照'"
                    :style="snapshot.pinned ? 'color: var(--warn)' : ''"
                    :disabled="!!pinningKey"
                    @click="pinSnapshot(project, snapshot)"
                  >
                    <svg v-if="pinningKey === `${project.project_id}:${snapshot.snapshot_id}`" class="spin" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 12a9 9 0 1 1-6.2-8.6"/></svg>
                    <svg v-else :viewBox="'0 0 24 24'" :fill="snapshot.pinned ? 'currentColor' : 'none'" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="14" height="14"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>
                  </button>
                </div>
              </div>
              <div v-else-if="snapshotsErrorIds[project.project_id]" class="snapshot-note">
                <span class="inline-note is-error">快照读取失败，请稍后重试。</span>
              </div>
              <div v-else class="snapshot-note">
                <span class="inline-note">{{ snapshotLoadingId === project.project_id ? '正在读取快照…' : '暂无快照' }}</span>
              </div>
            </div>
          </article>
        </div>

        <!-- 空态 -->
        <div v-else-if="!loading && !filteredProjects.length" class="card empty">
          <strong>{{ projects.length ? '没有匹配的项目' : '还没有项目' }}</strong>
          <p>{{ projects.length ? '换个关键词试试。' : '上传第一部作品，开始你的翻译之旅。' }}</p>
          <button v-if="!projects.length" class="btn btn-primary" @click="goHome">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
            上传第一部作品
          </button>
          <button v-else class="btn btn-secondary" @click="query = ''">清除搜索</button>
        </div>

        <!-- 加载态 -->
        <div v-if="loading" class="card empty">
          <span class="spin spin-lg" />
          <strong>正在读取项目…</strong>
        </div>
      </div>
    </main>

    <!-- 重命名 / 备注 -->
    <div v-if="renameTarget" class="overlay" @click.self="renameTarget = null">
      <div class="modal rename-modal">
        <div class="modal-head">
          <div>
            <span class="kicker">Project Meta</span>
            <h3>重命名 / 备注</h3>
          </div>
          <button class="icon-btn" aria-label="关闭" @click="renameTarget = null">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
          </button>
        </div>
        <div class="modal-body">
          <label class="field">
            <span>项目名称</span>
            <input v-model="renameDraft.title" type="text" placeholder="项目名称" />
          </label>
          <label class="field">
            <span>备注</span>
            <textarea v-model="renameDraft.note" rows="3" placeholder="可选的备注说明" />
          </label>
        </div>
        <div class="modal-foot">
          <button class="btn btn-ghost" @click="renameTarget = null">取消</button>
          <button class="btn btn-primary" :disabled="savingId === renameTarget.project_id" @click="confirmRename">
            {{ savingId === renameTarget.project_id ? '保存中…' : '保存' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 删除确认 -->
    <div v-if="deleteTarget" class="overlay" @click.self="deleteTarget = null">
      <div class="modal confirm-modal">
        <div class="modal-head">
          <div>
            <span class="kicker">Danger Zone</span>
            <h3>删除项目</h3>
          </div>
        </div>
        <div class="modal-body">
          <p class="confirm-copy">
            确定要删除「<strong>{{ deleteTarget.title || deleteTarget.project_id }}</strong>」吗？该操作会移除项目及其快照，且无法撤销。
          </p>
        </div>
        <div class="modal-foot">
          <button class="btn btn-ghost" @click="deleteTarget = null">取消</button>
          <button class="btn btn-danger" :disabled="deletingId === deleteTarget.project_id" @click="confirmDelete">
            {{ deletingId === deleteTarget.project_id ? '删除中…' : '确认删除' }}
          </button>
        </div>
      </div>
    </div>

    <!-- 快照恢复确认 -->
    <div v-if="snapshotTarget" class="overlay" @click.self="snapshotTarget = null">
      <div class="modal confirm-modal">
        <div class="modal-head">
          <div>
            <span class="kicker">Snapshot</span>
            <h3>恢复快照</h3>
          </div>
        </div>
        <div class="modal-body">
          <p class="confirm-copy">
            从「{{ snapshotTarget.project.title || snapshotTarget.project.project_id }}」的快照
            「<strong>{{ snapshotTarget.snapshot.summary || '未命名' }}</strong>」（{{ relTime(snapshotTarget.snapshot.created_at) }}）？
            <span class="confirm-hint">创建独立项目副本，并在其中继续编辑。原项目保留。</span>
          </p>
        </div>
        <div class="modal-foot">
          <button class="btn btn-ghost" @click="snapshotTarget = null">取消</button>
          <button
            class="btn btn-primary"
            :disabled="restoringSnapshotKey === `${snapshotTarget.project.project_id}:${snapshotTarget.snapshot.snapshot_id}`"
            @click="confirmRestoreSnapshot"
          >确认恢复</button>
        </div>
      </div>
    </div>

    <!-- Toast 栈 -->
    <div class="toast-stack">
      <TransitionGroup name="toast">
        <div v-for="item in toasts" :key="item.id" class="toast" :class="`is-${item.kind}`" @click="dismiss(item.id)">
          {{ item.message }}
        </div>
      </TransitionGroup>
    </div>
  </div>
</template>

<style scoped>
.sort-field { width: 118px; flex: none; }
.sort-field select { height: 30px; font-size: var(--fs-sub); }

.card-chips { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.project-card-note.is-empty { color: var(--text-3); font-style: italic; }

.load-error { display: flex; align-items: center; gap: 12px; padding: 14px 16px; }

.snapshot-items { display: flex; flex-direction: column; gap: 6px; }
.snapshot-note { padding: 4px 2px; }

.spin {
  display: inline-block; width: 12px; height: 12px;
  border: 2px solid var(--border-strong); border-top-color: currentColor;
  border-radius: 50%; animation: pv-spin .8s linear infinite; vertical-align: -2px;
}
.spin-lg { width: 22px; height: 22px; border-width: 2.5px; }
@keyframes pv-spin { to { transform: rotate(360deg); } }

.rename-modal { width: min(460px, 92vw); }
.rename-modal .modal-body { display: flex; flex-direction: column; gap: 14px; }

.confirm-modal { width: min(440px, 92vw); }
.confirm-modal .modal-head h3 { font-size: 16px; font-weight: 700; }
.confirm-copy { color: var(--text-2); line-height: 1.7; }
.confirm-copy strong { color: var(--text-1); }
.confirm-hint { display: block; margin-top: 8px; font-size: var(--fs-sub); color: var(--warn); }
.confirm-modal .modal-foot .btn-danger { border-color: transparent; background: var(--danger-dim); }

/* Toast 栈 */
.toast-stack {
  position: fixed; right: 18px; bottom: 18px; z-index: 200;
  display: flex; flex-direction: column; gap: 8px; max-width: min(420px, calc(100vw - 36px));
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  padding: 10px 14px;
  border-radius: var(--r-m);
  background: var(--bg-elevated);
  border: 1px solid var(--border-strong);
  box-shadow: var(--shadow-pop);
  font-size: var(--fs-sub);
  color: var(--text-1);
  cursor: pointer;
  display: flex; align-items: center; gap: 8px;
}
.toast::before { content: ""; width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--accent); }
.toast.is-success::before { background: var(--ok); }
.toast.is-error { border-color: var(--danger); }
.toast.is-error::before { background: var(--danger); }
.toast-enter-active, .toast-leave-active { transition: all var(--t-med) var(--ease); }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(6px); }
</style>
