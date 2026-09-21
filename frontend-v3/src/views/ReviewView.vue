<script setup>
import { computed, nextTick, onMounted, onUnmounted, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import {
  apiGetJson, toApiUrl,
  withCacheBust, withImagePreviewSize,
} from '../api/client.js'
import { useProject } from '../composables/useProject.js'
import { usePageEditor } from '../composables/usePageEditor.js'
import {
  buildStablePageImageUrl,
  regionIssueReason,
  regionNeedsAttention,
  resolveRegionText,
} from '../state/review-document.js'
import { hasPartialTranslation } from '../state/page-status.js'
import {
  nextIssueRegion,
  normalizeReviewLocation,
  normalizeReviewRegionFilter,
  reviewLocationStorageKey,
  selectRegionRange,
} from '../state/review-workspace-state.js'
import {
  clampCanvasBBox,
  resizeCanvasBBox,
  selectCanvasRegions,
} from '../state/review-canvas-geometry.js'
import {
  buildReviewStyleCommands,
  normalizeReviewDirection,
  resolveLayoutDirectionOverride,
  resolveReviewDirection,
  reviewStyleSnapshot,
  sourceCropStyle,
} from '../state/review-style.js'
import { buildBatchTranslationConfirmation, getProjectTranslateAction } from '../state/workflow-state.js'
import { useTaskEvents } from '../composables/useTaskEvents.js'
import { toasts, dismiss, toast, toastError } from '../composables/useToast.js'
import ThemeToggle from '../components/ThemeToggle.vue'
import { loadProcessingConfig } from '../api/processing-config.js'
import { rememberRecentPage } from '../state/recent-location.js'
import { useFontPreview } from '../composables/useFontPreview.js'

const route = useRoute()
const router = useRouter()
const sessionId = computed(() => String(route.params.sessionId || ''))
const pageId = computed(() => String(route.params.pageId || ''))

const { project, loadProject, adoptResponse } = useProject()
const taskEvents = useTaskEvents()
const { taskState } = taskEvents

const translatorLabelMap = {
  gemini: 'Gemini',
  'doubao-ark': '豆包 Ark',
  'openai-compatible': 'OpenAI Compatible',
}
const targetLanguageLabelMap = {
  CHS: '简体中文',
  CHT: '繁体中文',
  ENG: '英语',
  JPN: '日语',
  KOR: '韩语',
}

// ---- 基础数据 ----
const editor = usePageEditor({
  getConfig: id => project.value?.session_id === id ? project.value.config || {} : {},
  onResponse: (response, identity) => {
    if (identity.projectId === sessionId.value) adoptResponse(response)
  },
})
const { document, loading: docLoading, draftFor } = editor
const fonts = ref([])
const fontPreview = useFontPreview()
const saveStatus = computed(() => editor.pending.value ? '保存中…'
  : editor.error.value ? '保存失败 · 修改已保留'
    : editor.dirty.value ? '有未保存修改' : '修改已保存')

const images = computed(() => project.value?.images || [])
const currentIndex = computed(() => images.value.findIndex((img) => img.stored_name === pageId.value))
const pageNumber = computed(() => (currentIndex.value >= 0 ? currentIndex.value + 1 : 0))
const projectTitle = computed(() => project.value?.project?.title || '项目')
const regions = computed(() => document.value?.regions || [])
const regionCount = computed(() => regions.value.length)
const currentImage = computed(() => images.value[currentIndex.value] || null)
const currentArtifact = computed(() => currentImage.value?.artifact_state
  || project.value?.page_artifacts?.[pageId.value]
  || null)
const currentCapabilities = computed(() => currentArtifact.value?.capabilities || {})
const reviewed = computed(() => !editor.dirty.value && document.value?.canonical?.metadata?.review?.status === 'reviewed')
const pendingRenderPages = computed(() => images.value.filter((image) => {
  const caps = image?.artifact_state?.capabilities || project.value?.page_artifacts?.[image?.stored_name]?.capabilities || {}
  return Boolean(caps.final_stale || (caps.can_render && !caps.can_export))
}))
const pendingRenderCount = computed(() => pendingRenderPages.value.length)
function pageCapabilities(image) {
  return image?.artifact_state?.capabilities
    || project.value?.page_artifacts?.[image?.stored_name]?.capabilities
    || {}
}
const finalArtifactsReady = computed(() => Boolean(
  images.value.length
  && project.value?.download_url
  && images.value.every(image => {
    const caps = pageCapabilities(image)
    return Boolean(caps.can_export && !caps.final_stale)
  }),
))
const blankArtifactsReady = computed(() => Boolean(
  images.value.length && images.value.every(image => pageCapabilities(image).blank_ready),
))
const exporting = ref(false)
const finalRenderLabel = computed(() => {
  if (currentCapabilities.value.final_stale) return '成品需重新嵌字'
  if (currentCapabilities.value.can_export) return '成品已生成'
  if (currentCapabilities.value.can_render) return '尚未生成成品'
  return '成品状态待同步'
})

const preparingTask = ref(false)
const taskBusy = computed(() => {
  if (preparingTask.value) return true
  if (taskEvents.sessionId.value !== sessionId.value) return false
  if (taskEvents.busy.value) return true
  const s = taskState.value
  return Boolean(s.activeTaskId) && !['completed', 'failed', 'error', 'cancelled', 'interrupted'].includes(s.eventName)
})

const workflowStage = computed(() => String(project.value?.workflow_stage || 'idle').trim().toLowerCase())
const translatedPageCount = computed(() => images.value.filter((image) => {
  const caps = pageCapabilities(image)
  return Boolean(caps.translation_ready)
}).length)
const translatablePageCount = computed(() => images.value.filter((image) => Boolean(pageCapabilities(image).can_translate)).length)
const hasPartialTranslatedResults = computed(() => hasPartialTranslation(images.value))
const fullTranslateAction = computed(() => getProjectTranslateAction({
  workflowStage: workflowStage.value,
  hasPartialTranslatedResults: hasPartialTranslatedResults.value,
}))
const fullTranslationConfigLoading = ref(false)
const canFullTranslate = computed(() => Boolean(
  sessionId.value
  && images.value.length
  && !taskBusy.value
  && ((workflowStage.value === 'detected' && translatablePageCount.value > 0)
    || ((workflowStage.value === 'translated' || hasPartialTranslatedResults.value) && translatedPageCount.value > 0)),
))
const fullTranslateLabel = computed(() => fullTranslateAction.value === 'resume-translate' ? '继续翻译' : '重新翻译')
const pendingFullTranslateAction = ref('')
const fullTranslateConfirmationOpen = ref(false)
const fullTranslationConfig = ref(null)
const fullTranslationProjectId = ref('')
const projectTranslationRegionCount = computed(() => {
  const count = images.value.reduce((total, image) => total + Number(image?.region_count ?? image?.regionCount ?? 0), 0)
  return count || Number(project.value?.project?.region_count || 0)
})
const activeTranslatorServiceLabel = computed(() => {
  const config = fullTranslationConfig.value || project.value?.config || {}
  const provider = translatorLabelMap[config.translator] || config.translator || '翻译服务'
  if (config.translator === 'doubao-ark' && config.translator_model) return `${provider} / ${config.translator_model}`
  if (config.translator === 'openai-compatible' && config.openai_model) return `${provider} / ${config.openai_model}`
  return provider
})
const batchTranslationConfirmation = computed(() => buildBatchTranslationConfirmation({
  action: pendingFullTranslateAction.value || fullTranslateAction.value,
  pageCount: images.value.length,
  regionCount: projectTranslationRegionCount.value,
  providerLabel: activeTranslatorServiceLabel.value,
  targetLanguageLabel: targetLanguageLabelMap[String((fullTranslationConfig.value || project.value?.config || {}).target_lang || '').toUpperCase()]
    || (fullTranslationConfig.value || project.value?.config || {}).target_lang
    || '目标语言',
}))

const TERMINALS = ['completed', 'failed', 'error', 'cancelled', 'interrupted']

// ---- 图片层 ----
const panes = ref({ frame: true, final: true, src: false, blank: false })
const paneCount = computed(() => Object.values(panes.value).filter(Boolean).length)
const previewTypography = ref(true)

function togglePane(key) {
  if (!Object.hasOwn(panes.value, key)) return
  if (panes.value[key] && paneCount.value <= 1) {
    toast('至少保留一个画布视图。', 'warn', 1800)
    return
  }
  panes.value = { ...panes.value, [key]: !panes.value[key] }
}

// The right editor panel is useful at different widths and can be hidden for
// a larger canvas.  Keep a minimum width so one canvas always remains usable.
const panelCollapsed = ref(false)
const panelWidth = ref(348)
const panelResize = ref(null)
const panelStyle = computed(() => panelCollapsed.value ? { display: 'none' } : { width: `${panelWidth.value}px` })

function startPanelResize(event) {
  if (panelCollapsed.value) return
  event.preventDefault()
  panelResize.value = { startX: event.clientX, startWidth: panelWidth.value }
  window.addEventListener('pointermove', onPanelResizeMove)
  window.addEventListener('pointerup', onPanelResizeUp, { once: true })
}
function onPanelResizeMove(event) {
  const state = panelResize.value
  if (!state) return
  panelWidth.value = Math.min(520, Math.max(280, Math.round(state.startWidth + state.startX - event.clientX)))
}
function onPanelResizeUp() {
  window.removeEventListener('pointermove', onPanelResizeMove)
  panelResize.value = null
}
function onPanelResizeKeydown(event) {
  if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
    event.preventDefault()
    const delta = event.key === 'ArrowLeft' ? 16 : -16
    panelWidth.value = Math.min(520, Math.max(280, panelWidth.value + delta))
  } else if (event.key === 'Home' || event.key === 'End') {
    event.preventDefault()
    panelWidth.value = event.key === 'Home' ? 280 : 520
  }
}

