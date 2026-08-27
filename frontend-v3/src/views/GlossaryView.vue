<script setup>
/**
 * 专有名词库 — 词条 CRUD / AI 提取候选 / 替换预览 / 应用重嵌字
 * GET  /api/projects/{id}/glossary          → { entries: [{term,translation,note,category?}] }
 * PUT  /api/projects/{id}/glossary          → 整体替换 { entries: [...] }
 * POST /api/projects/{id}/glossary/extract  → AI 提取候选（宽容解析 candidates/entries/items）
 * POST /api/projects/{id}/glossary/preview  → 替换预览（宽容解析）
 * POST /api/projects/{id}/glossary/apply    → 应用替换
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiGetJson, apiPostJson, apiPutJson } from '../api/client.js'
import { dismiss, toast, toastError, toasts } from '../composables/useToast.js'
import { useProject } from '../composables/useProject.js'
import ThemeToggle from '../components/ThemeToggle.vue'

const route = useRoute()
const router = useRouter()
const sessionId = computed(() => String(route.params.sessionId || ''))

const CATEGORY_OPTIONS = ['作品标题', '角色称谓', '地名', '招式', '惯用语', '其他']

// ---- 项目标题（失败时回退 sessionId）----
const { project, loadProject } = useProject()
const projectTitle = computed(
  () => project.value?.project?.title || project.value?.title || sessionId.value || '未命名项目',
)

// ---- 词条编辑态 ----
const entries = ref([])
const savedSnapshot = ref('[]')
const loading = ref(true)
const loadError = ref('')
const saving = ref(false)

// ---- AI 提取 / 预览 / 应用 ----
const extracting = ref(false)
const candidates = ref([])
const extractDone = ref(false)
const previewing = ref(false)
const previewPayload = ref(null)
const previewOpen = ref(false)
const applying = ref(false)

function asText(value) {
  return typeof value === 'string' ? value : value == null ? '' : String(value)
}

function normalizeEntry(raw) {
  const e = raw && typeof raw === 'object' ? raw : {}
  return {
    term: asText(e.term ?? e.source ?? e.src),
    translation: asText(e.translation ?? e.target ?? e.dst),
    note: asText(e.note ?? e.comment),
    category: asText(e.category),
    _fromAi: Boolean(e._fromAi),
  }
}

function cleanEntries(list) {
  return list
    .map((e) => ({
      term: asText(e.term).trim(),
      translation: asText(e.translation).trim(),
      note: asText(e.note).trim(),
      category: asText(e.category).trim(),
    }))
    .filter((e) => e.term || e.translation)
}

const isDirty = computed(
  () => JSON.stringify(cleanEntries(entries.value)) !== savedSnapshot.value,
)

// ---- 加载 ----
async function loadGlossary({ silent = false } = {}) {
  if (!silent) loading.value = true
  loadError.value = ''
  try {
    const payload = await apiGetJson(
      `/api/projects/${encodeURIComponent(sessionId.value)}/glossary`,
      '读取名词库失败',
    )
    const list = Array.isArray(payload?.entries) ? payload.entries : []
    entries.value = list.map(normalizeEntry)
    savedSnapshot.value = JSON.stringify(cleanEntries(entries.value))
  } catch (err) {
    loadError.value = err.message || '读取名词库失败'
    toastError(err)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  loadGlossary()
  try {
    await loadProject(sessionId.value)
  } catch {
    /* 标题回退 sessionId，无需打断 */
  }
})

// ---- 编辑 ----
function addEntry() {
  entries.value.unshift({ term: '', translation: '', note: '', category: '', _fromAi: false })
}

function removeEntry(index) {
  entries.value.splice(index, 1)
}

function categoryOptionsFor(entry) {
  const c = asText(entry.category)
  return c && !CATEGORY_OPTIONS.includes(c) ? [...CATEGORY_OPTIONS, c] : CATEGORY_OPTIONS
}

