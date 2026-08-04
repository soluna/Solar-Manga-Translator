import { join, resolve, win32 } from 'node:path'

export const RUNTIME_DATA_DIR_NAME = '.runtime'

export function resolveApplicationDataDir({
  platform = process.platform,
  env = process.env,
  projectDir = '',
} = {}) {
  const explicitDataDir = String(env.APP_DATA_DIR || '').trim()
  if (explicitDataDir) {
    return platform === 'win32'
      ? win32.resolve(explicitDataDir)
      : resolve(explicitDataDir)
  }

  const root = String(projectDir || '').trim()
  if (!root) {
    throw new Error('无法确定项目目录。')
  }
  if (platform === 'win32') {
    return win32.join(root, RUNTIME_DATA_DIR_NAME)
  }
  return join(root, RUNTIME_DATA_DIR_NAME)
}

function joinForPlatform(platform, ...segments) {
  return platform === 'win32' ? win32.join(...segments) : join(...segments)
}

function legacyTempDirectories(platform, env, rootDir) {
  const delimiter = platform === 'win32' ? ';' : ':'
  const normalize = (value) => platform === 'win32' ? value.toLowerCase() : value
  const normalizedRoot = normalize(String(rootDir))
  const seen = new Set()
  const directories = []
  for (const rawValue of [env.TEMP, env.TMP, env.TMPDIR]) {
    const value = String(rawValue || '').trim()
    if (!value) {
      continue
    }
    const normalized = normalize(value)
    if (normalized === normalizedRoot || normalized.startsWith(`${normalizedRoot}${platform === 'win32' ? '\\' : '/'}`)) {
      continue
    }
    if (!seen.has(normalized)) {
      seen.add(normalized)
      directories.push(value)
    }
  }
  return directories.join(delimiter)
}

export function resolveRuntimeStorageLayout({
  platform = process.platform,
  env = process.env,
  projectDir = '',
} = {}) {
  const rootDir = resolveApplicationDataDir({ platform, env, projectDir })
  const underRoot = (...segments) => joinForPlatform(platform, rootDir, ...segments)
  const paths = {
    modelsDir: underRoot('models'),
    outputDir: underRoot('output'),
    logsDir: underRoot('logs'),
    cacheDir: underRoot('cache'),
    tempDir: underRoot('temp'),
    fontsDir: underRoot('fonts'),
    electronUserDataDir: underRoot('electron', 'user-data'),
    electronSessionDataDir: underRoot('electron', 'session-data'),
    electronCacheDir: underRoot('cache', 'electron'),
    electronLogsDir: underRoot('logs', 'electron'),
    electronCrashDumpsDir: underRoot('logs', 'crash-dumps'),
  }
  const cachePath = (...segments) => joinForPlatform(platform, paths.cacheDir, ...segments)
  const legacyTempDirs = legacyTempDirectories(platform, env, rootDir)
  const environment = {
    APP_DATA_DIR: rootDir,
    APP_MODELS_DIR: paths.modelsDir,
    APP_OUTPUT_DIR: paths.outputDir,
    APP_LOG_DIR: paths.logsDir,
    APP_CACHE_DIR: paths.cacheDir,
    APP_TEMP_DIR: paths.tempDir,
    APP_FONT_DIR: paths.fontsDir,
    TEMP: paths.tempDir,
    TMP: paths.tempDir,
    TMPDIR: paths.tempDir,
    XDG_CACHE_HOME: cachePath('external'),
    HF_HOME: cachePath('huggingface'),
    HF_HUB_CACHE: cachePath('huggingface', 'hub'),
    HUGGINGFACE_HUB_CACHE: cachePath('huggingface', 'hub'),
    HF_ASSETS_CACHE: cachePath('huggingface', 'assets'),
    HF_XET_CACHE: cachePath('huggingface', 'xet'),
    HF_DATASETS_CACHE: cachePath('huggingface', 'datasets'),
    TRANSFORMERS_CACHE: cachePath('huggingface', 'transformers'),
    TORCH_HOME: cachePath('torch'),
    TORCH_EXTENSIONS_DIR: cachePath('torch', 'extensions'),
    TORCHINDUCTOR_CACHE_DIR: cachePath('torch', 'inductor'),
    TRITON_CACHE_DIR: cachePath('triton'),
    MPLCONFIGDIR: cachePath('matplotlib'),
    NUMBA_CACHE_DIR: cachePath('numba'),
    CUDA_CACHE_PATH: cachePath('cuda'),
    PYTHONPYCACHEPREFIX: cachePath('python-bytecode'),
    PIP_CACHE_DIR: cachePath('pip'),
    npm_config_cache: cachePath('npm'),
    ELECTRON_CACHE: cachePath('electron-downloads'),
    ELECTRON_BUILDER_CACHE: cachePath('electron-builder'),
  }
  if (legacyTempDirs) {
    environment.APP_LEGACY_TEMP_DIRS = legacyTempDirs
  }
  return { rootDir, paths, environment }
}

export function applyRuntimeStorageLayout(layout, {
  app,
  commandLine,
  env = process.env,
  ensureDirectory,
} = {}) {
  if (!layout?.rootDir || !layout?.paths || !layout?.environment) {
    throw new Error('运行时存储布局无效。')
  }
  if (!app?.setPath || !commandLine?.appendSwitch || !ensureDirectory) {
    throw new Error('无法初始化 Electron 运行时存储。')
  }

  for (const path of new Set([layout.rootDir, ...Object.values(layout.paths)])) {
    ensureDirectory(path)
  }
  Object.assign(env, layout.environment)
  app.setPath('userData', layout.paths.electronUserDataDir)
  app.setPath('sessionData', layout.paths.electronSessionDataDir)
  app.setPath('temp', layout.paths.tempDir)
  app.setPath('logs', layout.paths.electronLogsDir)
  app.setPath('crashDumps', layout.paths.electronCrashDumpsDir)
  commandLine.appendSwitch('disk-cache-dir', layout.paths.electronCacheDir)
  return layout
}
