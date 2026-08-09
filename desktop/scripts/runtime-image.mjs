import { cpSync, existsSync, lstatSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { dirname, extname, relative, resolve, sep } from 'node:path'

const excludedBackendRoots = new Set([
  '.venv',
  '.venv-mac',
  'htmlcov',
  'manga-image-translator',
  'models',
  'node_modules',
  'output_images',
  'result',
  'temp_extracted',
  'temp_uploads',
  'tests',
  'venv',
])

const excludedNames = new Set([
  '.DS_Store',
  '.coverage',
  '.git',
  '.github',
  '.mypy_cache',
  '.pytest_cache',
  '.ruff_cache',
  '__pycache__',
])

const excludedExtensions = new Set(['.log', '.pyc', '.pyo'])
const upstreamRuntimePaths = ['LICENSE', 'dict', 'manga_translator']

function requirePath(path, label) {
  if (!existsSync(path)) {
    throw new Error(`${label} 不存在: ${path}`)
  }
}

function pathSegments(root, source) {
  const rel = relative(root, source)
  if (!rel) {
    return []
  }
  if (rel === '..' || rel.startsWith(`..${sep}`)) {
    throw new Error(`Runtime Image source escaped its root: ${source}`)
  }
  return rel.split(sep)
}

function isSecretEnvironmentFile(name) {
  return name === '.env' || name.startsWith('.env.')
}

function shouldCopyBackendPath(backendDir, source) {
  const segments = pathSegments(backendDir, source)
  if (!segments.length) {
    return true
  }
  if (excludedBackendRoots.has(segments[0])) {
    return false
  }
  const name = segments.at(-1)
  if (segments.some((segment) => excludedNames.has(segment))) {
    return false
  }
  if (isSecretEnvironmentFile(name) || excludedExtensions.has(extname(name))) {
    return false
  }
  return !lstatSync(source).isSymbolicLink()
}

function shouldCopyUpstreamPath(upstreamDir, source) {
  const segments = pathSegments(upstreamDir, source)
  if (!segments.length) {
    return true
  }
  const name = segments.at(-1)
  if (segments.some((segment) => excludedNames.has(segment))) {
    return false
  }
  if (isSecretEnvironmentFile(name) || excludedExtensions.has(extname(name))) {
    return false
  }
  return !lstatSync(source).isSymbolicLink()
}

function assertSafeTarget(backendDir, targetDir) {
  const source = resolve(backendDir)
  const target = resolve(targetDir)
  const rel = relative(source, target)
  if (!rel || (!rel.startsWith('..') && rel !== '..')) {
    throw new Error(`Runtime Image target must be outside backend source: ${target}`)
  }
}

function trackedBackendRuntimeFiles(backendDir) {
  let output
  try {
    output = execFileSync(
      'git',
      ['-C', backendDir, 'ls-files', '-z', '--', '.'],
      { encoding: 'buffer' },
    )
  } catch (error) {
    throw new Error(
      'Runtime Image 必须从 Git 工作树构建，无法读取 backend 跟踪文件。',
      { cause: error },
    )
  }
  return output
    .toString('utf8')
    .split('\0')
    .filter(Boolean)
    .filter((relativePath) => shouldCopyBackendPath(
      backendDir,
      resolve(backendDir, relativePath),
    ))
}

export function stageBackendRuntimeImage({
  backendDir,
  targetDir,
  upstreamDir = resolve(backendDir, 'manga-image-translator'),
}) {
  const sourceBackend = resolve(backendDir)
  const targetBackend = resolve(targetDir)
  const sourceUpstream = resolve(upstreamDir)

  requirePath(sourceBackend, 'backend source')
  requirePath(sourceUpstream, 'manga-image-translator checkout')
  assertSafeTarget(sourceBackend, targetBackend)

  rmSync(targetBackend, { recursive: true, force: true })
  mkdirSync(targetBackend, { recursive: true })
  for (const relativePath of trackedBackendRuntimeFiles(sourceBackend)) {
    const source = resolve(sourceBackend, relativePath)
    const target = resolve(targetBackend, relativePath)
    if (!lstatSync(source).isFile()) {
      continue
    }
    mkdirSync(dirname(target), { recursive: true })
    cpSync(source, target)
  }

  const targetUpstream = resolve(targetBackend, 'manga-image-translator')
  mkdirSync(targetUpstream, { recursive: true })
  for (const relativePath of upstreamRuntimePaths) {
    const source = resolve(sourceUpstream, relativePath)
    const target = resolve(targetUpstream, relativePath)
    requirePath(source, `upstream runtime path ${relativePath}`)
    cpSync(source, target, {
      recursive: true,
      filter: (candidate) => shouldCopyUpstreamPath(sourceUpstream, candidate),
    })
  }

  return targetBackend
}

export function validateBackendRuntimeImage({ pythonExecutable, targetDir }) {
  const targetBackend = resolve(targetDir)
  const executable = resolve(pythonExecutable)
  const validationStateDir = mkdtempSync(resolve(tmpdir(), 'manga-runtime-validation-'))
  const validationScript = String.raw`
import importlib
import json
from pathlib import Path
import sys

runtime_root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(runtime_root))
module = importlib.import_module("main")
origin = Path(module.__file__).resolve()
if runtime_root not in origin.parents:
    raise RuntimeError(f"main was imported outside the Runtime Image: {origin}")
if getattr(module, "app", None) is None:
    raise RuntimeError("main.app is missing")
print(json.dumps({"module": module.__name__, "origin": str(origin)}))
`

  requirePath(executable, 'backend Python executable')
  requirePath(resolve(targetBackend, 'main.py'), 'Runtime Image entry')

  try {
    const stdout = execFileSync(
      executable,
      ['-I', '-c', validationScript, targetBackend],
      {
        cwd: targetBackend,
        encoding: 'utf8',
        env: {
          ...process.env,
          APP_CODE_DIR: targetBackend,
          APP_DATA_DIR: validationStateDir,
          APP_ENABLE_API_DOCS: '',
          PYTHONDONTWRITEBYTECODE: '1',
        },
        stdio: ['ignore', 'pipe', 'pipe'],
      },
    )
    const resultLine = stdout.trim().split(/\r?\n/u).at(-1)
    const result = JSON.parse(resultLine)
    return {
      module: String(result.module),
      origin: resolve(String(result.origin)),
    }
  } catch (error) {
    const stderr = error?.stderr ? String(error.stderr).trim() : ''
    const stdout = error?.stdout ? String(error.stdout).trim() : ''
    const detail = [stderr, stdout, error instanceof Error ? error.message : String(error)]
      .filter(Boolean)
      .join('\n')
    throw new Error(`Runtime Image 入口验证失败:\n${detail}`, { cause: error })
  } finally {
    rmSync(validationStateDir, { recursive: true, force: true })
  }
}