async function saveGlossary() {
  if (saving.value) return
  saving.value = true
  try {
    const cleaned = cleanEntries(entries.value)
    const payload = await apiPutJson(
      `/api/projects/${encodeURIComponent(sessionId.value)}/glossary`,
      { entries: cleaned },
      '保存名词库失败',
    )
    // 后端可能回写规范化后的 entries，采纳之
    if (Array.isArray(payload?.entries)) {
      entries.value = payload.entries.map(normalizeEntry)
    }
    savedSnapshot.value = JSON.stringify(cleanEntries(entries.value))
    toast(`名词库已保存（${cleanEntries(entries.value).length} 条）。`, 'success')
  } catch (err) {
    toastError(err)
  } finally {
    saving.value = false
  }
}

// ---- AI 提取 ----
function pickArray(payload, keys) {
  if (Array.isArray(payload)) return payload
  if (payload && typeof payload === 'object') {
    for (const key of keys) {
      if (Array.isArray(payload[key])) return payload[key]
    }
  }
  return null
}

async function extractCandidates() {
  if (extracting.value) return
  extracting.value = true
  try {
    const payload = await apiPostJson(
      `/api/projects/${encodeURIComponent(sessionId.value)}/glossary/extract`,
      {},
      'AI 提取失败',
    )
    const list = pickArray(payload, ['candidates', 'entries', 'items']) || []
    candidates.value = list.map((raw) => ({ ...normalizeEntry(raw), _adopted: false }))
    extractDone.value = true
    if (!candidates.value.length) {
      toast('AI 未发现可提取的词条。', 'info')
    } else {
      toast(`AI 提取到 ${candidates.value.length} 条候选，点选采纳后加入词条列表。`, 'success')
    }
  } catch (err) {
    toastError(err)
  } finally {
    extracting.value = false
  }
}

function adoptCandidate(candidate) {
  if (candidate._adopted) return
  const term = asText(candidate.term).trim()
  if (term && entries.value.some((e) => asText(e.term).trim() === term)) {
    candidate._adopted = true
    toast(`「${term}」已在词条列表中。`, 'info')
    return
  }
  entries.value.unshift({
    term: candidate.term,
    translation: candidate.translation,
    note: candidate.note,
    category: candidate.category,
    _fromAi: true,
  })
  candidate._adopted = true
  toast(`已采纳「${term || '新词条'}」，保存后生效。`, 'success')
}

// ---- 替换预览 ----
async function runPreview() {
  if (previewing.value) return
  previewing.value = true
  try {
    const payload = await apiPostJson(
      `/api/projects/${encodeURIComponent(sessionId.value)}/glossary/preview`,
      { entries: cleanEntries(entries.value) },
      '生成替换预览失败',
    )
    previewPayload.value = payload
    previewOpen.value = true
  } catch (err) {
    toastError(err)
  } finally {
    previewing.value = false
  }
}

const previewItems = computed(() => {
  const list = pickArray(previewPayload.value, ['changes', 'items', 'preview', 'replacements', 'results'])
  if (!list) return []
  return list.map((raw) => {
    const item = raw && typeof raw === 'object' ? raw : {}
    return {
      page: asText(item.page ?? item.page_id ?? item.stored_name ?? item.image),
      from: asText(item.from ?? item.source ?? item.before ?? item.term),
      to: asText(item.to ?? item.after ?? item.target ?? item.translation),
      text: asText(item.text ?? item.context ?? item.sentence),
    }
  })
})

// ---- 应用 ----
async function applyGlossary() {
  if (applying.value) return
  applying.value = true
  try {
    await apiPostJson(
      `/api/projects/${encodeURIComponent(sessionId.value)}/glossary/apply`,
      {},
      '应用名词库失败',
    )
    previewOpen.value = false
    toast('已应用，回审校页查看效果。', 'success', 6000)
  } catch (err) {
    toastError(err)
  } finally {
    applying.value = false
  }
}

