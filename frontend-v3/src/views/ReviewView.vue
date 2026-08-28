<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  apiFetch, apiGetJson, apiPostJson, readApiError, toApiUrl,
  withCacheBust, withImagePreviewSize,
} from '../api/client.js'
import { useProject } from '../composables/useProject.js'
import { useTaskEvents } from '../composables/useTaskEvents.js'
import { toasts, dismiss, toast, toastError } from '../composables/useToast.js'
import ThemeToggle from '../components/ThemeToggle.vue'

const route = useRoute()
const router = useRouter()
const sessionId = computed(() => String(route.params.sessionId || ''))
const pageId = computed(() => String(route.params.pageId || ''))

const { project, loadProject, adoptResponse } = useProject()
const taskEvents = useTaskEvents()
const { taskState } = taskEvents

// ---- 基础数据 ----
const document = ref(null)
const docLoading = ref(false)
const fonts = ref([])
const savedAt = ref('')

const images = computed(() => project.value?.images || [])
const currentIndex = computed(() => images.value.findIndex((img) => img.stored_name === pageId.value))
const pageNumber = computed(() => (currentIndex.value >= 0 ? currentIndex.value + 1 : 0))
const projectTitle = computed(() => project.value?.project?.title || '项目')
const regions = computed(() => document.value?.regions || [])
const regionCount = computed(() => regions.value.length)

const taskBusy = computed(() => {
  const s = taskState.value
  return Boolean(s.activeTaskId) && !['completed', 'failed', 'error', 'cancelled', 'interrupted'].includes(s.eventName)
})

const TERMINALS = ['completed', 'failed', 'error', 'cancelled', 'interrupted']

// ---- 图片层 ----
const panes = ref({ frame: true, final: true, src: false, blank: false })
const paneCount = computed(() => Object.values(panes.value).filter(Boolean).length)

function imageUrl(kind, maxSide = 1280) {
  const path = `/api/pages/${sessionId.value}/${pageId.value}/${kind}`
  return withCacheBust(withImagePreviewSize(toApiUrl(path), maxSide))
}

// ---- 自由画布（缩放 + 平移；四个视图共享同一视口联动） ----
// 设计参照旧前端：stage 尺寸 = 原图坐标系（自然像素），img/overlay 同处一个
// 被 transform(translate+scale) 的 stage 内，缩放平移时框零换算自动跟随。
const ZOOM_MIN = 0.2
const ZOOM_MAX = 6
const natural = ref({ w: 0, h: 0 }) // 预览图自然尺寸（兜底坐标源）
const frameCanvas = ref(null)
const view = reactive({ zoom: 1, panX: 0, panY: 0 })
// 用户一旦缩放/平移/点 HUD 即视为接管视口；接管前允许布局稳定过程中反复 refit
let userTookOver = false

// 坐标系 = 原图像素：后端 dimensions 优先（bbox 的参考系），其次预览图自然尺寸
const imgW = computed(() => Number(document.value?.dimensions?.width) || natural.value.w || 800)
const imgH = computed(() => Number(document.value?.dimensions?.height) || natural.value.h || 1200)

const stageStyle = computed(() => ({
  width: `${imgW.value}px`,
  height: `${imgH.value}px`,
  transform: `translate(${view.panX}px, ${view.panY}px) scale(${view.zoom})`,
}))

function clampZoom(v) { return Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Number(v) || 1)) }
function anyCanvasEl() { return frameCanvas.value || window.document.querySelector('.pane-canvas') }

function fitView() {
  const el = anyCanvasEl()
  if (!el || !imgW.value || !imgH.value) return
  const rect = el.getBoundingClientRect()
  if (rect.width < 10 || rect.height < 10) return
  view.zoom = clampZoom(Math.min((rect.width - 2) / imgW.value, (rect.height - 2) / imgH.value))
  view.panX = (rect.width - imgW.value * view.zoom) / 2
  view.panY = (rect.height - imgH.value * view.zoom) / 2
}

function fitWidth() {
  userTookOver = true
  const el = anyCanvasEl()
  if (!el || !imgW.value) return
  const rect = el.getBoundingClientRect()
  view.zoom = clampZoom((rect.width - 2) / imgW.value)
  view.panX = 1
  view.panY = Math.max(1, (rect.height - imgH.value * view.zoom) / 2)
}

function zoomAt(nextZoom, x, y, el = anyCanvasEl()) {
  if (!el) return
  userTookOver = true // 用户主动缩放，接管视口（ResizeObserver 兜底 fit 不再干预）
  const rect = el.getBoundingClientRect()
  const px = x ?? rect.width / 2
  const py = y ?? rect.height / 2
  const k = clampZoom(nextZoom) / view.zoom
  view.panX = px - (px - view.panX) * k
  view.panY = py - (py - view.panY) * k
  view.zoom = clampZoom(nextZoom)
}

function onCanvasWheel(event) {
  if (!document.value) return
  const rect = event.currentTarget.getBoundingClientRect()
  const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12
  zoomAt(view.zoom * factor, event.clientX - rect.left, event.clientY - rect.top, event.currentTarget)
}

// 空白处拖拽平移（添加框模式 / 点在框或控件上时不触发）
const panState = ref(null)
const panning = computed(() => Boolean(panState.value))
function onCanvasPanDown(event) {
  if (addingMode.value || !document.value) return
  if (event.target.closest('.region-box, .region-pop, .canvas-hud, button, a')) return
  event.preventDefault()
  userTookOver = true // 用户主动平移，接管视口
  panState.value = { startX: event.clientX, startY: event.clientY, panX: view.panX, panY: view.panY }
  window.addEventListener('pointermove', onPanMove)
  window.addEventListener('pointerup', onPanUp, { once: true })
}
function onPanMove(event) {
  const d = panState.value
  if (!d) return
  view.panX = d.panX + (event.clientX - d.startX)
  view.panY = d.panY + (event.clientY - d.startY)
}
function onPanUp() {
  window.removeEventListener('pointermove', onPanMove)
  panState.value = null
}

