import assert from 'node:assert/strict'
import { test } from 'node:test'
import { useSettings } from '../src/composables/useSettings.js'
import { PROVIDERS, visibleSettingKeys } from '../src/state/settings-fields.js'
import { loadProcessingConfig } from '../src/api/processing-config.js'

const saved = () => ({ translator: 'gemini', target_lang: 'CHS', api_key: '', openai_base_url: '',
  openai_model: '', use_gpu: true, configured_secrets: { api_key: true },
  translation_region_overrides: { private: 'not an editable setting' }, style_font_keys: { gothic: 'system/a.otf' } })
const savedWithSecrets = () => ({ ...saved(), image_cleanup_api_key: '', advanced_erase_api_key: '',
  configured_secrets: { api_key: true, image_cleanup_api_key: true, advanced_erase_api_key: true } })
const deferred = () => {
  let resolve, reject
  return {
    promise: new Promise((r, j) => { resolve = r; reject = j }),
    resolve,
    reject,
  }
}

test('OpenCode Go exposes a model field and hides the compatible base URL', () => {
  const keys = visibleSettingKeys(['translator', 'api_key', 'openai_base_url', 'openai_model'], { translator: 'opencode-go', api_key: '', openai_base_url: '', openai_model: '' })
  assert.deepEqual(keys, ['translator', 'api_key', 'openai_model'])
  assert.ok(PROVIDERS.some(provider => provider.value === 'opencode-go'))
})

test('settings validate the current draft with canonical fields and preserve redacted secrets', async () => {
  let validation, patch
  const state = useSettings({ load: async () => ({ settings: saved() }),
    validate: async body => { validation = body; return { ok: true } },
    save: async body => { patch = body; return { settings: { ...saved(), ...body } } } })
  await state.load()
  assert.equal(state.secretConfigured('api_key'), true)
  assert.equal('translation_region_overrides' in state.draft, false)
  assert.equal('configured_secrets' in state.draft, false)
  Object.assign(state.draft, { translator: 'openai-compatible', openai_base_url: 'https://example.test/v1', openai_model: 'custom' })
  await state.validate()
  assert.equal(validation.translator, 'openai-compatible')
  assert.equal(validation.openai_model, 'custom')
  assert.equal('translation_region_overrides' in validation, false)
  assert.equal('api_key' in validation, false)
  await state.save()
  assert.equal('api_key' in patch, false)
  assert.equal(state.dirty.value, false)
})

test('saving keeps newer edits and clears only the submitted secret after acknowledgement', async () => {
  const reply = deferred()
  const state = useSettings({ load: async () => ({ settings: saved() }), save: () => reply.promise })
  await state.load()
  state.draft.api_key = 'synthetic-key-for-test'
  state.draft.target_lang = 'ENG'
  const saving = state.save()
  state.draft.target_lang = 'JPN'
  reply.resolve({ settings: { ...saved(), target_lang: 'ENG' } })
  await saving
  assert.equal(state.draft.target_lang, 'JPN')
  assert.equal(state.draft.api_key, '')
  assert.equal(state.dirty.value, true)
})

test('failed persistence blocks flushing and retains the configuration for retry', async () => {
  const state = useSettings({ load: async () => ({ settings: saved() }), save: async () => { throw new Error('offline') } })
  await state.load()
  state.draft.target_lang = 'ENG'
  await assert.rejects(state.flush(), /offline/)
  assert.equal(state.dirty.value, true)
  assert.equal(state.draft.target_lang, 'ENG')
})

test('the settings save action retries a failed write after the service recovers', async () => {
  const firstResponse = deferred()
  let firstWriteStarted
  const started = new Promise(resolve => { firstWriteStarted = resolve })
  let serviceReady = false
  const writes = []
  const state = useSettings({
    load: async () => ({ settings: saved() }),
    save: async body => {
      writes.push(body)
      if (writes.length === 1) {
        firstWriteStarted()
        await firstResponse.promise
        throw new Error('offline')
      }
      if (!serviceReady) throw new Error('offline')
      return { settings: { ...saved(), ...body } }
    },
  })
  await state.load()
  state.draft.target_lang = 'ENG'

  const saveButton = () => state.flush({ retryFailedSave: true })
  const firstClick = saveButton()
  await started
  assert.equal(writes.length, 1)
  firstResponse.resolve()
  await assert.rejects(firstClick, /offline/)
  assert.equal(writes.length, 1)
  assert.equal(state.dirty.value, true)

  serviceReady = true
  await saveButton()
  assert.equal(writes.length, 2)
  assert.equal(state.dirty.value, false)
})