// ---- 导航 ----
function goBack() {
  router.back()
}
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
            <p class="sub">统一角色、地名与招式的译法，应用后自动重嵌字。</p>
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
          <button class="btn btn-secondary" @click="addEntry">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
            新增词条
          </button>
          <button class="btn btn-secondary" :disabled="!isDirty || saving" @click="saveGlossary">
            <span v-if="saving" class="spin" />
            <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/><polyline points="17 21 17 13 7 13 7 21"/><polyline points="7 3 7 8 15 8"/></svg>
            {{ saving ? '保存中…' : '保存' }}
          </button>
          <button class="btn btn-secondary" :disabled="extracting" @click="extractCandidates">
            <span v-if="extracting" class="spin" />
            <span v-else class="badge is-ai no-dot">AI</span>
            {{ extracting ? '提取中…' : '提取 / 补充' }}
          </button>
          <button class="btn btn-secondary" :disabled="previewing" @click="runPreview">
            <span v-if="previewing" class="spin" />
            <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>
            {{ previewing ? '生成中…' : '预览应用' }}
          </button>
          <button class="btn btn-secondary" :disabled="loading" @click="loadGlossary()">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>
            刷新
          </button>
          <button class="btn btn-primary" :disabled="applying" @click="applyGlossary">
            <span v-if="applying" class="spin" />
            <svg v-else width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>
            {{ applying ? '应用中…' : '应用并重嵌字' }}
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
          <button class="btn btn-primary" @click="addEntry">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M5 12h14"/><path d="M12 5v14"/></svg>
            新增词条
          </button>
        </div>

        <!-- 词条卡片 -->
        <article v-for="(entry, index) in entries" :key="index" class="card glossary-entry">
          <header class="glossary-entry-head">
            <span class="g-src">{{ entry.term || '（未填写原文）' }}</span>
            <span class="g-arrow">→</span>
            <span class="g-dst">{{ entry.translation || '—' }}</span>
            <span v-if="entry.category" class="tag">{{ entry.category }}</span>
            <span v-if="entry._fromAi" class="tag is-accent">AI 提取</span>
            <div class="spacer"></div>
            <button class="btn btn-danger btn-sm" @click="removeEntry(index)">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              删除
            </button>
          </header>
          <div class="glossary-entry-body">
            <label class="field">
              <span>原文</span>
              <input v-model="entry.term" type="text" placeholder="日文原文" />
            </label>
            <label class="field">
              <span>译名</span>
              <input v-model="entry.translation" type="text" placeholder="统一译法" />
            </label>
            <label class="field">
              <span>分类</span>
              <select v-model="entry.category">
                <option value="">未分类</option>
                <option v-for="opt in categoryOptionsFor(entry)" :key="opt" :value="opt">{{ opt }}</option>
              </select>
            </label>
            <label class="field">
              <span>备注</span>
              <input v-model="entry.note" type="text" placeholder="可选备注" />
            </label>
          </div>
        </article>

        <!-- AI 候选区 -->
        <section v-if="extractDone && candidates.length" class="card glossary-entry">
          <header class="glossary-entry-head">
            <span class="g-src">AI 提取候选</span>
            <span class="tag is-accent">{{ candidates.filter((c) => !c._adopted).length }} 待采纳</span>
            <div class="spacer"></div>
            <span class="inline-note">点选采纳加入词条列表，保存后生效</span>
          </header>
          <div class="glossary-occ candidate-list">
            <div v-for="(candidate, ci) in candidates" :key="ci" class="occ-item candidate-item">
              <span class="cand-copy">
                <strong>{{ candidate.term || '（无原文）' }}</strong>
                <span class="g-arrow">→</span>
                <span class="cand-dst">{{ candidate.translation || '—' }}</span>
                <span v-if="candidate.category" class="tag">{{ candidate.category }}</span>
                <span v-if="candidate.note" class="cand-note">{{ candidate.note }}</span>
              </span>
              <button class="btn btn-secondary btn-sm" :disabled="candidate._adopted" @click="adoptCandidate(candidate)">
                {{ candidate._adopted ? '已采纳' : '采纳' }}
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
          <div v-if="previewItems.length" class="glossary-occ">
            <div v-for="(item, pi) in previewItems" :key="pi" class="occ-item">
              <span v-if="item.page" class="tag">{{ item.page }}</span>
              <span>
                <span style="color: var(--text-3);">{{ item.from }}</span>
                →
                <strong style="color: var(--accent); font-weight: 600;">{{ item.to }}</strong>
                <template v-if="item.text">「{{ item.text }}」</template>
              </span>
            </div>
          </div>
          <div v-else class="glossary-occ">
            <span class="inline-note">后端返回的原始结果：</span>
            <pre class="preview-raw">{{ JSON.stringify(previewPayload, null, 2) }}</pre>
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