function onFrameImgLoad(event) {
  const img = event.target
  natural.value = { w: img.naturalWidth || 0, h: img.naturalHeight || 0 }
  // nextTick + rAF：等 Vue 提交 DOM 且浏览器完成布局后再测量
  if (!userTookOver) nextTick(() => requestAnimationFrame(() => fitView()))
}

function zoomFit() { userTookOver = true; fitView() }
function zoomWidth() { fitWidth() }
function zoomOut() { zoomAt(view.zoom / 1.15) }
function zoomIn() { zoomAt(view.zoom * 1.15) }
function zoomReset() {
  userTookOver = true
  const el = anyCanvasEl()
  if (!el) { view.zoom = 1; return }
  const rect = el.getBoundingClientRect()
  view.zoom = 1
  view.panX = (rect.width - imgW.value) / 2
  view.panY = (rect.height - imgH.value) / 2
}

// 开/关视图后可用尺寸变化，重新 fit；换页后重置接管标志，由 img onload/RO 重新 fit
watch(paneCount, () => nextTick(() => fitView()))
watch(pageId, () => { userTookOver = false })

// 布局晚稳定（字体/侧栏渲染）时尺寸会多次变化：ResizeObserver 在用户接管前
// 持续 refit，最终一次即为稳定布局的正确 fit；用户交互后立即停手
let canvasResizeObs = null
onMounted(() => {
  canvasResizeObs = new ResizeObserver(() => { if (!userTookOver) fitView() })
  if (frameCanvas.value) canvasResizeObs.observe(frameCanvas.value)
})
onUnmounted(() => canvasResizeObs?.disconnect())

// ---- 文档加载 ----
async function loadDocument() {
  docLoading.value = true
  try {
    const data = await apiGetJson(`/api/pages/${sessionId.value}/${pageId.value}/document`, '加载页面文档失败')
    document.value = data?.document || null
  } catch (err) {
    toastError(err)
    document.value = null
  } finally {
    docLoading.value = false
  }
}

async function ensureProject() {
  if (!project.value || project.value.session_id !== sessionId.value) {
    try {
      await loadProject(sessionId.value)
    } catch {
      /* error 已承载 */
    }
  }
}

async function loadFonts() {
  try {
    const data = await apiGetJson('/api/fonts', '读取字体失败')
    fonts.value = Array.isArray(data?.fonts) ? data.fonts : []
  } catch {
    fonts.value = []
  }
}

function switchPage(targetId) {
  if (targetId && targetId !== pageId.value) {
    router.push(`/review/${sessionId.value}/${encodeURIComponent(targetId)}`)
  }
}

function prevPage() {
  const idx = currentIndex.value
  if (idx > 0) switchPage(images.value[idx - 1].stored_name)
}
function nextPage() {
  const idx = currentIndex.value
  if (idx >= 0 && idx < images.value.length - 1) switchPage(images.value[idx + 1].stored_name)
}

// ---- 命令提交 ----
async function submitCommands(commands, { reload = true } = {}) {
  if (!document.value || !commands.length) return null
  try {
    const res = await apiFetch(toApiUrl(`/api/pages/${sessionId.value}/${pageId.value}/commands`), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        config: project.value?.config || {},
        commands,
        expected_page_revision: document.value.revision,
      }),
    })
    if (res.status === 409) {
      const detail = await res.json().catch(() => ({}))
      toast(detail?.detail?.message || '页面已被其他操作更新，已刷新。', 'warn')
      await loadDocument()
      return null
    }
    if (!res.ok) {
      throw new Error(await readApiError(res, '命令执行失败'))
    }
    const view = await res.json()
    adoptResponse(view)
    savedAt.value = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
    if (reload) await loadDocument()
    return view
  } catch (err) {
    toastError(err)
    return null
  }
}

// ---- 文本框状态 ----
const selectedRegionId = ref('')
const openRegionIds = ref(new Set())
const searchQuery = ref('')
const filter = ref('all') // all | attention | manual | disabled

const filteredRegions = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return regions.value.filter((r, index) => {
    const needle = `${r.id} ${r.source_text || ''} ${resolveRegionTranslation(r)} ${index + 1}`.toLowerCase()
    if (q && !needle.includes(q)) return false
    if (filter.value === 'attention' && !needsAttention(r)) return false
    if (filter.value === 'manual' && !isManual(r)) return false
    if (filter.value === 'disabled' && !isDisabled(r)) return false
    return true
  })
})

const attentionCount = computed(() => regions.value.filter(needsAttention).length)
const manualCount = computed(() => regions.value.filter(isManual).length)
const disabledCount = computed(() => regions.value.filter(isDisabled).length)

function needsAttention(r) {
  if (String(r.recognition_status || 'ready') !== 'ready') return true
  if (String(r.translation_status || '') === 'failed') return true
  const src = String(r.source_text || '').length
  const dst = resolveRegionTranslation(r).length
  if (src > 0 && dst > src * 1.8) return true
  return false
}
function isManual(r) {
  return Boolean(r.is_manual || r.manual || String(r.id || '').startsWith('manual'))
}
function isDisabled(r) {
  return Boolean(r.disabled)
}
function regionStatusLabel(r) {
  if (isDisabled(r)) return '已停用'
  const dir = String(r.direction || 'horizontal') === 'vertical' ? '纵排' : '横排'
  return `已启用 · ${dir}`
}
function regionFontLabel(r) {
  const key = r.font_key || r.font_family || ''
  const hit = fonts.value.find((f) => f.id === key || f.name === key)
  return hit ? hit.label : (key || '默认字体')
}
function regionSrcText(r) {
  return String(r.source_text || '').trim()
}
function resolveRegionTranslation(r) {
  // 后端 translation 字段是 {machine, edited, resolved} 字典，取值 resolved > edited > machine；
  // 兼容历史字符串形状与 machine_translation 兜底，统一 trim。
  const pick = (v) => {
    if (v && typeof v === 'object') return String(v.resolved || v.edited || v.machine || '').trim()
    return String(v || '').trim()
  }
  return pick(r.translation) || pick(r.machine_translation)
}
function regionDstText(r) {
  return resolveRegionTranslation(r)
}

