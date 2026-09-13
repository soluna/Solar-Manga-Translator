<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { dismiss, toast, toastError, toasts } from '../composables/useToast.js'
import { useProject } from '../composables/useProject.js'
import { useGlossary } from '../composables/useGlossary.js'
import ThemeToggle from '../components/ThemeToggle.vue'
import { loadProcessingConfig } from '../api/processing-config.js'

const route = useRoute(), router = useRouter()
const sessionId = computed(() => String(route.params.sessionId || ''))
const { project, loadProject } = useProject()
const projectTitle = computed(() => project.value?.project?.title || '项目')
const glossary = useGlossary()
const { entries, candidates, previewPayload, dirty: isDirty, error: loadError, canApply } = glossary
const preparing = ref(false)
const busy = computed(() => preparing.value || Boolean(glossary.busy.value) || !glossary.loaded.value)
const loading = computed(() => glossary.busy.value === 'load')
const saving = computed(() => glossary.busy.value === 'save')
const extracting = computed(() => glossary.busy.value === 'extract')
const previewing = computed(() => glossary.busy.value === 'preview')
const applying = computed(() => glossary.busy.value === 'apply')
const previewOpen = ref(false), extractDone = ref(false)
const CATEGORY_OPTIONS = ['人名', '组织/团体', '地点', '作品/道具/技能', '行业术语', '其他']
const previewItems = computed(() => (previewPayload.value?.changes || []).map(item => ({
  page: item.page_name || item.page_id, pageId: item.page_id, regionId: item.region_id,
  from: item.before, to: item.after, text: item.source_text,
})))
let viewEpoch = 0

function isCurrent(id, epoch) {
  return epoch === viewEpoch && String(id || '') === sessionId.value
}

async function loadGlossary() {
  const id = sessionId.value
  const epoch = viewEpoch
  if (!id) return
  if (isDirty.value && !await saveGlossary()) return
  if (!isCurrent(id, epoch)) return
  try {
    await glossary.load(id)
  } catch (error) {
    if (isCurrent(id, epoch)) toastError(error)
  }
}
watch(sessionId, async id => {
  const epoch = ++viewEpoch
  previewOpen.value = false
  extractDone.value = false
  const results = await Promise.allSettled([glossary.load(id), loadProject(id)])
  if (!isCurrent(id, epoch)) return
  for (const result of results) if (result.status === 'rejected') toastError(result.reason)
}, { immediate: true })

function addEntry() {
  if (busy.value) return
  entries.value.unshift({ id: `term_${crypto.randomUUID()}`, source: '', translation: '',
    note: '', category: '其他', replacement: '', source_kind: 'user', occurrences: [] })
}
function removeEntry(index) { if (!busy.value) entries.value.splice(index, 1) }
function categoryOptionsFor(entry) {
  return entry.category && !CATEGORY_OPTIONS.includes(entry.category)
    ? [...CATEGORY_OPTIONS, entry.category] : CATEGORY_OPTIONS
}
async function saveGlossary() {
  try {
    await glossary.save()
    toast(`名词库已保存（${entries.value.length} 条）。`, 'ok')
    return !isDirty.value
  } catch (error) { toastError(error); return false }
}
async function extractCandidates() {
  if (busy.value) return
  const id = sessionId.value
  const epoch = viewEpoch
  // Config loading is asynchronous too; reserve the extraction action before
  // awaiting it so a double click cannot queue duplicate requests.
  preparing.value = true
  try {
    const config = await loadProcessingConfig(project.value?.config || {})
    if (!isCurrent(id, epoch)) return
    const result = await glossary.extract(config)
    if (!isCurrent(id, epoch)) return
    extractDone.value = true
    toast(candidates.value.length ? `提取到 ${candidates.value.length} 个新候选，采纳后保存。`
      : result?.message || '没有发现新候选。', candidates.value.length ? 'ok' : 'info')
  } catch (error) { if (isCurrent(id, epoch)) toastError(error) }
  finally { preparing.value = false }
}
function adoptCandidate(candidate) { glossary.adopt(candidate) }
async function runPreview() {
  if (busy.value) return
  const id = sessionId.value
  const epoch = viewEpoch
  try {
    await glossary.preview()
    if (isCurrent(id, epoch)) previewOpen.value = true
  } catch (error) { if (isCurrent(id, epoch)) toastError(error) }
}
async function applyGlossary() {
  if (!canApply.value || busy.value) return
  const id = sessionId.value
  const epoch = viewEpoch
  try {
    const result = await glossary.apply()
    if (!isCurrent(id, epoch)) return
    previewOpen.value = false
    toast(`已应用，更新 ${result?.change_count || 0} 处译文。`, 'ok')
  } catch (error) { if (isCurrent(id, epoch)) toastError(error) }
}
function evidenceTarget(pageId, regionId) {
  const target = { path: `/review/${encodeURIComponent(sessionId.value)}/${encodeURIComponent(pageId)}` }
  if (regionId) target.query = { region: regionId }
  return target
}
function goBack() {
  const page = String(route.query.page || project.value?.images?.[0]?.stored_name || '')
  router.push(page ? evidenceTarget(page, '') : `/pages/${encodeURIComponent(sessionId.value)}`)
}
async function leaveSafely() {
  if (busy.value) { toast('名词库操作尚未完成，请稍候。', 'warn'); return false }
  return !isDirty.value || await saveGlossary()
}
function beforeUnload(event) {
  if (isDirty.value || glossary.busy.value) { event.preventDefault(); event.returnValue = '' }
}
onBeforeRouteLeave(leaveSafely)
onBeforeRouteUpdate(leaveSafely)
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))
</script>