function imageUrl(kind, maxSide = 1280) {
  return buildStablePageImageUrl({ sessionId: sessionId.value, pageId: pageId.value, kind, maxSide,
    document: document.value, artifact: project.value?.page_artifacts?.[pageId.value],
    toApiUrl, withImagePreviewSize })
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

// 滚轮模型对齐旧版：普通滚轮 = 平移（Shift 滚轮 = 横向平移），Ctrl/⌘ + 滚轮 = 缩放
function onCanvasWheel(event) {
  if (!document.value) return
  const el = event.currentTarget
  const rect = el.getBoundingClientRect()
  if (event.ctrlKey || event.metaKey) {
    const factor = event.deltaY < 0 ? 1.12 : 1 / 1.12
    zoomAt(view.zoom * factor, event.clientX - rect.left, event.clientY - rect.top, el)
    return
  }
  userTookOver = true // 用户滚轮平移，接管视口
  const dx = event.shiftKey ? event.deltaY : event.deltaX
  const dy = event.shiftKey ? 0 : event.deltaY
  view.panX -= dx
  view.panY -= dy
}

// 空白处拖拽平移（添加框模式 / 点在框或控件上时不触发）；按住 Space 时任意位置拖平移
const panState = ref(null)
const panning = computed(() => Boolean(panState.value))
const spacePan = ref(false)
function onCanvasPanDown(event) {
  if (addingMode.value || !document.value) return
  if (!spacePan.value && event.target.closest('.region-box, .region-pop, .canvas-hud, button, a')) return
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
  window.addEventListener('keydown', onGlobalKeydown)
  window.addEventListener('keyup', onGlobalKeyup)
})
onUnmounted(() => {
  canvasResizeObs?.disconnect()
  window.removeEventListener('keydown', onGlobalKeydown)
  window.removeEventListener('keyup', onGlobalKeyup)
})

// ---- 文档加载 ----
async function loadDocument() {
  try { await editor.load(sessionId.value, pageId.value) } catch (error) { toastError(error) }
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

function switchPage(targetId, { remember = true } = {}) {
  if (targetId && targetId !== pageId.value) {
    if (remember) rememberCurrentLocationAsPrevious()
    router.push(`/review/${sessionId.value}/${encodeURIComponent(targetId)}`)
  }
}

const reviewList = ref(null)
const savedLocation = ref(normalizeReviewLocation())
function readSavedLocation(projectId = sessionId.value) {
  const key = reviewLocationStorageKey(projectId)
  if (!key) return normalizeReviewLocation()
  try {
    return normalizeReviewLocation(JSON.parse(window.localStorage.getItem(key) || '{}'))
  } catch {
    return normalizeReviewLocation()
  }
}
function writeSavedLocation(location) {
  const key = reviewLocationStorageKey(sessionId.value)
  if (!key) return
  const normalized = normalizeReviewLocation(location)
  savedLocation.value = normalized
  try { window.localStorage.setItem(key, JSON.stringify(normalized)) } catch { /* storage is optional */ }
}
function rememberCurrentLocationAsPrevious() {
  const previous = readSavedLocation()
  writeSavedLocation({
    ...previous,
    previousPageId: pageId.value,
    previousRegionId: selectedRegionId.value,
  })
}
const recentPageId = computed(() => savedLocation.value.previousPageId || '')
const hasRecentPage = computed(() => Boolean(recentPageId.value && recentPageId.value !== pageId.value
  && images.value.some((image) => image.stored_name === recentPageId.value)))
function returnToRecentPage() {
  if (!hasRecentPage.value) return
  const targetPageId = recentPageId.value
  const targetRegionId = savedLocation.value.previousRegionId
  writeSavedLocation({
    ...savedLocation.value,
    pageId: targetPageId,
    regionId: targetRegionId,
    previousPageId: pageId.value,
    previousRegionId: selectedRegionId.value,
  })
  switchPage(targetPageId, { remember: false })
}
function persistReviewLocation() {
  if (!document.value || document.value.page_id !== pageId.value) return
  const previous = readSavedLocation()
  writeSavedLocation({
    ...previous,
    pageId: pageId.value,
    regionId: selectedRegionId.value,
    filter: filter.value,
    searchQuery: searchQuery.value,
    panelCollapsed: panelCollapsed.value,
    panelWidth: panelWidth.value,
    previewTypography: previewTypography.value,
    panes: panes.value,
    listScrollTop: reviewList.value?.scrollTop ?? previous.listScrollTop,
  })
}
function restoreReviewLocation(projectId = sessionId.value) {
  const location = readSavedLocation(projectId)
  savedLocation.value = location
  panelCollapsed.value = location.panelCollapsed
  panelWidth.value = location.panelWidth
  previewTypography.value = location.previewTypography
  panes.value = { ...panes.value, ...location.panes }
  filter.value = normalizeReviewRegionFilter(location.filter)
  searchQuery.value = location.searchQuery
  nextTick(() => {
    if (reviewList.value) reviewList.value.scrollTop = location.listScrollTop
  })
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
async function submitCommands(commands, options = {}) {
  if (taskBusy.value) { toast('请等待当前处理任务结束。', 'warn'); return null }
  try { return await editor.execute(commands, options) }
  catch (error) { toastError(error); return null }
}

async function saveDraft(region, fields) {
  try { return await editor.saveDraft(region.id, fields) }
  catch (error) { toastError(error); return null }
}

// ---- 文本框状态 ----
const selectedRegionId = ref('')
const selectedRegionIds = ref(new Set())
const selectionAnchorId = ref('')
const openRegionIds = ref(new Set())
const searchQuery = ref('')
const filter = ref('all') // all | attention | manual | keep-original | untranslated | font-override | disabled

const filteredRegions = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return regions.value.filter((r) => {
    const needle = `${r.id} ${r.source_text || ''} ${resolveRegionTranslation(r)} ${r.number || r.sequence + 1}`.toLowerCase()
    if (q && !needle.includes(q)) return false
    if (filter.value === 'attention' && !needsAttention(r)) return false
    if (filter.value === 'manual' && !isManual(r)) return false
    if (filter.value === 'keep-original' && !isKeepOriginal(r)) return false
    if (filter.value === 'untranslated' && resolveRegionTranslation(r).trim()) return false
    if (filter.value === 'font-override' && !hasFontOverride(r)) return false
    if (filter.value === 'disabled' && !isDisabled(r)) return false
    return true
  })
})
const revealedRegionId = ref('')
const visibleRegions = computed(() => {
  const list = filteredRegions.value
  if (!revealedRegionId.value || list.some(region => region.id === revealedRegionId.value)) return list
  const target = regions.value.find(region => region.id === revealedRegionId.value)
  return target ? [target, ...list] : list
})
const revealedRegionIsFiltered = computed(() => Boolean(
  revealedRegionId.value
  && !filteredRegions.value.some(region => region.id === revealedRegionId.value),
))

const attentionCount = computed(() => regions.value.filter(needsAttention).length)
const manualCount = computed(() => regions.value.filter(isManual).length)
const keepOriginalCount = computed(() => regions.value.filter(isKeepOriginal).length)
const untranslatedCount = computed(() => regions.value.filter((r) => !resolveRegionTranslation(r).trim()).length)
const fontOverrideCount = computed(() => regions.value.filter(hasFontOverride).length)
const disabledCount = computed(() => regions.value.filter(isDisabled).length)

function needsAttention(r) {
  return regionNeedsAttention(r)
}
function issueReason(r) { return regionIssueReason(r) }
function isManual(r) {
  return Boolean(r?.is_manual || r?.manual || String(r?.id || '').startsWith('manual'))
}
function isDisabled(r) {
  return Boolean(r.disabled)
}
function isKeepOriginal(r) {
  return Boolean(draftFor(r).keepOriginal)
}
function hasFontOverride(r) {
  return Boolean(String(draftFor(r).fontKey || r.font_key_override || '').trim())
}
function reviewStyleConfig() {
  const config = project.value?.config || {}
  const overrides = project.value?.overrides
  return overrides ? { ...config, overrides } : config
}
function effectiveDirection(r) {
  return resolveReviewDirection(r, reviewStyleConfig(), draftFor(r))
}
function toggledDirection(r) {
  return effectiveDirection(r) === 'vertical' ? 'horizontal' : 'vertical'
}
function regionStatusLabel(r) {
  if (isDisabled(r)) return '已停用'
  const dir = effectiveDirection(r) === 'vertical' ? '纵排' : '横排'
  return `已启用 · ${dir}`
}
function regionFontLabel(r) {
  const key = r.font_key || r.font_family || ''
  const hit = fonts.value.find((f) => f.id === key || f.name === key)
  return hit ? hit.label : (key || '默认字体')
}
function regionSrcText(r) {
  return String(r.source_text || '').trim() || (isManual(r) ? '尚未识别原文' : '')
}
function resolveRegionTranslation(r) {
  return resolveRegionText(r)
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

function selectRegion(r, { open = true, event = null } = {}) {
  if (!r?.id) return
  const additive = Boolean(event?.metaKey || event?.ctrlKey)
  const range = Boolean(event?.shiftKey)
  let next = new Set(selectedRegionIds.value)
  if (range && selectionAnchorId.value) {
    next = selectRegionRange(regions.value, selectionAnchorId.value, r.id, next)
  } else if (additive) {
    if (next.has(r.id)) next.delete(r.id)
    else next.add(r.id)
  } else {
    next = new Set([r.id])
  }
  if (!next.size) next.add(r.id)
  selectedRegionIds.value = next
  selectedRegionId.value = r.id
  selectionAnchorId.value = selectionAnchorId.value || r.id
  if (!additive && !range) selectionAnchorId.value = r.id
  if (open) toggleOpen(r, true)
}

const selectedRegion = computed(() => regions.value.find((r) => r.id === selectedRegionId.value) || null)
const selectedRegions = computed(() => regions.value.filter((r) => selectedRegionIds.value.has(r.id)))
const selectionCount = computed(() => selectedRegionIds.value.size)
function isSelected(r) { return selectedRegionIds.value.has(r?.id) }
function clearSelection() {
  selectedRegionId.value = ''
  selectedRegionIds.value = new Set()
  selectionAnchorId.value = ''
}

function onRegionCardKeydown(event, r) {
  if (event.key === 'Enter' || event.key === ' ') {
    event.preventDefault()
    selectRegion(r, { event })
    toggleOpen(r, true)
  }
}

function locateRegion(r) {
  if (!r?.id) return
  revealedRegionId.value = filteredRegions.value.some(region => region.id === r.id) ? '' : r.id
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
    const card = Array.from(window.document.querySelectorAll('[data-region-card]'))
      .find(element => element.dataset.regionCard === r.id)
    card?.scrollIntoView({ block: 'nearest' })
  })
}

