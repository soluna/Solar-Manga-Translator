#!/usr/bin/env node
/**
 * Standalone InkStage workflow E2E against the real FastAPI app.
 *
 * This intentionally is not named test-*.mjs: unit-test globs must not launch
 * a browser. It creates isolated synthetic app data and starts normal v3 dev.
 * The only substituted calls are local text-mask detection and LaMa inference.
 */
import assert from 'node:assert/strict'
import { randomBytes } from 'node:crypto'
import { existsSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { tmpdir } from 'node:os'
import path from 'node:path'
import net from 'node:net'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'

import { launchChromium } from '../../frontend/scripts/playwright-launcher.mjs'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..')
const BACKEND_DIR = path.join(ROOT, 'backend')
const FRONTEND_V3_DIR = path.join(ROOT, 'frontend-v3')
const FIXTURE_SCRIPT = path.join(ROOT, 'scripts/create_canvas_test_fixture.py')
const BACKEND_RUNNER = path.join(ROOT, 'scripts/run_canvas_e2e_backend.py')
const PROJECT_ID = 'canvas-e2e-fixture'
const PROJECT_TITLE = 'Canvas E2E Fixture'
const PAGE_ID = '0001.jpg'
const SERVER_TIMEOUT_MS = 60_000
const UI_TIMEOUT_MS = 30_000
const WORKFLOW_ARTIFACTS_DIR = path.join(FRONTEND_V3_DIR, 'test-artifacts', 'parity-agents', 'browser-workflow')

const INFERENCE_BOUNDARY_HOOK = [
  'import importlib.abc',
  'import importlib.machinery',
  'import os',
  'import sys',
  '',
  '',
  'def _record(name):',
  '    target = os.environ.get("WORKFLOW_E2E_INFERENCE_STUB_LOG", "")',
  '    if target:',
  '        with open(target, "a", encoding="utf-8") as handle:',
  '            handle.write(name + chr(10))',
  '',
  '',
  'def _install_inference_boundary(module):',
  '    engine = getattr(module, "translator_engine", None)',
  '    inference = getattr(engine, "inference_backend", None)',
  '    if engine is None or inference is None:',
  '        raise RuntimeError("workflow-e2e could not locate the real translator inference boundary")',
  '',
  '    import cv2',
  '    import numpy as np',
  '',
  '    async def detect_synthetic_fixture_text(source_image, **_kwargs):',
  '        height, width = source_image.shape[:2]',
  '        x1, y1 = min(82, width - 1), min(98, height - 1)',
  '        x2, y2 = min(420, width), min(138, height)',
  '        mask = np.zeros((height, width), dtype=np.uint8)',
  '        if x2 > x1 and y2 > y1:',
  '            gray = cv2.cvtColor(source_image, cv2.COLOR_RGB2GRAY)',
  '            roi = gray[y1:y2, x1:x2]',
  '            mask[y1:y2, x1:x2] = np.where(roi < 205, 255, 0).astype(np.uint8)',
  '        _record("detect_text_mask")',
  '        return {',
  '            "mask": mask,',
  '            "textlines": [{',
  '                "points": [[x1, y1], [x2 - 1, y1], [x2 - 1, y2 - 1], [x1, y2 - 1]],',
  '                "probability": 0.99,',
  '            }],',
  '        }',
  '',
  '    async def inpaint_synthetic_fixture(base_image, selection_mask, **_kwargs):',
  '        image = np.asarray(base_image, dtype=np.uint8)',
  '        mask = np.asarray(selection_mask)',
  '        if mask.ndim == 3:',
  '            mask = cv2.cvtColor(mask[:, :, :3], cv2.COLOR_RGB2GRAY)',
  '        mask = np.where(mask > 0, 255, 0).astype(np.uint8)',
  '        _record("erase_selection")',
  '        if not cv2.countNonZero(mask):',
  '            return image.copy()',
  '        return cv2.inpaint(image, mask, 3, cv2.INPAINT_TELEA)',
  '',
  '    inference.detect_text_mask = detect_synthetic_fixture_text',
  '    inference.erase_selection = inpaint_synthetic_fixture',
  '    # Device selection is configuration for the substituted model call.',
  '    # Keep this scenario CPU-only and avoid importing an accelerator runtime.',
  '    engine._select_local_inpainting_device = lambda _use_gpu: "cpu"',
  '    _record("installed")',
  '',
  '',
  'class _MainModuleLoader(importlib.abc.Loader):',
  '    def __init__(self, wrapped):',
  '        self.wrapped = wrapped',
  '',
  '    def create_module(self, spec):',
  '        creator = getattr(self.wrapped, "create_module", None)',
  '        return creator(spec) if creator else None',
  '',
  '    def exec_module(self, module):',
  '        self.wrapped.exec_module(module)',
  '        _install_inference_boundary(module)',
  '',
  '',
  'class _MainModuleFinder(importlib.abc.MetaPathFinder):',
  '    def find_spec(self, fullname, path=None, target=None):',
  '        if fullname != "main":',
  '            return None',
  '        spec = importlib.machinery.PathFinder.find_spec(fullname, path)',
  '        if spec and spec.loader and not isinstance(spec.loader, _MainModuleLoader):',
  '            spec.loader = _MainModuleLoader(spec.loader)',
  '        return spec',
  '',
  '',
  'sys.meta_path.insert(0, _MainModuleFinder())',
].join(String.fromCharCode(10))

const children = []
let browser = null
let browserPage = null
let temporaryRoot = ''
let workflowSteps = []
let currentStep = 'startup'

function markStep(name, detail = {}) {
  currentStep = name
  const entry = { at: new Date().toISOString(), step: name, ...detail }
  workflowSteps.push(entry)
  console.log(`[workflow-e2e] ${name}${Object.keys(detail).length ? ` ${JSON.stringify(detail)}` : ''}`)
}

async function persistWorkflowSteps() {
  await mkdir(WORKFLOW_ARTIFACTS_DIR, { recursive: true })
  await writeFile(path.join(WORKFLOW_ARTIFACTS_DIR, 'steps.json'), `${JSON.stringify(workflowSteps, null, 2)}\n`, 'utf8')
}

async function captureBrowserSnapshot(label, page) {
  if (!page) return
  await mkdir(WORKFLOW_ARTIFACTS_DIR, { recursive: true })
  const prefix = path.join(WORKFLOW_ARTIFACTS_DIR, label)
  const bodyLocator = page.locator('body')
  const ariaSnapshotPromise = typeof bodyLocator.ariaSnapshot === 'function'
    ? bodyLocator.ariaSnapshot().catch(() => 'ariaSnapshot() failed')
    : Promise.resolve('ariaSnapshot() unavailable')
  const [snapshot, roleProbe, ariaSnapshot] = await Promise.all([
    page.evaluate(() => {
    const visible = element => {
      const style = getComputedStyle(element)
      const rect = element.getBoundingClientRect()
      return style.display !== 'none'
        && style.visibility !== 'hidden'
        && Number(style.opacity) !== 0
        && rect.width > 0
        && rect.height > 0
    }
    const controls = Array.from(document.querySelectorAll('button, a, [role], h1, h2, h3'))
      .map(element => {
        const rect = element.getBoundingClientRect()
        return {
          tag: element.tagName.toLowerCase(),
          role: element.getAttribute('role'),
          text: (element.innerText || element.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 180),
          ariaLabel: element.getAttribute('aria-label'),
          title: element.getAttribute('title'),
          href: element.getAttribute('href'),
          disabled: 'disabled' in element ? Boolean(element.disabled) : null,
          visible: visible(element),
          bounds: { x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) },
        }
      })
    return {
      url: location.href,
      title: document.title,
      bodyText: (document.body?.innerText || '').slice(0, 12000),
      controls,
    }
    }),
    (async () => ({
      exactExportButtonCount: await page.getByRole('button', { name: '导出', exact: true }).count(),
      partialExportButtonCount: await page.getByRole('button', { name: /导出/ }).count(),
      cssTextButtonCount: await page.locator('button').filter({ hasText: '导出' }).count(),
    }))(),
    ariaSnapshotPromise,
  ])
  snapshot.roleProbe = roleProbe
  snapshot.ariaSnapshot = ariaSnapshot
  // A .html file under the Vite root triggers a full page reload while this
  // workflow is running. Keep the captured markup as .dom so diagnostics do
  // not change the page being tested.
  await writeFile(`${prefix}.dom`, await page.content(), 'utf8')
  await writeFile(`${prefix}.json`, `${JSON.stringify(snapshot, null, 2)}\n`, 'utf8')
  await page.screenshot({ path: `${prefix}.png`, fullPage: true, animations: 'disabled' })
  console.log(`[workflow-e2e] browser snapshot: ${prefix}.{dom,json,png}`)
}

