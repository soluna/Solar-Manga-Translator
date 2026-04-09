import { cpSync, existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { basename, dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const desktopDir = resolve(__dirname, '..')
const repoRoot = resolve(desktopDir, '..')
const stagingRoot = resolve(desktopDir, 'resources-staging')

function ensureCleanDirectory(path) {
  rmSync(path, { recursive: true, force: true })
  mkdirSync(path, { recursive: true })
}

function detectPythonRuntimeDir() {
  const envPath = process.env.MANGA_TRANSLATOR_PYTHON_RUNTIME
  const candidates = [
    envPath,
    resolve(repoRoot, 'backend', 'venv'),
    resolve(repoRoot, 'backend', '.venv-mac'),
  ].filter(Boolean)

  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      return candidate
    }
  }

  throw new Error(
    '未找到可打包的 Python runtime。请先在 Windows 上准备 backend/venv，或设置 MANGA_TRANSLATOR_PYTHON_RUNTIME。'
  )
}

function shouldCopyBackend(path) {
  const name = basename(path)
  const ignored = new Set([
    'venv',
    '.venv-mac',
    'models',
    'temp_uploads',
    'temp_extracted',
    'output_images',
    '__pycache__',
    '.pytest_cache',
    '.mypy_cache',
  ])
  return !ignored.has(name)
}

function copyTree(source, target, filter) {
  cpSync(source, target, {
    recursive: true,
    filter,
  })
}

function main() {
  const frontendDist = resolve(repoRoot, 'frontend', 'dist')
  if (!existsSync(frontendDist)) {
    throw new Error('frontend/dist 不存在，请先运行前端构建。')
  }

  const runtimeDir = detectPythonRuntimeDir()
  ensureCleanDirectory(stagingRoot)

  copyTree(frontendDist, resolve(stagingRoot, 'frontend-dist'))
  copyTree(
    resolve(repoRoot, 'backend'),
    resolve(stagingRoot, 'backend-source'),
    (path) => shouldCopyBackend(path)
  )

  const fontsDir = resolve(repoRoot, 'fonts')
  if (existsSync(fontsDir)) {
    copyTree(fontsDir, resolve(stagingRoot, 'fonts'))
  } else {
    mkdirSync(resolve(stagingRoot, 'fonts'), { recursive: true })
  }

  copyTree(runtimeDir, resolve(stagingRoot, 'python-runtime'))

  writeFileSync(
    resolve(stagingRoot, 'release-manifest.json'),
    JSON.stringify(
      {
        created_at: new Date().toISOString(),
        frontend_dist: frontendDist,
        backend_source: resolve(repoRoot, 'backend'),
        fonts: fontsDir,
        python_runtime: runtimeDir,
      },
      null,
      2
    ),
    'utf-8'
  )
}

main()
