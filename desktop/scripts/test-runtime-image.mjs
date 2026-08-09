import assert from 'node:assert/strict'
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  realpathSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  stageBackendRuntimeImage,
  validateBackendRuntimeImage,
} from './runtime-image.mjs'

const __dirname = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(__dirname, '..', '..')
const backendDir = resolve(repoRoot, 'backend')
const workspace = mkdtempSync(join(tmpdir(), 'manga-runtime-image-'))
const targetDir = resolve(workspace, 'backend-source')
const upstreamDir = resolve(workspace, 'upstream-fixture')

for (const relativePath of ['dict', 'manga_translator']) {
  mkdirSync(resolve(upstreamDir, relativePath), { recursive: true })
}
writeFileSync(resolve(upstreamDir, 'LICENSE'), 'runtime fixture\n', 'utf8')
writeFileSync(resolve(upstreamDir, 'dict', 'README'), 'runtime dictionary fixture\n', 'utf8')
writeFileSync(resolve(upstreamDir, 'manga_translator', '__init__.py'), '', 'utf8')
writeFileSync(resolve(upstreamDir, 'README.md'), 'must not be staged\n', 'utf8')
mkdirSync(resolve(upstreamDir, 'models'), { recursive: true })

function resolvePythonExecutable() {
  const runtimeOverride = process.env.MANGA_TRANSLATOR_PYTHON_RUNTIME
  const candidates = [
    process.env.MANGA_TRANSLATOR_PYTHON_EXECUTABLE,
    runtimeOverride ? resolve(runtimeOverride, 'Scripts', 'python.exe') : null,
    runtimeOverride ? resolve(runtimeOverride, 'bin', 'python') : null,
    resolve(backendDir, 'venv', 'Scripts', 'python.exe'),
    resolve(backendDir, 'venv', 'bin', 'python'),
    resolve(backendDir, '.venv-mac', 'bin', 'python'),
  ].filter(Boolean)
  const executable = candidates.find((candidate) => existsSync(candidate))
  assert.ok(executable, 'a prepared backend Python runtime is required')
  return executable
}

try {
  stageBackendRuntimeImage({
    backendDir,
    targetDir,
    upstreamDir,
  })

  for (const relativePath of [
    'main.py',
    'inference_backend.py',
    'translation_provider.py',
    'workflow_coordinator.py',
    'domain/project_state.py',
    'engine/translator.py',
    'utils/file_handler.py',
    'manga-image-translator/LICENSE',
    'manga-image-translator/dict',
    'manga-image-translator/manga_translator',
  ]) {
    assert.equal(
      existsSync(resolve(targetDir, relativePath)),
      true,
      `runtime image should include ${relativePath}`,
    )
  }

  for (const relativePath of [
    '.venv-mac',
    '__pycache__',
    'models',
    'output_images',
    'temp_extracted',
    'temp_uploads',
    'tests',
    'manga-image-translator/.git',
    'manga-image-translator/README.md',
    'manga-image-translator/models',
    'manga-image-translator/test',
  ]) {
    assert.equal(
      existsSync(resolve(targetDir, relativePath)),
      false,
      `runtime image should exclude ${relativePath}`,
    )
  }

  const validation = validateBackendRuntimeImage({
    pythonExecutable: resolvePythonExecutable(),
    targetDir,
  })
  assert.equal(validation.module, 'main')
  assert.equal(validation.origin, realpathSync(resolve(targetDir, 'main.py')))
} finally {
  rmSync(workspace, { recursive: true, force: true })
}

console.log('runtime image staging tests passed')