test('concurrent secret clears are serialized after an in-flight save and flush persists newer edits', async () => {
  const saveReply = deferred()
  const clearReplies = [deferred(), deferred()]
  const calls = []
  let clearIndex = 0
  const state = useSettings({
    load: async () => ({ settings: savedWithSecrets() }),
    save: async body => {
      calls.push(['save', body])
      await saveReply.promise
      return { settings: { ...savedWithSecrets(), ...body } }
    },
    clearSecrets: async keys => {
      const index = clearIndex++
      calls.push(['clear', keys])
      await clearReplies[index].promise
      const configured = { ...savedWithSecrets().configured_secrets }
      for (const key of keys) configured[key] = false
      return { settings: { ...savedWithSecrets(), configured_secrets: configured } }
    },
  })
  await state.load()
  state.draft.target_lang = 'ENG'
  const saving = state.save()
  const firstClear = state.clearSecrets('api_key')
  const secondClear = state.clearSecrets('image_cleanup_api_key')
  await Promise.resolve()
  assert.deepEqual(calls.map(([kind]) => kind), ['save'])
  saveReply.resolve()
  await saving
  await Promise.resolve()
  assert.deepEqual(calls.map(([kind]) => kind), ['save', 'clear'])
  clearReplies[0].resolve()
  await firstClear
  await Promise.resolve()
  assert.deepEqual(calls.map(([kind]) => kind), ['save', 'clear', 'clear'])
  clearReplies[1].resolve()
  await secondClear
  state.draft.target_lang = 'JPN'
  await state.flush()
  assert.deepEqual(calls.map(([kind]) => kind), ['save', 'clear', 'clear', 'save'])
  assert.equal(calls.at(-1)[1].target_lang, 'JPN')
  assert.equal(state.dirty.value, false)
})

test('a failed clear keeps the draft and records the error for a later retry', async () => {
  const state = useSettings({
    load: async () => ({ settings: savedWithSecrets() }),
    clearSecrets: async () => { throw new Error('secret store unavailable') },
  })
  await state.load()
  state.draft.target_lang = 'ENG'
  await assert.rejects(state.clearSecrets(['api_key']), /secret store unavailable/)
  assert.equal(state.draft.target_lang, 'ENG')
  assert.equal(state.dirty.value, true)
  assert.equal(state.error.value, 'secret store unavailable')
})

test('a pending clear keeps flush blocked after rejection and permits an explicit retry', async () => {
  const firstReply = deferred()
  const calls = []
  let attempt = 0
  const state = useSettings({
    load: async () => ({ settings: savedWithSecrets() }),
    clearSecrets: async keys => {
      calls.push(keys)
      attempt += 1
      if (attempt === 1) {
        await firstReply.promise
        throw new Error('secret store unavailable')
      }
      const configured = { ...savedWithSecrets().configured_secrets, [keys[0]]: false }
      return { settings: { ...savedWithSecrets(), configured_secrets: configured } }
    },
  })
  await state.load()

  const pendingClear = state.clearSecrets('api_key')
  const pendingFlush = state.flush()
  const pendingActionFlush = state.flush({ retryFailedSave: true })
  await Promise.resolve()
  assert.equal(state.dirty.value, false)
  assert.deepEqual(calls, [['api_key']])

  firstReply.resolve()
  await assert.rejects(pendingClear, /secret store unavailable/)
  await assert.rejects(pendingFlush, /secret store unavailable/)
  await assert.rejects(pendingActionFlush, /secret store unavailable/)
  assert.equal(state.dirty.value, false)
  assert.equal(state.error.value, 'secret store unavailable')

  await state.clearSecrets('api_key')
  await state.flush()
  assert.deepEqual(calls, [['api_key'], ['api_key']])
  assert.equal(state.secretConfigured('api_key'), false)
})

test('new processing uses updated settings without replacing saved page overrides', async () => {
  const result = await loadProcessingConfig({ translator: 'gemini', translation_region_overrides: { 'region-a': 'edited text' } },
    async () => ({ settings: { ...saved(), translator: 'openai-compatible', openai_model: 'new-model' } }))
  assert.equal(result.translator, 'openai-compatible')
  assert.equal(result.selected_translator, 'openai-compatible')
  assert.equal(result.openai_model, 'new-model')
  assert.deepEqual(result.translation_region_overrides, { 'region-a': 'edited text' })
})
