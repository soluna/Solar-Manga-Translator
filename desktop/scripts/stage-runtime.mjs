import { cpSync, existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { basename, dirname, extname, isAbsolute, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const desktopDir = resolve(__dirname, '..')
const repoRoot = resolve(desktopDir, '..')
const backendDir = resolve(repoRoot, 'backend')
const upstreamDir = resolve(backendDir, 'manga-image-translator')
const stagingRoot = resolve(desktopDir, 'resources-staging')

const ignoredNames = new Set([
  '.DS_Store',
  '.git',
  '.github',
  '.mypy_cache',
  '.pytest_cache',
  '__pycache__',
  'models',
  'node_modules',
  'output_images',
  'result',
  'temp_extracted',
  'temp_uploads',
  'venv',
  '.venv-mac',
])

const ignoredExtensions = new Set(['.pyc', '.pyo'])

const backendFiles = [
  'desktop_server.py',
  'install_deps.py',
  'main.py',
  'patch_pydensecrf.py',
  'patched_custom_openai.py',
  'patched_inpainting_init.py',
  'patched_manga_translator_init.py',
  'patched_rendering_init.py',
  'patched_rerender_cache.py',
  'patched_text_mask_utils.py',
  'patched_text_render.py',
  'patched_utils_init.py',
  'requirements-upstream.txt',
  'requirements.txt',
  'runtime_paths.py',
  'system_fonts.py',
  'upstream.json',
]

const backendDirs = ['engine', 'utils']
const upstreamFiles = ['LICENSE']
const upstreamDirs = ['dict', 'manga_translator']

function ensureCleanDirectory(path) {
  rmSync(path, { recursive: true, force: true })
  mkdirSync(path, { recursive: true })
}

function assertInside(root, target) {
  const rel = relative(root, target)
  if (!rel || rel.startsWith('..') || isAbsolute(rel)) {
    throw new Error(`Refusing to stage path outside ${root}: ${target}`)
  }
}

function requirePath(path, label) {
  if (!existsSync(path)) {
    throw new Error(`${label} 不存在: ${path}`)
  }
}

function safeCopyFilter(source) {
  const name = basename(source)
  if (ignoredNames.has(name)) {
    return false
  }
  if (ignoredExtensions.has(extname(name))) {
    return false
  }
  return true
}

function copyPath(source, target) {
  cpSync(source, target, {
    recursive: true,
    filter: safeCopyFilter,
  })
}

function detectPythonRuntimeDir() {
  const envPath = process.env.MANGA_TRANSLATOR_PYTHON_RUNTIME
  const candidates = [
    envPath,
    resolve(backendDir, 'venv'),
    resolve(backendDir, '.venv-mac'),
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

function readPinnedUpstreamCommit() {
  const data = JSON.parse(readFileSync(resolve(backendDir, 'upstream.json'), 'utf8'))
  return data.manga_image_translator.commit
}

function currentUpstreamCommit() {
  return execFileSync('git', ['-C', upstreamDir, 'rev-parse', 'HEAD'], {
    encoding: 'utf8',
  }).trim()
}

function validatePreparedUpstream() {
  requirePath(upstreamDir, 'manga-image-translator checkout')
  const pinnedCommit = readPinnedUpstreamCommit()
  const actualCommit = currentUpstreamCommit()
  if (actualCommit !== pinnedCommit) {
    throw new Error(
      `manga-image-translator checkout commit mismatch. expected ${pinnedCommit}, got ${actualCommit}. ` +
        'Run backend/install_deps.py --prepare-only before packaging.'
    )
  }

  const patchedFiles = [
    resolve(upstreamDir, 'manga_translator', 'utils', 'rerender_cache.py'),
    resolve(upstreamDir, 'manga_translator', 'mask_refinement', 'text_mask_utils.py'),
    resolve(upstreamDir, 'manga_translator', 'rendering', '__init__.py'),
  ]
  for (const file of patchedFiles) {
    requirePath(file, 'patched upstream runtime file')
  }
}

function copyBackendSource() {
  const targetBackend = resolve(stagingRoot, 'backend-source')
  mkdirSync(targetBackend, { recursive: true })

  for (const file of backendFiles) {
    const source = resolve(backendDir, file)
    const target = resolve(targetBackend, file)
    assertInside(backendDir, source)
    requirePath(source, `backend file ${file}`)
    copyPath(source, target)
  }

  for (const dir of backendDirs) {
    const source = resolve(backendDir, dir)
    const target = resolve(targetBackend, dir)
    assertInside(backendDir, source)
    requirePath(source, `backend directory ${dir}`)
    copyPath(source, target)
  }

  const targetUpstream = resolve(targetBackend, 'manga-image-translator')
  mkdirSync(targetUpstream, { recursive: true })

  for (const file of upstreamFiles) {
    const source = resolve(upstreamDir, file)
    if (existsSync(source)) {
      copyPath(source, resolve(targetUpstream, file))
    }
  }

  for (const dir of upstreamDirs) {
    const source = resolve(upstreamDir, dir)
    const target = resolve(targetUpstream, dir)
    requirePath(source, `upstream directory ${dir}`)
    copyPath(source, target)
  }
}

function main() {
  const frontendDist = resolve(repoRoot, 'frontend', 'dist')
  requirePath(frontendDist, 'frontend/dist')
  validatePreparedUpstream()

  const runtimeDir = detectPythonRuntimeDir()
  ensureCleanDirectory(stagingRoot)

  copyPath(frontendDist, resolve(stagingRoot, 'frontend-dist'))
  copyBackendSource()
  copyPath(runtimeDir, resolve(stagingRoot, 'python-runtime'))

  writeFileSync(
    resolve(stagingRoot, 'release-manifest.json'),
    JSON.stringify(
      {
        created_at: new Date().toISOString(),
        frontend_dist: 'frontend/dist',
        backend_source: 'backend',
        python_runtime: process.env.MANGA_TRANSLATOR_PYTHON_RUNTIME ? 'MANGA_TRANSLATOR_PYTHON_RUNTIME' : 'backend/venv',
        upstream: {
          repository: 'https://github.com/zyddnys/manga-image-translator.git',
          commit: currentUpstreamCommit(),
          staged_paths: ['manga_translator/', 'dict/', 'LICENSE'],
        },
        excluded: ['fonts/', 'models/', 'examples/', 'result/', 'temp_uploads/', '.git/'],
      },
      null,
      2
    ),
    'utf-8'
  )
}

main()