function isOpen(r) {
  return openRegionIds.value.has(r.id)
}
function toggleOpen(r, force) {
  const next = new Set(openRegionIds.value)
  if (force === true) next.add(r.id)
  else if (force === false) next.delete(r.id)
  else if (next.has(r.id)) next.delete(r.id)
  else next.add(r.id)
  openRegionIds.value = next
}

function selectRegion(r, { open = true } = {}) {
  selectedRegionId.value = r.id
  if (open) toggleOpen(r, true)
}

function locateRegion(r) {
  selectRegion(r)
  // 自由画布：pan 使目标框移动到视口中心（scrollIntoView 对 transform 舞台无效）
  const el = anyCanvasEl()
  if (el && r?.bbox) {
    const rect = el.getBoundingClientRect()
    const [x1, y1, x2, y2] = r.bbox
    view.panX = rect.width / 2 - ((x1 + x2) / 2) * view.zoom
    view.panY = rect.height / 2 - ((y1 + y2) / 2) * view.zoom
  }
  nextTick(() => {
    const box = window.document.querySelector(`[data-canvas-region="${r.id}"]`)
    if (box) {
      box.classList.remove('flash')
      void box.offsetWidth
      box.classList.add('flash')
    }
  })
}

// ---- 画布框 ----
function regionBoxStyle(r) {
  const [x1, y1, x2, y2] = r.bbox || [0, 0, 0, 0]
  const w = imgW.value
  const h = imgH.value
  return {
    left: `${(x1 / w) * 100}%`,
    top: `${(y1 / h) * 100}%`,
    width: `${((x2 - x1) / w) * 100}%`,
    height: `${((y2 - y1) / h) * 100}%`,
    fontSize: '11px',
  }
}

function regionBoxText(r) {
  return resolveRegionTranslation(r) || r.source_text || ''
}

// 拖动/缩放
const dragState = ref(null)

function onRegionPointerDown(event, r) {
  if (dragState.value || !r?.bbox) return
  event.preventDefault()
  event.stopPropagation()
  selectRegion(r, { open: true })
  const stage = event.currentTarget.closest('.pane-stage')
  const rect = stage.getBoundingClientRect()
  const [x1, y1, x2, y2] = r.bbox
  const sx = rect.width / imgW.value
  const sy = rect.height / imgH.value
  const mode = event.target.closest('.handle') ? 'resize' : 'move'
  dragState.value = {
    mode,
    id: r.id,
    startX: event.clientX,
    startY: event.clientY,
    bbox: [...r.bbox],
    sx, sy,
    moved: false,
  }
  window.addEventListener('pointermove', onDragMove)
  window.addEventListener('pointerup', onDragUp, { once: true })
}

function onDragMove(event) {
  const d = dragState.value
  if (!d) return
  const dx = Math.round((event.clientX - d.startX) / d.sx)
  const dy = Math.round((event.clientY - d.startY) / d.sy)
  if (Math.abs(dx) + Math.abs(dy) > 2) d.moved = true
  const [x1, y1, x2, y2] = d.bbox
  if (d.mode === 'move') {
    dragState.value.preview = [x1 + dx, y1 + dy, x2 + dx, y2 + dy]
  } else {
    dragState.value.preview = [x1, y1, Math.max(x1 + 12, x2 + dx), Math.max(y1 + 12, y2 + dy)]
  }
}

async function onDragUp() {
  window.removeEventListener('pointermove', onDragMove)
  const d = dragState.value
  dragState.value = null
  if (!d?.moved || !d.preview) return
  const [px1, py1, px2, py2] = d.preview
  if (px2 - px1 < 20 || py2 - py1 < 12) return
  await submitCommands([{ type: 'update_region_bbox', region_id: d.id, bbox: [px1, py1, px2, py2] }])
}

// 手动添加框
const addingMode = ref(false)
const addDrag = ref(null)

function onFramePointerDown(event) {
  const d = document.value
  if (!addingMode.value || !d) return
  const rect = event.currentTarget.getBoundingClientRect()
  const sx = rect.width / imgW.value
  const sy = rect.height / imgH.value
  const x = Math.round((event.clientX - rect.left) / sx)
  const y = Math.round((event.clientY - rect.top) / sy)
  addDrag.value = { start: [x, y], current: [x, y] }
  window.addEventListener('pointermove', onAddMove)
  window.addEventListener('pointerup', onAddUp, { once: true })
}

function onAddMove(event) {
  const d = addDrag.value
  if (!d) return
  const rect = window.document.querySelector('.pane-stage')?.getBoundingClientRect()
  if (!rect) return
  d.current = [
    Math.round((event.clientX - rect.left) / (rect.width / imgW.value)),
    Math.round((event.clientY - rect.top) / (rect.height / imgH.value)),
  ]
}

async function onAddUp() {
  window.removeEventListener('pointermove', onAddMove)
  const d = addDrag.value
  addDrag.value = null
  if (!d) return
  const [x1, y1] = d.start
  const [x2, y2] = d.current
  const bbox = [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)]
  if (bbox[2] - bbox[0] < 20 || bbox[3] - bbox[1] < 12) return
  const res = await submitCommands([{ type: 'create_region', bbox }])
  if (res?.created_region?.[0]?.id || res?.created_region_payload?.id) {
    const rid = res.created_region_payload?.id || res.created_region?.[0]?.id
    addingMode.value = false
    await loadDocument()
    nextTick(() => {
      const r = regions.value.find((item) => item.id === rid)
      if (r) selectRegion(r)
    })
  }
}

