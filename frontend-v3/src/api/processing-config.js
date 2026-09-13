import { apiGetJson } from './client.js'
import { editableSettings } from '../state/settings-fields.js'

/** New processing uses current app preferences without replacing page overrides. */
export async function loadProcessingConfig(projectConfig = {}, getSettings = () => apiGetJson('/api/app/settings', '读取处理设置失败')) {
  const response = await getSettings()
  if (!response?.settings || typeof response.settings !== 'object') throw new Error('后端没有返回有效的处理设置。')
  const preferences = editableSettings(response.settings)
  return { ...projectConfig, ...preferences, selected_translator: preferences.translator || projectConfig.selected_translator }
}