// 上/下一个文本框（面板按钮与 Alt+↑/↓ 共用）
function stepRegion(delta) {
  const list = filteredRegions.value
  if (!list.length) return
  const current = list.findIndex((r) => r.id === selectedRegionId.value)
  const next = current < 0 ? 0 : (current + delta + list.length) % list.length
  const r = list[next]
  selectRegion(r)
  locateRegion(r)
}

function nextIssue() {
  const target = nextIssueRegion(regions.value, selectedRegionId.value, { isIssue: needsAttention })
  if (!target) {
    toast('当前页没有待处理问题。', 'ok', 1600)
    return
  }
  locateRegion(target)
}

// ---- 画布框 ----
function regionBoxStyle(r) {
  // 拖动/缩放过程中用本地 preview 即时渲染（对齐旧版 layout override 体感）
  const pb = dragState.value && dragState.value.id === r.id && dragState.value.preview
    ? dragState.value.preview
    : (r.bbox || [0, 0, 0, 0])
  const [x1, y1, x2, y2] = pb
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

function regionPopStyle(r) {
  const pb = dragState.value && dragState.value.id === r.id && dragState.value.preview
    ? dragState.value.preview
    : (r.bbox || [0, 0, 0, 0])
  const [x1, y1, , y2] = pb
  const edgeSpace = 42 // 工具条约 34px 高，另留 8px 间距
  const placeBelow = y1 < edgeSpace && imgH.value - y2 >= edgeSpace
  const anchorY = placeBelow ? y2 : y1
  return {
    left: `clamp(4px, ${(x1 / imgW.value) * 100}%, calc(100% - 220px))`,
    top: `${(anchorY / imgH.value) * 100}%`,
    transform: placeBelow ? 'translateY(8px)' : 'translateY(calc(-100% - 8px))',
  }
}

function regionBoxText(r) {
  const draft = draftFor(r)
  return String(draft.translation || draft.sourceText || r.source_text || '')
}

function previewFontFor(r) {
  const draft = draftFor(r), config = reviewStyleConfig()
  const key = reviewStyleSnapshot(r, { draft, config, fonts: fonts.value }).fontKey
  return fonts.value.find(font => font.id === key || font.name === key || (!key && font.name === r.font_family))
}
watch(() => regions.value.map(r => previewFontFor(r)?.id || '').join('|'), () => {
  for (const font of new Map(regions.value.map(r => previewFontFor(r)).filter(Boolean).map(font => [font.id, font])).values()) fontPreview.load(font)
})
const previewFontUnavailable = computed(() => regions.value.some(r => {
  const font = previewFontFor(r)
  return !font || fontPreview.failures[font.id]
}))

function regionPreviewStyle(r) {
  const draft = draftFor(r)
  const font = previewFontFor(r)
  const size = Math.max(8, Number(draft.fontSize) || 12)
  const lineSpacing = Math.max(0.8, Number(draft.lineSpacing) || 1)
  const strokeWidth = Math.max(0, Number(draft.strokeWidth) || 0)
  return {
    fontFamily: fontPreview.families[font?.id] || 'sans-serif',
    fontSize: `${size}px`,
    fontWeight: 'normal',
    fontSynthesis: 'none',
    writingMode: effectiveDirection(r) === 'vertical' ? 'vertical-rl' : 'horizontal-tb',
    letterSpacing: `${Math.max(0, (Number(draft.letterSpacing) - 1) * size)}px`,
    lineHeight: String(lineSpacing),
    color: draft.fgColor || '#1A1712',
    WebkitTextStroke: strokeWidth ? `${size * strokeWidth * 0.35}px ${draft.bgColor || '#FFFFFF'}` : undefined,
    paintOrder: 'stroke fill',
    whiteSpace: 'pre-wrap',
    transform: Number(draft.rotation) ? `rotate(${Number(draft.rotation)}deg)` : undefined,
  }
}

function shouldShowSourceCrop(r) {
  const draft = draftFor(r)
  return Boolean(r?.bbox && (draft.keepOriginal || !draft.enabled))
}

function sourceCropImageStyle(r) {
  return sourceCropStyle(r?.bbox, imgW.value, imgH.value)
}

// 拖动/缩放
const dragState = ref(null)
const resizeHandleOptions = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w']

function onRegionPointerDown(event, r) {
  if (spacePan.value) return // 空格平移模式：不拦截，事件冒泡给画布平移
  if (dragState.value || !r?.bbox) return
  flushPendingNudge() // 有未提交的微调先落库，避免历史顺序颠倒
  event.preventDefault()
  event.stopPropagation()
  selectRegion(r, { open: true, event })
  if (event.metaKey || event.ctrlKey || event.shiftKey) return
  const stage = event.currentTarget.closest('.pane-stage')
  const rect = stage.getBoundingClientRect()
  const [x1, y1, x2, y2] = r.bbox
  const sx = rect.width / imgW.value
  const sy = rect.height / imgH.value
  const handleEl = event.target.closest('.handle')
  const mode = handleEl ? 'resize' : 'move'
  const handle = handleEl
    ? resizeHandleOptions.find((h) => handleEl.classList.contains(h)) || 'se'
    : ''
  dragState.value = {
    mode,
    handle,
    id: r.id,
    startX: event.clientX,
    startY: event.clientY,
    bbox: [...r.bbox],
    sx, sy,
    moved: false,
    preview: null,
  }
  window.addEventListener('pointermove', onDragMove)
  window.addEventListener('pointerup', onDragUp, { once: true })
}

function onDragMove(event) {
  const d = dragState.value
  if (!d) return
  let dx = Math.round((event.clientX - d.startX) / d.sx)
  let dy = Math.round((event.clientY - d.startY) / d.sy)
  if (Math.abs(dx) + Math.abs(dy) > 2) d.moved = true
  if (d.mode === 'move') {
    if (event.shiftKey) {
      // 锁轴移动（对齐旧版）
      if (Math.abs(dx) >= Math.abs(dy)) dy = 0
      else dx = 0
    }
    d.preview = translateBBoxPage(d.bbox, dx, dy)
  } else {
    // 八方向缩放；Shift 仅对角手柄等比，Alt 中心（对齐旧版）
    d.preview = resizeBBoxPage(d.bbox, d.handle, dx, dy, {
      proportional: event.shiftKey,
      fromCenter: event.altKey,
    })
  }
}

async function onDragUp() {
  window.removeEventListener('pointermove', onDragMove)
  const d = dragState.value
  dragState.value = null
  if (!d?.moved || !d.preview) return
  const [px1, py1, px2, py2] = d.preview
  if (px2 - px1 < 20 || py2 - py1 < 12) return
  await submitCommands([{ type: 'update_region_bbox', region_id: d.id, bbox: d.preview }])

}

// 手动添加框
const addingMode = ref(false)
const addDrag = ref(null)
const marqueeState = ref(null)

function canvasPagePoint(event, stage) {
  const rect = stage?.getBoundingClientRect?.() || stage
  if (!rect || rect.width < 1 || rect.height < 1) return null
  return {
    x: Math.min(imgW.value, Math.max(0, (event.clientX - rect.left) * imgW.value / rect.width)),
    y: Math.min(imgH.value, Math.max(0, (event.clientY - rect.top) * imgH.value / rect.height)),
  }
}

function onFramePointerDown(event) {
  const d = document.value
  if (!d || event.button !== 0 || spacePan.value) return
  if (!addingMode.value) {
    const target = event.target instanceof Element ? event.target : null
    if (target?.closest('.region-box, .region-pop, .canvas-hud, button, a')) return
    const stageRect = event.currentTarget.getBoundingClientRect()
    const point = canvasPagePoint(event, stageRect)
    if (!point) return
    marqueeState.value = {
      stageRect: { left: stageRect.left, top: stageRect.top, width: stageRect.width, height: stageRect.height },
      pageId: pageId.value,
      pointerId: event.pointerId,
      start: point,
      current: point,
      additive: Boolean(event.shiftKey || event.metaKey || event.ctrlKey),
      moved: false,
    }
    event.preventDefault()
    event.stopPropagation()
    window.addEventListener('pointermove', onMarqueeMove)
    window.addEventListener('pointerup', onMarqueeUp, { once: true })
    return
  }
  const rect = event.currentTarget.getBoundingClientRect()
  const sx = rect.width / imgW.value
  const sy = rect.height / imgH.value
  const x = Math.round((event.clientX - rect.left) / sx)
  const y = Math.round((event.clientY - rect.top) / sy)
  addDrag.value = { start: [x, y], current: [x, y] }
  window.addEventListener('pointermove', onAddMove)
  window.addEventListener('pointerup', onAddUp, { once: true })
}

function onMarqueeMove(event) {
  const d = marqueeState.value
  if (!d || d.pageId !== pageId.value) return
  if (d.pointerId != null && event.pointerId != null && d.pointerId !== event.pointerId) return
  const point = canvasPagePoint(event, d.stageRect)
  if (!point) return
  d.current = point
  d.moved = d.moved || Math.abs(point.x - d.start.x) >= 4 || Math.abs(point.y - d.start.y) >= 4
}

function marqueeStyle() {
  const d = marqueeState.value
  if (!d) return {}
  const x1 = Math.min(d.start.x, d.current.x)
  const y1 = Math.min(d.start.y, d.current.y)
  const x2 = Math.max(d.start.x, d.current.x)
  const y2 = Math.max(d.start.y, d.current.y)
  return {
    left: `${(x1 / imgW.value) * 100}%`,
    top: `${(y1 / imgH.value) * 100}%`,
    width: `${((x2 - x1) / imgW.value) * 100}%`,
    height: `${((y2 - y1) / imgH.value) * 100}%`,
  }
}

function onMarqueeUp(event) {
  window.removeEventListener('pointermove', onMarqueeMove)
  const d = marqueeState.value
  marqueeState.value = null
  if (!d || d.pageId !== pageId.value) return
  if (d.pointerId != null && event.pointerId != null && d.pointerId !== event.pointerId) return
  if (!d.moved) {
    clearSelection()
    return
  }
  const marquee = [
    Math.min(d.start.x, d.current.x), Math.min(d.start.y, d.current.y),
    Math.max(d.start.x, d.current.x), Math.max(d.start.y, d.current.y),
  ]
  const nextIds = selectCanvasRegions(regions.value, marquee, {
    selectedIds: selectedRegionIds.value,
    additive: d.additive,
  })
  selectedRegionIds.value = new Set(nextIds)
  selectedRegionId.value = nextIds[0] || ''
  selectionAnchorId.value = nextIds[0] || ''
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
  let bbox = [Math.min(x1, x2), Math.min(y1, y2), Math.max(x1, x2), Math.max(y1, y2)]
  if (bbox[2] - bbox[0] < 20 || bbox[3] - bbox[1] < 12) return
  bbox = clampBBoxPage(bbox) // 钳制在页面内
  const res = await submitCommands([{ type: 'create_region', bbox }])
  if (res?.created_region_id) {
    const rid = res.created_region_id
    addingMode.value = false
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

// ---- bbox 几何（对齐旧版：边界钳制 / 锁轴移动 / 四角缩放 / Shift 等比 / Alt 中心）----
function clampBBoxPage(bbox) {
  return clampCanvasBBox(bbox, imgW.value, imgH.value)
}

function translateBBoxPage(origin, dx, dy) {
  const W = imgW.value
  const H = imgH.value
  const w = Math.max(8, origin[2] - origin[0])
  const h = Math.max(8, origin[3] - origin[1])
  const x1 = Math.min(Math.max(0, origin[0] + dx), Math.max(0, W - w))
  const y1 = Math.min(Math.max(0, origin[1] + dy), Math.max(0, H - h))
  return [Math.round(x1), Math.round(y1), Math.round(x1 + w), Math.round(y1 + h)]
}

// handle 取 nw/n/ne/e/se/s/sw/w；proportional=Shift 等比；fromCenter=Alt 中心缩放（对齐旧版语义）
function resizeBBoxPage(origin, handle, dx, dy, { proportional = false, fromCenter = false } = {}) {
  return resizeCanvasBBox(origin, handle, dx, dy, {
    width: imgW.value,
    height: imgH.value,
    proportional,
    fromCenter,
  })
}

// Every persisted edit shares one history with page/document revision guards.
const { canUndo, canRedo } = editor
async function undoEdit() {
  try { if (await flushPendingNudge()) await editor.undo() } catch (error) { toastError(error) }
}
async function redoEdit() {
  try { if (await flushPendingNudge()) await editor.redo() } catch (error) { toastError(error) }
}

// ---- 方向键微调（本地即时预览，松开方向键合并成一次提交，对齐旧版 pendingCanvasNudge）----
let pendingNudge = null // { regionId, originBBox }
function nudgeSelectedRegion(dx, dy) {
  const r = regions.value.find((x) => x.id === selectedRegionId.value)
  if (!r?.bbox) return
  if (pendingNudge && pendingNudge.regionId !== r.id) flushPendingNudge()
  if (!pendingNudge) pendingNudge = { regionId: r.id, originBBox: [...r.bbox] }
  const next = translateBBoxPage(r.bbox, dx, dy)
  if (next.every((v, i) => v === r.bbox[i])) return
  r.bbox = next // 直接改本地文档副本，框即时跟随
}

async function flushPendingNudge() {
  const p = pendingNudge
  pendingNudge = null
  if (!p) return true
  const r = regions.value.find((x) => x.id === p.regionId)
  if (!r?.bbox) { pendingNudge = p; return false }
  if (r.bbox.every((v, i) => v === p.originBBox[i])) return true
  const redoBBox = [...r.bbox]
  const res = await submitCommands([{ type: 'update_region_bbox', region_id: p.regionId, bbox: redoBBox }])
  if (res) return true
  pendingNudge = p
  return false
}

// ---- 全局快捷键（对齐旧版 handleGlobalCanvasKeydown）----
function isTypingTarget(target) {
  return target instanceof HTMLElement && Boolean(target.closest('input, textarea, select, [contenteditable="true"]'))
}

function onGlobalKeydown(event) {
  if (fullTranslateConfirmationOpen.value) {
    if (event.key === 'Escape') {
      event.preventDefault()
      closeFullTranslationConfirmation()
    }
    return
  }
  if (event.key === 'Escape') {
    event.preventDefault()
    addingMode.value = false
    exportMenuOpen.value = false
    rerenderMenuOpen.value = false
    if (marqueeState.value) {
      window.removeEventListener('pointermove', onMarqueeMove)
      marqueeState.value = null
    }
    clearSelection()
    return
  }
  if (isTypingTarget(event.target)) return
  if (event.code === 'Space') {
    spacePan.value = true
    event.preventDefault()
    return
  }
  if (!document.value) return
  const meta = event.metaKey || event.ctrlKey
  const key = event.key.toLowerCase()
  if (meta && !event.shiftKey && key === 'z') {
    event.preventDefault()
    if (!taskBusy.value) undoEdit()
    return
  }
  if ((meta && event.shiftKey && key === 'z') || (meta && key === 'y')) {
    event.preventDefault()
    if (!taskBusy.value) redoEdit()
    return
  }
  if (event.key === 'PageUp' || event.key === '[') {
    event.preventDefault()
    prevPage()
    return
  }
  if (event.key === 'PageDown' || event.key === ']') {
    event.preventDefault()
    nextPage()
    return
  }
  if (event.altKey && event.key === 'ArrowUp') {
    event.preventDefault()
    stepRegion(-1)
    return
  }
  if (event.altKey && event.key === 'ArrowDown') {
    event.preventDefault()
    stepRegion(1)
    return
  }
  if (event.shiftKey && event.code === 'Digit1') {
    event.preventDefault()
    zoomFit()
    return
  }
  if (event.shiftKey && event.code === 'Digit0') {
    event.preventDefault()
    zoomReset()
    return
  }
  if (event.shiftKey && event.code === 'Digit2') {
    event.preventDefault()
    if (selectedRegion.value) locateRegion(selectedRegion.value)
    return
  }
  if (event.key === 'Enter' && selectedRegion.value) {
    event.preventDefault()
    toggleOpen(selectedRegion.value, true)
    nextTick(() => {
      window.document.querySelector('.region-card.is-selected textarea')?.focus()
    })
    return
  }
  // 方向键微调：1px，Ctrl/⌘ 5px，Shift 10px
  if (!selectedRegion.value?.bbox || addingMode.value || taskBusy.value) return
  const nudgeMap = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] }
  const d = nudgeMap[event.key]
  if (!d) return
  event.preventDefault()
  const step = event.shiftKey ? 10 : meta ? 5 : 1
  nudgeSelectedRegion(d[0] * step, d[1] * step)
}

function onGlobalKeyup(event) {
  if (event.code === 'Space') spacePan.value = false
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(event.key)) {
    flushPendingNudge()
  }
}

