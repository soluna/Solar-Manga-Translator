import assert from 'node:assert/strict'
import { test } from 'node:test'
import { useSettings } from '../src/composables/useSettings.js'
import { loadProcessingConfig } from '../src/api/processing-config.js'

const saved = () => ({ translator: 'gemini', target_lang: 'CHS', api_key: '', openai_base_url: '',
  openai_model: '', use_gpu: true, configured_secrets: { api_key: true },
  translation_region_overrides: { private: 'not an editable setting' }, style_font_keys: { gothic: 'system/a.otf' } })
const deferred = () => { let resolve; return { promise: new Promise(r => { resolve = r }), resolve } }

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

test('new processing uses updated settings without replacing saved page overrides', async () => {
  const result = await loadProcessingConfig({ translator: 'gemini', translation_region_overrides: { 'region-a': 'edited text' } },
    async () => ({ settings: { ...saved(), translator: 'openai-compatible', openai_model: 'new-model' } }))
  assert.equal(result.translator, 'openai-compatible')
  assert.equal(result.selected_translator, 'openai-compatible')
  assert.equal(result.openai_model, 'new-model')
  assert.deepEqual(result.translation_region_overrides, { 'region-a': 'edited text' })
})
