<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { apiGetJson, apiPostJson, toApiUrl, withCacheBust, withImagePreviewSize } from '../api/client.js'
import { useProject } from '../composables/useProject.js'
import { toasts, dismiss, toast, toastError } from '../composables/useToast.js'
import ThemeToggle from '../components/ThemeToggle.vue'

const route = useRoute()
const router = useRouter()
const sessionId = computed(() => String(route.params.sessionId || ''))
const pageId = computed(() => String(route.params.pageId || ''))

const { project, loadProject, adoptResponse } = useProject()

const projectTitle = computed(() => project.value?.project?.title || sessionId.value)
const pageIndex = computed(() => {
  const imgs = project.value?.images || []
  const i = imgs.findIndex((img) => img.stored_name === pageId.value)
  return i >= 0 ? i + 1 : '?'
})

// ---- 画布 ----
const canvasImg = ref(null)
const imgNatural = ref({ w: 0, h: 0 })
const imgError = ref(false)

const baseImageUrl = computed(() => {
  const primary = withImagePreviewSize(toApiUrl(`/api/pages/${sessionId.value}/${pageId.value}/base-image`), 1024)
  return withCacheBust(primary)
})
const fallbackImageUrl = computed(() =>
  withCacheBust(withImagePreviewSize(toApiUrl(`/api/pages/${sessionId.value}/${pageId.value}/source-image`), 1024)),
)
const displayUrl = ref('')

function onImgLoad() {
  const el = canvasImg.value
  if (el && el.naturalWidth) {
    imgNatural.value = { w: el.naturalWidth, h: el.naturalHeight }
  }
}

function onImgError() {
  if (!imgError.value) {
    imgError.value = true
    displayUrl.value = fallbackImageUrl.value
  }
}

function toNaturalPoint(event) {
  const el = canvasImg.value
  if (!el || !imgNatural.value.w) return null
  const rect = el.getBoundingClientRect()
  const scale = imgNatural.value.w / rect.width
  const x = Math.round((event.clientX - rect.left) * scale)
  const y = Math.round((event.clientY - rect.top) * scale)
  return [x, y]
}

// ---- 模式与工具 ----
const scope = ref('selection') // full | selection
const tool = ref('click') // click | box | brush
const brushSize = ref(48)
const maskMode = ref('stroke') // stroke | region → local_mask_mode

const TOOL_HINTS = {
  click: '直接点击文字；系统优先命中已有文本框，需要时调用本地文字检测。',
  box: '在图上拖拽框出区域，可连续添加多个矩形。',
  brush: '在文字上涂抹；画笔与点击、框选结果合并成一张 mask。',
}

// ---- 标记 ----
const marks = ref([]) // {kind:'auto'|'box'|'brush', bbox?, points?, radius?, label}
const drawing = ref(null) // 进行中的 box/brush

function addMark(mark) {
  marks.value.push(mark)
}

function removeMark(index) {
  marks.value.splice(index, 1)
}

function clearMarks() {
  marks.value = []
}

async function onCanvasPointerDown(event) {
  if (scope.value === 'full') return
  const pt = toNaturalPoint(event)
  if (!pt) return
  if (tool.value === 'click') {
    await handleClickSelect(pt)
    return
  }
  if (tool.value === 'box') {
    drawing.value = { kind: 'box', start: pt, current: pt }
    window.addEventListener('pointermove', onCanvasPointerMove)
    window.addEventListener('pointerup', onCanvasPointerUp, { once: true })
    return
  }
  if (tool.value === 'brush') {
    drawing.value = { kind: 'brush', points: [pt] }
    window.addEventListener('pointermove', onCanvasPointerMove)
    window.addEventListener('pointerup', onCanvasPointerUp, { once: true })
  }
}

function onCanvasPointerMove(event) {
  const pt = toNaturalPoint(event)
  if (!pt || !drawing.value) return
  if (drawing.value.kind === 'box') {
    drawing.value.current = pt
  } else {
    drawing.value.points.push(pt)
  }
}

