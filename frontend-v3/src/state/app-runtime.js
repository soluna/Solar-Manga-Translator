const objectValue = value => value && typeof value === 'object' ? value : {}

export const CRITICAL_GPU_STATUSES = new Set([
  'torch_unavailable',
  'torch_cpu_build',
  'cuda_initialization_failed',
  'cuda_query_failed',
  'unsupported_gpu_architecture',
])

export const EMPTY_RUNTIME = {
  desktop_mode: false,
  app_version: 'web',
  backend_base_url: '',
  data_dir: '',
  models_dir: '',
  output_dir: '',
  logs_dir: '',
  cache_dir: '',
  temp_dir: '',
  settings_path: '',
  settings_exists: false,
  font_root: '',
  font_dirs: {},
  migration: { needed: false, status: 'pending', target: {}, summary: {} },
}

export const EMPTY_DIAGNOSTICS = {
  platform: '',
  python_version: '',
  disk: { total_bytes: 0, used_bytes: 0, free_bytes: 0 },
  gpu: { available: false, device_count: 0, devices: [], cuda_version: '' },
  checks: [],
}

const EMPTY_MIGRATION = {
  needed: false,
  status: 'pending',
  target: {},
  summary: {},
  cleanup: {},
  legacy: {},
}

/** Read one of the wrapped application responses without accepting a foreign shape. */
export function unwrapPayload(payload, key) {
  const value = objectValue(payload)[key]
  return value && typeof value === 'object' ? value : {}
}

export function normalizeRuntime(payload, fallback = EMPTY_RUNTIME) {
  const previous = objectValue(fallback)
  const next = unwrapPayload(payload, 'runtime')
  return {
    ...EMPTY_RUNTIME,
    ...previous,
    ...next,
    migration: normalizeMigration(next.migration ?? previous.migration, previous.migration),
  }
}

export function normalizeDiagnostics(payload, fallback = EMPTY_DIAGNOSTICS) {
  const next = unwrapPayload(payload, 'diagnostics')
  return {
    ...EMPTY_DIAGNOSTICS,
    ...objectValue(fallback),
    ...next,
    disk: { ...EMPTY_DIAGNOSTICS.disk, ...objectValue(fallback).disk, ...objectValue(next).disk },
    gpu: { ...EMPTY_DIAGNOSTICS.gpu, ...objectValue(fallback).gpu, ...objectValue(next).gpu },
    checks: Array.isArray(next.checks) ? next.checks : (Array.isArray(objectValue(fallback).checks) ? objectValue(fallback).checks : []),
  }
}

export function normalizeMigration(payload, fallback = {}) {
  const read = value => {
    const object = objectValue(value)
    return object.migration && typeof object.migration === 'object'
      ? object.migration
      : object
  }
  const next = read(payload)
  const previous = read(fallback)
  return {
    ...EMPTY_MIGRATION,
    ...previous,
    ...next,
    cleanup: { ...objectValue(previous).cleanup, ...objectValue(next).cleanup },
    legacy: { ...objectValue(previous).legacy, ...objectValue(next).legacy },
    target: { ...objectValue(previous).target, ...objectValue(next).target },
    summary: { ...objectValue(previous).summary, ...objectValue(next).summary },
  }
}

export function normalizeRemoteDiagnostics(payload, fallback = {}) {
  const previous = objectValue(fallback)
  const next = unwrapPayload(payload, 'remote_diagnostics')
  return {
    active: next.active === undefined ? Boolean(next.authorized ?? previous.active) : Boolean(next.active),
    read_only: next.read_only === undefined ? Boolean(previous.read_only ?? true) : Boolean(next.read_only),
    port: Number(next.port ?? previous.port ?? 0),
    urls: Array.isArray(next.urls) ? next.urls.map(value => String(value || '').trim()).filter(Boolean) : (previous.urls || []),
    expires_at: String(next.expires_at ?? previous.expires_at ?? ''),
    remaining_seconds: Number(next.remaining_seconds ?? previous.remaining_seconds ?? 0),
    // The backend deliberately exposes this only when issuing a token. Keep the
    // last issued value in this tab so a status refresh does not erase the copy action.
    token: String(next.token ?? previous.token ?? ''),
  }
}