function addDragStyle() {
  const d = addDrag.value
  if (!d || !document.value) return {}
  const w = imgW.value
  const h = imgH.value
  const [x1, y1] = d.start
  const [x2, y2] = d.current
  return {
    left: `${(Math.min(x1, x2) / w) * 100}%`,
    top: `${(Math.min(y1, y2) / h) * 100}%`,
    width: `${(Math.abs(x2 - x1) / w) * 100}%`,
    height: `${(Math.abs(y2 - y1) / h) * 100}%`,
  }
}

// ---- 卡片编辑 ----
const draftCache = ref({}) // regionId → {translation, fontKey, fontSize, rotation, strokeWidth, letterSpacing, lineSpacing, fgColor, bgColor, preserveBackground}

function draftFor(r) {
  if (!draftCache.value[r.id]) {
    const adv = r.advanced_style || r.advanced || {}
    draftCache.value[r.id] = {
      translation: regionDstText(r),
      fontKey: r.font_key || r.font_family || '',
      fontSize: Math.max(8, Math.round(Number(r.font_size || 12))),
      bold: Boolean(r.resolved_style?.bold || r.auto_style?.bold),
      italic: Boolean(r.resolved_style?.italic || r.auto_style?.italic),
      rotation: adv.rotation ?? 0,
      strokeWidth: adv.stroke_width ?? 1,
      letterSpacing: adv.letter_spacing ?? 1,
      lineSpacing: adv.line_spacing ?? 1.2,
      fgColor: adv.fg_color ? hexFromTriplet(adv.fg_color) : '#1A1712',
      bgColor: adv.bg_color ? hexFromTriplet(adv.bg_color) : '#FFFFFF',
      preserveBackground: Boolean(adv.preserve_background),
      enabled: !isDisabled(r),
      direction: String(r.direction || 'horizontal'),
      keepOriginal: Boolean(r.override_skip),
    }
  }
  return draftCache.value[r.id]
}

function hexFromTriplet(t) {
  if (!Array.isArray(t) || t.length < 3) return '#1A1712'
  return `#${t.slice(0, 3).map((v) => Math.max(0, Math.min(255, Math.round(Number(v) || 0))).toString(16).padStart(2, '0')).join('')}`
}
function tripletFromHex(hex) {
  const m = String(hex || '').replace('#', '')
  if (m.length !== 6) return [21, 26, 52]
  return [parseInt(m.slice(0, 2), 16), parseInt(m.slice(2, 4), 16), parseInt(m.slice(4, 6), 16)]
}

async function applyTranslation(r) {
  const d = draftFor(r)
  await submitCommands([{ type: 'update_translation', region_id: r.id, text: d.translation }])
}

async function applyToggleEnabled(r) {
  const d = draftFor(r)
  const enabled = !d.enabled
  d.enabled = enabled
  await submitCommands([{ type: enabled ? 'restore_region' : 'disable_region', region_id: r.id }])
}

async function applyDirection(r, direction) {
  const d = draftFor(r)
  d.direction = direction
  await submitCommands([{ type: 'update_text_direction', region_id: r.id, direction }])
}

async function applyKeepOriginal(r) {
  const d = draftFor(r)
  await submitCommands([{ type: 'set_keep_original', region_id: r.id, enabled: Boolean(d.keepOriginal) }])
}

async function applyFont(r) {
  const d = draftFor(r)
  await submitCommands([{ type: 'update_region_font', region_id: r.id, font_key: d.fontKey }])
}

async function applyFontSize(r) {
  const d = draftFor(r)
  const size = Math.max(8, Math.round(Number(d.fontSize) || 12))
  d.fontSize = size
  await submitCommands([{ type: 'update_font_size', region_id: r.id, font_size: size }])
}

async function applyFontStyle(r) {
  const d = draftFor(r)
  const style = [d.bold ? 'bold' : '', d.italic ? 'italic' : ''].filter(Boolean).join(' ') || ''
  await submitCommands([{ type: 'update_font_style', region_id: r.id, style }])
}

async function applyAdvancedStyle(r) {
  const d = draftFor(r)
  await submitCommands([{
    type: 'update_region_style',
    region_id: r.id,
    rotation: Number(d.rotation || 0),
    stroke_width: Number(d.strokeWidth ?? 1),
    letter_spacing: Number(d.letterSpacing ?? 1),
    line_spacing: Number(d.lineSpacing ?? 1.2),
    fg_color: tripletFromHex(d.fgColor),
    bg_color: tripletFromHex(d.bgColor),
    preserve_background: Boolean(d.preserveBackground),
  }])
}

async function deleteRegion(r) {
  const res = await submitCommands([{ type: 'delete_manual_region', region_id: r.id }], { reload: false })
  openRegionIds.value.delete(r.id)
  selectedRegionId.value = ''
  await loadDocument()
}

// 样式复制/粘贴
const styleClipboard = ref(null)
function copyStyle(r) {
  const d = draftFor(r)
  styleClipboard.value = {
    fontKey: d.fontKey,
    fontSize: d.fontSize,
    bold: d.bold,
    italic: d.italic,
    direction: d.direction,
    rotation: d.rotation,
    strokeWidth: d.strokeWidth,
    letterSpacing: d.letterSpacing,
    lineSpacing: d.lineSpacing,
    fgColor: d.fgColor,
    bgColor: d.bgColor,
    preserveBackground: d.preserveBackground,
  }
  toast('样式已复制', 'ok', 1500)
}
async function pasteStyle(r) {
  const s = styleClipboard.value
  if (!s) return
  const d = draftFor(r)
  Object.assign(d, s)
  await submitCommands([
    { type: 'update_text_direction', region_id: r.id, direction: s.direction },
    { type: 'update_region_font', region_id: r.id, font_key: s.fontKey },
    { type: 'update_font_size', region_id: r.id, font_size: s.fontSize },
    { type: 'update_font_style', region_id: r.id, style: [s.bold ? 'bold' : '', s.italic ? 'italic' : ''].filter(Boolean).join(' ') || '' },
    {
      type: 'update_region_style',
      region_id: r.id,
      rotation: s.rotation,
      stroke_width: s.strokeWidth,
      letter_spacing: s.letterSpacing,
      line_spacing: s.lineSpacing,
      fg_color: tripletFromHex(s.fgColor),
      bg_color: tripletFromHex(s.bgColor),
      preserve_background: s.preserveBackground,
    },
  ])
}