function pathIsWithin(candidate, parent) {
  const relative = path.relative(parent, candidate)
  return relative === '' || (!relative.startsWith(`..${path.sep}`) && relative !== '..' && !path.isAbsolute(relative))
}

function choosePython() {
  if (process.env.PYTHON) return { command: process.env.PYTHON, prefix: [] }
  const candidates = process.platform === 'win32'
    ? [path.join(BACKEND_DIR, 'venv', 'Scripts', 'python.exe')]
    : [path.join(BACKEND_DIR, '.venv-mac', 'bin', 'python'), path.join(BACKEND_DIR, 'venv', 'bin', 'python')]
  const existing = candidates.find(candidate => existsSync(candidate))
  if (existing) return { command: existing, prefix: [] }
  return process.platform === 'win32'
    ? { command: 'py', prefix: ['-3'] }
    : { command: 'python3', prefix: [] }
}

function rememberOutput(entry, chunk) {
  entry.output = `${entry.output}${chunk.toString()}`.slice(-24_000)
}

function spawnManaged(command, args, options = {}) {
  const child = spawn(command, args, {
    cwd: ROOT,
    env: process.env,
    detached: process.platform !== 'win32',
    stdio: ['ignore', 'pipe', 'pipe'],
    ...options,
  })
  const entry = { child, output: '' }
  children.push(entry)
  child.stdout?.on('data', chunk => rememberOutput(entry, chunk))
  child.stderr?.on('data', chunk => rememberOutput(entry, chunk))
  return entry
}