// ---- 卡片编辑 ----
async function applySourceText(r) { return saveDraft(r, ['sourceText']) }
async function applyTranslation(r) { return saveDraft(r, ['translation']) }
async function applyToggleEnabled(r) {
  draftFor(r).enabled = !draftFor(r).enabled
  return saveDraft(r, ['enabled'])
}
async function applyDirection(r, direction) {
  if (!r?.id) return null
  const draft = draftFor(r)
  const previousDirection = draft.directionIntent == null
    ? resolveLayoutDirectionOverride(r, reviewStyleConfig())
    : normalizeReviewDirection(draft.directionIntent)
  if (previousDirection === normalizeReviewDirection(direction)) return null
  try { return await editor.saveDirectionIntent(r.id, direction, previousDirection) }
  catch (error) { toastError(error); return null }
}
async function applyKeepOriginal(r) { return saveDraft(r, ['keepOriginal']) }
async function applyFont(r) { return saveDraft(r, ['fontKey']) }
async function applyFontSize(r) {
  const draft = draftFor(r)
  const baseline = editor.document.value?.regions.find(region => region.id === r.id)
  draft.fontSizeOverride = Math.max(8, Math.round(Number(draft.fontSize) || 12))
  const fields = ['fontSize']
  if (draft.fontSizeOverride !== baseline?.font_size_override) fields.push('fontSizeOverride')
  return saveDraft(r, fields)
}
async function resetFontSize(r) {
  const draft = draftFor(r)
  if (draft.fontSizeOverride == null) return null
  draft.fontSize = Math.max(8, Number(r.detected_font_size) || Number(r.font_size) || 12)
  draft.fontSizeOverride = null
  return saveDraft(r, ['fontSizeOverride'])
}
async function applyFontStyle(r) { return saveDraft(r, ['fontStyle']) }
async function applyAdvancedStyle(r) {
  return saveDraft(r, ['rotation', 'strokeWidth', 'letterSpacing', 'lineSpacing', 'fgColor', 'bgColor', 'preserveBackground'])
}

