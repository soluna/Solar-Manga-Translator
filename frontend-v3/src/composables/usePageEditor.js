import { computed, reactive, ref } from 'vue'
import { apiFetch, apiGetJson, readApiJson } from '../api/client.js'
import { assertSupportedPageCommands, projectPageDocument } from '../state/review-document.js'
import { createSerialProjectCommandQueue } from './useReviewCommandQueue.js'
import { pageCommandHistory } from '../state/page-command-history.js'

const copy = value => JSON.parse(JSON.stringify(value))
const equal = (left, right) => JSON.stringify(left) === JSON.stringify(right)
function reviewStateFrom(payload, page) {
  const candidate = payload?.review_state
    ?? page?.review_state
    ?? page?.metadata?.review
  if (candidate && typeof candidate === 'object') {
    const status = ['reviewed', 'unreviewed'].includes(String(candidate.status || '').toLowerCase())
      ? String(candidate.status).toLowerCase()
      : 'unreviewed'
    return { ...copy(candidate), status }
  }
  const status = String(payload?.review_status ?? page?.review_status ?? 'unreviewed').toLowerCase()
  return { status: status === 'reviewed' ? 'reviewed' : 'unreviewed' }
}
const endpoint = (project, page) => `/api/pages/${encodeURIComponent(project)}/${encodeURIComponent(page)}`
const defaultApi = {
  load: (project, page) => apiGetJson(`${endpoint(project, page)}/document`, '加载页面失败'),
  async command(project, page, body) {
    const response = await apiFetch(`${endpoint(project, page)}/commands`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    })
    const payload = await readApiJson(response, '保存页面失败')
    if (!response.ok) {
      const detail = payload?.detail
      throw Object.assign(new Error(typeof detail === 'string' ? detail : detail?.message || '保存页面失败'), {
        status: response.status, document: detail?.document,
      })
    }
    return payload
  },
}