<template>
  <div class="app">
    <!-- 顶部命令栏 -->
    <header class="topbar">
      <a class="topbar-brand" title="回到首页" @click="router.push('/')">
        <span class="brand-mark">S</span>
        <span class="brand-word">Solar<small>MANGA&nbsp;STUDIO</small></span>
      </a>
      <div class="topbar-divider"></div>
      <button class="btn btn-ghost btn-sm" data-tip="返回审校" @click="goBack">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
        返回审校
      </button>
      <div class="topbar-title">
        <strong>{{ projectTitle }}</strong>
        <span>{{ entries.length }} 词条</span>
      </div>
      <div class="topbar-spacer"></div>
      <div class="topbar-actions">
        <ThemeToggle />
      </div>
    </header>

    <main class="glossary-view">
      <div class="glossary-inner">
        <!-- 页头 -->
        <div class="pages-head">
          <div>
            <span class="kicker">Project Glossary</span>
            <h2>专有名词库</h2>
            <p class="sub">统一角色、地名与招式的译法。先预览当前词条，再应用替换并重嵌字。</p>
            <p class="inline-note">AI 提取会将项目 OCR 原文发送给所选翻译服务；候选经你采纳并保存后加入名词库。</p>
          </div>
        </div>

        <!-- 统计 -->
        <div class="glossary-stats">
          <div class="glossary-stat">
            <span>词条</span>
            <strong>{{ entries.length }}</strong>
          </div>
          <div class="glossary-stat">
            <span>AI 候选</span>
            <strong>{{ candidates.length }}</strong>
          </div>
          <div class="glossary-stat">
            <span>待保存修改</span>
            <strong :style="isDirty ? 'color: var(--warn);' : ''">{{ isDirty ? '有' : '无' }}</strong>
          </div>
        </div>

        <!-- 操作行 -->
        <div class="inline-actions">
          <button class="btn btn-secondary" :disabled="busy" @click="addEntry">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
            新增词条
          </button>
          <button class="btn btn-secondary" :disabled="!isDirty || busy" @click="saveGlossary">
            <span v-if="saving" class="spin" />
            <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
            {{ saving ? '保存中…' : '保存' }}
          </button>
          <button class="btn btn-secondary" :disabled="busy" @click="extractCandidates">
            <span v-if="extracting" class="spin" />
            <span v-else class="badge is-ai no-dot">AI</span>
            {{ extracting ? '提取中…' : '提取 / 补充' }}
          </button>
          <button class="btn btn-secondary" :disabled="busy" @click="runPreview">
            <span v-if="previewing" class="spin" />
            <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
            {{ previewing ? '生成中…' : '预览应用' }}
          </button>
          <button class="btn btn-secondary" :disabled="Boolean(glossary.busy.value)" @click="loadGlossary()">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
            刷新
          </button>
          <button class="btn btn-primary" :disabled="!canApply" @click="applyGlossary">
            <span v-if="applying" class="spin" />
            <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
            {{ applying ? '应用中…' : '应用预览并重嵌字' }}
          </button>
        </div>

        <!-- 加载失败 -->
        <div v-if="loadError" class="card load-error">
          <span class="inline-note is-error">{{ loadError }}</span>
          <button class="btn btn-secondary btn-sm" @click="loadGlossary()">重试</button>
        </div>

        <!-- 加载态 -->
        <div v-if="loading" class="card empty">
          <span class="spin spin-lg" />
          <strong>正在读取名词库…</strong>
        </div>

        <!-- 空态 -->
        <div v-else-if="!entries.length && !loadError" class="card empty">
          <strong>还没有词条</strong>
          <p>手动新增，或用 AI 从当前项目提取候选。</p>
          <button class="btn btn-primary" :disabled="busy" @click="addEntry">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
            新增词条
          </button>
        </div>

        <!-- 词条卡片 -->
        <article v-for="(entry, index) in entries" :key="entry.id" class="card glossary-entry">
          <header class="glossary-entry-head">
            <span class="g-src">{{ entry.source || '（未填写原文）' }}</span>
            <span class="g-arrow">→</span>
            <span class="g-dst">{{ entry.translation || '—' }}</span>
            <span v-if="entry.category" class="tag">{{ entry.category }}</span>
            <span v-if="entry.source_kind === 'system'" class="tag is-accent">AI 提取</span>
            <div class="spacer"></div>
            <button class="btn btn-danger btn-sm" :disabled="busy" @click="removeEntry(index)">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              删除
            </button>
          </header>
          <div class="glossary-entry-body">
            <label class="field">
              <span>原文</span>
              <input :disabled="busy" v-model="entry.source" type="text" placeholder="日文原文" />
            </label>
            <label class="field">
              <span>译名</span>
              <input :disabled="busy" v-model="entry.translation" type="text" placeholder="统一译法" />
            </label>
            <label class="field">
              <span>分类</span>
              <select :disabled="busy" v-model="entry.category">
                <option value="">未分类</option>
                <option v-for="opt in categoryOptionsFor(entry)" :key="opt" :value="opt">{{ opt }}</option>
              </select>
            </label>
            <label class="field">
              <span>备注</span>
              <input :disabled="busy" v-model="entry.note" type="text" placeholder="可选备注" />
            </label>
            <label class="field">
              <span>替换旧译名</span>
              <input :disabled="busy" v-model="entry.replacement" type="text" placeholder="可选：需要替换的旧译法" />
            </label>
          </div>
          <div v-if="entry.occurrences?.length" class="glossary-occ">
            <router-link v-for="(occ, oi) in entry.occurrences" :key="oi" class="occ-item"
              :to="evidenceTarget(occ.page_id, occ.region_id)">
              <span class="tag">{{ occ.page_name || occ.page_id }}</span>
              <span>{{ occ.source_text }}</span>
              <span>定位原文 →</span>
            </router-link>
          </div>
        </article>

        <!-- AI 候选区 -->
        <section v-if="extractDone && candidates.length" class="card glossary-entry">
          <header class="glossary-entry-head">
            <span class="g-src">AI 提取候选</span>
            <span class="tag is-accent">{{ candidates.length }} 待采纳</span>
            <div class="spacer"></div>
            <span class="inline-note">点选采纳加入词条列表，保存后生效</span>
          </header>
          <div class="glossary-occ candidate-list">
            <div v-for="(candidate, ci) in candidates" :key="ci" class="occ-item candidate-item">
              <span class="cand-copy">
                <strong>{{ candidate.source || '（无原文）' }}</strong>
                <span class="g-arrow">→</span>
                <span class="cand-dst">{{ candidate.translation || '—' }}</span>
                <span v-if="candidate.category" class="tag">{{ candidate.category }}</span>
                <span v-if="candidate.note" class="cand-note">{{ candidate.note }}</span>
              </span>
              <button class="btn btn-secondary btn-sm" :disabled="busy" @click="adoptCandidate(candidate)">
                采纳
              </button>
            </div>
          </div>
        </section>

        <!-- 替换预览 -->
        <section v-if="previewOpen && previewPayload" class="card glossary-entry">
          <header class="glossary-entry-head">
            <span class="g-src">替换预览</span>
            <span v-if="previewItems.length" class="tag">{{ previewItems.length }} 处变更</span>
            <div class="spacer"></div>
            <button class="icon-btn" aria-label="关闭预览" @click="previewOpen = false">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
            </button>
            <span class="inline-note">应用后自动重嵌字</span>
          </header>
          <p v-if="!canApply && !applying" class="inline-note" role="status">词条已修改，请重新预览后再应用。</p>
          <div v-if="previewItems.length" class="glossary-occ">
            <div v-for="(item, pi) in previewItems" :key="pi" class="occ-item">
              <router-link v-if="item.pageId" class="tag" :to="evidenceTarget(item.pageId, item.regionId)">{{ item.page }} ↗</router-link>
              <span>
                <span style="color: var(--text-3);">{{ item.from }}</span>
                →
                <strong style="color: var(--accent); font-weight: 600;">{{ item.to }}</strong>
                <template v-if="item.text">「{{ item.text }}」</template>
              </span>
            </div>
          </div>
          <div v-else class="glossary-occ">
            <span class="inline-note">当前词条不会改变已有译文。应用仍会保存词条，用于后续翻译。</span>
          </div>
        </section>
      </div>
    </main>

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
.load-error { display: flex; align-items: center; gap: 12px; padding: 14px 16px; }

.spin {
  display: inline-block; width: 12px; height: 12px;
  border: 2px solid var(--border-strong); border-top-color: currentColor;
  border-radius: 50%; animation: gv-spin .8s linear infinite; vertical-align: -2px;
}
.spin-lg { width: 22px; height: 22px; border-width: 2.5px; }
@keyframes gv-spin { to { transform: rotate(360deg); } }

.candidate-list { gap: 8px; }
.candidate-item { align-items: center; }
.candidate-item .cand-copy { flex: 1; min-width: 0; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.cand-copy strong { color: var(--text-1); }
.cand-dst { color: var(--accent); font-weight: 600; }
.cand-note { color: var(--text-3); font-size: var(--fs-micro); }

.preview-raw {
  margin: 0; padding: 10px 12px;
  background: var(--bg-panel); border: 1px solid var(--border); border-radius: var(--r-s);
  font-family: var(--font-mono); font-size: var(--fs-micro); color: var(--text-2);
  white-space: pre-wrap; word-break: break-all; max-height: 320px; overflow: auto;
}

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