async function deleteRegion(r) {
  if (!r) return
  const type = isManual(r) ? 'delete_manual_region' : 'disable_region'
  const res = await submitCommands([{ type, region_id: r.id }])
  if (!res) return
  const nextOpen = new Set(openRegionIds.value)
  nextOpen.delete(r.id)
  openRegionIds.value = nextOpen
  const nextSelected = new Set(selectedRegionIds.value)
  nextSelected.delete(r.id)
  selectedRegionIds.value = nextSelected
  if (selectedRegionId.value === r.id) selectedRegionId.value = [...nextSelected][0] || ''
}

// 样式复制/粘贴
const styleClipboard = ref(null)
function copyStyle(r) {
  styleClipboard.value = reviewStyleSnapshot(r, {
    draft: draftFor(r),
    config: reviewStyleConfig(),
    fonts: fonts.value,
  })
  toast('样式已复制', 'ok', 1500)
}
async function pasteStyle(r) {
  const s = styleClipboard.value
  if (!s || !r?.id) return
  const targets = selectedRegionIds.value.has(r.id) && selectionCount.value > 1
    ? selectedRegions.value
    : [r]
  const commands = []
  for (const target of targets) {
    commands.push(...buildReviewStyleCommands(target.id, s))
  }
  await submitCommands(commands, { label: targets.length > 1 ? '批量粘贴样式' : '粘贴样式' })
}

const ocrBusyRegionIds = ref(new Set())
function isOcrBusy(r) { return ocrBusyRegionIds.value.has(r?.id) }
async function retryOcr(r) {
  if (!r || isOcrBusy(r)) return
  const next = new Set(ocrBusyRegionIds.value)
  next.add(r.id)
  ocrBusyRegionIds.value = next
  try {
    const result = await submitCommands([
      { type: 'retry_region_ocr', region_id: r.id },
    ], { label: '重新识别原文' })
    if (result?.recognized_region_payload?.recognition_status === 'failed') {
      toast(`OCR 失败，原有内容已保留：${result.recognized_region_payload.recognition_error}`, 'error')
    } else if (result) toast('OCR 原文已更新，请检查译文。', 'ok', 1800)
  } finally {
    const done = new Set(ocrBusyRegionIds.value)
    done.delete(r.id)
    ocrBusyRegionIds.value = done
  }
}

function batchTargets() {
  return selectedRegions.value
}

async function applyBatchField(field, value, label) {
  const targets = batchTargets()
  if (targets.length < 2) return
  const commands = []
  for (const region of targets) {
    commands.push(...fieldCommand(region.id, field, value))
  }
  await submitCommands(commands, { label })
}

async function duplicateRegion(r) {
  if (!r?.id || taskBusy.value) return null
  const result = await submitCommands([
    { type: 'duplicate_region', region_id: r.id },
  ], { label: '复制文本框' })
  const createdId = result?.created_region_id
  if (!createdId) return result
  const next = new Set([createdId])
  selectedRegionIds.value = next
  selectedRegionId.value = createdId
  selectionAnchorId.value = createdId
  nextTick(() => {
    const created = regions.value.find(region => region.id === createdId)
    if (created) {
      toggleOpen(created, true)
      locateRegion(created)
    }
  })
  toast('文本框已复制', 'ok', 1500)
  return result
}

function fieldCommand(regionId, field, value) {
  if (field === 'fontKey') return [{ type: 'update_region_font', region_id: regionId, font_key: value || '' }]
  if (field === 'fontSize') return [{ type: 'update_font_size', region_id: regionId, font_size: Math.max(8, Math.round(Number(value) || 12)) }]
  if (field === 'fontStyle') return [{ type: 'update_font_style', region_id: regionId, style: value || '' }]
  if (field === 'direction') return [{ type: 'update_text_direction', region_id: regionId, direction: value || 'auto' }]
  if (field === 'enabled') return [{ type: value ? 'restore_region' : 'disable_region', region_id: regionId }]
  if (field === 'keepOriginal') return [{ type: 'set_keep_original', region_id: regionId, enabled: Boolean(value) }]
  return []
}

const batchFontKey = ref('')
const batchFontSize = ref('')
const batchFontStyle = ref('')
const batchDirection = ref('horizontal')

async function mergeSelected() {
  const targets = batchTargets().filter(region => !isDisabled(region))
  if (targets.length < 2) return
  const result = await submitCommands([
    { type: 'merge_regions', region_ids: targets.map((region) => region.id) },
  ], { label: '合并文本框' })
  const mergedId = result?.created_region_id
  if (mergedId) {
    selectedRegionIds.value = new Set([mergedId])
    selectedRegionId.value = mergedId
    selectionAnchorId.value = mergedId
    const merged = regions.value.find((region) => region.id === mergedId)
    if (merged) toggleOpen(merged, true)
  }
}

// ---- 任务 ----
async function runPageTask(action, targetPageId = pageId.value, { resolveTargetAfterFlush = false, processingConfig = null } = {}) {
  if (preparingTask.value || taskEvents.busy.value) return
  const sid = sessionId.value, pid = pageId.value
  preparingTask.value = true
  try {
    if (!await leaveSafely()) return
    const config = processingConfig || await loadProcessingConfig(project.value?.config || {})
    if (sid !== sessionId.value || pid !== pageId.value) return
    const resolvedTargetPageId = resolveTargetAfterFlush
      ? pendingRenderPages.value.map(image => image.stored_name)
      : targetPageId
    if (resolveTargetAfterFlush && !resolvedTargetPageId.length) {
      toast('没有需要重新嵌字的页面。', 'ok', 1800)
      return
    }
    if (Array.isArray(resolvedTargetPageId)) taskEvents.startBatch(sid, action, config, resolvedTargetPageId)
    else taskEvents.start(sid, action, config, resolvedTargetPageId)
  } catch (error) { toastError(error) }
  finally { preparingTask.value = false }
}
function runTranslatePage() { return runPageTask('translate-page') }
async function requestFullTranslation() {
  if (!canFullTranslate.value) return
  const requestedProjectId = sessionId.value
  fullTranslationConfigLoading.value = true
  try {
    const loadedConfig = await loadProcessingConfig(project.value?.config || {})
    if (requestedProjectId !== sessionId.value || !canFullTranslate.value) return
    fullTranslationConfig.value = loadedConfig
    fullTranslationProjectId.value = requestedProjectId
    pendingFullTranslateAction.value = fullTranslateAction.value
    fullTranslateConfirmationOpen.value = true
  } catch (error) {
    toastError(error)
  } finally {
    fullTranslationConfigLoading.value = false
  }
}
function closeFullTranslationConfirmation() {
  fullTranslateConfirmationOpen.value = false
  pendingFullTranslateAction.value = ''
  fullTranslationConfig.value = null
  fullTranslationProjectId.value = ''
}
function confirmFullTranslation() {
  const action = pendingFullTranslateAction.value || fullTranslateAction.value
  const requestedProjectId = fullTranslationProjectId.value
  const processingConfig = fullTranslationConfig.value
  closeFullTranslationConfirmation()
  if (!requestedProjectId || requestedProjectId !== sessionId.value || !processingConfig || !canFullTranslate.value) return null
  return runPageTask(action, '', { processingConfig })
}
function runRerender(scope = 'page') {
  rerenderMenuOpen.value = false
  return scope === 'page'
    ? runPageTask('rerender', pageId.value)
    : runPageTask('rerender', null, { resolveTargetAfterFlush: true })
}
function runRerenderCurrent() { return runRerender('page') }
function runRerenderPending() { return runRerender('pending') }
async function cancelTask() {
  try {
    const requested = await taskEvents.cancel()
    toast(requested ? '已请求取消，后续页面不会继续启动。' : '任务已发送，正在确认后台任务；后续页面不会继续启动。', requested ? 'ok' : 'warn')
  } catch (err) {
    toastError(err)
  }
}

