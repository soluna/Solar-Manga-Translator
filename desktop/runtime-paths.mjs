import { join, win32 } from 'node:path'

export const APP_DATA_DIR_NAME = 'Solar-Manga-Translator'

export function resolveApplicationDataDir({
  platform = process.platform,
  env = process.env,
  fallbackUserData = '',
} = {}) {
  const fallback = String(fallbackUserData || '').trim()
  if (platform === 'win32') {
    const localAppData = String(env.LOCALAPPDATA || '').trim()
    if (localAppData) {
      return win32.join(localAppData, APP_DATA_DIR_NAME)
    }
  }
  if (!fallback) {
    throw new Error('无法确定应用数据目录。')
  }
  return join(fallback)
}
