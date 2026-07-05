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
