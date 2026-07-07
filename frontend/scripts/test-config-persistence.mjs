import assert from 'node:assert/strict'

import {
  browserFallbackConfigKeys,
  createBrowserConfigCache,
  mergePersistedConfigWithBrowserPreferences,
} from '../src/config-persistence.js'


const backendSettings = {
  translator: 'openai-compatible',
  openai_base_url: 'https://opencode.example.com/v1',
  openai_model: 'deepseek-v4-flash',
  target_lang: 'CHS',
}

const browserDefaults = {
  translator: 'gemini',
  openai_base_url: '',
  openai_model: '',
  target_lang: 'CHT',
  workspace_width_mode: 'fixed',
}

const merged = mergePersistedConfigWithBrowserPreferences(
  backendSettings,
  browserDefaults,
  ['workspace_width_mode'],
)

assert.equal(merged.translator, 'openai-compatible')
assert.equal(merged.openai_base_url, 'https://opencode.example.com/v1')
assert.equal(merged.openai_model, 'deepseek-v4-flash')
assert.equal(merged.target_lang, 'CHS')
assert.equal(merged.workspace_width_mode, 'fixed')

const browserCache = createBrowserConfigCache({
  translator: 'openai-compatible',
  openai_base_url: 'https://opencode.example.com/v1',
  openai_model: 'deepseek-v4-flash',
  api_key: 'must-not-enter-local-storage',
  image_cleanup_api_key: 'must-not-enter-local-storage',
  advanced_erase_api_key: 'must-not-enter-local-storage',
  target_lang: 'CHS',
}, browserFallbackConfigKeys)

assert.equal(browserCache.translator, 'openai-compatible')
assert.equal(browserCache.openai_base_url, 'https://opencode.example.com/v1')
assert.equal(browserCache.openai_model, 'deepseek-v4-flash')
assert.equal(browserCache.target_lang, 'CHS')
assert.equal(Object.hasOwn(browserCache, 'api_key'), false)
assert.equal(Object.hasOwn(browserCache, 'image_cleanup_api_key'), false)
assert.equal(Object.hasOwn(browserCache, 'advanced_erase_api_key'), false)

console.log('Config persistence merge tests passed.')