async function runProcess(command, args, options = {}) {
  const entry = spawnManaged(command, args, options)
  const result = await new Promise((resolve, reject) => {
    entry.child.once('error', reject)
    entry.child.once('close', (code, signal) => resolve({ code, signal }))
  })
  if (result.code !== 0) {
    throw new Error(`${command} exited ${result.code ?? result.signal}:\n${entry.output}`)
  }
  return entry.output
}

async function freePort() {
  const server = net.createServer()
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  const address = server.address()
  assert.ok(address && typeof address === 'object')
  const { port } = address
  await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  return port
}

async function waitForHttp(url, entry, label, timeoutMs = SERVER_TIMEOUT_MS) {
  const startedAt = Date.now()
  let lastError = ''
  while (Date.now() - startedAt < timeoutMs) {
    if (entry?.child.exitCode !== null && entry?.child.exitCode !== undefined) {
      throw new Error(`${label} exited before becoming ready:\n${entry.output}`)
    }
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1_500) })
      if (response.ok) return
      lastError = `HTTP ${response.status}`
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error)
    }
    await delay(250)
  }
  throw new Error(`${label} did not become ready at ${url}: ${lastError}\n${entry?.output || ''}`)
}

async function stopChild(entry) {
  if (!entry) return
  const { child } = entry
  if (child.exitCode !== null || child.signalCode !== null) return
  try {
    if (process.platform === 'win32') child.kill('SIGTERM')
    else if (child.pid) process.kill(-child.pid, 'SIGTERM')
  } catch { /* The process may already have exited. */ }
  await Promise.race([once(child, 'exit').catch(() => {}), delay(4_000)])
  if (child.exitCode === null && child.signalCode === null) {
    try {
      if (process.platform === 'win32') child.kill('SIGKILL')
      else if (child.pid) process.kill(-child.pid, 'SIGKILL')
    } catch { /* The process may already have exited. */ }
  }
}

async function backendJson(baseUrl, token, route) {
  const response = await fetch(`${baseUrl}${route}`, {
    headers: { Authorization: `Bearer ${token}` },
    signal: AbortSignal.timeout(10_000),
  })
  const payload = await response.json().catch(() => ({}))
  assert.ok(response.ok, `Backend ${route} returned HTTP ${response.status}: ${JSON.stringify(payload)}`)
  return payload
}

async function waitForDownload(page, trigger, outputPath) {
  let resolveDownload
  let rejectDownload
  const result = new Promise((resolve, reject) => {
    resolveDownload = resolve
    rejectDownload = reject
  })
  const onDownload = download => resolveDownload(download)
  const popupPages = []
  const onPopup = popup => {
    popupPages.push(popup)
    popup.on('download', onDownload)
  }
  const timeout = setTimeout(() => rejectDownload(new Error('导出请求没有触发浏览器下载。')), UI_TIMEOUT_MS + 15_000)
  page.on('download', onDownload)
  page.on('popup', onPopup)
  try {
    await trigger()
    const download = await result
    const failure = await download.failure()
    assert.equal(failure, null, `导出下载失败：${failure || ''}`)
    await download.saveAs(outputPath)
    return download
  } finally {
    clearTimeout(timeout)
    page.off('download', onDownload)
    page.off('popup', onPopup)
    for (const popup of popupPages) popup.off('download', onDownload)
  }
}

async function setNumberInput(locator, value) {
  await locator.fill(String(value))
  await locator.press('Tab')
  assert.equal(await locator.inputValue(), String(value))
}

async function drawStroke(page, canvas, start, end) {
  const bounds = await canvas.boundingBox()
  assert.ok(bounds && bounds.width > 20 && bounds.height > 20, '画笔画布没有有效尺寸。')
  await page.mouse.move(bounds.x + bounds.width * start[0], bounds.y + bounds.height * start[1])
  await page.mouse.down()
  await page.mouse.move(bounds.x + bounds.width * end[0], bounds.y + bounds.height * end[1], { steps: 4 })
  await page.mouse.up()
}

async function assertControlInViewport(page, locator, label) {
  await locator.scrollIntoViewIfNeeded()
  const bounds = await locator.boundingBox()
  const viewport = page.viewportSize()
  assert.ok(bounds && viewport, `${label}没有可见边界。`)
  assert.ok(
    bounds.x >= -1
      && bounds.y >= -1
      && bounds.x + bounds.width <= viewport.width + 1
      && bounds.y + bounds.height <= viewport.height + 1,
    `${label}滚动后仍未进入视口。`,
  )
  assert.ok(await locator.isEnabled(), `${label}应可操作。`)
}

async function waitForBrushReady(page) {
  await page.waitForFunction(() => {
    const error = document.querySelector('.brush-canvas-status.is-error')
    if (error) throw new Error(error.textContent || '画笔底图加载失败。')
    const canvas = document.querySelector('[data-testid="erase-brush-canvas"]')
    const size = document.querySelector('[aria-label="当前画笔大小数值"]')
    return Boolean(canvas?.width && size && !size.disabled)
  })
}

async function colorAt(canvas, xRatio, yRatio) {
  return canvas.evaluate((element, point) => {
    const x = Math.max(0, Math.min(element.width - 1, Math.round(element.width * point.x)))
    const y = Math.max(0, Math.min(element.height - 1, Math.round(element.height * point.y)))
    const [red, green, blue] = element.getContext('2d').getImageData(x, y, 1, 1).data
    return `#${[red, green, blue].map(value => value.toString(16).padStart(2, '0')).join('')}`
  }, { x: xRatio, y: yRatio })
}

