import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { existsSync } from 'node:fs'
import { promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { findAvailablePort } from '../../scripts/local-port.mjs'
import { launchChromium } from './playwright-launcher.mjs'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const frontendDir = path.resolve(__dirname, '..')
const repoRoot = path.resolve(frontendDir, '..')
const backendDir = path.join(repoRoot, 'backend')
const artifactDir = path.join(frontendDir, 'test-artifacts', 'v2-workspace')

const FIXTURE_PROJECT_ID = 'canvas-e2e-fixture'
const FIXTURE_PROJECT_TITLE = 'Canvas E2E Fixture'
const FIXTURE_OPENAI_BASE_URL = 'https://api.example.invalid/v1'
const FIXTURE_OPENAI_MODEL = 'fixture-model'
const PREVIEW_TYPOGRAPHY_ONLY = process.argv.includes('--preview-typography-only')
const FIXTURE_PAGE_WIDTH = 1280
const FIXTURE_PAGE_ONE_FONT_SIZES = new Map([
  ['fixture-0001-r1', 34],
  ['fixture-0001-r2', 30],
  ['fixture-0001-r3', 24],
])
const FIXTURE_PAGE_ONE_FONT_IDS = new Map([
  ['fixture-0001-r1', 'system:SourceHanSansSC-Regular-2.otf'],
  ['fixture-0001-r2', 'system:SourceHanSansSC-Medium-2.otf'],
  ['fixture-0001-r3', 'system:SourceHanSansSC-Regular-2.otf'],
])
const ownsAppDataDir = !process.env.APP_DATA_DIR
const E2E_APP_DATA_DIR = process.env.APP_DATA_DIR || await fs.mkdtemp(path.join(os.tmpdir(), 'manga-translator-v2-e2e-'))
const E2E_API_TOKEN = process.env.CANVAS_E2E_API_TOKEN || process.env.APP_API_TOKEN || randomBytes(32).toString('base64url')
process.env.APP_DATA_DIR = E2E_APP_DATA_DIR
process.env.APP_API_TOKEN = E2E_API_TOKEN
const generatedBackendPort = process.env.CANVAS_E2E_BACKEND_URL
  ? ''
  : await findAvailablePort({ preferredPort: 0, host: '127.0.0.1' })
const generatedFrontendPort = process.env.CANVAS_E2E_FRONTEND_URL
  ? ''
  : await findAvailablePort({
      preferredPort: 0,
      host: '127.0.0.1',
      blockedPorts: new Set([generatedBackendPort]),
    })
const BACKEND_URL = process.env.CANVAS_E2E_BACKEND_URL || `http://127.0.0.1:${generatedBackendPort}`
const FRONTEND_URL = process.env.CANVAS_E2E_FRONTEND_URL || `http://127.0.0.1:${generatedFrontendPort}`

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function apiHeaders() {
  return { Authorization: `Bearer ${E2E_API_TOKEN}` }
}

function isIgnorableConsoleEntry(entry) {
  return (
    entry.includes('ERR_CONTENT_LENGTH_MISMATCH')
    || entry.includes('Failed to decode downloaded font')
    || entry.includes('OTS parsing error: vmtx: Required vhea table missing')
    || entry.includes('vmtx: Failed to parse table')
  )
}

function createLogger(prefix, output = process.stdout) {
  return (chunk) => {
    const text = chunk.toString()
    if (!text.trim()) return
    output.write(`[${prefix}] ${text}`)
  }
}

function spawnProcess(command, args, options = {}) {
  const useProcessGroup = Boolean(options.processGroup && process.platform !== 'win32')
  const child = spawn(command, args, {
    cwd: options.cwd,
    env: {
      ...process.env,
      ...options.env,
    },
    detached: useProcessGroup,
    shell: false,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  child.solarProcessGroup = useProcessGroup
  child.stdout?.on('data', createLogger(options.label || path.basename(command)))
  child.stderr?.on('data', createLogger(options.label || path.basename(command), process.stderr))
  return child
}

function pickBackendPython() {
  const candidates = process.platform === 'win32'
    ? [path.join(repoRoot, 'backend', 'venv', 'Scripts', 'python.exe')]
    : [
        path.join(repoRoot, 'backend', '.venv-mac', 'bin', 'python'),
        process.env.PYTHON || 'python3',
      ]
  return candidates.find((candidate) => candidate && (candidate === 'python3' || candidate === process.env.PYTHON || existsSync(candidate))) || candidates[0]
}

async function httpOk(url) {
  try {
    const response = await fetch(url, { headers: apiHeaders() })
    return response.ok
  } catch {
    return false
  }
}

async function waitForHttp(url, label, timeoutMs = 30000) {
  const start = Date.now()
  while (Date.now() - start < timeoutMs) {
    if (await httpOk(url)) {
      return
    }
    await sleep(500)
  }
  throw new Error(`等待 ${label} 超时：${url}`)
}

async function stopProcesses(processes) {
  for (const child of processes.reverse()) {
    if (!child) continue
    const signal = (name) => {
      try {
        if (child.solarProcessGroup && child.pid) {
          process.kill(-child.pid, name)
          return
        }
        child.kill(name)
      } catch (error) {
        if (error?.code !== 'ESRCH') {
          throw error
        }
      }
    }
    signal('SIGTERM')
    await sleep(300)
    signal('SIGKILL')
  }
}

async function ensureFixture() {
  const python = pickBackendPython()
  const fixtureScript = path.join(repoRoot, 'scripts', 'create_canvas_test_fixture.py')
  await new Promise((resolve, reject) => {
    const child = spawnProcess(python, [fixtureScript, '--project-id', FIXTURE_PROJECT_ID], {
      cwd: repoRoot,
      label: 'fixture-v2',
    })
    child.once('error', reject)
    child.once('exit', (code) => {
      if (code === 0) {
        resolve()
        return
      }
      reject(new Error(`创建 V2 测试夹具失败，退出码 ${code}`))
    })
  })
}

async function seedPersistedSettings() {
  const response = await fetch(`${BACKEND_URL}/api/app/settings`, {
    method: 'PATCH',
    headers: {
      ...apiHeaders(),
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      translator: 'openai-compatible',
      target_lang: 'CHS',
      openai_base_url: FIXTURE_OPENAI_BASE_URL,
      openai_model: FIXTURE_OPENAI_MODEL,
      font_key: 'system:SourceHanSansSC-Regular-2.otf',
      font_style_mode: 'auto-map',
      style_font_gothic_key: 'system:SourceHanSansSC-Regular-2.otf',
      style_font_mincho_key: 'system:SourceHanSansSC-Medium-2.otf',
      style_font_rounded_key: 'system:SourceHanSansSC-Bold.otf',
      style_font_cartoon_key: 'system:SourceHanSansSC-Bold.otf',
      style_font_handwritten_key: 'system:SourceHanSansSC-Medium-2.otf',
      style_font_sfx_key: 'system:SourceHanSansSC-Bold.otf',
    }),
  })
  if (!response.ok) {
    throw new Error(`无法准备持久设置夹具：HTTP ${response.status}`)
  }
}

function hashPreviewFontKey(value) {
  let hash = 2166136261
  for (const char of String(value || '')) {
    hash ^= char.codePointAt(0) || 0
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(36)
}

async function ensureServices() {
  const started = []
  try {
    if (!(await httpOk(`${BACKEND_URL}/api/status`))) {
      const python = pickBackendPython()
      const backendProcess = spawnProcess(
        python,
        ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', new URL(BACKEND_URL).port || '8000'],
        {
          cwd: backendDir,
          env: {
            APP_DATA_DIR: E2E_APP_DATA_DIR,
            APP_API_TOKEN: E2E_API_TOKEN,
          },
          label: 'backend-v2-e2e',
          processGroup: true,
        }
      )
      started.push(backendProcess)
      await waitForHttp(`${BACKEND_URL}/api/status`, '后端服务')
    }

    if (!(await httpOk(FRONTEND_URL))) {
      const frontendProcess = spawnProcess(
        'npm',
        ['run', 'dev', '--', '--host', '127.0.0.1', '--port', new URL(FRONTEND_URL).port || '5173', '--strictPort'],
        {
          cwd: frontendDir,
          env: {
            FRONTEND_PORT: new URL(FRONTEND_URL).port || '5173',
            VITE_API_BASE_URL: BACKEND_URL,
            VITE_API_TOKEN: E2E_API_TOKEN,
            VITE_DEV_PORT: new URL(FRONTEND_URL).port || '5173',
            VITE_DEV_PROXY_TARGET: BACKEND_URL,
          },
          label: 'frontend-v2-e2e',
          processGroup: true,
        }
      )
      started.push(frontendProcess)
      await waitForHttp(FRONTEND_URL, '前端服务')
    }

    return started
  } catch (error) {
    await stopProcesses(started)
    throw error
  }
}

async function saveScreenshot(page, name) {
  const filePath = path.join(artifactDir, name)
  await page.screenshot({ path: filePath, fullPage: false })
  return filePath
}

async function createSupplementFixture() {
  const sourceImage = path.join(E2E_APP_DATA_DIR, 'output', FIXTURE_PROJECT_ID, 'source', '0001.jpg')
  const targetImage = path.join(artifactDir, 'Fixture Page 1.jpg')
  await fs.copyFile(sourceImage, targetImage)
  return targetImage
}

async function createInvalidUploadFixture() {
  const targetArchive = path.join(artifactDir, 'invalid-upload.zip')
  await fs.writeFile(targetArchive, 'this is not a zip archive', 'utf8')
  return targetArchive
}

async function assertText(locator, expected, message) {
  const text = (await locator.textContent()) || ''
  if (!text.includes(expected)) {
    throw new Error(`${message}：期望包含 "${expected}"，实际为 "${text}"`)
  }
}

function isPageCommandResponse(response, commandType) {
  if (!response.url().includes(`/api/pages/${FIXTURE_PROJECT_ID}/`) || !response.url().includes('/commands')) {
    return false
  }
  if (response.request().method() !== 'POST') {
    return false
  }
  try {
    const payload = response.request().postDataJSON()
    return (payload?.commands || []).some((command) => command?.type === commandType)
  } catch {
    return false
  }
}

async function waitForLocatorCount(locator, expected, message, timeoutMs = 20000) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    const actual = await locator.count()
    if (actual === expected) {
      return
    }
    await sleep(100)
  }
  throw new Error(`${message}：期望 ${expected}，实际 ${await locator.count()}`)
}

async function readBoxMetrics(locator) {
  return locator.evaluate((element) => {
    const label = element.querySelector('.style-box-label')
    const boxRect = element.getBoundingClientRect()
    const labelRect = label?.getBoundingClientRect() || null
    return {
      left: Number.parseFloat(element.style.left || '0'),
      top: Number.parseFloat(element.style.top || '0'),
      borderRadius: getComputedStyle(element).borderRadius,
      labelRect: labelRect
        ? {
            left: labelRect.left,
            top: labelRect.top,
            right: labelRect.right,
            bottom: labelRect.bottom,
          }
        : null,
      boxRect: {
        left: boxRect.left,
        top: boxRect.top,
        right: boxRect.right,
        bottom: boxRect.bottom,
      },
    }
  })
}

async function readPreviewTextCentering(locator) {
  return locator.evaluate((element) => {
    const previewText = element.querySelector('.style-box-preview-text-content')
    if (!previewText) {
      return null
    }
    const boxRect = element.getBoundingClientRect()
    const textRect = previewText.getBoundingClientRect()
    const boxCenterX = (boxRect.left + boxRect.right) / 2
    const boxCenterY = (boxRect.top + boxRect.bottom) / 2
    const textCenterX = (textRect.left + textRect.right) / 2
    const textCenterY = (textRect.top + textRect.bottom) / 2
    return {
      boxWidth: boxRect.width,
      boxHeight: boxRect.height,
      deltaX: Math.abs(textCenterX - boxCenterX),
      deltaY: Math.abs(textCenterY - boxCenterY),
      textAlign: getComputedStyle(previewText).textAlign,
    }
  })
}

async function readCanvasPreviewTypography(page) {
  const previewTexts = page.locator('.v2-pane-card-frame .style-box-preview-text-content')
  await waitForLocatorCount(
    previewTexts,
    FIXTURE_PAGE_ONE_FONT_SIZES.size,
    '框页没有完整显示首屏文字预览',
  )
  await page.evaluate(async () => {
    await Promise.race([
      document.fonts?.ready,
      new Promise((resolve) => setTimeout(resolve, 20000)),
    ])
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
  return previewTexts.evaluateAll((elements) => elements.map((element) => {
    const stage = element.closest('.v2-canvas-stage')
    const computed = getComputedStyle(element)
    return {
      regionId: element.dataset.regionId || '',
      fontSize: Number.parseFloat(computed.fontSize || '0'),
      fontFamily: computed.fontFamily,
      fontFaces: Array.from(document.fonts)
        .filter((face) => computed.fontFamily.includes(face.family))
        .map((face) => ({ family: face.family, status: face.status })),
      stageWidth: stage?.clientWidth || 0,
      stageTransform: getComputedStyle(stage).transform,
    }
  }))
}

async function assertCanvasPreviewTypography(page) {
  const initialMetrics = await readCanvasPreviewTypography(page)
  const errors = []
  for (const metric of initialMetrics) {
    const sourceFontSize = FIXTURE_PAGE_ONE_FONT_SIZES.get(metric.regionId)
    if (!sourceFontSize) {
      errors.push(`出现未知框 ${metric.regionId}`)
      continue
    }
    const expectedFontSize = sourceFontSize * metric.stageWidth / FIXTURE_PAGE_WIDTH
    if (Math.abs(metric.fontSize - expectedFontSize) > 1) {
      errors.push(
        `${metric.regionId} 初始字号应为 ${expectedFontSize.toFixed(2)}px，实际为 ${metric.fontSize.toFixed(2)}px`,
      )
    }
    const expectedFontId = FIXTURE_PAGE_ONE_FONT_IDS.get(metric.regionId)
    const expectedFontAlias = `codex-preview-font-${hashPreviewFontKey(expectedFontId)}`
    if (!metric.fontFamily.includes(expectedFontAlias)) {
      errors.push(
        `${metric.regionId} 首次打开字体应为 ${expectedFontId}，实际为 ${metric.fontFamily}`,
      )
    }
    if (!metric.fontFaces.some((face) => face.family === expectedFontAlias && face.status === 'loaded')) {
      errors.push(
        `${metric.regionId} 的字体 ${expectedFontId} 没有真正加载：${JSON.stringify(metric.fontFaces)}`,
      )
    }
  }

  const initialFontSizes = new Map(initialMetrics.map((metric) => [metric.regionId, metric.fontSize]))
  await page.getByRole('button', { name: '定位当前框' }).click()
  await page.waitForTimeout(150)
  const focusedMetrics = await readCanvasPreviewTypography(page)
  for (const metric of focusedMetrics) {
    const initialFontSize = initialFontSizes.get(metric.regionId)
    if (initialFontSize && Math.abs(metric.fontSize - initialFontSize) > 0.5) {
      errors.push(
        `${metric.regionId} 定位缩放后 CSS 字号从 ${initialFontSize.toFixed(2)}px 变成 ${metric.fontSize.toFixed(2)}px，画布缩放被重复计算`,
      )
    }
  }

  if (errors.length) {
    throw new Error(`框页字体预览与嵌字坐标不一致：\n- ${errors.join('\n- ')}`)
  }
}

async function main() {
  await fs.mkdir(artifactDir, { recursive: true })

  let started = []
  let browser
  try {
    started = await ensureServices()
    await ensureFixture()
    await seedPersistedSettings()

    browser = await launchChromium({ headless: true })
    const page = await browser.newPage({ viewport: { width: 1440, height: 1024 } })
    const consoleErrors = []
    page.on('console', (message) => {
      if (['error', 'warning'].includes(message.type())) {
        consoleErrors.push(`[${message.type()}] ${message.text()}`)
      }
    })
    page.on('pageerror', (error) => {
      consoleErrors.push(`[pageerror] ${error.message}`)
    })

    await page.goto(FRONTEND_URL, { waitUntil: 'networkidle' })
    await page.getByTestId('v2-home-view').waitFor({ state: 'visible', timeout: 20000 })
    const homeGalleryCount = await page.locator('.v2-home-gallery-card').count()
    if (homeGalleryCount !== 0) {
      throw new Error(`首页仍然保留了示例卡片：${homeGalleryCount}`)
    }
    const homeShot = await saveScreenshot(page, 'v2-home.png')

    if (PREVIEW_TYPOGRAPHY_ONLY) {
      await page.getByRole('banner').getByRole('button', { name: '项目管理' }).click()
      await page.getByTestId('v2-history-modal').waitFor({ state: 'visible', timeout: 20000 })
      const fixtureCard = page.locator('.v2-history-card', { hasText: FIXTURE_PROJECT_TITLE }).first()
      await fixtureCard.waitFor({ state: 'visible', timeout: 20000 })
      await fixtureCard.getByRole('button', { name: '恢复项目' }).click()
      await page.getByTestId('v2-picker-view').waitFor({ state: 'visible', timeout: 20000 })
      await page.locator('.v2-page-card').first().click()
      await page.getByTestId('v2-review-view').waitFor({ state: 'visible', timeout: 20000 })
      await page.locator('.v2-region-card').first().click()
      await assertCanvasPreviewTypography(page)
      console.log(JSON.stringify({ projectId: FIXTURE_PROJECT_ID, previewTypography: 'passed' }, null, 2))
      return
    }

    await page.getByRole('banner').getByRole('button', { name: '打开设置' }).click()
    await page.getByTestId('v2-settings-panel').waitFor({ state: 'visible', timeout: 20000 })
    const persistedBaseUrl = await page.getByTestId('v2-settings-panel').getByLabel('API Base URL').inputValue()
    const persistedModel = await page.getByTestId('v2-settings-panel')
      .getByPlaceholder('gpt-4o / deepseek-chat / ...')
      .inputValue()
    if (persistedBaseUrl !== FIXTURE_OPENAI_BASE_URL || persistedModel !== FIXTURE_OPENAI_MODEL) {
      throw new Error(
        `OpenAI Compatible 设置重载后丢失：base=${persistedBaseUrl} model=${persistedModel}`
      )
    }
    await page.getByTestId('v2-settings-panel').getByRole('button', { name: '关闭设置' }).click()

    await page.getByRole('banner').getByRole('button', { name: '项目管理' }).click()
    await page.getByTestId('v2-history-modal').waitFor({ state: 'visible', timeout: 20000 })
    await page.getByTestId('v2-history-modal').getByRole('button', { name: '新建项目' }).waitFor({ state: 'visible', timeout: 20000 })
    const historyShot = await saveScreenshot(page, 'v2-history-modal.png')

    const fixtureCard = page.locator('.v2-history-card', { hasText: FIXTURE_PROJECT_TITLE }).first()
    await fixtureCard.waitFor({ state: 'visible', timeout: 20000 })
    await fixtureCard.getByRole('button', { name: '恢复项目' }).click()

    await page.getByTestId('v2-picker-view').waitFor({ state: 'visible', timeout: 20000 })
    await assertText(page.locator('.v2-section-title').first(), FIXTURE_PROJECT_TITLE, '选页页项目标题不正确')
    await page.getByRole('banner').getByRole('button', { name: '项目管理' }).waitFor({ state: 'visible', timeout: 20000 })
    await page.getByRole('button', { name: '专有名词库' }).waitFor({ state: 'visible', timeout: 20000 })
    await page.getByTestId('v2-workflow-strip').waitFor({ state: 'visible', timeout: 20000 })
    await page.getByTestId('v2-workflow-strip').getByRole('button', { name: /识别并生成空页|重新识别并生成空页/ }).waitFor({ state: 'visible', timeout: 20000 })
    await page.getByTestId('v2-workflow-strip').getByRole('button', { name: /翻译并生成初稿|重新翻译并生成初稿/ }).waitFor({ state: 'visible', timeout: 20000 })
    const pickerShot = await saveScreenshot(page, 'v2-picker.png')

    const pageCards = page.locator('.v2-page-card')
    const pageCardCount = await pageCards.count()
    if (pageCardCount < 2) {
      throw new Error(`选页页缩略图数量异常：${pageCardCount}`)
    }

    const invalidUpload = await createInvalidUploadFixture()
    await Promise.all([
      page.waitForResponse((response) => (
        response.url().includes('/api/upload')
        && response.request().method() === 'POST'
        && !response.ok()
      )),
      page.getByTestId('v2-project-file-input').setInputFiles(invalidUpload),
    ])
    await page.getByTestId('v2-picker-view').waitFor({ state: 'visible', timeout: 20000 })
    await assertText(page.locator('.v2-section-title').first(), FIXTURE_PROJECT_TITLE, '上传失败后原项目标题被清空')
    if (await pageCards.count() !== pageCardCount) {
      throw new Error('上传失败后原项目页列表被替换')
    }
    await page.waitForTimeout(100)
    for (let index = consoleErrors.length - 1; index >= 0; index -= 1) {
      if (consoleErrors[index].includes('status of 400 (Bad Request)')) {
        consoleErrors.splice(index, 1)
        break
      }
    }

    await pageCards.first().click()

    await page.getByTestId('v2-review-view').waitFor({ state: 'visible', timeout: 20000 })
    await page.locator('.v2-review-toolbar').waitFor({ state: 'visible', timeout: 20000 })
    await page.locator('.v2-region-card').first().click()
    const activeBox = page.locator('.v2-canvas-shell .style-box.active').first()
    await activeBox.waitFor({ state: 'visible', timeout: 20000 })
    const initialBoxMetrics = await readBoxMetrics(activeBox)
    if (initialBoxMetrics.borderRadius !== '0px') {
      throw new Error(`当前选中框仍然不是直角边：${initialBoxMetrics.borderRadius}`)
    }
    const labelOutsideBox = !initialBoxMetrics.labelRect || (
      initialBoxMetrics.labelRect.bottom <= initialBoxMetrics.boxRect.top + 1
      || initialBoxMetrics.labelRect.top >= initialBoxMetrics.boxRect.bottom - 1
      || initialBoxMetrics.labelRect.right <= initialBoxMetrics.boxRect.left + 1
      || initialBoxMetrics.labelRect.left >= initialBoxMetrics.boxRect.right - 1
    )
    if (!labelOutsideBox) {
      throw new Error('当前选中框的编号标签仍然压在框内容区域内')
    }
    const handleCount = await activeBox.locator('.style-box-handle').count()
    if (handleCount === 0) {
      throw new Error('当前选中框没有出现拖拽/缩放控制点')
    }
    await page.getByRole('button', { name: '定位当前框' }).click()
    await page.waitForTimeout(120)
    const focusedHandleBox = await activeBox.locator('.style-box-handle').first().boundingBox()
    const focusedSettingsBox = await page.locator('.v2-pane-card-frame .style-box-settings-button').first().boundingBox()
    if (!focusedHandleBox || focusedHandleBox.width > 14 || focusedHandleBox.height > 14) {
      throw new Error(`高倍定位后缩放控制点不再保持固定尺寸：${JSON.stringify(focusedHandleBox)}`)
    }
    if (!focusedSettingsBox || focusedSettingsBox.width > 30 || focusedSettingsBox.height > 30) {
      throw new Error(`高倍定位后样式按钮不再保持固定尺寸：${JSON.stringify(focusedSettingsBox)}`)
    }
    await page.getByRole('button', { name: '重置视图' }).click()
    const hudText = (await page.locator('.v2-canvas-hud').first().textContent()) || ''
    if (/滚轮|Ctrl|Shift|快捷键/.test(hudText)) {
      throw new Error(`画布 HUD 仍然显示常驻操作说明：${hudText}`)
    }
    const translationInput = page.locator('.v2-region-card .translation-review-input').first()
    const originalTranslation = await translationInput.inputValue()
    const editedTranslation = `${originalTranslation || 'E2E translation'} / stable edit`
    const translationField = page.locator('.v2-region-card.active label.v2-field').first()
    const fontSizeInput = page.locator('.v2-region-card.active .v2-stepper input').first()
    const fontSizeInputType = await fontSizeInput.getAttribute('type')
    if (fontSizeInputType !== 'text') {
      throw new Error(`字号输入框仍然会显示浏览器数字上下控件：type=${fontSizeInputType}`)
    }
    const fieldHeightBeforeEdit = (await translationField.boundingBox())?.height
    if (!fieldHeightBeforeEdit) {
      throw new Error('无法读取译文字段高度，无法验证保存状态不会造成跳变')
    }
    const editSave = page.waitForResponse((response) => (
      response.url().includes(`/api/pages/${FIXTURE_PROJECT_ID}/`)
      && response.url().includes('/commands')
      && response.request().method() === 'POST'
      && response.ok()
    ))
    await translationInput.fill(editedTranslation)
    await page.locator('.v2-region-card.active .v2-region-commit-icon.is-dirty.is-visible').waitFor({ state: 'visible', timeout: 20000 })
    if (await page.locator('.v2-region-card.active .v2-region-commit-state').count()) {
      throw new Error('右侧栏仍然渲染会改变高度的保存状态文字标签')
    }
    const fieldHeightWithDirtyIcon = (await translationField.boundingBox())?.height
    if (Math.abs((fieldHeightWithDirtyIcon || 0) - fieldHeightBeforeEdit) > 1) {
      throw new Error(`保存状态图标仍然导致译文字段高度跳变：before=${fieldHeightBeforeEdit} after=${fieldHeightWithDirtyIcon}`)
    }
    await translationInput.blur()
    const editSaveResponse = await editSave
    const editSavePayload = editSaveResponse.request().postDataJSON()
    if (!Number.isInteger(editSavePayload?.expected_revision) || editSavePayload.expected_revision < 0) {
      throw new Error(`页面命令没有携带服务端文档版本：${JSON.stringify(editSavePayload)}`)
    }
    await page.waitForTimeout(150)
    if (await page.locator('.v2-region-commit-icon.is-failed.is-visible').count()) {
      throw new Error('译文编辑提交后出现失败状态')
    }
    const exportResultButton = page.getByRole('button', { name: '导出结果', exact: true }).first()
    if (!(await exportResultButton.isDisabled())) {
      throw new Error('译文编辑后旧嵌字结果仍然可以直接导出')
    }
    const previewCentering = await readPreviewTextCentering(activeBox)
    if (!previewCentering) {
      throw new Error('译文编辑后没有出现框内预览文本，无法验证居中效果')
    }
    if (
      previewCentering.deltaX > Math.max(8, previewCentering.boxWidth * 0.18)
      || previewCentering.deltaY > Math.max(8, previewCentering.boxHeight * 0.18)
    ) {
      throw new Error(`框内预览文本没有稳定居中：${JSON.stringify(previewCentering)}`)
    }
    const boxBounds = await activeBox.boundingBox()
    if (!boxBounds) {
      throw new Error('无法读取当前选中框的位置，无法验证拖拽能力')
    }
    await page.mouse.move(boxBounds.x + boxBounds.width / 2, boxBounds.y + boxBounds.height / 2)
    await page.mouse.down()
    await page.mouse.move(boxBounds.x + boxBounds.width / 2 + 18, boxBounds.y + boxBounds.height / 2 + 12, { steps: 6 })
    await page.mouse.up()
    await page.waitForTimeout(250)
    const draggedBoxMetrics = await readBoxMetrics(activeBox)
    if (draggedBoxMetrics.left === initialBoxMetrics.left && draggedBoxMetrics.top === initialBoxMetrics.top) {
      throw new Error('拖动当前选中框后，位置没有变化')
    }
    await page.evaluate(() => {
      window.dispatchEvent(new KeyboardEvent('keydown', {
        key: 'ArrowLeft',
        ctrlKey: true,
        bubbles: true,
      }))
    })
    await page.waitForTimeout(150)
    const nudgedBoxMetrics = await readBoxMetrics(activeBox)
    if (nudgedBoxMetrics.left === draggedBoxMetrics.left && nudgedBoxMetrics.top === draggedBoxMetrics.top) {
      throw new Error('方向键微调当前选中框后，位置没有变化')
    }
    const translationAfterLayoutEdit = await translationInput.inputValue()
    if (translationAfterLayoutEdit !== editedTranslation) {
      throw new Error(`框体编辑后译文发生回滚：${translationAfterLayoutEdit}`)
    }

    const regionCards = page.locator('.v2-region-card')
    const regionCountBeforeManualDraw = await regionCards.count()
    await page.getByRole('button', { name: '手动添加框' }).click()
    const canvasStage = page.locator('.v2-pane-card-frame .v2-canvas-stage').first()
    const canvasStageBounds = await canvasStage.boundingBox()
    if (!canvasStageBounds) {
      throw new Error('无法读取框页画布位置，无法验证手动添加框')
    }
    const createRegionResponse = page.waitForResponse(
      (response) => isPageCommandResponse(response, 'create_region') && response.ok(),
      { timeout: 30000 },
    )
    const recognizeRegionResponse = page.waitForResponse(
      (response) => isPageCommandResponse(response, 'recognize_manual_region') && response.ok(),
      { timeout: 60000 },
    )
    await page.mouse.move(
      canvasStageBounds.x + canvasStageBounds.width * 0.70,
      canvasStageBounds.y + canvasStageBounds.height * 0.72,
    )
    await page.mouse.down()
    await page.mouse.move(
      canvasStageBounds.x + canvasStageBounds.width * 0.84,
      canvasStageBounds.y + canvasStageBounds.height * 0.82,
      { steps: 8 },
    )
    await page.mouse.up()
    await createRegionResponse
    await recognizeRegionResponse
    await waitForLocatorCount(regionCards, regionCountBeforeManualDraw + 1, '手动添加框后文本框数量不正确')
    const manualRegionCard = page.locator('.v2-region-card.active').first()
    await assertText(manualRegionCard, '用户添加', '新建文本框没有标记为用户手动框')
    const manualFontSize = Number(await manualRegionCard.getByLabel('字号').inputValue())
    if (!Number.isFinite(manualFontSize) || manualFontSize < 8 || manualFontSize > 200) {
      throw new Error(`手动框识别后的字号异常：${manualFontSize}`)
    }

    const deleteRegionResponse = page.waitForResponse(
      (response) => isPageCommandResponse(response, 'delete_manual_region') && response.ok(),
      { timeout: 30000 },
    )
    await manualRegionCard.getByRole('button', { name: '删除手动框' }).click()
    await deleteRegionResponse
    await waitForLocatorCount(regionCards, regionCountBeforeManualDraw, '删除手动框后文本框数量不正确')

    const restoreRegionResponse = page.waitForResponse(
      (response) => isPageCommandResponse(response, 'restore_manual_region') && response.ok(),
      { timeout: 30000 },
    )
    await page.getByRole('button', { name: '撤销' }).click()
    await restoreRegionResponse
    await waitForLocatorCount(regionCards, regionCountBeforeManualDraw + 1, '撤销删除后手动框没有恢复')

    const redoDeleteResponse = page.waitForResponse(
      (response) => isPageCommandResponse(response, 'delete_manual_region') && response.ok(),
      { timeout: 30000 },
    )
    await page.getByRole('button', { name: '重做' }).click()
    await redoDeleteResponse
    await waitForLocatorCount(regionCards, regionCountBeforeManualDraw, '重做删除后手动框仍然存在')
    await regionCards.first().click()

    const reviewShot = await saveScreenshot(page, 'v2-review.png')

    await page.getByRole('button', { name: '下一个对白框' }).click()
    await assertText(page.locator('.v2-region-sidebar-summary strong'), '#2', '框导航没有切换到下一个对白框')

    await page.getByRole('button', { name: '下一页' }).click()
    await assertText(page.locator('.v2-topbar-project-copy span'), '第 2 页', '页导航没有切换到下一页')

    const supplementImage = await createSupplementFixture()
    await Promise.all([
      page.waitForResponse((response) => (
        response.url().includes(`/api/projects/${FIXTURE_PROJECT_ID}/base-images`)
        && response.request().method() === 'POST'
        && response.ok()
      )),
      page.getByTestId('v2-supplement-file-input').setInputFiles(supplementImage),
    ])

    await page.getByRole('button', { name: '打开设置' }).last().click()
    await page.getByTestId('v2-settings-panel').waitFor({ state: 'visible', timeout: 20000 })
    const settingsShot = await saveScreenshot(page, 'v2-settings-drawer.png')

    const report = {
      projectId: FIXTURE_PROJECT_ID,
      projectTitle: FIXTURE_PROJECT_TITLE,
      screenshots: [homeShot, historyShot, pickerShot, reviewShot, settingsShot],
      pageCardCount,
      consoleErrors,
    }

    await fs.writeFile(
      path.join(artifactDir, 'report.json'),
      JSON.stringify(report, null, 2),
      'utf8',
    )

    if (consoleErrors.some((entry) => !isIgnorableConsoleEntry(entry))) {
      throw new Error(`V2 页面存在控制台错误：\n${consoleErrors.join('\n')}`)
    }

    console.log(JSON.stringify(report, null, 2))
  } finally {
    await browser?.close()
    await stopProcesses(started)
    if (ownsAppDataDir) {
      await fs.rm(E2E_APP_DATA_DIR, { recursive: true, force: true })
    }
  }
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