// ---- 任务 ----
async function runTranslatePage() {
  try {
    taskEvents.start(sessionId.value, 'translate-page', project.value?.config || {}, pageId.value)
  } catch (err) {
    toastError(err)
  }
}
async function runRerender() {
  try {
    taskEvents.start(sessionId.value, 'rerender', project.value?.config || {})
  } catch (err) {
    toastError(err)
  }
}
async function cancelTask() {
  const taskId = taskState.value.activeTaskId
  if (!taskId) return
  try {
    await apiPostJson(`/api/tasks/${taskId}/cancel`, {}, '取消失败')
  } catch (err) {
    toastError(err)
  }
}

watch(() => taskState.value.eventName, (name) => {
  if (name === 'completed') {
    toast('任务完成', 'ok')
    loadDocument()
  } else if (name === 'error' || name === 'failed') {
    toast(taskState.value.statusMessage || '任务失败', 'error')
    loadDocument()
  }
})

// ---- 导出 ----
const exportMenuOpen = ref(false)
function exportResult() {
  const url = project.value?.download_url
  if (!url) { toast('还没有可导出的结果。', 'warn'); return }
  window.open(withCacheBust(toApiUrl(url)), '_blank')
  exportMenuOpen.value = false
}
function exportBlank() {
  window.open(withCacheBust(toApiUrl(`/api/download/${sessionId.value}/blank`)), '_blank')
  exportMenuOpen.value = false
}

// ---- 生命周期 ----
onMounted(async () => {
  await ensureProject()
  await Promise.all([loadDocument(), loadFonts()])
})
watch(pageId, async () => {
  selectedRegionId.value = ''
  openRegionIds.value = new Set()
  draftCache.value = {}
  await loadDocument()
})
onUnmounted(() => {
  taskEvents.disconnect()
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointermove', onAddMove)
})
</script>