function onCanvasPointerUp() {
  window.removeEventListener('pointermove', onCanvasPointerMove)
  const d = drawing.value
  drawing.value = null
  if (!d) return
  if (d.kind === 'box' && d.current) {
    const [x1, y1] = d.start
    const [x2, y2] = d.current
    const bbox = [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)]
    if (bbox[2] - bbox[0] > 6 && bbox[3] - bbox[1] > 6) {
      addMark({ kind: 'box', bbox, label: `选区 ${marks.value.length + 1}` })
    }
  } else if (d.kind === 'brush' && d.points.length > 2) {
    addMark({ kind: 'brush', points: d.points, radius: brushSize.value / 2, label: `笔触 ${marks.value.length + 1}` })
  }
}

async function handleClickSelect(pt) {
  try {
    const data = await apiPostJson(
      `/api/pages/${sessionId.value}/${pageId.value}/advanced-erase/suggest-selection`,
      { point: pt, config: {} },
      '点击选区失败',
    )
    const sel = data?.selection
    const bbox = Array.isArray(sel?.bbox) ? sel.bbox : (Array.isArray(sel) ? sel : null)
    if (bbox && bbox.length === 4) {
      addMark({ kind: 'auto', bbox: bbox.map(Math.round), label: `自动 ${marks.value.length + 1}` })
    } else {
      toast('未能识别该位置的文字区域，试试框选或画笔。', 'warn')
    }
  } catch (err) {
    toastError(err)
  }
}

// bbox → 画布显示坐标（百分比）
function markStyle(mark) {
  if (!mark.bbox || !imgNatural.value.w) return {}
  const [x1, y1, x2, y2] = mark.bbox
  return {
    left: `${(x1 / imgNatural.value.w) * 100}%`,
    top: `${(y1 / imgNatural.value.h) * 100}%`,
    width: `${((x2 - x1) / imgNatural.value.w) * 100}%`,
    height: `${((y2 - y1) / imgNatural.value.h) * 100}%`,
  }
}

function drawingStyle() {
  if (!drawing.value || drawing.value.kind !== 'box' || !imgNatural.value.w) return {}
  return markStyle({ bbox: [
    Math.min(drawing.value.start[0], drawing.value.current[0]),
    Math.min(drawing.value.start[1], drawing.value.current[1]),
    Math.max(drawing.value.start[0], drawing.value.current[0]),
    Math.max(drawing.value.start[1], drawing.value.current[1]),
  ] })
}

function brushPolyline(mark) {
  if (!imgNatural.value.w) return ''
  return mark.points.map(([x, y]) => `${(x / imgNatural.value.w) * 100},${(y / imgNatural.value.h) * 100}`).join(' ')
}

// ---- 执行 ----
const running = ref(false)
const previewAttempt = ref(null) // {attempt_id, kinds: {candidate, mask, overlay}}

async function runErase() {
  if (running.value) return
  running.value = true
  try {
    if (scope.value === 'full') {
      const res = await apiPostJson(
        `/api/pages/${sessionId.value}/${pageId.value}/advanced-erase`,
        { action: 'erase', config: {} },
        '整页擦除失败',
      )
      adoptResponse(res)
      toast('整页擦除完成。', 'ok')
      displayUrl.value = withCacheBust(displayUrl.value)
      return
    }
    const selections = marks.value.filter((m) => m.bbox).map((m) => ({ bbox: m.bbox }))
    const strokes = marks.value.filter((m) => m.points).map((m) => ({ points: m.points, radius: m.radius }))
    if (!selections.length && !strokes.length) {
      toast('请先在画布上标记要擦除的区域。', 'warn')
      return
    }
    const res = await apiPostJson(
      `/api/pages/${sessionId.value}/${pageId.value}/advanced-erase`,
      {
        action: 'local-selection',
        config: {},
        selections: selections.length ? selections : undefined,
        selection_strokes: strokes.length ? strokes : undefined,
        local_mask_mode: maskMode.value === 'stroke' ? 'local' : 'region',
      },
      '选区擦除失败',
    )
    adoptResponse(res)
    toast('选区擦除完成。', 'ok')
    marks.value = []
    displayUrl.value = withCacheBust(displayUrl.value)
  } catch (err) {
    toastError(err)
  } finally {
    running.value = false
  }
}