const color = values => `#${values.map(value => Math.max(0, Math.min(255, Number(value) || 0)).toString(16).padStart(2, '0')).join('')}`
const triplet = value => {
  if (!/^#[0-9a-f]{6}$/i.test(value)) throw new Error('颜色必须是有效的六位色值。')
  return [1, 3, 5].map(index => parseInt(value.slice(index, index + 2), 16))
}

function draftFrom(region) {
  return {
    sourceText: region.source_text,
    translation: region.current_translation, fontKey: region.font_key_override,
    fontSize: region.font_size, fontSizeOverride: region.font_size_override, fontStyle: region.override_style,
    rotation: region.rotation, strokeWidth: region.stroke_width,
    letterSpacing: region.letter_spacing, lineSpacing: region.line_spacing,
    fgColor: color(region.fg_color), bgColor: color(region.bg_color),
    preserveBackground: region.preserve_background, enabled: !region.disabled,
    direction: region.direction, directionIntent: null, directionUndo: null,
    keepOriginal: region.keep_original,
  }
}

const ADVANCED = { rotation: 'rotation', strokeWidth: 'stroke_width', letterSpacing: 'letter_spacing',
  lineSpacing: 'line_spacing', fgColor: 'fg_color', bgColor: 'bg_color', preserveBackground: 'preserve_background' }

function draftCommands(regionId, changes) {
  const commands = [], advanced = {}
  for (const [field, value] of Object.entries(changes)) {
    let command
    if (field === 'sourceText') command = { type: 'update_source_text', text: value }
    if (field === 'translation') command = { type: 'update_translation', text: value }
    if (field === 'fontKey') command = { type: 'update_region_font', font_key: value }
    if (field === 'fontStyle') command = { type: 'update_font_style', style: value }
    if (field === 'fontSize' || field === 'fontSizeOverride') {
      if (value == null || value === '') command = { type: 'update_font_size', font_size: null }
      else {
        if (!Number.isFinite(Number(value)) || Number(value) < 8) throw new Error('字号必须是至少 8 的数字。')
        command = { type: 'update_font_size', font_size: Math.round(Number(value)) }
      }
    }
    if (field === 'direction' || field === 'directionIntent') {
      command = { type: 'update_text_direction', direction: value }
    }
    if (field === 'enabled') command = { type: value ? 'restore_region' : 'disable_region' }
    if (field === 'keepOriginal') command = { type: 'set_keep_original', enabled: value }
    if (ADVANCED[field]) {
      if (field === 'fgColor' || field === 'bgColor') advanced[ADVANCED[field]] = triplet(value)
      else if (field === 'preserveBackground') advanced.preserve_background = Boolean(value)
      else {
        if (!Number.isFinite(Number(value))) throw new Error('样式参数必须是有效数字。')
        advanced[ADVANCED[field]] = Number(value)
      }
    }
    if (command) commands.push({ ...command, region_id: regionId })
  }
  if (Object.keys(advanced).length) commands.push({ type: 'update_region_style', region_id: regionId, ...advanced })
  return commands
}

/** Owns saved documents and unsaved drafts by project/page, across asynchronous UI work. */
export function usePageEditor({ api = defaultApi, getConfig = () => ({}), onResponse = () => {} } = {}) {
  const pages = reactive({}), activeKey = ref(''), pending = ref(0)
  const queue = createSerialProjectCommandQueue({ onChange: ({ pendingByProject }) => {
    pending.value = Object.values(pendingByProject).reduce((sum, count) => sum + count, 0)
  } })
  const current = computed(() => pages[activeKey.value] || null)
  const document = computed(() => current.value?.document || null)
  const dirtyState = state => Object.entries(state?.drafts || {}).some(([id, draft]) => !equal(draft, state.baselines[id]))
  const dirty = computed(() => dirtyState(current.value))

  function pageState(projectId, pageId) {
    if (!projectId || !pageId) throw new Error('缺少项目或页面 ID。')
    const key = JSON.stringify([projectId, pageId])
    pages[key] ||= { projectId, pageId, document: null, drafts: {}, baselines: {}, loading: false,
      error: '', savedAt: '', loadEpoch: 0, undo: [], redo: [], reviewState: { status: 'unreviewed' }, reviewStatus: 'unreviewed' }
    return { key, state: pages[key] }
  }

  function accept(state, payload, sent = {}) {
    const next = projectPageDocument(payload)
    if (!next || next.page_id !== state.pageId) throw new Error('返回的页面文档与当前操作不一致。')
    if (state.document && next.revision < state.document.revision) return
    const valid = new Set(next.regions.map(region => region.id))
    for (const region of next.regions) {
      const baseline = draftFrom(region), previous = state.baselines[region.id] || {}
      const draft = state.drafts[region.id] || {}
      for (const [field, value] of Object.entries(baseline)) {
        const acknowledged = Object.hasOwn(sent[region.id] || {}, field)
          && equal(draft[field], sent[region.id][field])
        if (!(field in draft) || equal(draft[field], previous[field]) || acknowledged) draft[field] = value
      }
      state.drafts[region.id] = draft
      state.baselines[region.id] = baseline
    }
    for (const id of Object.keys(state.drafts)) {
      if (!valid.has(id) && equal(state.drafts[id], state.baselines[id])) {
        delete state.drafts[id]
        delete state.baselines[id]
      }
    }
    state.document = next
    state.reviewState = reviewStateFrom(payload, next)
    state.reviewStatus = state.reviewState.status
  }

  async function load(projectId, pageId) {
    const { key, state } = pageState(projectId, pageId)
    activeKey.value = key
    const epoch = ++state.loadEpoch
    state.loading = true
    try {
      const response = await api.load(projectId, pageId)
      if (epoch === state.loadEpoch) {
        if (state.document && response.document?.metadata?.revision > state.document.revision) {
          state.undo = []
          state.redo = []
        }
        accept(state, response)
        state.error = ''
      }
      return state.document
    } catch (error) {
      if (epoch === state.loadEpoch) state.error = error.message
      throw error
    } finally { if (epoch === state.loadEpoch) state.loading = false }
  }

  function draftFor(region) {
    const id = typeof region === 'string' ? region : region?.id
    const draft = current.value?.drafts[id]
    if (!draft) throw new Error('目标文本区域不存在。')
    return draft
  }

  function enqueue(state, build, { history = true, label } = {}) {
    if (!state?.document) return Promise.reject(new Error('页面尚未加载。'))
    return queue.enqueue(state.projectId, state.pageId, async () => {
      try {
        const { commands, sent = {}, directionUndoByRegion = {} } = build()
        if (!commands.length) return null
        assertSupportedPageCommands(commands)
        const before = copy(state.document.canonical)
        const response = await api.command(state.projectId, state.pageId, {
          commands, expected_page_revision: state.document.revision, config: copy(getConfig(state.projectId) || {}),
        })
        accept(state, response, sent)
        for (const command of commands) {
          if (command.type === 'delete_manual_region') {
            delete state.drafts[command.region_id]
            delete state.baselines[command.region_id]
          }
        }
        if (history) {
          const entry = pageCommandHistory(commands, before, response, label, { directionUndoByRegion })
          state.undo = entry ? [...state.undo, entry].slice(-50) : []
          state.redo = []
        }
        onResponse(response, { projectId: state.projectId, pageId: state.pageId })
        state.error = ''
        state.savedAt = new Date().toISOString()
        return response
      } catch (error) {
        if (error.status === 409 && error.document) {
          accept(state, error.document)
          state.undo = []
          state.redo = []
        }
        state.error = error.message || '保存失败，修改已保留。'
        throw error
      }
    })
  }

  function saveStateDraft(state, regionId, fields) {
    const draft = state?.drafts[regionId]
    if (!draft) return Promise.reject(new Error('目标文本区域不存在。'))
    const changes = Object.fromEntries(Object.entries(draft).filter(([field, value]) =>
      (!fields || fields.includes(field)) && !equal(value, state.baselines[regionId]?.[field])))
    const sent = copy(changes)
    return enqueue(state, () => {
      if (!state.document.regions.some(region => region.id === regionId)) throw new Error('文本区域已被删除，未保存的草稿仍保留。')
      const remaining = Object.fromEntries(Object.entries(sent).filter(([field, value]) => !equal(value, state.baselines[regionId]?.[field])))
      const directionUndoByRegion = Object.hasOwn(sent, 'directionIntent') && Object.hasOwn(sent, 'directionUndo')
        ? { [regionId]: sent.directionUndo }
        : {}
      return { commands: draftCommands(regionId, remaining), sent: { [regionId]: sent }, directionUndoByRegion }
    })
  }

  function saveDraft(regionId, fields) { return saveStateDraft(current.value, regionId, fields) }

  function saveDirectionIntent(regionId, direction, undoDirection = 'auto') {
    const state = current.value, draft = state?.drafts[regionId]
    if (!draft) return Promise.reject(new Error('目标文本区域不存在。'))
    draft.direction = direction
    draft.directionIntent = direction
    draft.directionUndo = undoDirection
    return saveStateDraft(state, regionId, ['directionIntent', 'directionUndo'])
  }

  async function flushState(state) {
    if (!state) return
    // Keep the originating page even if a caller selects another page while waiting.
    await queue.waitForProject(state.projectId)
    while (dirtyState(state)) {
      for (const id of Object.keys(state.drafts)) {
        if (!equal(state.drafts[id], state.baselines[id])) await saveStateDraft(state, id)
      }
    }
  }

  function flush() { return flushState(current.value) }

  async function execute(commands, options) {
    const state = current.value, submitted = copy(commands)
    await flushState(state)
    return enqueue(state, () => ({ commands: submitted }), options)
  }

  let historyAction = false
  async function moveHistory(direction) {
    if (historyAction) return null
    historyAction = true
    const state = current.value
    try {
      await flush()
      const stack = state?.[direction], entry = stack?.at(-1)
      if (!entry) return null
      const response = await enqueue(state, () => ({ commands: copy(entry[direction]) }), { history: false })
      stack.pop()
      state[direction === 'undo' ? 'redo' : 'undo'].push(entry)
      return response
    } finally { historyAction = false }
  }

  return { document, dirty, pending, draftFor, load, saveDraft, saveDirectionIntent, execute, flush,
    undo: () => moveHistory('undo'), redo: () => moveHistory('redo'),
    canUndo: computed(() => Boolean(current.value?.undo.length) && !pending.value),
    canRedo: computed(() => Boolean(current.value?.redo.length) && !pending.value),
    hasUnsaved: computed(() => pending.value > 0 || Object.values(pages).some(dirtyState)),
    loading: computed(() => current.value?.loading || false),
    error: computed(() => current.value?.error || ''),
    savedAt: computed(() => current.value?.savedAt || ''),
    reviewState: computed(() => current.value?.reviewState || { status: 'unreviewed' }),
    reviewStatus: computed(() => current.value?.reviewStatus || 'unreviewed'),
  }
}