export function normalizeRemoteExecution(payload, fallback = {}) {
  const previous = objectValue(fallback)
  const next = unwrapPayload(payload, 'remote_execution')
  return {
    enabled: Boolean(next.enabled ?? previous.enabled ?? false),
    active: Boolean(next.active ?? previous.active ?? false),
    persistent: Boolean(next.persistent ?? previous.persistent ?? true),
    port: Number(next.port ?? previous.port ?? 0),
    urls: Array.isArray(next.urls) ? next.urls.map(value => String(value || '').trim()).filter(Boolean) : (previous.urls || []),
    token: String(next.token ?? previous.token ?? ''),
    tasks: Array.isArray(next.tasks) ? next.tasks.map(value => String(value || '').trim()).filter(Boolean) : (previous.tasks || []),
    worker_root: String(next.worker_root ?? previous.worker_root ?? ''),
  }
}

export function diagnosticTone(status) {
  const normalized = String(status || '').trim().toLowerCase()
  if (['ready', 'ok', 'success', 'completed'].includes(normalized)) return 'ok'
  if (['error', 'fail', 'failed'].includes(normalized)) return 'fail'
  if (['run', 'running', 'checking'].includes(normalized)) return 'run'
  return 'warn'
}

export function diagnosticOverallTone(checks = []) {
  const tones = checks.map(check => diagnosticTone(check?.status))
  if (tones.includes('fail')) return 'fail'
  if (tones.includes('warn')) return 'warn'
  return tones.length ? 'ok' : 'warn'
}

export function gpuLabel(diagnostics = {}) {
  const gpu = objectValue(diagnostics.gpu)
  if (!gpu.available) return String(gpu.message || (gpu.error ? `未启用 (${gpu.error})` : '未检测到可用 GPU'))
  const devices = Array.isArray(gpu.devices) ? gpu.devices : []
  const primary = devices[0]?.name || `${gpu.device_count || 1} 块设备`
  const runtime = gpu.cuda_version ? `CUDA ${gpu.cuda_version}` : String(gpu.accelerator || '').toUpperCase()
  return runtime ? `${primary} / ${runtime}` : primary
}

export function formatBytes(value) {
  const bytes = Number(value)
  if (!Number.isFinite(bytes) || bytes <= 0) return '未知'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  const amount = bytes / (1024 ** index)
  return `${amount >= 10 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`
}

export function hasLegacyData(migration = {}) {
  return Boolean(migration.needed || Number(objectValue(migration.summary).legacy_bytes || 0) > 0)
}

export function needsOnboarding({ desktopMode = false, runtime = {}, settings = {}, diagnostics = {} } = {}) {
  if (!desktopMode) return false
  if (!Boolean(runtime.settings_exists)) return true
  const translator = String(settings.translator || settings.selected_translator || '').trim()
  const translatorRequiresKey = ['gemini', 'doubao-ark', 'openai-compatible'].includes(translator)
  const configured = Boolean(settings.configured_secrets?.api_key || String(settings.api_key || '').trim())
  return Boolean(translatorRequiresKey && !configured) || CRITICAL_GPU_STATUSES.has(String(diagnostics.gpu?.status || ''))
}

export function remoteDiagnosticsConnectionText(remote = {}) {
  const urls = Array.isArray(remote.urls) ? remote.urls.filter(Boolean) : []
  const token = String(remote.token || '').trim()
  if (!urls.length || !token) return ''
  return [
    'Solar Manga Translator 只读远程诊断',
    ...urls.map((url, index) => `URL ${index + 1}: ${url}`),
    `Token: ${token}`,
    '认证方式: Authorization: Bearer <Token>',
    `有效期至: ${remote.expires_at || '约 60 分钟后'}`,
  ].join('\n')
}

export function remoteExecutionConnectionText(remote = {}) {
  const urls = Array.isArray(remote.urls) ? remote.urls.filter(Boolean) : []
  const token = String(remote.token || '').trim()
  if (!urls.length || !token) return ''
  return [
    'Solar Manga Translator 持久远程任务节点',
    ...urls.map((url, index) => `URL ${index + 1}: ${url}`),
    `Token: ${token}`,
    '认证方式: Authorization: Bearer <Token>',
    `任务能力: ${(remote.tasks || []).join(', ')}`,
    '支持上传测试包、执行 CUDA/诊断/命令任务、读取实时日志、下载结果和停止任务。',
    '自动启动: 已开启（只需保持 Solar Manga Translator 正在运行）',
  ].join('\n')
}