watch(() => [taskState.value.activeTaskId, taskState.value.eventName], async ([taskId, name]) => {
  if (!taskId || !TERMINALS.includes(name)) return
  // Capture the task identity before awaiting project/document refreshes. The
  // route may change while a cross-page task finishes.
  const callbackSessionId = taskEvents.sessionId.value
  const callbackPageId = taskState.value.activeTaskTargetStoredName
  const callbackMessage = taskState.value.statusMessage
  if (callbackSessionId !== sessionId.value) return
  if (name === 'completed') {
    toast('任务完成', 'ok')
    try { await loadProject(callbackSessionId) } catch (error) { toastError(error) }
    if (callbackSessionId !== sessionId.value) return
    if (!callbackPageId || callbackPageId === pageId.value) await loadDocument()
  } else if (name === 'error' || name === 'failed') {
    toast(callbackMessage || '任务失败', 'error')
    if (callbackSessionId === sessionId.value && (!callbackPageId || callbackPageId === pageId.value)) {
      await loadDocument()
    }
  }
})

// ---- 导出 ----
const exportMenuOpen = ref(false)
const rerenderMenuOpen = ref(false)
async function exportResult() {
  if (exporting.value) return
  const callbackSessionId = sessionId.value
  if (taskBusy.value) { toast('请等待当前任务完成后再导出。', 'warn'); return }
  exporting.value = true
  try {
    if (!await leaveSafely()) return
    if (callbackSessionId !== sessionId.value) return
    // Refresh artifact capabilities after the final save. A stale project
    // download URL must never make an older archive look current.
    await loadProject(callbackSessionId)
    if (callbackSessionId !== sessionId.value) return
    const url = project.value?.download_url
    if (!url || !finalArtifactsReady.value || taskBusy.value || editor.hasUnsaved.value) {
      toast('所有页面都需要有效的最新成品后才能导出。', 'warn')
      return
    }
    window.open(withCacheBust(toApiUrl(url)), '_blank')
    exportMenuOpen.value = false
  } catch (error) { toastError(error) }
  finally { exporting.value = false }
}
async function exportBlank() {
  if (exporting.value) return
  const callbackSessionId = sessionId.value
  if (taskBusy.value) { toast('请等待当前任务完成后再导出。', 'warn'); return }
  exporting.value = true
  try {
    if (!await leaveSafely()) return
    if (callbackSessionId !== sessionId.value) return
    await loadProject(callbackSessionId)
    if (callbackSessionId !== sessionId.value) return
    if (!blankArtifactsReady.value || taskBusy.value || editor.hasUnsaved.value) {
      toast('所有页面都需要有效的空页后才能导出。', 'warn')
      return
    }
    window.open(withCacheBust(toApiUrl(`/api/download/${callbackSessionId}/blank`)), '_blank')
    exportMenuOpen.value = false
  } catch (error) { toastError(error) }
  finally { exporting.value = false }
}

// Flush before changing route identity; a failed save keeps the current editor open.
let navigationFlush = null
function leaveSafely() {
  if (navigationFlush) return navigationFlush
  navigationFlush = (async () => {
    try {
      if (!await flushPendingNudge()) return false
      await editor.flush()
      return true
    } catch (error) { toastError(error); return false }
    finally { navigationFlush = null }
  })()
  return navigationFlush
}
onBeforeRouteLeave(leaveSafely)
onBeforeRouteUpdate((to, from) => to.params.sessionId === from.params.sessionId && to.params.pageId === from.params.pageId
  ? true : leaveSafely())