<template>
  <div class="app">
    <header class="topbar">
      <a class="topbar-brand" href="#/">
        <span class="brand-mark">S</span>
        <span class="brand-word">Solar<small>MANGA&nbsp;STUDIO</small></span>
      </a>
      <div class="topbar-divider"></div>
      <button class="btn btn-ghost btn-sm" type="button" @click="router.push(`/pages/${sessionId}`)">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
        页面列表
      </button>
      <div class="topbar-title">
        <strong>{{ projectTitle }}</strong>
        <span>第 {{ pageNumber }} / {{ images.length }} 页</span>
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
        <span v-else-if="savedAt" class="saved-hint">已保存 {{ savedAt }}</span>
        <a class="btn btn-ghost" href="#/glossary" @click.prevent="router.push(`/glossary/${sessionId}`)">专有名词库</a>
        <button v-if="taskBusy" class="btn btn-ghost" type="button" @click="cancelTask">取消任务</button>
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
        <button class="btn btn-primary" type="button" :disabled="taskBusy" @click="runRerender">重新嵌字</button>
        <ThemeToggle />
        <a class="icon-btn" href="#/settings" data-tip="设置" aria-label="设置">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/></svg>
        </a>
      </div>
    </header>

    <div class="review">
      <aside class="page-rail">
        <div class="page-rail-head">
          <span class="kicker">页面</span>
          <strong>{{ images.length }}</strong>
        </div>
        <div class="page-rail-list">
          <a
            v-for="(img, index) in images"
            :key="img.stored_name"
            class="rail-item"
            :class="{ active: img.stored_name === pageId }"
            href="javascript:void(0)"
            :title="img.name"
            @click="switchPage(img.stored_name)"
          >
            <img :src="withImagePreviewSize(toApiUrl(img.url || `/api/pages/${sessionId}/${img.stored_name}/source-image`), 120)" :alt="`第 ${index + 1} 页`" loading="lazy" />
            <span class="rail-no">{{ index + 1 }}</span>
            <span class="rail-state" :class="img.artifact_state?.capabilities?.can_export ? 's-ok' : 's-idle'"></span>
          </a>
        </div>
      </aside>

      <section class="stage">
        <div class="stage-bar">
          <div class="seg" role="group" aria-label="对比视图">
            <button :class="{ active: panes.frame }" @click="panes.frame = !panes.frame">
              <svg class="pane-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><rect x="7" y="7" width="6" height="8" rx="1"/></svg>
              框页
            </button>
            <button :class="{ active: panes.final }" @click="panes.final = !panes.final">
              <svg class="pane-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="m9 12 2 2 4-4"/></svg>
              嵌后
            </button>
            <button :class="{ active: panes.src }" @click="panes.src = !panes.src">
              <svg class="pane-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.5-3.5a2 2 0 0 0-3 0L6 20"/></svg>
              原图
            </button>
            <button :class="{ active: panes.blank }" @click="panes.blank = !panes.blank">
              <svg class="pane-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 15c4-1 8 1 10 3 2.5 2.5 6 1 8-1"/></svg>
              空页
            </button>
          </div>

          <div class="spacer"></div>

          <div class="stage-bar-group">
            <button class="icon-btn" data-tip="上一页" aria-label="上一页" :disabled="pageNumber <= 1" @click="prevPage">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
            </button>
            <span class="num" style="font-size: var(--fs-sub); color: var(--text-2);">{{ pageNumber }} / {{ images.length }}</span>
            <button class="icon-btn" data-tip="下一页" aria-label="下一页" :disabled="pageNumber >= images.length" @click="nextPage">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>
            </button>
          </div>

          <div class="topbar-divider"></div>

          <div class="stage-bar-group">
            <button class="btn btn-ghost btn-sm" :class="{ 'is-on': addingMode }" type="button" @click="addingMode = !addingMode">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><rect x="3" y="3" width="18" height="18" rx="2" stroke-dasharray="4 3"/><path d="M12 8v8M8 12h8"/></svg>
              {{ addingMode ? '拖拽绘制新框' : '手动添加框' }}
            </button>
            <a class="btn btn-ghost btn-sm" href="javascript:void(0)" @click="router.push(`/erase/${sessionId}/${pageId}`)">
              擦除
              <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m4 6 4 4 4-4"/></svg>
            </a>
            <button class="btn btn-ghost btn-sm" type="button" :disabled="taskBusy" @click="runTranslatePage">重新翻译</button>
          </div>
        </div>

        <div class="pane-strip" :data-count="paneCount">
          <div v-if="panes.frame" class="pane">
            <span class="pane-label"><i></i>框页 · 可编辑</span>
            <div
              ref="frameCanvas"
              class="pane-canvas"
              :class="{ 'is-panning': panning }"
              @wheel.prevent="onCanvasWheel"
              @pointerdown="onCanvasPanDown"
            >
              <div class="pane-stage" :style="stageStyle" @pointerdown="onFramePointerDown">
                <img
                  :src="imageUrl('base-image')"
                  alt="框页"
                  draggable="false"
                  @load="onFrameImgLoad"
                />
                <template v-if="document">
                  <div
                    v-for="r in document.regions"
                    :key="r.id"
                    class="region-box"
                    :class="{ 'is-active': r.id === selectedRegionId, 'is-disabled': isDisabled(r) }"
                    :data-canvas-region="r.id"
                    :style="regionBoxStyle(r)"
                    @pointerdown="onRegionPointerDown($event, r)"
                  >
                    <span class="region-no">{{ regions.indexOf(r) + 1 }}</span>
                    <span v-if="r.id === selectedRegionId" class="box-text">{{ regionBoxText(r) }}</span>
                    <span v-if="r.id === selectedRegionId" class="handle tl"></span>
                    <span v-if="r.id === selectedRegionId" class="handle tr"></span>
                    <span v-if="r.id === selectedRegionId" class="handle bl"></span>
                    <span v-if="r.id === selectedRegionId" class="handle br"></span>
                  </div>
                  <div v-if="addDrag" class="region-box is-drawing" :style="addDragStyle()"></div>
                  <div
                    v-if="selectedRegionId && regions.find((r) => r.id === selectedRegionId)"
                    class="region-pop"
                    :style="regionBoxStyle(regions.find((r) => r.id === selectedRegionId))"
                  >
                    <span class="pop-coord num">x{{ regions.find((r) => r.id === selectedRegionId).bbox?.[0] }} · {{ regions.find((r) => r.id === selectedRegionId).bbox?.[1] }}</span>
                    <span class="pop-divider"></span>
                    <button class="icon-btn" type="button" data-tip="定位到卡片" aria-label="定位到卡片" @click="locateRegion(regions.find((r) => r.id === selectedRegionId))">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/><circle cx="12" cy="12" r="7"/></svg>
                    </button>
                    <button class="icon-btn" type="button" data-tip="复制全部样式" aria-label="复制全部样式" @click="copyStyle(regions.find((r) => r.id === selectedRegionId))">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22a10 10 0 1 1 10-10"/><path d="M12 12 7 7"/><path d="m17 16 4-4-4-4"/><path d="M21 12H9"/></svg>
                    </button>
                    <button class="icon-btn" type="button" data-tip="纵排 / 横排" aria-label="纵横排" @click="applyDirection(regions.find((r) => r.id === selectedRegionId), regions.find((r) => r.id === selectedRegionId).direction === 'vertical' ? 'horizontal' : 'vertical')">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M7 4v13M7 17l-3-3M7 17l3-3"/><path d="M17 20V7M17 7l-3 3M17 7l3 3"/></svg>
                    </button>
                    <button class="icon-btn" type="button" data-tip="删除此框" aria-label="删除此框" @click="deleteRegion(regions.find((r) => r.id === selectedRegionId))">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                  </div>
                </template>
              </div>
            </div>
            <div class="canvas-hud">
              <button type="button" data-tip="适合窗口" @click="zoomFit">适合</button>
              <button type="button" data-tip="适应宽度" @click="zoomWidth">适宽</button>
              <span class="pop-divider" style="width:1px;height:16px;background:var(--border-strong);margin:0 3px;"></span>
              <button type="button" aria-label="缩小" @click="zoomOut">−</button>
              <span class="hud-zoom" data-tip="回到 100%" role="button" @click="zoomReset">{{ Math.round(view.zoom * 100) }}%</span>
              <button type="button" aria-label="放大" @click="zoomIn">＋</button>
            </div>
          </div>

          <div v-if="panes.final" class="pane">
            <span class="pane-label is-final"><i></i>嵌后 · 联动</span>
            <div class="pane-canvas" :class="{ 'is-panning': panning }" @wheel.prevent="onCanvasWheel" @pointerdown="onCanvasPanDown">
              <div class="pane-stage" :style="stageStyle">
                <img :src="imageUrl('translated-image')" alt="嵌字结果" loading="lazy" />
              </div>
            </div>
          </div>

          <div v-if="panes.src" class="pane">
            <span class="pane-label is-src"><i></i>原图</span>
            <div class="pane-canvas" :class="{ 'is-panning': panning }" @wheel.prevent="onCanvasWheel" @pointerdown="onCanvasPanDown">
              <div class="pane-stage" :style="stageStyle">
                <img :src="imageUrl('source-image')" alt="原图" loading="lazy" />
              </div>
            </div>
          </div>

          <div v-if="panes.blank" class="pane">
            <span class="pane-label is-blank"><i></i>空页</span>
            <div class="pane-canvas" :class="{ 'is-panning': panning }" @wheel.prevent="onCanvasWheel" @pointerdown="onCanvasPanDown">
              <div class="pane-stage" :style="stageStyle">
                <img :src="imageUrl('base-image', 1024)" alt="空页" loading="lazy" />
              </div>
            </div>
          </div>

          <div v-if="!paneCount" class="pane pane-empty">至少保留一个视图</div>
          <div v-if="docLoading" class="pane-doc-loading">加载页面文档…</div>
          <div v-else-if="!document" class="pane-doc-loading">该页还没有文档，先在上一步执行识别/翻译。</div>
        </div>
      </section>

      <aside class="region-panel">
        <div class="region-panel-head">
          <div class="region-panel-top">
            <span class="kicker">文本框</span>
            <strong>{{ openRegionIds.size }} / {{ regionCount }}</strong>
            <div class="spacer"></div>
            <button class="icon-btn" type="button" data-tip="上一个文本框" aria-label="上一个文本框" @click="stepRegion(-1)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m18 15-6-6-6 6"/></svg></button>
            <button class="icon-btn" type="button" data-tip="定位当前框" aria-label="定位当前框" @click="selectedRegion ? locateRegion(selectedRegion) : null"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/><circle cx="12" cy="12" r="7"/></svg></button>
            <button class="icon-btn" type="button" data-tip="下一个文本框" aria-label="下一个文本框" @click="stepRegion(1)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg></button>
          </div>
          <div class="search-box">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/></svg>
            <input v-model="searchQuery" type="search" placeholder="搜索原文、译文或编号" />
          </div>
          <div class="filter-chips">
            <button class="chip" :class="{ active: filter === 'all' }" @click="filter = 'all'">全部 {{ regionCount }}</button>
            <button class="chip" :class="{ active: filter === 'attention' }" @click="filter = 'attention'">需留意 {{ attentionCount }}</button>
            <button class="chip" :class="{ active: filter === 'manual' }" @click="filter = 'manual'">手动 {{ manualCount }}</button>
            <button class="chip" :class="{ active: filter === 'disabled' }" @click="filter = 'disabled'">已停用 {{ disabledCount }}</button>
          </div>
        </div>

        <div class="region-list">
          <article
            v-for="(r, index) in filteredRegions"
            :key="r.id"
            class="region-card"
            :class="{ 'is-open': isOpen(r), 'is-selected': r.id === selectedRegionId, 'is-disabled': isDisabled(r) }"
            @click="selectRegion(r)"
          >
            <header class="region-card-head" @click.stop="toggleOpen(r)">
              <span class="rid">#{{ index + 1 }}</span>
              <span class="tag">{{ regionFontLabel(r) }}</span>
              <span v-if="needsAttention(r)" class="tag is-warn">需留意</span>
              <span v-if="isManual(r)" class="tag">手动</span>
              <div class="spacer"></div>
              <span class="mini-state">{{ regionStatusLabel(r) }}</span>
            </header>
            <div class="region-card-texts">
              <p class="src">{{ regionSrcText(r) }}</p>
              <p class="dst">{{ regionDstText(r) }}</p>
            </div>

            <div v-if="isOpen(r)" class="region-card-body" @click.stop>
              <label class="region-edit-field">
                <span>译文</span>
                <textarea rows="3" :value="draftFor(r).translation" @input="draftFor(r).translation = $event.target.value" @blur="applyTranslation(r)"></textarea>
              </label>
              <label class="check-row">
                <input type="checkbox" :checked="draftFor(r).keepOriginal" @change="draftFor(r).keepOriginal = $event.target.checked; applyKeepOriginal(r)" />
                <span>保留原文（不嵌字）</span>
              </label>

              <div class="toggle-chips">
                <button class="toggle-chip" :class="{ active: draftFor(r).enabled }" type="button" @click="applyToggleEnabled(r)">{{ draftFor(r).enabled ? '已启用' : '已停用' }}</button>
                <button class="toggle-chip" :class="{ active: draftFor(r).direction === 'horizontal' }" type="button" @click="applyDirection(r, 'horizontal')">横排</button>
                <button class="toggle-chip" :class="{ active: draftFor(r).direction === 'vertical' }" type="button" @click="applyDirection(r, 'vertical')">纵排</button>
              </div>

              <div class="region-edit-grid">
                <label class="field">
                  <span>字体</span>
                  <select :value="draftFor(r).fontKey" @change="draftFor(r).fontKey = $event.target.value; applyFont(r)">
                    <option value="">默认字体</option>
                    <option v-for="f in fonts" :key="f.id" :value="f.id">{{ f.label }}</option>
                  </select>
                </label>
                <div class="field">
                  <span>字号</span>
                  <div class="stepper">
                    <button type="button" @click="draftFor(r).fontSize = Math.max(8, draftFor(r).fontSize - 1)">−</button>
                    <input
                      type="text"
                      inputmode="numeric"
                      :value="draftFor(r).fontSize"
                      @change="draftFor(r).fontSize = Number($event.target.value) || 12; applyFontSize(r)"
                    />
                    <button type="button" @click="draftFor(r).fontSize = Math.min(200, draftFor(r).fontSize + 1)">＋</button>
                  </div>
                </div>
              </div>

              <div class="font-style-row">
                <button class="toggle-chip" :class="{ active: draftFor(r).bold }" type="button" @click="draftFor(r).bold = !draftFor(r).bold; applyFontStyle(r)">粗体</button>
                <button class="toggle-chip" :class="{ active: draftFor(r).italic }" type="button" @click="draftFor(r).italic = !draftFor(r).italic; applyFontStyle(r)">斜体</button>
              </div>

              <details class="adv-block">
                <summary>高级样式 <span style="font-size: var(--fs-micro); color: var(--text-3); font-weight: 400;">旋转 · 描边 · 字距 · 行距 · 颜色</span></summary>
                <div class="adv-body">
                  <div class="adv-grid">
                    <label class="field"><span>旋转 °</span><input type="number" :value="draftFor(r).rotation" min="-180" max="180" @change="draftFor(r).rotation = Number($event.target.value) || 0; applyAdvancedStyle(r)" /></label>
                    <label class="field"><span>描边强度</span><input type="number" :value="draftFor(r).strokeWidth" step="0.05" @change="draftFor(r).strokeWidth = Number($event.target.value) || 0; applyAdvancedStyle(r)" /></label>
                    <label class="field"><span>字距</span><input type="number" :value="draftFor(r).letterSpacing" step="0.05" @change="draftFor(r).letterSpacing = Number($event.target.value) || 0; applyAdvancedStyle(r)" /></label>
                    <label class="field"><span>行距</span><input type="number" :value="draftFor(r).lineSpacing" step="0.05" @change="draftFor(r).lineSpacing = Number($event.target.value) || 0; applyAdvancedStyle(r)" /></label>
                  </div>
                  <div class="field">
                    <span>{{ '文字色' }}</span>
                    <div class="swatches">
                      <button class="swatch" :class="{ active: draftFor(r).fgColor === '#1A1712' }" style="background:#1A1712" type="button" aria-label="墨色" @click="draftFor(r).fgColor = '#1A1712'; applyAdvancedStyle(r)"></button>
                      <button class="swatch" :class="{ active: draftFor(r).fgColor === '#FFFFFF' }" style="background:#FFFFFF" type="button" aria-label="白" @click="draftFor(r).fgColor = '#FFFFFF'; applyAdvancedStyle(r)"></button>
                      <button class="swatch" :class="{ active: draftFor(r).fgColor === '#E5534B' }" style="background:#E5534B" type="button" aria-label="红" @click="draftFor(r).fgColor = '#E5534B'; applyAdvancedStyle(r)"></button>
                      <button class="swatch" :class="{ active: draftFor(r).fgColor === '#2F6FED' }" style="background:#2F6FED" type="button" aria-label="蓝" @click="draftFor(r).fgColor = '#2F6FED'; applyAdvancedStyle(r)"></button>
                      <input type="color" :value="draftFor(r).fgColor" aria-label="自定义文字色" style="width:24px;height:24px;padding:0;border:1px solid var(--border-strong);border-radius:5px;background:none;" @change="draftFor(r).fgColor = $event.target.value; applyAdvancedStyle(r)" />
                    </div>
                  </div>
                  <div class="field">
                    <span>{{ '底 / 描边色' }}</span>
                    <div class="swatches">
                      <button class="swatch" :class="{ active: draftFor(r).bgColor === '#1A1712' }" style="background:#1A1712" type="button" aria-label="墨色" @click="draftFor(r).bgColor = '#1A1712'; applyAdvancedStyle(r)"></button>
                      <button class="swatch" :class="{ active: draftFor(r).bgColor === '#FFFFFF' }" style="background:#FFFFFF" type="button" aria-label="白" @click="draftFor(r).bgColor = '#FFFFFF'; applyAdvancedStyle(r)"></button>
                      <button class="swatch" :class="{ active: draftFor(r).bgColor === '#E8A33D' }" style="background:#E8A33D" type="button" aria-label="琥珀" @click="draftFor(r).bgColor = '#E8A33D'; applyAdvancedStyle(r)"></button>
                      <button class="swatch" :class="{ active: draftFor(r).bgColor === '#1E9E6A' }" style="background:#1E9E6A" type="button" aria-label="绿" @click="draftFor(r).bgColor = '#1E9E6A'; applyAdvancedStyle(r)"></button>
                      <input type="color" :value="draftFor(r).bgColor" aria-label="自定义底色" style="width:24px;height:24px;padding:0;border:1px solid var(--border-strong);border-radius:5px;background:none;" @change="draftFor(r).bgColor = $event.target.value; applyAdvancedStyle(r)" />
                    </div>
                  </div>
                  <label class="check-row">
                    <input type="checkbox" :checked="draftFor(r).preserveBackground" @change="draftFor(r).preserveBackground = $event.target.checked; applyAdvancedStyle(r)" />
                    <span>保留底图（不重绘气泡底色）</span>
                  </label>
                </div>
              </details>

              <div class="region-card-actions">
                <button class="icon-btn" type="button" data-tip="复制全部样式" aria-label="复制全部样式" @click="copyStyle(r)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22a10 10 0 1 1 10-10"/><path d="M12 12 7 7"/><path d="m17 16 4-4-4-4"/><path d="M21 12H9"/></svg></button>
                <button class="icon-btn" type="button" data-tip="粘贴全部样式" aria-label="粘贴全部样式" @click="pasteStyle(r)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="m9 14 2 2 4-4"/></svg></button>
                <div class="spacer"></div>
                <button class="icon-btn" type="button" data-tip="删除此框" aria-label="删除此框" @click="deleteRegion(r)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
              </div>
            </div>
          </article>

          <div v-if="!filteredRegions.length" class="region-empty">
            <p>{{ docLoading ? '加载中…' : '没有匹配的文本框。' }}</p>
          </div>
        </div>
      </aside>
    </div>

    <div class="toast-stack">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="`is-${t.kind}`" @click="dismiss(t.id)">{{ t.message }}</div>
    </div>
  </div>
</template>

<script>
export default {
  computed: {
    selectedRegion() {
      if (!this.document) return null
      return this.document.regions.find((r) => r.id === this.selectedRegionId) || null
    },
  },
  methods: {
    stepRegion(delta) {
      const list = this.filteredRegions || []
      if (!list.length) return
      const current = list.findIndex((r) => r.id === this.selectedRegionId)
      const next = current < 0 ? 0 : (current + delta + list.length) % list.length
      const r = list[next]
      this.selectedRegionId = r.id
      this.toggleOpen(r, true)
      this.locateRegion(r)
    },
  },
}
</script>