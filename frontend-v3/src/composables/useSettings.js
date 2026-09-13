import { computed, reactive, ref } from 'vue'
import { apiGetJson, apiPatchJson, apiPostJson } from '../api/client.js'
import { editableSettings, SETTINGS_FIELDS } from '../state/settings-fields.js'

const defaultApi = {
  load: () => apiGetJson('/api/app/settings', '读取设置失败'),
  save: body => apiPatchJson('/api/app/settings', body, '保存设置失败'),
  validate: body => apiPostJson('/api/app/settings/validate', body, '验证翻译服务失败'),
}
const copy = value => JSON.parse(JSON.stringify(value))

export function useSettings(api = defaultApi) {
  const settings = ref({}), baseline = ref({}), draft = reactive({})
  const loading = ref(false), saving = ref(false), validating = ref(false), error = ref('')
  let savePromise = null, loaded = false
  const changedKeys = computed(() => Object.keys(draft).filter(key => draft[key] !== baseline.value[key]))
  const dirty = computed(() => changedKeys.value.length > 0)

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
    if (!dirty.value) return Promise.resolve()
    const sent = copy(draft)
    let body
    try { body = payload(changedKeys.value) } catch (err) { return Promise.reject(err) }
    saving.value = true
    savePromise = (async () => {
      try { accept(await api.save(body), sent); error.value = '' }
      catch (err) { error.value = err.message; throw err }
      finally { saving.value = false; savePromise = null }
    })()
    return savePromise
  }

  async function flush() {
    if (savePromise) await savePromise
    while (dirty.value) await save()
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

  return { settings, draft, loading, saving, validating, error, changedKeys, dirty,
    load, save, flush, validate, reset,
    secretConfigured: key => Boolean(settings.value.configured_secrets?.[key]),
  }
}