async function runLamaPreview() {
  if (running.value) return
  running.value = true
  try {
    const selections = marks.value.filter((m) => m.bbox).map((m) => ({ bbox: m.bbox }))
    const strokes = marks.value.filter((m) => m.points).map((m) => ({ points: m.points, radius: m.radius }))
    const res = await apiPostJson(
      `/api/pages/${sessionId.value}/${pageId.value}/advanced-erase`,
      {
        action: 'local-advanced-preview',
        config: {},
        selections: selections.length ? selections : undefined,
        selection_strokes: strokes.length ? strokes : undefined,
        local_mask_mode: maskMode.value === 'stroke' ? 'local' : 'region',
      },
      'LaMa 预览失败',
    )
    const attemptId = res?.attempt_id || res?.preview?.attempt_id || ''
    if (!attemptId) {
      toast('后端未返回预览 ID。', 'warn')
      return
    }
    previewAttempt.value = { attempt_id: attemptId }
    toast('预览已生成。', 'ok')
  } catch (err) {
    toastError(err)
  } finally {
    running.value = false
  }
}

function previewUrl(kind) {
  if (!previewAttempt.value) return ''
  return toApiUrl(`/api/pages/${sessionId.value}/${pageId.value}/advanced-erase/previews/${previewAttempt.value.attempt_id}/${kind}`)
}

async function applyPreview() {
  if (!previewAttempt.value || running.value) return
  running.value = true
  try {
    const res = await apiPostJson(
      `/api/pages/${sessionId.value}/${pageId.value}/advanced-erase`,
      { action: 'local-advanced-apply', config: {}, attempt_id: previewAttempt.value.attempt_id },
      '应用预览失败',
    )
    adoptResponse(res)
    previewAttempt.value = null
    marks.value = []
    toast('已应用新空页。', 'ok')
    displayUrl.value = withCacheBust(displayUrl.value)
  } catch (err) {
    toastError(err)
  } finally {
    running.value = false
  }
}

async function restoreBlank() {
  if (running.value) return
  running.value = true
  try {
    const res = await apiPostJson(
      `/api/pages/${sessionId.value}/${pageId.value}/advanced-erase`,
      { action: 'restore', config: {} },
      '恢复失败',
    )
    adoptResponse(res)
    toast('已恢复擦除前的空页。', 'ok')
    displayUrl.value = withCacheBust(displayUrl.value)
  } catch (err) {
    toastError(err)
  } finally {
    running.value = false
  }
}

function backToReview() {
  router.back()
}

onMounted(async () => {
  displayUrl.value = baseImageUrl.value
  try {
    await loadProject(sessionId.value)
  } catch {
    /* 允许离线浏览画布 */
  }
})

onUnmounted(() => {
  window.removeEventListener('pointermove', onCanvasPointerMove)
})
</script>