function beforeUnload(event) {
  if (editor.hasUnsaved.value || pendingNudge || taskEvents.batch.value) { event.preventDefault(); event.returnValue = '' }
}
onMounted(() => {
  loadFonts()
  window.addEventListener('beforeunload', beforeUnload)
  restoreReviewLocation(sessionId.value)
})
watch([sessionId, pageId], async () => {
  if (fullTranslateConfirmationOpen.value) closeFullTranslationConfirmation()
  selectedRegionId.value = ''
  selectedRegionIds.value = new Set()
  selectionAnchorId.value = ''
  openRegionIds.value = new Set()
  userTookOver = false
  const selectedProject = sessionId.value, selectedPage = pageId.value
  if (savedLocation.value.pageId !== selectedPage || savedLocation.value.version !== 1) {
    // Preferences persist across pages; the current route remains authoritative.
    restoreReviewLocation(selectedProject)
  }
  await Promise.all([ensureProject(), loadDocument()])
  if (sessionId.value !== selectedProject || pageId.value !== selectedPage) return
  rememberRecentPage(selectedProject, selectedPage)
  const targetId = route.query.region || (savedLocation.value.pageId === selectedPage ? savedLocation.value.regionId : '')
  const target = regions.value.find(r => r.id === String(targetId || ''))
  if (target) nextTick(() => locateRegion(target))
  persistReviewLocation()
}, { immediate: true })
watch([filter, searchQuery], () => { revealedRegionId.value = '' })
watch([selectedRegionId, filter, searchQuery, panelCollapsed, panelWidth, previewTypography, panes], persistReviewLocation, { deep: true })
watch(() => reviewList.value?.scrollTop, persistReviewLocation)
watch(() => route.query.region, id => {
  const target = regions.value.find(r => r.id === String(id || ''))
  if (target) locateRegion(target)
})
onUnmounted(() => {
  taskEvents.disconnect()
  window.removeEventListener('beforeunload', beforeUnload)
  onPanelResizeUp()
  persistReviewLocation()
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', onDragUp)
  window.removeEventListener('pointermove', onAddMove)
  window.removeEventListener('pointerup', onAddUp)
  window.removeEventListener('pointermove', onPanMove)
  window.removeEventListener('pointerup', onPanUp)
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
      <button v-if="hasRecentPage" class="btn btn-ghost btn-sm recent-location" type="button" title="返回上一次审校页面和文本框位置" @click="returnToRecentPage">
        返回最近位置
      </button>
      <div class="topbar-spacer"></div>
      <div class="topbar-actions">
        <span v-if="taskBusy" class="task-pill is-busy">
          <span class="dot"></span>
          <span class="task-text">{{ taskState.statusMessage || '任务进行中…' }}<small v-if="taskState.activeAction === 'rerender'"> · {{ taskState.activeTaskTargetStoredName ? '当前页' : '待处理页' }}</small></span>
          <progress
            v-if="taskState.progress && taskState.progress.total"
            :value="taskState.progress.current"
            :max="taskState.progress.total"
          ></progress>
        </span>
        <span v-else class="saved-hint" role="status" aria-live="polite">{{ saveStatus }}</span>
        <button class="btn btn-ghost btn-sm" :disabled="taskBusy || !document" :aria-pressed="reviewed"
          title="人工确认本页；内容变化后需要重新审校"
          @click="submitCommands([{ type: 'set_review_status', status: reviewed ? 'unreviewed' : 'reviewed' }], { label: '人工审校标记' })">
          {{ reviewed ? '人工已审校 ✓' : '标记人工已审校' }}
        </button>
        <button v-if="editor.dirty.value || editor.error.value" class="btn btn-ghost btn-sm" type="button" :disabled="editor.pending.value > 0" @click="leaveSafely">保存修改</button>
        <button class="btn btn-ghost btn-sm" type="button" :disabled="!canFullTranslate || fullTranslationConfigLoading" title="使用当前设置和模型处理整本漫画" @click="requestFullTranslation">{{ fullTranslationConfigLoading ? '读取设置…' : fullTranslateLabel }}</button>
        <a class="btn btn-ghost" href="#/glossary" @click.prevent="router.push({ path: `/glossary/${sessionId}`, query: { page: pageId } })">专有名词库</a>
        <button v-if="taskBusy" class="btn btn-ghost" type="button" @click="cancelTask">取消任务</button>
        <div class="export-menu-wrap">
          <button class="btn btn-secondary" type="button" @click="exportMenuOpen = !exportMenuOpen">
            导出
            <svg width="10" height="10" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m4 6 4 4 4-4"/></svg>
          </button>
          <div v-if="exportMenuOpen" class="export-menu">
            <button type="button" :disabled="!finalArtifactsReady || taskBusy || exporting" @click="exportResult">导出结果（.zip）</button>
            <button type="button" :disabled="!blankArtifactsReady || taskBusy || exporting" @click="exportBlank">导出空页（.zip）</button>
          </div>
        </div>
        <div class="rerender-menu-wrap">
          <button class="btn btn-primary" type="button" :disabled="taskBusy" title="重新嵌当前页" @click="runRerenderCurrent">
            重嵌当前页
          </button>
          <button class="btn btn-primary rerender-menu-trigger" type="button" :disabled="taskBusy" aria-label="打开重嵌范围菜单" title="选择当前页或待处理页" @click="rerenderMenuOpen = !rerenderMenuOpen">⌄</button>
          <div v-if="rerenderMenuOpen" class="export-menu rerender-menu">
            <button type="button" @click="runRerenderCurrent">重嵌当前页（第 {{ pageNumber }} 页）</button>
            <button type="button" :disabled="!pendingRenderCount" @click="runRerenderPending">重嵌待处理页（{{ pendingRenderCount }} 页）</button>
          </div>
        </div>
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
            <button :class="{ active: panes.frame }" title="显示或隐藏空页与框选画布" :aria-pressed="panes.frame" @click="togglePane('frame')">
              <svg class="pane-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><rect x="7" y="7" width="6" height="8" rx="1"/></svg>
              框页
            </button>
            <button :class="{ active: panes.final }" title="显示或隐藏已嵌字成品画布" :aria-pressed="panes.final" @click="togglePane('final')">
              <svg class="pane-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><path d="m9 12 2 2 4-4"/></svg>
              嵌后
            </button>
            <button :class="{ active: panes.src }" title="显示或隐藏原图画布" :aria-pressed="panes.src" @click="togglePane('src')">
              <svg class="pane-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.5-3.5a2 2 0 0 0-3 0L6 20"/></svg>
              原图
            </button>
            <button :class="{ active: panes.blank }" title="显示或隐藏空页画布" :aria-pressed="panes.blank" @click="togglePane('blank')">
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
            <button class="btn btn-ghost btn-sm" type="button" :disabled="taskBusy" title="只翻译并嵌字当前页" @click="runTranslatePage">翻译当前页</button>
            <button class="btn btn-ghost btn-sm" type="button" :class="{ 'is-on': previewTypography }" title="在空页上显示当前字体和排版草稿" @click="previewTypography = !previewTypography">草稿预览</button>
            <button class="btn btn-ghost btn-sm" type="button" :disabled="!attentionCount" title="定位到下一个待处理问题" @click="nextIssue">下个问题</button>
            <button v-if="!panelCollapsed" class="icon-btn" type="button" aria-label="隐藏文本框面板" title="隐藏文本框面板" @click="panelCollapsed = true"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="m15 18-6-6 6-6"/></svg></button>
            <button v-else class="btn btn-ghost btn-sm" type="button" title="显示文本框面板" @click="panelCollapsed = false">显示面板</button>
          </div>
        </div>

        <p v-if="previewTypography" class="preview-note">{{ previewFontUnavailable ? '部分字体不可用，当前使用替代字体。' : '' }}草稿用于检查字体和排版；最终断行、字形和溢出请以重新嵌字后的成品为准。</p>
        <div class="pane-strip" :data-count="paneCount">
          <div v-if="panes.frame" class="pane">
            <span class="pane-label"><i></i>框页 · {{ previewTypography ? '草稿预览' : '可编辑框' }}</span>
            <div
              ref="frameCanvas"
              class="pane-canvas"
              :class="{ 'is-panning': panning, 'is-space-pan': spacePan }"
              title="滚轮平移 · Shift 滚轮横向平移 · Ctrl/⌘+滚轮缩放 · 按住 Space 拖平移 · 移动时 Shift 锁轴 · 缩框 Shift 等比 / Alt 中心"
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
                    :class="{ 'is-active': r.id === selectedRegionId, 'is-multi-selected': isSelected(r), 'is-disabled': isDisabled(r) }"
                    :data-canvas-region="r.id"
                    :style="regionBoxStyle(r)"
                    @pointerdown="onRegionPointerDown($event, r)"
                  >
                    <span class="region-no">{{ r.number }}</span>
                    <span v-if="shouldShowSourceCrop(r)" class="box-source-crop">
                      <img :src="imageUrl('source-image')" :style="sourceCropImageStyle(r)" alt="" draggable="false" />
                    </span>
                    <span v-else-if="previewTypography && draftFor(r).enabled && !draftFor(r).keepOriginal" class="box-text" :style="regionPreviewStyle(r)">{{ regionBoxText(r) }}</span>
                    <template v-if="r.id === selectedRegionId">
                      <span v-for="handle in resizeHandleOptions" :key="`${r.id}-${handle}`" class="handle" :class="handle"></span>
                    </template>
                  </div>
                  <div v-if="marqueeState" class="region-marquee" :style="marqueeStyle()"></div>
                  <div v-if="addDrag" class="region-box is-drawing" :style="addDragStyle()"></div>
                  <div
                    v-if="selectedRegionId && regions.find((r) => r.id === selectedRegionId)"
                    class="region-pop"
                    :style="regionPopStyle(regions.find((r) => r.id === selectedRegionId))"
                  >
                    <span class="pop-coord num">x{{ regions.find((r) => r.id === selectedRegionId).bbox?.[0] }} · {{ regions.find((r) => r.id === selectedRegionId).bbox?.[1] }}</span>
                    <span class="pop-divider"></span>
                    <button class="icon-btn" type="button" data-tip="定位到卡片" aria-label="定位到卡片" @click="locateRegion(regions.find((r) => r.id === selectedRegionId))">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/><circle cx="12" cy="12" r="7"/></svg>
                    </button>
                    <button class="icon-btn" type="button" data-tip="复制全部样式" aria-label="复制全部样式" @click="copyStyle(regions.find((r) => r.id === selectedRegionId))">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 22a10 10 0 1 1 10-10"/><path d="M12 12 7 7"/><path d="m17 16 4-4-4-4"/><path d="M21 12H9"/></svg>
                    </button>
                    <button class="icon-btn" type="button" data-tip="纵排 / 横排" aria-label="纵横排" @click="applyDirection(regions.find((r) => r.id === selectedRegionId), toggledDirection(regions.find((r) => r.id === selectedRegionId)))">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M7 4v13M7 17l-3-3M7 17l3-3"/><path d="M17 20V7M17 7l-3 3M17 7l3 3"/></svg>
                    </button>
                    <button class="icon-btn" type="button" :data-tip="isManual(selectedRegion) ? '删除此框' : '停用此框'" :aria-label="isManual(selectedRegion) ? '删除此框' : '停用此框'" @click="deleteRegion(regions.find((r) => r.id === selectedRegionId))">
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
                    </button>
                  </div>
                </template>
              </div>
            </div>
            <div class="canvas-hud">
              <button type="button" data-tip="撤销（⌘/Ctrl+Z）" :disabled="!canUndo" @click="undoEdit">↶</button>
              <button type="button" data-tip="重做（⇧⌘/Ctrl+Z 或 Ctrl+Y）" :disabled="!canRedo" @click="redoEdit">↷</button>
              <span class="pop-divider" style="width:1px;height:16px;background:var(--border-strong);margin:0 3px;"></span>
              <button type="button" data-tip="适合窗口（Shift+1）" @click="zoomFit">适合</button>
              <button type="button" data-tip="适应宽度" @click="zoomWidth">适宽</button>
              <span class="pop-divider" style="width:1px;height:16px;background:var(--border-strong);margin:0 3px;"></span>
              <button type="button" aria-label="缩小" @click="zoomOut">−</button>
              <span class="hud-zoom" data-tip="回到 100%（Shift+0）" role="button" @click="zoomReset">{{ Math.round(view.zoom * 100) }}%</span>
              <button type="button" aria-label="放大" @click="zoomIn">＋</button>
            </div>
          </div>

          <div v-if="panes.final" class="pane">
            <span class="pane-label is-final"><i></i>嵌后 · {{ finalRenderLabel }}</span>
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
            <span class="pane-label is-blank"><i></i>空页 · 草稿预览</span>
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

      <div v-if="!panelCollapsed" class="panel-resizer" role="separator" tabindex="0" aria-label="调整文本框面板宽度" aria-orientation="vertical" :aria-valuemin="280" :aria-valuemax="520" :aria-valuenow="panelWidth" title="拖动或使用方向键调整文本框面板宽度" @pointerdown="startPanelResize" @keydown="onPanelResizeKeydown"></div>
      <aside v-if="!panelCollapsed" class="region-panel" :style="panelStyle">
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
            <button class="chip" :class="{ active: filter === 'keep-original' }" @click="filter = 'keep-original'">保留原文 {{ keepOriginalCount }}</button>
            <button class="chip" :class="{ active: filter === 'untranslated' }" @click="filter = 'untranslated'">未翻译 {{ untranslatedCount }}</button>
            <button class="chip" :class="{ active: filter === 'font-override' }" @click="filter = 'font-override'">字体覆盖 {{ fontOverrideCount }}</button>
            <button class="chip" :class="{ active: filter === 'disabled' }" @click="filter = 'disabled'">已停用 {{ disabledCount }}</button>
          </div>
        </div>

        <div v-if="selectionCount > 1" class="batch-controls" role="group" aria-label="批量编辑所选文本框">
          <strong>已选 {{ selectionCount }} 个框</strong>
          <button class="btn btn-ghost btn-sm" :disabled="taskBusy" @click="mergeSelected">合并启用框</button>
          <button class="btn btn-ghost btn-sm" @click="clearSelection">清除选择</button>
          <select v-model="batchFontKey" aria-label="批量字体" @change="applyBatchField('fontKey', batchFontKey, '批量设置字体')">
            <option value="">默认字体</option><option v-for="f in fonts" :key="f.id" :value="f.id">{{ f.label }}</option>
          </select>
          <input v-model="batchFontSize" type="number" min="8" placeholder="批量字号" aria-label="批量字号" @change="applyBatchField('fontSize', batchFontSize, '批量设置字号')" />
          <button class="btn btn-ghost btn-sm" @click="applyBatchField('direction', 'horizontal', '批量横排')">横排</button>
          <button class="btn btn-ghost btn-sm" @click="applyBatchField('direction', 'vertical', '批量纵排')">纵排</button>
          <button class="btn btn-ghost btn-sm" @click="applyBatchField('enabled', true, '批量启用')">启用</button>
          <button class="btn btn-ghost btn-sm" @click="applyBatchField('enabled', false, '批量停用')">停用</button>
          <button class="btn btn-ghost btn-sm" @click="applyBatchField('keepOriginal', true, '批量保留原文')">保留原文</button>
          <button class="btn btn-ghost btn-sm" @click="applyBatchField('keepOriginal', false, '批量恢复嵌字')">恢复嵌字</button>
        </div>
        <div class="region-list" ref="reviewList" @scroll="persistReviewLocation">
          <p v-if="revealedRegionIsFiltered" class="preview-note">已显示定位目标 #{{ regions.find(region => region.id === revealedRegionId)?.number }}；它被当前筛选暂时隐藏。</p>
          <article
            v-for="r in visibleRegions"
            :key="r.id"
            class="region-card"
            :class="{ 'is-open': isOpen(r), 'is-selected': isSelected(r), 'is-disabled': isDisabled(r) }"
            :data-region-card="r.id"
            tabindex="0" @keydown.self="onRegionCardKeydown($event, r)"
            @click="selectRegion(r, { event: $event })"
          >
            <header class="region-card-head" @click.stop="selectRegion(r, { event: $event, open: false }); toggleOpen(r)">
              <input type="checkbox" :checked="isSelected(r)" :aria-label="`选择第 ${r.number} 个文本框`" @click.stop="selectRegion(r, { event: { ctrlKey: true }, open: false })" />
              <span class="rid">#{{ r.number }}</span>
              <span class="tag">{{ regionFontLabel(r) }}</span>
              <span v-if="needsAttention(r)" class="tag is-warn" :title="issueReason(r)">{{ issueReason(r) }}</span>
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
                <span>原文</span>
                <textarea rows="2" :value="draftFor(r).sourceText" @input="draftFor(r).sourceText = $event.target.value" @blur="applySourceText(r)"></textarea>
              </label>
              <button class="btn btn-ghost btn-sm" :disabled="taskBusy || isOcrBusy(r)" @click="retryOcr(r)">{{ isOcrBusy(r) ? '识别中…' : '重新识别原文（本地 OCR）' }}</button>
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
                <button class="toggle-chip" :class="{ active: effectiveDirection(r) === 'horizontal' }" type="button" @click="applyDirection(r, 'horizontal')">横排</button>
                <button class="toggle-chip" :class="{ active: effectiveDirection(r) === 'vertical' }" type="button" @click="applyDirection(r, 'vertical')">纵排</button>
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
                  <div class="font-size-control">
                    <div class="stepper">
                      <button type="button" @click="draftFor(r).fontSize = Math.max(8, Number(draftFor(r).fontSize) - 1); applyFontSize(r)">−</button>
                      <input
                        type="text"
                        inputmode="numeric"
                        :value="draftFor(r).fontSize"
                        @change="draftFor(r).fontSize = Number($event.target.value) || 12; applyFontSize(r)"
                      />
                      <button type="button" @click="draftFor(r).fontSize = Math.min(200, Number(draftFor(r).fontSize) + 1); applyFontSize(r)">＋</button>
                    </div>
                    <button class="font-auto-btn" type="button" :disabled="taskBusy || draftFor(r).fontSizeOverride == null" title="恢复检测到的自动字号" @click="resetFontSize(r)">自动</button>
                  </div>
                </div>
              </div>

              <label class="field font-style-row">
                <span>字体风格</span>
                <select :value="draftFor(r).fontStyle" @change="draftFor(r).fontStyle = $event.target.value; applyFontStyle(r)">
                  <option value="">自动识别</option>
                  <option value="gothic">黑体</option><option value="mincho">宋体</option>
                  <option value="rounded">圆体</option><option value="cartoon">漫画体</option>
                  <option value="handwritten">手写体</option><option value="sfx">拟声字</option>
                </select>
              </label>

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
                <button class="icon-btn" type="button" data-tip="粘贴全部样式" aria-label="粘贴全部样式" :disabled="!styleClipboard" @click="pasteStyle(r)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="m9 14 2 2 4-4"/></svg></button>
                <button class="icon-btn" type="button" data-tip="复制文本框" aria-label="复制文本框" :disabled="taskBusy" @click="duplicateRegion(r)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/></svg></button>
                <div class="spacer"></div>
                <button class="icon-btn" type="button" :data-tip="isManual(r) ? '删除此框' : '停用此框'" :aria-label="isManual(r) ? '删除此框' : '停用此框'" @click="deleteRegion(r)"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button>
              </div>
            </div>
          </article>

          <div v-if="!visibleRegions.length" class="region-empty">
            <p>{{ docLoading ? '加载中…' : '没有匹配的文本框。' }}</p>
          </div>
        </div>
      </aside>
    </div>

    <div v-if="fullTranslateConfirmationOpen" class="batch-confirm-overlay" @click.self="closeFullTranslationConfirmation">
      <section class="batch-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="batch-translation-title">
        <header class="batch-confirm-head">
          <div>
            <span class="kicker">批量任务</span>
            <h2 id="batch-translation-title">{{ batchTranslationConfirmation.title }}</h2>
          </div>
          <button class="icon-btn" type="button" aria-label="关闭确认" @click="closeFullTranslationConfirmation">✕</button>
        </header>
        <div class="batch-confirm-body">
          <p>{{ batchTranslationConfirmation.summary }}</p>
          <ul>
            <li v-for="item in batchTranslationConfirmation.items" :key="item">{{ item }}</li>
          </ul>
        </div>
        <footer class="batch-confirm-actions">
          <button class="btn btn-secondary" type="button" @click="closeFullTranslationConfirmation">{{ batchTranslationConfirmation.cancelLabel }}</button>
          <button class="btn btn-primary" type="button" @click="confirmFullTranslation">{{ batchTranslationConfirmation.confirmLabel }}</button>
        </footer>
      </section>
    </div>

    <div class="toast-stack">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="`is-${t.kind}`" @click="dismiss(t.id)">{{ t.message }}</div>
    </div>
  </div>
</template>

<style scoped>
.pane-canvas.is-space-pan { cursor: grab; }
.pane-canvas.is-space-pan .region-box,
.pane-canvas.is-space-pan .handle { cursor: grab !important; }
.canvas-hud button:disabled { opacity: .35; pointer-events: none; }
.panel-resizer { width: 6px; flex: none; cursor: col-resize; background: var(--border); touch-action: none; }
.panel-resizer:hover { background: var(--accent); }
.region-panel { flex: none; min-width: 280px; max-width: 45vw; }
.region-box .handle.nw { top: -4px; left: -4px; cursor: nwse-resize; }
.region-box .handle.n { top: -4px; left: 50%; margin-left: -4px; cursor: ns-resize; }
.region-box .handle.ne { top: -4px; right: -4px; cursor: nesw-resize; }
.region-box .handle.e { top: 50%; right: -4px; margin-top: -4px; cursor: ew-resize; }
.region-box .handle.se { bottom: -4px; right: -4px; cursor: nwse-resize; }
.region-box .handle.s { bottom: -4px; left: 50%; margin-left: -4px; cursor: ns-resize; }
.region-box .handle.sw { bottom: -4px; left: -4px; cursor: nesw-resize; }
.region-box .handle.w { top: 50%; left: -4px; margin-top: -4px; cursor: ew-resize; }
.region-box .box-source-crop,
.region-box .box-text { position: absolute; inset: 0; overflow: hidden; pointer-events: none; }
.region-box .box-source-crop img { position: absolute; max-width: none; pointer-events: none; user-select: none; -webkit-user-drag: none; }
.region-box.is-disabled { background: transparent; border-style: dashed; }
.font-size-control { display: flex; align-items: stretch; gap: 5px; }
.font-size-control .stepper { flex: 1; min-width: 0; }
.font-auto-btn { min-width: 36px; padding: 0 7px; border: 1px solid var(--border); border-radius: var(--r-s); background: var(--bg-inset); color: var(--text-2); font-size: var(--fs-micro); }
.font-auto-btn:hover:not(:disabled) { border-color: var(--accent-border); color: var(--accent); }
.font-auto-btn:disabled { opacity: .45; cursor: not-allowed; }
.batch-controls { display: flex; flex-wrap: wrap; gap: 5px; padding: 10px; border-bottom: 1px solid var(--border); }
.batch-controls input { width: 90px; }
.batch-controls select { max-width: 100%; }
.stage-bar { height: auto; min-height: 46px; flex-wrap: wrap; padding-block: 7px; }
.stage-bar-group { flex-wrap: wrap; }
.topbar { height: auto; min-height: 56px; flex-wrap: wrap; }
.topbar-actions { flex-wrap: wrap; }
.region-card-head .is-warn { max-width: 140px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.region-box.is-multi-selected { border-color: var(--region-active); background: var(--region-active-fill); }
.region-marquee { position: absolute; z-index: 7; border: 1px dashed var(--accent); background: var(--accent-dim); pointer-events: none; }
.region-card:focus-visible, .panel-resizer:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
.preview-note { margin: 0; padding: 5px 12px; font-size: 11px; color: var(--text-2); background: var(--bg-panel); }
.batch-confirm-overlay { position: fixed; inset: 0; z-index: 30; display: grid; place-items: center; padding: 20px; background: rgba(17, 24, 39, .38); }
.batch-confirm-modal { width: min(520px, 100%); border: 1px solid var(--border-strong); border-radius: 12px; background: var(--bg-elevated); box-shadow: var(--shadow-pop); }
.batch-confirm-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; padding: 18px 20px 12px; border-bottom: 1px solid var(--border); }
.batch-confirm-head h2 { margin: 4px 0 0; font-size: 18px; }
.batch-confirm-body { padding: 16px 20px 6px; color: var(--text-2); line-height: 1.55; }
.batch-confirm-body p { margin: 0 0 10px; }
.batch-confirm-body ul { margin: 0; padding-left: 20px; }
.batch-confirm-actions { display: flex; justify-content: flex-end; gap: 8px; padding: 14px 20px 18px; }
</style>
