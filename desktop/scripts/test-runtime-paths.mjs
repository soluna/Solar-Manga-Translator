import assert from 'node:assert/strict'

import {
  applyRuntimeStorageLayout,
  resolveApplicationDataDir,
  resolveRuntimeStorageLayout,
} from '../runtime-paths.mjs'


assert.equal(
  resolveApplicationDataDir({
    platform: 'win32',
    env: { LOCALAPPDATA: 'C:\\Users\\Test\\AppData\\Local' },
    projectDir: 'E:\\Projects\\manga-translator',
  }),
  'E:\\Projects\\manga-translator\\.runtime',
)

assert.equal(
  resolveApplicationDataDir({
    platform: 'win32',
    env: { APP_DATA_DIR: 'D:\\MangaData' },
    projectDir: 'E:\\Projects\\manga-translator',
  }),
  'D:\\MangaData',
)

assert.equal(
  resolveApplicationDataDir({
    platform: 'darwin',
    env: {},
    projectDir: '/tmp/manga-translator',
  }),
  '/tmp/manga-translator/.runtime',
)

const windowsStorage = resolveRuntimeStorageLayout({
  platform: 'win32',
  env: {
    LOCALAPPDATA: 'C:\\Users\\Test\\AppData\\Local',
    APPDATA: 'C:\\Users\\Test\\AppData\\Roaming',
    TEMP: 'C:\\Users\\Test\\AppData\\Local\\Temp',
  },
  projectDir: 'E:\\Projects\\manga-translator',
})

assert.equal(windowsStorage.rootDir, 'E:\\Projects\\manga-translator\\.runtime')
assert.deepEqual(windowsStorage.paths, {
  modelsDir: 'E:\\Projects\\manga-translator\\.runtime\\models',
  outputDir: 'E:\\Projects\\manga-translator\\.runtime\\output',
  logsDir: 'E:\\Projects\\manga-translator\\.runtime\\logs',
  cacheDir: 'E:\\Projects\\manga-translator\\.runtime\\cache',
  tempDir: 'E:\\Projects\\manga-translator\\.runtime\\temp',
  fontsDir: 'E:\\Projects\\manga-translator\\.runtime\\fonts',
  electronUserDataDir: 'E:\\Projects\\manga-translator\\.runtime\\electron\\user-data',
  electronSessionDataDir: 'E:\\Projects\\manga-translator\\.runtime\\electron\\session-data',
  electronCacheDir: 'E:\\Projects\\manga-translator\\.runtime\\cache\\electron',
  electronLogsDir: 'E:\\Projects\\manga-translator\\.runtime\\logs\\electron',
  electronCrashDumpsDir: 'E:\\Projects\\manga-translator\\.runtime\\logs\\crash-dumps',
})
assert.equal(windowsStorage.environment.APP_DATA_DIR, windowsStorage.rootDir)
assert.equal(windowsStorage.environment.APP_TEMP_DIR, windowsStorage.paths.tempDir)
assert.equal(windowsStorage.environment.TEMP, windowsStorage.paths.tempDir)
assert.equal(windowsStorage.environment.TMP, windowsStorage.paths.tempDir)
assert.equal(windowsStorage.environment.TMPDIR, windowsStorage.paths.tempDir)
assert.equal(
  windowsStorage.environment.HF_HOME,
  'E:\\Projects\\manga-translator\\.runtime\\cache\\huggingface',
)
assert.equal(
  windowsStorage.environment.TORCH_HOME,
  'E:\\Projects\\manga-translator\\.runtime\\cache\\torch',
)
assert.equal(
  windowsStorage.environment.CUDA_CACHE_PATH,
  'E:\\Projects\\manga-translator\\.runtime\\cache\\cuda',
)
assert.equal(
  windowsStorage.environment.APP_LEGACY_TEMP_DIRS,
  'C:\\Users\\Test\\AppData\\Local\\Temp',
)

for (const path of Object.values(windowsStorage.paths)) {
  assert.ok(
    path.startsWith(`${windowsStorage.rootDir}\\`),
    `runtime path escaped project root: ${path}`,
  )
}
for (const [name, path] of Object.entries(windowsStorage.environment)) {
  if (name === 'APP_LEGACY_TEMP_DIRS') {
    continue
  }
  assert.ok(
    path === windowsStorage.rootDir || path.startsWith(`${windowsStorage.rootDir}\\`),
    `${name} escaped project root: ${path}`,
  )
}

const createdDirectories = []
const electronPathOverrides = []
const commandLineSwitches = []
const childEnvironment = {}
applyRuntimeStorageLayout(windowsStorage, {
  app: {
    setPath(name, path) {
      electronPathOverrides.push([name, path])
    },
  },
  commandLine: {
    appendSwitch(name, value) {
      commandLineSwitches.push([name, value])
    },
  },
  env: childEnvironment,
  ensureDirectory(path) {
    createdDirectories.push(path)
  },
})

assert.deepEqual(electronPathOverrides, [
  ['userData', windowsStorage.paths.electronUserDataDir],
  ['sessionData', windowsStorage.paths.electronSessionDataDir],
  ['temp', windowsStorage.paths.tempDir],
  ['logs', windowsStorage.paths.electronLogsDir],
  ['crashDumps', windowsStorage.paths.electronCrashDumpsDir],
])
assert.deepEqual(commandLineSwitches, [
  ['disk-cache-dir', windowsStorage.paths.electronCacheDir],
])
assert.deepEqual(
  createdDirectories,
  [...new Set([windowsStorage.rootDir, ...Object.values(windowsStorage.paths)])],
)
assert.equal(childEnvironment.APP_DATA_DIR, windowsStorage.rootDir)
assert.equal(childEnvironment.HF_HOME, windowsStorage.environment.HF_HOME)

console.log('Desktop runtime path checks passed.')
