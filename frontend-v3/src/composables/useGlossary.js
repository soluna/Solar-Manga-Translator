import { computed, ref } from 'vue'
import { apiGetJson, apiPostJson, apiPutJson } from '../api/client.js'

const copy = value => JSON.parse(JSON.stringify(value))
const url = id => `/api/projects/${encodeURIComponent(id)}/glossary`
const defaultApi = {
  load: id => apiGetJson(`${url(id)}?include_occurrences=true`, '读取名词库失败'),
  save: (id, body) => apiPutJson(url(id), body, '保存名词库失败'),
  preview: (id, body) => apiPostJson(`${url(id)}/preview`, body, '生成替换预览失败'),
  apply: (id, body) => apiPostJson(`${url(id)}/apply`, body, '应用名词库失败'),
  extract: (id, body) => apiPostJson(`${url(id)}/extract`, body, 'AI 提取失败'),
}
function readEntries(payload) {
  if (!Array.isArray(payload?.glossary?.entries)) throw new Error('名词库响应格式不正确，已有草稿已保留。')
  return copy(payload.glossary.entries)
}
function requestEntries(entries) {
  return entries.map(entry => Object.fromEntries(
    ['id', 'source', 'translation', 'category', 'replacement', 'note', 'source_kind']
      .map(key => [key, String(entry[key] || '').trim()]),
  ))
}
function validate(entries) {
  const seen = new Set()
  for (const [index, entry] of entries.entries()) {
    if (!entry.source || !entry.translation) throw new Error(`第 ${index + 1} 条词条需要填写原文和译名。`)
    if (seen.has(entry.source)) throw new Error(`原文「${entry.source}」重复，请合并后再保存。`)
    seen.add(entry.source)
  }
}

/** Owns one glossary draft and binds application to its last successful preview. */
export function useGlossary(api = defaultApi) {
  const entries = ref([]), candidates = ref([]), previewPayload = ref(null)
  const busy = ref(''), error = ref(''), loaded = ref(false)
  const saved = ref('[]'), previewSnapshot = ref('')
  let projectId = '', epoch = 0, previewDraft = null
  const fingerprint = () => JSON.stringify(requestEntries(entries.value))
  const dirty = computed(() => fingerprint() !== saved.value)
  const canApply = computed(() => loaded.value && !busy.value && Boolean(previewPayload.value)
    && fingerprint() === previewSnapshot.value)

  function accept(payload, sent = null) {
    const next = readEntries(payload)
    const oldEvidence = new Map(entries.value.map(entry => [entry.id, entry]))
    const evidenceLoaded = payload.glossary.occurrences_loaded === true
    for (const entry of next) {
      const previous = oldEvidence.get(entry.id)
      if (!evidenceLoaded && previous?.source === entry.source) {
        entry.occurrences = previous.occurrences || []
        entry.occurrence_count = previous.occurrence_count
      }
    }
    if (!sent || fingerprint() === sent) entries.value = next
    saved.value = JSON.stringify(requestEntries(next))
    return payload
  }
  async function load(id) {
    const expected = ++epoch
    if (projectId !== String(id || '')) { entries.value = []; saved.value = '[]' }
    projectId = String(id || '')
    if (!projectId) throw new Error('缺少项目 ID')
    loaded.value = false
    busy.value = 'load'
    error.value = ''
    previewPayload.value = null
    candidates.value = []
    try {
      const payload = await api.load(projectId)
      if (epoch !== expected) return null
      accept(payload)
      loaded.value = true
      return payload
    } catch (failure) {
      if (epoch !== expected) return null
      error.value = failure.message
      throw failure
    } finally {
      if (epoch === expected) busy.value = ''
    }
  }
  async function run(action, work) {
    if (!loaded.value) throw new Error('名词库尚未成功加载，请先重试读取。')
    if (busy.value) throw new Error('名词库操作进行中，请稍候。')
    const identity = { id: projectId, epoch }
    busy.value = action
    error.value = ''
    try { return await work(identity) }
    catch (failure) {
      if (epoch === identity.epoch) error.value = failure.message
      throw failure
    } finally {
      if (epoch === identity.epoch) busy.value = ''
    }
  }
  function save() {
    return run('save', async identity => {
      const body = { entries: requestEntries(entries.value) }
      validate(body.entries)
      const sent = JSON.stringify(body.entries)
      const payload = await api.save(identity.id, body)
      if (epoch !== identity.epoch) return null
      previewPayload.value = null
      return accept(payload, sent)
    })
  }
  function preview() {
    return run('preview', async identity => {
      const body = { entries: requestEntries(entries.value) }
      validate(body.entries)
      const sent = JSON.stringify(body.entries)
      const payload = await api.preview(identity.id, copy(body))
      if (epoch !== identity.epoch) return null
      if (!Array.isArray(payload?.changes)) throw new Error('替换预览响应格式不正确。')
      previewDraft = body
      previewSnapshot.value = sent
      previewPayload.value = payload
      return payload
    })
  }
  function apply() {
    if (!canApply.value) return Promise.reject(new Error('请先预览当前词条，再应用替换。'))
    const body = copy(previewDraft)
    const sent = previewSnapshot.value
    return run('apply', async identity => {
      const payload = await api.apply(identity.id, body)
      if (epoch !== identity.epoch) return null
      accept(payload, sent)
      previewPayload.value = null
      return payload
    })
  }
  function extract(config = {}) {
    return run('extract', async identity => {
      const payload = await api.extract(identity.id, { config, preview_only: true })
      if (epoch !== identity.epoch) return null
      const existing = new Set(entries.value.map(entry => entry.source.trim()))
      candidates.value = readEntries(payload).filter(entry => !existing.has(entry.source))
      return payload
    })
  }
  function adopt(candidate) {
    if (busy.value || entries.value.some(entry => entry.source.trim() === candidate.source.trim())) return
    entries.value.push(copy(candidate))
    candidates.value = candidates.value.filter(entry => entry.id !== candidate.id)
  }
  return { entries, candidates, previewPayload, busy, error, loaded, dirty, canApply,
    load, save, preview, apply, extract, adopt }
}