async function firstTranslationField(page) {
  const firstCard = page.locator('.region-card').first()
  await firstCard.waitFor({ state: 'visible' })
  const body = firstCard.locator('.region-card-body')
  if (!(await body.isVisible().catch(() => false))) {
    await firstCard.locator('.region-card-head').click()
  }
  return body.locator('textarea').nth(1)
}

async function main() {
  workflowSteps = []
  markStep('prepare-isolated-runtime')
  await mkdir(WORKFLOW_ARTIFACTS_DIR, { recursive: true })
  await Promise.all(['failure.dom', 'failure.json', 'failure.png'].map(name =>
    rm(path.join(WORKFLOW_ARTIFACTS_DIR, name), { force: true }),
  ))
  await writeFile(path.join(WORKFLOW_ARTIFACTS_DIR, 'steps.json'), '[]\n', 'utf8')
  const python = choosePython()
  const token = randomBytes(32).toString('hex')
  const externalRequests = []
  const pageErrors = []
  let backendServer
  let frontendServer

  temporaryRoot = await mkdtemp(path.join(tmpdir(), 'inkstage-workflow-e2e-'))
  const appDataDir = path.join(temporaryRoot, 'app-data')
  const stubDir = path.join(temporaryRoot, 'python-stubs')
  const inferenceStubLog = path.join(temporaryRoot, 'inference-boundary.log')
  const exportedArchive = path.join(temporaryRoot, 'result.zip')
  const protectedRuntime = path.resolve(ROOT, '.runtime')
  assert.ok(!pathIsWithin(appDataDir, ROOT), '临时 APP_DATA_DIR 必须在仓库之外。')
  assert.ok(!pathIsWithin(appDataDir, protectedRuntime), 'E2E 不得使用 .runtime 数据目录。')
  await mkdir(stubDir, { recursive: true })
  await writeFile(path.join(stubDir, 'sitecustomize.py'), INFERENCE_BOUNDARY_HOOK, 'utf8')

  const fixtureEnv = {
    ...process.env,
    APP_DATA_DIR: appDataDir,
    PYTHONDONTWRITEBYTECODE: '1',
  }
  const fixtureBootstrap = [
    'from backend.tests._textblock_stub import textblock_module_patch',
    'textblock_module_patch().start()',
    'import runpy, sys',
    `sys.argv = [${JSON.stringify(FIXTURE_SCRIPT)}, '--project-id', ${JSON.stringify(PROJECT_ID)}]`,
    `runpy.run_path(${JSON.stringify(FIXTURE_SCRIPT)}, run_name='__main__')`,
  ].join('\n')
  const fixtureOutput = await runProcess(python.command, [...python.prefix, '-c', fixtureBootstrap], {
    cwd: ROOT,
    env: fixtureEnv,
  })
  const fixtureResult = JSON.parse(fixtureOutput.trim())
  assert.equal(fixtureResult.project_id, PROJECT_ID)
  assert.equal(fixtureResult.page_count, 2)
  markStep('synthetic-fixture-created', { pages: fixtureResult.page_count, projectId: PROJECT_ID })

  const backendPort = await freePort()
  const frontendPort = await freePort()
  const backendBaseUrl = `http://127.0.0.1:${backendPort}`
  const frontendBaseUrl = `http://127.0.0.1:${frontendPort}`
  const pythonPath = [stubDir, ROOT, BACKEND_DIR, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter)
  const backendEnv = {
    ...fixtureEnv,
    APP_API_TOKEN: token,
    PYTHONPATH: pythonPath,
    WORKFLOW_E2E_INFERENCE_STUB_LOG: inferenceStubLog,
  }
  delete backendEnv.MANGA_TRANSLATOR_API_TOKEN

  backendServer = spawnManaged(python.command, [
    ...python.prefix,
    BACKEND_RUNNER,
    '--host', '127.0.0.1',
    '--port', String(backendPort),
  ], { cwd: ROOT, env: backendEnv })
  await waitForHttp(`${backendBaseUrl}/api/status`, backendServer, '真实 FastAPI 后端')
  markStep('real-fastapi-ready', { baseUrl: backendBaseUrl })

  const frontendEnv = {
    ...process.env,
    VITE_API_BASE_URL: '',
    VITE_API_TOKEN: token,
    VITE_DEV_PORT: String(frontendPort),
    VITE_DEV_PROXY_TARGET: backendBaseUrl,
    VITE_MOCK_API: '0',
  }
  frontendServer = spawnManaged(process.platform === 'win32' ? 'npm.cmd' : 'npm', [
    '--prefix', FRONTEND_V3_DIR,
    'run', 'dev', '--',
    '--host', '127.0.0.1',
    '--port', String(frontendPort),
    '--strictPort',
  ], {
    cwd: ROOT,
    env: frontendEnv,
    shell: process.platform === 'win32',
  })
  await waitForHttp(frontendBaseUrl, frontendServer, 'frontend-v3 正常 dev')
  markStep('frontend-v3-ready', { baseUrl: frontendBaseUrl })

  browser = await launchChromium({
    headless: process.env.WORKFLOW_E2E_HEADFUL !== '1',
    args: process.platform === 'linux' ? ['--no-sandbox'] : [],
  })
  const context = await browser.newContext({ acceptDownloads: true })
  browserPage = await context.newPage()
  browserPage.setDefaultTimeout(UI_TIMEOUT_MS)
  browserPage.setDefaultNavigationTimeout(UI_TIMEOUT_MS)
  browserPage.on('pageerror', error => pageErrors.push(error.message))
  browserPage.on('request', request => {
    try {
      const requestUrl = new URL(request.url())
      if (['http:', 'https:', 'ws:', 'wss:'].includes(requestUrl.protocol)
        && !['127.0.0.1', 'localhost'].includes(requestUrl.hostname)) {
        externalRequests.push(request.url())
      }
    } catch { /* Ignore non-HTTP resource URLs. */ }
  })

  // History/project entry → reopen the synthetic project → list and export.
  await browserPage.goto(`${frontendBaseUrl}/#/`, { waitUntil: 'domcontentloaded' })
  markStep('home-loaded', { url: browserPage.url() })
  await browserPage.getByRole('link', { name: '项目管理', exact: true }).click()
  const projectCard = browserPage.locator('.project-card').filter({ hasText: PROJECT_TITLE })
  await projectCard.waitFor({ state: 'visible' })
  await projectCard.getByRole('button', { name: '继续', exact: true }).click()
  await browserPage.getByRole('heading', { name: '页面列表', exact: true }).waitFor({ state: 'visible' })
  await browserPage.locator('.page-card').first().waitFor({ state: 'visible' })
  markStep('historical-project-reopened', { projectId: PROJECT_ID, url: browserPage.url() })
  markStep('page-list-rendered', { url: browserPage.url(), pageCards: await browserPage.locator('.page-card').count() })

  await captureBrowserSnapshot('before-export-control', browserPage)
  // The shared [data-tip] pseudo-element text is included in this button's
  // accessible name ("导出 导出结果或无字页"); match its visible-label prefix.
  const exportMenuButton = browserPage.getByRole('button', { name: /^导出(?:\s|$)/ })
  assert.equal(await exportMenuButton.count(), 1, '页面列表应有唯一的导出菜单按钮。')
  await exportMenuButton.click()
  const exportButton = browserPage.getByRole('menuitem', { name: '导出结果（.zip）', exact: true })
  assert.ok(await exportButton.isEnabled(), '合成项目应能导出当前译文结果。')
  await waitForDownload(browserPage, () => exportButton.click(), exportedArchive)
  const archiveBytes = await readFile(exportedArchive)
  assert.ok(archiveBytes.length > 64 && archiveBytes.subarray(0, 2).toString() === 'PK', '导出结果不是有效 ZIP。')
  markStep('export-downloaded', { bytes: archiveBytes.length, format: 'zip' })

  // Opening then cancelling whole-book translation must not start a task.
  await browserPage.getByRole('button', { name: '进入审校', exact: true }).click()
  await browserPage.locator('.region-card').first().waitFor({ state: 'visible' })
  markStep('review-opened', { url: browserPage.url() })
  const fullTranslateButton = browserPage.getByRole('button', { name: '重新翻译', exact: true })
  await fullTranslateButton.waitFor({ state: 'visible' })
  await browserPage.waitForFunction(() => {
    const button = Array.from(document.querySelectorAll('button'))
      .find(element => element.title === '使用当前设置和模型处理整本漫画')
    return Boolean(button && !button.disabled)
  }, null, { timeout: UI_TIMEOUT_MS })
  assert.ok(await fullTranslateButton.isEnabled(), '合成已译项目应提供整本翻译确认入口。')
  await fullTranslateButton.click()
  const confirmation = browserPage.getByRole('dialog', { name: '开始批量翻译前确认' })
  await confirmation.waitFor({ state: 'visible' })
  await confirmation.getByRole('button', { name: '再检查一下', exact: true }).click()
  await confirmation.waitFor({ state: 'hidden' })
  const taskSnapshot = await backendJson(backendBaseUrl, token, `/api/projects/${PROJECT_ID}/task`)
  const taskStatus = String(taskSnapshot.task?.status || '').toLowerCase()
  assert.ok(!taskStatus || ['completed', 'failed', 'cancelled', 'interrupted'].includes(taskStatus),
    `取消整本确认后不应启动任务，实际状态：${taskStatus}`)
  markStep('batch-translation-confirmation-cancelled', { taskStatus: taskStatus || 'none' })

  // Review save/reload uses the real project document command endpoint.
  await browserPage.locator('.region-card').first().waitFor({ state: 'visible' })
  let translationField = await firstTranslationField(browserPage)
  const savedTranslation = 'workflow-e2e 已保存的合成译文'
  const saveResponse = browserPage.waitForResponse(response =>
    response.request().method() === 'POST'
      && new URL(response.url()).pathname.includes(`/api/pages/${PROJECT_ID}/${PAGE_ID}/commands`),
  )
  await translationField.fill(savedTranslation)
  await translationField.press('Tab')
  const saved = await saveResponse
  assert.equal(saved.status(), 200, '审校命令保存失败。')
  await browserPage.reload({ waitUntil: 'domcontentloaded' })
  translationField = await firstTranslationField(browserPage)
  assert.equal(await translationField.inputValue(), savedTranslation, '审校译文没有在重载后保留。')
  markStep('review-save-survives-reload')

  // Provider selection marks and manual brush marks stay separate.
  await browserPage.getByRole('link', { name: /擦除/ }).click()
  await browserPage.locator('.erase-app').waitFor({ state: 'visible' })
  markStep('erase-workspace-opened')
  const provider = browserPage.locator('.erase-side label.field').filter({ hasText: '处理方式' }).locator('select')
  const sourceImage = browserPage.locator('.erase-stage > img')
  await sourceImage.waitFor({ state: 'visible' })
  await browserPage.waitForFunction(() => {
    const image = document.querySelector('.erase-stage > img')
    return Boolean(image?.complete && image.naturalWidth > 0 && image.naturalHeight > 0)
  })
  await browserPage.locator('.tool-list .tool-item').filter({ hasText: '框选' }).click()
  const imageBounds = await sourceImage.boundingBox()
  assert.ok(imageBounds && imageBounds.width > 40 && imageBounds.height > 40, '修图底图没有有效尺寸。')
  await browserPage.mouse.move(imageBounds.x + imageBounds.width * 0.30, imageBounds.y + imageBounds.height * 0.34)
  await browserPage.mouse.down()
  await browserPage.mouse.move(imageBounds.x + imageBounds.width * 0.38, imageBounds.y + imageBounds.height * 0.42, { steps: 4 })
  await browserPage.mouse.up()
  await browserPage.locator('.mark-item').first().waitFor({ state: 'visible' })
  assert.match(await browserPage.locator('.mark-item').first().innerText(), /框选/)
  markStep('selection-mark-created')

  await provider.selectOption('brush')
  const brushCanvas = browserPage.getByTestId('erase-brush-canvas')
  await brushCanvas.waitFor({ state: 'visible' })
  await waitForBrushReady(browserPage)
  assert.equal(await browserPage.locator('.mark-item').count(), 0, '切到修补画笔时不应显示选区标记。')

  const sizeInput = browserPage.getByRole('spinbutton', { name: '当前画笔大小数值' })
  const featherInput = browserPage.getByRole('spinbutton', { name: '画笔羽化数值' })
  const colorInput = browserPage.getByRole('textbox', { name: '画笔颜色 Hex' })
  await setNumberInput(sizeInput, 42)
  await setNumberInput(featherInput, 8)
  await colorInput.fill('#c04d7f')
  await colorInput.press('Tab')
  await drawStroke(browserPage, brushCanvas, [0.72, 0.80], [0.74, 0.80])

  // Sample a synthetic page pixel, then save it as the second paint stroke's
  // own color. The first stroke must retain its earlier explicit hex value.
  const samplePoint = { x: 0.15, y: 0.10 }
  const sampledColor = await colorAt(brushCanvas, samplePoint.x, samplePoint.y)
  await browserPage.getByTestId('erase-pick-color').click()
  const canvasBounds = await brushCanvas.boundingBox()
  assert.ok(canvasBounds)
  await browserPage.mouse.click(canvasBounds.x + canvasBounds.width * samplePoint.x, canvasBounds.y + canvasBounds.height * samplePoint.y)
  assert.equal((await colorInput.inputValue()).toLowerCase(), sampledColor, '吸色结果与画布像素不一致。')
  await drawStroke(browserPage, brushCanvas, [0.80, 0.62], [0.82, 0.62])

  await browserPage.getByTestId('erase-brush-tool-erase').click()
  assert.equal(await sizeInput.inputValue(), '20', '橡皮擦应保留独立的初始大小。')
  await setNumberInput(sizeInput, 61)
  await drawStroke(browserPage, brushCanvas, [0.84, 0.78], [0.85, 0.78])

  await browserPage.getByTestId('erase-brush-tool-restore').click()
  assert.equal(await sizeInput.inputValue(), '20', '恢复画笔应保留独立的初始大小。')
  await setNumberInput(sizeInput, 29)
  await drawStroke(browserPage, brushCanvas, [0.56, 0.55], [0.57, 0.55])

  await browserPage.getByTestId('erase-brush-tool-paint').click()
  assert.equal(await sizeInput.inputValue(), '42', '切换画笔模式后涂色大小没有保留。')
  assert.equal(await featherInput.inputValue(), '8', '涂色羽化参数没有保留。')
  assert.equal((await colorInput.inputValue()).toLowerCase(), sampledColor, '涂色颜色没有保留吸色结果。')
  assert.equal(await browserPage.locator('.mark-item').count(), 4)

  await provider.selectOption('local')
  assert.equal(await browserPage.locator('.mark-item').count(), 1, '切回选区擦除时应只显示原选区。')
  assert.match(await browserPage.locator('.mark-item').first().innerText(), /框选/)
  await provider.selectOption('brush')
  await brushCanvas.waitFor({ state: 'visible' })
  await waitForBrushReady(browserPage)
  assert.equal(await browserPage.locator('.mark-item').count(), 4, '切回画笔时应恢复各自的笔迹。')
  assert.ok((await browserPage.locator('.mark-list').innerText()).includes('涂色覆盖'))
  assert.ok((await browserPage.locator('.mark-list').innerText()).includes('橡皮擦'))
  assert.ok((await browserPage.locator('.mark-list').innerText()).includes('恢复原图'))

  const brushRequestPromise = browserPage.waitForRequest(request =>
    request.method() === 'POST' && new URL(request.url()).pathname.endsWith(`/api/pages/${PROJECT_ID}/${PAGE_ID}/brush-edit`),
  )
  const brushResponsePromise = browserPage.waitForResponse(response =>
    response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith(`/api/pages/${PROJECT_ID}/${PAGE_ID}/brush-edit`),
  )
  const baseRefreshPromise = browserPage.waitForResponse(response =>
    response.request().method() === 'GET'
      && response.status() === 200
      && new URL(response.url()).pathname.endsWith(`/api/pages/${PROJECT_ID}/${PAGE_ID}/base-image`),
  )
  await browserPage.getByRole('button', { name: '应用修补画笔', exact: true }).click()
  const [brushRequest, brushResponse, refreshedBase] = await Promise.all([
    brushRequestPromise,
    brushResponsePromise,
    baseRefreshPromise,
  ])
  assert.equal(brushResponse.status(), 200, '画笔修改没有保存到真实后端。')
  const operations = brushRequest.postDataJSON().operations
  assert.equal(operations.length, 4)
  assert.deepEqual(operations.map(operation => operation.mode), ['paint', 'paint', 'erase', 'restore'])
  const pageMinSide = 1280
  for (const [operation, expectedSize, expectedFeather] of [
    [operations[0], 42, 8],
    [operations[1], 42, 8],
    [operations[2], 61, 0],
    [operations[3], 29, 0],
  ]) {
    assert.ok(Math.abs(operation.size * pageMinSide - expectedSize) < 0.01, '每笔画笔大小未按创建时参数保存。')
    assert.ok(Math.abs(operation.feather * pageMinSide - expectedFeather) < 0.01, '每笔羽化参数未按创建时参数保存。')
  }
  assert.deepEqual(operations[0].color, [192, 77, 127], '第一笔颜色不应被后续吸色覆盖。')
  assert.deepEqual(operations[1].color, sampledColor.slice(1).match(/../g).map(channel => Number.parseInt(channel, 16)))
  const brushResult = await brushResponse.json()
  assert.equal(brushResult.brush_edit?.operation_count, 4)
  assert.ok(Number(brushResult.brush_edit?.changed_ratio) > 0, '画笔保存没有修改空页像素。')
  assert.ok(refreshedBase.ok(), '保存画笔后没有重新加载当前空页底图。')
  assert.equal(await browserPage.locator('.mark-item').count(), 0, '保存成功后画笔草稿应清空。')
  const savedPaintPixel = await colorAt(brushCanvas, 0.72, 0.80)
  const savedPaintRgb = savedPaintPixel.slice(1).match(/../g).map(channel => Number.parseInt(channel, 16))
  const paintPixelError = Math.max(...operations[0].color.map((channel, index) => Math.abs(channel - savedPaintRgb[index])))
  // The refreshed max_side image is served as quality-88 WebP, which can move
  // a sampled preview pixel by a few RGB levels. The POST payload above checks
  // the exact requested hex color; this checks that the saved mark is visible.
  assert.ok(paintPixelError <= 8,
    `刷新后的空页没有显示已保存的涂色笔迹：期望 rgb(${operations[0].color.join(',')})，实际 ${savedPaintPixel}。`)
  markStep('brush-edit-saved-and-preview-refreshed', { operationCount: operations.length, previewPixel: savedPaintPixel, maxChannelError: paintPixelError })

  // Selection marks remain isolated after the brush transaction.
  await provider.selectOption('local')
  assert.equal(await browserPage.locator('.mark-item').count(), 1)
  await browserPage.locator('.erase-side-foot').getByRole('button', { name: '清空', exact: true }).click()
  assert.equal(await browserPage.locator('.mark-item').count(), 0)
  await browserPage.getByRole('button', { name: '整页处理', exact: true }).click()

  // The real advanced-erase workflow creates and serves a candidate preview;
  // only detector and inpainting calls are deterministic local substitutes.
  const previewRequestPromise = browserPage.waitForRequest(request =>
    request.method() === 'POST' && new URL(request.url()).pathname.endsWith(`/api/pages/${PROJECT_ID}/${PAGE_ID}/advanced-erase`),
  )
  const previewResponsePromise = browserPage.waitForResponse(response =>
    response.request().method() === 'POST' && new URL(response.url()).pathname.endsWith(`/api/pages/${PROJECT_ID}/${PAGE_ID}/advanced-erase`),
  )
  await browserPage.getByRole('button', { name: '生成整页预览', exact: true }).click()
  const [previewRequest, previewResponse] = await Promise.all([previewRequestPromise, previewResponsePromise])
  assert.equal(previewRequest.postDataJSON().action, 'local-advanced-preview')
  assert.equal(previewResponse.status(), 200, '真实后端没有生成整页候选预览。')
  const eraseWorkspace = browserPage.locator('.erase-view')
  const erasePreview = browserPage.getByTestId('erase-preview')
  await erasePreview.waitFor({ state: 'visible' })
  assert.equal(await browserPage.locator('.compare3 figure').count(), 3, '预览应同时显示原图、候选结果和遮罩。')
  for (const alt of ['原图', '擦除结果', 'mask']) {
    const image = browserPage.getByAltText(alt, { exact: true })
    await image.waitFor({ state: 'visible' })
    await image.evaluate(element => element.decode())
    assert.ok(await image.evaluate(element => element.naturalWidth > 0), `${alt}预览没有加载。`)
  }
  await browserPage.getByRole('checkbox', { name: '显示擦除范围' }).check()
  const overlay = browserPage.locator('.preview-mask-overlay')
  await overlay.waitFor({ state: 'visible' })
  await overlay.evaluate(element => element.decode())

  const viewport = browserPage.viewportSize()
  const workspaceBounds = await eraseWorkspace.boundingBox()
  const sideFooterBounds = await browserPage.locator('.erase-side-foot').boundingBox()
  const previewBounds = await erasePreview.boundingBox()
  assert.ok(viewport && workspaceBounds && sideFooterBounds && previewBounds)
  assert.ok(
    workspaceBounds.height >= viewport.height * 0.6,
    `显示整页预览时修图工作区被压缩到 ${workspaceBounds.height}px。`,
  )
  assert.ok(
    workspaceBounds.y + workspaceBounds.height <= previewBounds.y + 1,
    '整页预览与原修图工作区发生重叠。',
  )
  assert.ok(
    sideFooterBounds.y + sideFooterBounds.height <= workspaceBounds.y + workspaceBounds.height + 1,
    '修图侧栏按钮被挤出工作区边界。',
  )
  markStep('erase-layout-separated', {
    workspaceHeight: Math.round(workspaceBounds.height),
    sideFooterBottom: Math.round(sideFooterBounds.y + sideFooterBounds.height),
    previewTop: Math.round(previewBounds.y),
  })

  const eraseApp = browserPage.locator('.erase-app')
  const scrollTopBeforePreview = await eraseApp.evaluate(element => element.scrollTop)
  await erasePreview.scrollIntoViewIfNeeded()
  const scrollTopAtPreview = await eraseApp.evaluate(element => element.scrollTop)
  assert.ok(scrollTopAtPreview > scrollTopBeforePreview, '整页预览应能通过修图工作区滚动到达。')
  markStep('erase-preview-reachable-by-scroll', { scrollTop: Math.round(scrollTopAtPreview) })
  await captureBrowserSnapshot('before-preview-discard', browserPage)

  const discardPreviewButton = browserPage.getByRole('button', { name: '放弃结果', exact: true })
  const applyPreviewButton = browserPage.getByRole('button', { name: '应用新空页', exact: true })
  await assertControlInViewport(browserPage, applyPreviewButton, '应用新空页按钮')
  await assertControlInViewport(browserPage, discardPreviewButton, '放弃结果按钮')
  markStep('preview-actions-reachable', { discardEnabled: true, applyEnabled: true })
  await captureBrowserSnapshot('preview-actions-in-view', browserPage)
  await discardPreviewButton.click()
  await browserPage.getByTestId('erase-preview').waitFor({ state: 'detached' })
  markStep('advanced-preview-discarded')

  const inferenceCalls = await readFile(inferenceStubLog, 'utf8')
  assert.match(inferenceCalls, /detect_text_mask/)
  assert.match(inferenceCalls, /erase_selection/)
  assert.deepEqual(externalRequests, [], `workflow E2E 不应访问在线服务：${externalRequests.join(', ')}`)
  assert.deepEqual(pageErrors, [], `浏览器页面出现运行时错误：${pageErrors.join('\n')}`)

  markStep('all-workflow-assertions-passed', { externalRequests: externalRequests.length, pageErrors: pageErrors.length })
  await persistWorkflowSteps()
  console.log('workflow-e2e passed: history/project, review save/reload, batch-cancel, export, brush edit, and local erase preview/discard.')
  await context.close()
}

try {
  await main()
} catch (error) {
  markStep('failed', { error: error instanceof Error ? error.message : String(error) })
  await persistWorkflowSteps().catch(() => {})
  await captureBrowserSnapshot('failure', browserPage).catch(captureError => {
    console.error(`Could not capture browser failure state: ${captureError instanceof Error ? captureError.message : captureError}`)
  })
  console.error(error instanceof Error ? error.stack || error.message : String(error))
  for (const entry of children) {
    if (entry.output) console.error(`--- child process output (pid ${entry.child.pid ?? 'unknown'}) ---\n${entry.output}`)
  }
  process.exitCode = 1
} finally {
  await browser?.close().catch(() => {})
  for (const entry of [...children].reverse()) await stopChild(entry)
  if (temporaryRoot) {
    if (process.env.WORKFLOW_E2E_KEEP_DATA === '1') {
      console.log(`workflow-e2e temporary data kept at ${temporaryRoot}`)
    } else {
      await rm(temporaryRoot, { recursive: true, force: true })
    }
  }
  if (process.exitCode !== 1) await persistWorkflowSteps().catch(() => {})
}
