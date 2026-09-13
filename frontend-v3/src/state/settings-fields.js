const choices = entries => entries.map(([value, label]) => ({ value, label }))
export const PROVIDERS = choices([['gemini', 'Gemini'], ['doubao-ark', '豆包 Ark'], ['openai-compatible', 'OpenAI Compatible']])
export const LANGUAGES = choices([['CHS', '简体中文'], ['CHT', '繁体中文'], ['ENG', '英语'], ['JPN', '日语'], ['KOR', '韩语']])
export const STYLE_NAMES = { gothic: '黑体 / 对话', mincho: '宋体 / 旁白', rounded: '圆体', cartoon: '漫画体', handwritten: '手写体', sfx: '拟声字' }

// Only user-configurable fields belong in the form. Runtime paths, per-region
// overrides, derived font maps and configured_secrets are read-only metadata.
export const SETTINGS_FIELDS = {
  translator: { label: '翻译服务', options: PROVIDERS },
  target_lang: { label: '目标语言', options: LANGUAGES },
  api_key: { label: '翻译服务 API Key', type: 'secret' },
  translator_model: { label: '豆包模型 / 接入点', provider: 'doubao-ark' },
  openai_base_url: { label: 'API Base URL', provider: 'openai-compatible', wide: true },
  openai_model: { label: '模型名称', provider: 'openai-compatible' },
  use_gpu: { label: '使用 GPU', type: 'boolean' },
  pause_after_detection: { label: '识别完成后暂停，先检查原文', type: 'boolean' },
  mask_cleanup_strength: { label: '擦字边缘清理强度', options: choices([['standard', '标准'], ['clean', '加强清理'], ['aggressive', '强力清理']]) },
  advanced_text_repair: { label: '识别文字修复', options: choices([['auto', '自动'], ['off', '关闭'], ['force', '始终执行']]) },
  font_key: { label: '默认字体', type: 'font' },
  ...Object.fromEntries(Object.entries(STYLE_NAMES).map(([key, label]) => [`style_font_${key}_key`, { label: `${label}字体`, type: 'font' }])),
  render_alignment: { label: '文字对齐', options: choices([['left', '左对齐'], ['center', '居中'], ['right', '右对齐']]) },
  render_letter_spacing: { label: '字距倍率', type: 'number', min: 0.85, max: 1.35, step: 0.01 },
  rerender_output_format: { label: '输出格式', options: choices([['source', '保留原格式'], ['png', 'PNG']]) },
  image_cleanup_mode: { label: '在线图像清理', options: choices([['off', '关闭'], ['gemini-image', 'Gemini Image'], ['seedream-image', 'Seedream Image']]) },
  image_cleanup_model: { label: '图像清理模型' },
  image_cleanup_api_key: { label: '图像清理 API Key', type: 'secret' },
  advanced_erase_base_url: { label: '在线擦除 API 地址', wide: true },
  advanced_erase_model: { label: '在线擦除模型' },
  advanced_erase_api_key: { label: '在线擦除 API Key', type: 'secret' },
  advanced_erase_timeout_seconds: { label: '在线擦除超时（秒）', type: 'number' },
  advanced_erase_selection_prompt: { label: '在线局部擦除提示词', type: 'textarea' },
  export_mask_debug: { label: '保留擦字调试图', type: 'boolean' },
}

export const SETTINGS_GROUPS = [
  { id: 'translation', label: '翻译服务', keys: ['translator', 'target_lang', 'api_key', 'translator_model', 'openai_base_url', 'openai_model'] },
  { id: 'detect', label: '检测与识别', keys: ['use_gpu', 'pause_after_detection', 'mask_cleanup_strength', 'advanced_text_repair'] },
  { id: 'render', label: '渲染与嵌字', keys: ['font_key', ...Object.keys(STYLE_NAMES).map(key => `style_font_${key}_key`), 'render_alignment', 'render_letter_spacing', 'rerender_output_format'] },
  { id: 'cleanup', label: '图像清理与擦除', keys: ['image_cleanup_mode', 'image_cleanup_model', 'image_cleanup_api_key', 'advanced_erase_base_url', 'advanced_erase_model', 'advanced_erase_api_key', 'advanced_erase_timeout_seconds', 'advanced_erase_selection_prompt', 'export_mask_debug'] },
  { id: 'fonts', label: '字体' }, { id: 'advanced', label: '高级' },
]

export function editableSettings(settings) {
  return Object.fromEntries(Object.keys(SETTINGS_FIELDS).filter(key => Object.hasOwn(settings, key)).map(key => [key, settings[key]]))
}

export function visibleSettingKeys(keys, draft) {
  return keys.filter(key => Object.hasOwn(draft, key) && (!SETTINGS_FIELDS[key].provider || SETTINGS_FIELDS[key].provider === draft.translator))
}