<template>
  <div class="app" style="overflow:auto;">
    <header class="topbar">
      <a class="topbar-brand" href="#/">
        <span class="brand-mark">S</span>
        <span class="brand-word">Solar<small>MANGA&nbsp;STUDIO</small></span>
      </a>
      <div class="topbar-divider"></div>
      <button class="btn btn-ghost btn-sm" type="button" @click="backToReview">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
        返回审校
      </button>
      <div class="topbar-title">
        <strong>擦除 · 第 {{ pageIndex }} 页</strong>
        <span>{{ projectTitle }}</span>
      </div>
      <div class="topbar-spacer"></div>
      <div class="topbar-actions">
        <ThemeToggle />
      </div>
    </header>

    <div class="erase-view" style="flex:none;min-height:calc(100vh - 46px);">
      <section class="erase-main">
        <div class="stage-bar">
          <span style="font-size:var(--fs-sub);color:var(--text-3);flex:none;">范围</span>
          <div class="seg" role="group" aria-label="擦除范围">
            <button :class="{ active: scope === 'full' }" @click="scope = 'full'">整页处理</button>
            <button :class="{ active: scope === 'selection' }" @click="scope = 'selection'">指定区域</button>
          </div>
          <div class="spacer"></div>
        </div>

        <div class="erase-canvas-wrap">
          <div class="erase-page" @pointerdown="onCanvasPointerDown">
            <img ref="canvasImg" :src="displayUrl" alt="页面底图" draggable="false" @load="onImgLoad" @error="onImgError" />
            <template v-if="scope === 'selection'">
              <div v-for="(mark, i) in marks.filter(m => m.bbox)" :key="`m${i}`" class="erase-mark" :style="markStyle(mark)">
                <span>{{ mark.kind === 'auto' ? '自动' : '框选' }}</span>
              </div>
              <div v-if="drawing?.kind === 'box'" class="erase-mark is-drawing" :style="drawingStyle()"></div>
              <svg v-if="marks.some(m => m.points) || drawing?.kind === 'brush'" class="brush-layer" viewBox="0 0 100 100" preserveAspectRatio="none">
                <polyline
                  v-for="(mark, i) in marks.filter(m => m.points)"
                  :key="`b${i}`"
                  :points="brushPolyline(mark)"
                  fill="none"
                  stroke="rgba(232,163,61,.75)"
                  :stroke-width="((mark.radius * 2) / imgNatural.w) * 100"
                  stroke-linecap="round"
                  vector-effect="non-scaling-stroke"
                />
                <polyline
                  v-if="drawing?.kind === 'brush'"
                  :points="brushPolyline(drawing)"
                  fill="none"
                  stroke="rgba(232,163,61,.9)"
                  :stroke-width="(brushSize / imgNatural.w) * 100"
                  stroke-linecap="round"
                  vector-effect="non-scaling-stroke"
                />
              </svg>
            </template>
            <div v-if="scope === 'full'" class="full-scope-hint">
              <span class="badge is-accent no-dot" style="height:auto;padding:8px 14px;font-size:var(--fs-sub);">整页处理 · 将自动检测并擦除本页全部文字</span>
            </div>
          </div>
        </div>
      </section>

      <aside class="erase-side">
        <div class="erase-side-body">
          <template v-if="scope === 'selection'">
            <span class="kicker">选择工具</span>
            <div class="tool-list">
              <button class="tool-item" :class="{ active: tool === 'click' }" @click="tool = 'click'">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 4.1 12 6"/><path d="m5.1 8-2.9-.8"/><path d="m6 12-1.9 2"/><path d="M7.2 2.2 8 5.1"/><path d="M9.037 9.69a.498.498 0 0 1 .653-.653l11 4.5a.5.5 0 0 1-.074.949l-4.349 1.041a1 1 0 0 0-.74.739l-1.04 4.35a.5.5 0 0 1-.95.074z"/></svg>
                <span><strong>点击选中</strong><small>自动扩展文字范围</small></span>
              </button>
              <button class="tool-item" :class="{ active: tool === 'box' }" @click="tool = 'box'">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3a2 2 0 0 0-2 2"/><path d="M19 3a2 2 0 0 1 2 2"/><path d="M21 19a2 2 0 0 1-2 2"/><path d="M5 21a2 2 0 0 1-2-2"/><path d="M9 3h1"/><path d="M9 21h1"/><path d="M14 3h1"/><path d="M14 21h1"/><path d="M3 9v1"/><path d="M21 9v1"/><path d="M3 14v1"/><path d="M21 14v1"/></svg>
                <span><strong>框选</strong><small>拖拽矩形区域</small></span>
              </button>
              <button class="tool-item" :class="{ active: tool === 'brush' }" @click="tool = 'brush'">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9.06 11.9 8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/><path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/></svg>
                <span><strong>画笔</strong><small>涂抹补充 mask</small></span>
              </button>
            </div>

            <div class="field" v-if="tool === 'brush'">
              <span>笔刷大小</span>
              <div style="display:flex;align-items:center;gap:10px;">
                <input v-model.number="brushSize" type="range" min="4" max="200" aria-label="笔刷大小" style="flex:1;height:auto;padding:0;border:none;background:transparent;accent-color:var(--accent);" />
                <span class="num" style="flex:none;font-size:var(--fs-sub);color:var(--text-2);">{{ brushSize }} px</span>
              </div>
            </div>

            <p class="erase-hint">{{ TOOL_HINTS[tool] }}</p>
          </template>
          <p v-else class="erase-hint">整页处理将自动检测本页全部文字区域并合成 mask，无需手动标记范围。</p>

          <span class="kicker">擦除方式</span>
          <div style="display:flex;flex-direction:column;gap:10px;">
            <div>
              <label class="check-row"><input v-model="maskMode" type="radio" value="stroke" /><span>只擦文字笔画</span></label>
              <small style="display:block;margin-left:22px;font-size:var(--fs-micro);color:var(--text-3);">尽量保留气泡和边框</small>
            </div>
            <div>
              <label class="check-row"><input v-model="maskMode" type="radio" value="region" /><span>擦整个选区</span></label>
              <small style="display:block;margin-left:22px;font-size:var(--fs-micro);color:var(--text-3);">适合无边框拟声字或整块重绘</small>
            </div>
          </div>

          <template v-if="scope === 'selection'">
            <span class="kicker">范围标记 · {{ marks.length }}</span>
            <div class="mark-list">
              <div v-for="(mark, i) in marks" :key="i" class="mark-item">
                <span class="tag" :class="{ 'is-accent': mark.kind === 'auto' }">{{ mark.kind === 'auto' ? '自动' : mark.kind === 'box' ? '框选' : '画笔' }}</span>
                <span>{{ mark.label }}</span>
                <button class="icon-btn" type="button" aria-label="删除标记" @click="removeMark(i)">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                </button>
              </div>
              <p v-if="!marks.length" class="inline-note">还没有标记。用左侧工具在画布上标记。</p>
            </div>
          </template>
        </div>

        <div class="erase-side-foot">
          <button class="btn btn-ghost" style="width:100%;" :disabled="running" @click="restoreBlank">恢复擦除前空页</button>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">
            <button class="btn btn-ghost" type="button" :disabled="running || !marks.length" @click="marks.pop()">撤销</button>
            <button class="btn btn-ghost" type="button" :disabled="running || !marks.length" @click="clearMarks">清空</button>
          </div>
          <button class="btn btn-primary btn-lg" style="width:100%;" :disabled="running" @click="runErase">
            {{ running ? '处理中…' : '执行擦除' }}
          </button>
          <button class="btn btn-secondary" style="width:100%;" :disabled="running" @click="runLamaPreview">LaMa 预览</button>
        </div>
      </aside>
    </div>

    <section v-if="previewAttempt" class="pages-view" style="flex:none;border-top:1px solid var(--border);">
      <div class="pages-inner">
        <div>
          <span class="kicker">LaMa 预览</span>
          <h2 class="section-title">本地擦除对比</h2>
        </div>
        <div class="compare3">
          <figure>
            <figcaption><strong>原图</strong></figcaption>
            <div class="cmp-img"><img :src="fallbackImageUrl" alt="原图" /></div>
          </figure>
          <figure>
            <figcaption><strong>擦除结果 · LaMa</strong></figcaption>
            <div class="cmp-img"><img :src="previewUrl('candidate')" alt="擦除结果" /></div>
          </figure>
          <figure>
            <figcaption><strong>Mask</strong></figcaption>
            <div class="cmp-img"><img :src="previewUrl('mask')" alt="mask" /></div>
          </figure>
        </div>
        <div class="inline-actions" style="justify-content:flex-end;">
          <button class="btn btn-ghost" type="button" :disabled="running" @click="previewAttempt = null">放弃结果</button>
          <button class="btn btn-primary" type="button" :disabled="running" @click="applyPreview">应用新空页</button>
        </div>
      </div>
    </section>

    <div class="toast-stack">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="`is-${t.kind}`" @click="dismiss(t.id)">{{ t.message }}</div>
    </div>
  </div>
</template>

<style scoped>
.erase-page { position: relative; user-select: none; touch-action: none; }
.erase-page img { display: block; width: 100%; height: auto; pointer-events: none; }
.brush-layer { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.erase-mark.is-drawing { border-style: dashed; }
.full-scope-hint { position: absolute; inset: 0; display: grid; place-items: center; pointer-events: none; }
.toast-stack { position: fixed; right: 20px; bottom: 20px; display: flex; flex-direction: column; gap: 8px; z-index: 100; }
.toast { padding: 10px 16px; border-radius: 10px; background: var(--surface-3, #232838); border: 1px solid var(--line, rgba(255,255,255,.1)); color: var(--text-1, #e8eaf0); font-size: 13px; cursor: pointer; max-width: 360px; }
.toast.is-error { border-color: #e05656; }
.toast.is-warn { border-color: #e8a33d; }
.toast.is-ok { border-color: #3ecfc0; }
</style>