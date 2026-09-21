import { computed, reactive, ref } from 'vue'
import { apiGetJson, apiPatchJson, apiPostJson } from '../api/client.js'
import { editableSettings, SETTINGS_FIELDS } from '../state/settings-fields.js'

const defaultApi = {
  load: () => apiGetJson('/api/app/settings', '读取设置失败'),
  save: body => apiPatchJson('/api/app/settings', body, '保存设置失败'),
  clearSecrets: keys => apiPatchJson('/api/app/settings', { _clear_secrets: keys }, '清除密钥失败'),
  validate: body => apiPostJson('/api/app/settings/validate', body, '验证翻译服务失败'),
}
const copy = value => JSON.parse(JSON.stringify(value))

export function useSettings(api = defaultApi) {
  api = { ...defaultApi, ...api }
  const settings = ref({}), baseline = ref({}), draft = reactive({})
  const loading = ref(false), saving = ref(false), clearing = ref(false), validating = ref(false), error = ref('')
  let savePromise = null, clearPromise = null, mutationTail = Promise.resolve(), mutationTailKind = '', loaded = false
  const changedKeys = computed(() => Object.keys(draft).filter(key => draft[key] !== baseline.value[key]))
  const dirty = computed(() => changedKeys.value.length > 0)

  // All writes share one tail. Keep failures visible to flush/route guards,
  // while letting the next explicit write retry after the failed operation.
  function enqueueMutation(task, kind) {
    const operation = mutationTail.then(task, task)
    mutationTail = operation
    mutationTailKind = kind
    return operation
  }

  function accept(payload, sent = {}) {
    if (!payload?.settings || typeof payload.settings !== 'object') throw new Error('后端没有返回有效的设置。')
    const next = editableSettings(payload.settings)
    for (const [key, value] of Object.entries(next)) {
      if (!Object.hasOwn(draft, key) || draft[key] === baseline.value[key]
        || (Object.hasOwn(sent, key) && draft[key] === sent[key])) draft[key] = value
    }
    baseline.value = copy(next)
    settings.value = payload.settings
    loaded = true
  }

  async function load() {
    loading.value = true
    error.value = ''
    try { accept(await api.load()) }
    catch (err) { error.value = err.message; throw err }
    finally { loading.value = false }
  }

  function payload(keys) {
    if (!loaded) throw new Error('请先加载设置。')
    const body = {}
    for (const key of keys) {
      const field = SETTINGS_FIELDS[key], value = draft[key]
      if (!field) continue
      // Empty redacted fields mean keep the stored secret, never erase it.
      if (field.type === 'secret' && !String(value || '').trim()) continue
      if (field.type === 'number' && !Number.isFinite(Number(value))) throw new Error(`${field.label}必须是有效数字。`)
      body[key] = field.type === 'number' ? Number(value) : value
    }
    if (body.translator) body.selected_translator = body.translator
    return body
  }

  function save() {
    if (savePromise) return savePromise
    if (!dirty.value) return mutationTail
    const sent = copy(draft)
    let body
    try { body = payload(changedKeys.value) } catch (err) { return Promise.reject(err) }
    const operation = enqueueMutation(async () => {
      saving.value = true
      try { accept(await api.save(body), sent); error.value = '' }
      catch (err) { error.value = err.message; throw err }
      finally { saving.value = false }
    }, 'save')
    savePromise = operation
    operation.then(
      () => { if (savePromise === operation) savePromise = null },
      () => { if (savePromise === operation) savePromise = null },
    )
    return operation
  }

  async function clearSecrets(keys) {
    if (!loaded) throw new Error('请先加载设置。')
    const requested = [...new Set((Array.isArray(keys) ? keys : [keys])
      .filter(key => SETTINGS_FIELDS[key]?.type === 'secret'))]
    if (!requested.length) throw new Error('没有可清除的密钥。')
    // Capture the values at call time. If the user types a replacement while a
    // previous save/clear is pending, its draft must remain available for the
    // next save after this clear is acknowledged.
    const sent = Object.fromEntries(requested.map(key => [key, draft[key]]))
    const operation = enqueueMutation(async () => {
      clearing.value = true
      try {
        const payload = await api.clearSecrets(requested)
        accept(payload, sent)
        error.value = ''
        return payload
      } catch (err) {
        error.value = err.message
        throw err
      } finally {
        clearing.value = false
      }
    }, 'clear')
    clearPromise = operation
    operation.then(
      () => { if (clearPromise === operation) clearPromise = null },
      () => { if (clearPromise === operation) clearPromise = null },
    )
    return operation
  }

  async function flush({ retryFailedSave = false } = {}) {
    let attempts = 0
    while (true) {
      try {
        await mutationTail
      } catch (err) {
        // A user action may retry the failed settings save. Route-leave flushes
        // stay strict, and a failed secret clear is never converted to a save.
        if (!retryFailedSave || mutationTailKind !== 'save' || !dirty.value) throw err
      }
      if (!dirty.value) return
      await save()
      await mutationTail
      // Edits made while the request was in flight are intentionally retained;
      // loop until the current draft has been acknowledged as well.
      attempts += 1
      if (attempts > 100) throw new Error('设置仍有未保存改动，请稍后重试。')
    }
  }

  async function validate() {
    if (validating.value) return null
    validating.value = true
    try {
      const result = await api.validate(payload(Object.keys(draft)))
      if (typeof result?.ok !== 'boolean') throw new Error('翻译服务返回的验证结果无效。')
      return result
    } finally { validating.value = false }
  }

  function reset() {
    for (const key of Object.keys(draft)) delete draft[key]
    Object.assign(draft, copy(baseline.value))
  }

  return { settings, draft, loading, saving, clearing, validating, error, changedKeys, dirty,
    load, save, flush, validate, clearSecrets, reset,
    secretConfigured: key => Boolean(settings.value.configured_secrets?.[key]),
  }
}
