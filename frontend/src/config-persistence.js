export const browserFallbackConfigKeys = Object.freeze([
  'translator',
  'translator_model',
  'translator_model_custom',
  'openai_base_url',
  'openai_model',
  'target_lang',
  'use_gpu',
  'font_key',
  'font_style_mode',
  'style_font_gothic_key',
  'style_font_mincho_key',
  'style_font_rounded_key',
  'style_font_cartoon_key',
  'style_font_handwritten_key',
  'style_font_sfx_key',
  'render_alignment',
  'render_letter_spacing',
  'rerender_output_format',
  'default_review_mode',
  'workspace_width_mode',
  'pause_after_detection',
  'mask_cleanup_strength',
  'export_mask_debug',
  'advanced_text_repair',
  'image_cleanup_mode',
  'image_cleanup_model',
  'advanced_erase_provider',
  'advanced_erase_base_url',
  'advanced_erase_model',
  'advanced_erase_timeout_seconds',
  'advanced_erase_selection_prompt',
])

const secretConfigKeys = new Set([
  'api_key',
  'image_cleanup_api_key',
  'advanced_erase_api_key',
])

export function mergePersistedConfigWithBrowserPreferences(
  persistedConfig,
  browserConfig,
  browserPreferenceKeys = [],
) {
  const persisted = persistedConfig && typeof persistedConfig === 'object'
    ? persistedConfig
    : {}
  const browser = browserConfig && typeof browserConfig === 'object'
    ? browserConfig
    : {}
  const merged = { ...persisted }

  for (const key of browserPreferenceKeys) {
    if (
      !Object.prototype.hasOwnProperty.call(merged, key)
      && Object.prototype.hasOwnProperty.call(browser, key)
    ) {
      merged[key] = browser[key]
    }
  }
  return merged
}

export function createBrowserConfigCache(value, keys = browserFallbackConfigKeys) {
  const source = value && typeof value === 'object' ? value : {}
  return Object.fromEntries(
    keys
      .filter((key) => !secretConfigKeys.has(key))
      .filter((key) => Object.prototype.hasOwnProperty.call(source, key))
      .map((key) => [key, source[key]])
  )
}
