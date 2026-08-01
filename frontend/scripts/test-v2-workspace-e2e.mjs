import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { existsSync } from 'node:fs'
import { promises as fs } from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { findAvailablePort } from '../../scripts/local-port.mjs'
import { createZeroTextRegionsWorkspaceFixture } from './create-v2-workspace-fixture.mjs'
import { launchChromium } from './playwright-launcher.mjs'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const frontendDir = path.resolve(__dirname, '..')
const repoRoot = path.resolve(frontendDir, '..')
const backendDir = path.join(repoRoot, 'backend')
const artifactDir = path.join(frontendDir, 'test-artifacts', 'v2-workspace')

const FIXTURE_PROJECT_ID = 'canvas-e2e-fixture'
const FIXTURE_PROJECT_TITLE = 'Canvas E2E Fixture'
const ZERO_TEXT_REGIONS_FIXTURE = createZeroTextRegionsWorkspaceFixture()
const FIXTURE_OPENAI_BASE_URL = 'https://api.example.invalid/v1'
const FIXTURE_OPENAI_MODEL = 'fixture-model'
const PREVIEW_TYPOGRAPHY_ONLY = process.argv.includes('--preview-typography-only')
const INTERACTION_REDESIGN_ONLY = process.argv.includes('--interaction-redesign-only')
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
      const backendLauncher = path.join(repoRoot, 'scripts', 'run_canvas_e2e_backend.py')
      const backendProcess = spawnProcess(
        python,
        [backendLauncher, '--host', '127.0.0.1', '--port', new URL(BACKEND_URL).port || '8000'],
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

async function installWorkspaceFixtureRoutes(page, fixture) {
  const routeHits = new Map()
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const pathname = new URL(request.url()).pathname
    const routeKey = `${request.method()} ${pathname}`

    if (routeKey === 'GET /api/projects') {
      const response = await route.fetch()
      const payload = await response.json()
      routeHits.set(routeKey, (routeHits.get(routeKey) || 0) + 1)
      await route.fulfill({
        response,
        json: {
          ...payload,
          projects: [
            fixture.project,
            ...(payload.projects || []).filter((project) => project.project_id !== fixture.project.project_id),
          ],
        },
      })
      return
    }

    const fixtureResponse = fixture.routeResponses[routeKey]
    if (!fixtureResponse) {
      await route.continue()
      return
    }
    routeHits.set(routeKey, (routeHits.get(routeKey) || 0) + 1)
    await route.fulfill(fixtureResponse)
  })
  return routeHits
}

async function assertZeroTextRegionsWorkspace(page, fixture, routeHits) {
  const fixtureCard = page.locator('.v2-history-card', { hasText: fixture.project.title }).first()
  await fixtureCard.waitFor({ state: 'visible', timeout: 20000 })
  await fixtureCard.getByRole('button', { name: '恢复项目' }).click()

  await page.getByTestId('v2-picker-view').waitFor({ state: 'visible', timeout: 20000 })
  const pageCard = page.locator('.v2-page-card').first()
  await pageCard.waitFor({ state: 'visible', timeout: 20000 })
  await assertText(pageCard, '0 个框', '零文本框页面没有显示真实框数量')
  await assertText(pageCard, '已完成', '零文本框页面没有显示翻译完成状态')
  await assertText(pageCard, '已生成结果', '零文本框页面没有显示最终页面产物')

  const pageCardTitle = pageCard.locator('.v2-page-card-head strong')
  const pageCardStatus = pageCard.locator('.v2-page-status')
  const pageCardRegressionFailures = []
  const [titleBox, statusBox, titleLayout, titleTooltip] = await Promise.all([
    pageCardTitle.boundingBox(),
    pageCardStatus.boundingBox(),
    pageCardTitle.evaluate((element) => {
      const style = window.getComputedStyle(element)
      return {
        overflow: style.overflow,
        textOverflow: style.textOverflow,
        whiteSpace: style.whiteSpace,
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
      }
    }),
    pageCardTitle.getAttribute('title'),
  ])
  if (!titleBox || !statusBox || titleBox.x + titleBox.width > statusBox.x) {
    pageCardRegressionFailures.push('长页面名称与状态标签发生重叠')
  }
  if (
    titleLayout.overflow !== 'hidden'
    || titleLayout.textOverflow !== 'ellipsis'
    || titleLayout.whiteSpace !== 'nowrap'
    || titleLayout.scrollWidth <= titleLayout.clientWidth
  ) {
    pageCardRegressionFailures.push(`长页面名称没有在卡片内截断：${JSON.stringify(titleLayout)}`)
  }
  if (titleTooltip !== fixture.pageName) {
    pageCardRegressionFailures.push(`长页面名称缺少完整 tooltip：${titleTooltip || '<empty>'}`)
  }

  const pageCardImage = pageCard.locator('.v2-page-card-media img')
  await pageCardImage.evaluate((image) => {
    image.src = 'data:image/png;base64,AAAA'
  })
  const imageHandle = await pageCardImage.elementHandle()
  try {
    await page.waitForFunction(
      (image) => image.naturalWidth > 1 && !image.currentSrc.startsWith('data:'),
      imageHandle,
      { timeout: 3000 },
    )
  } catch (_error) {
    const imageState = await pageCardImage.evaluate((image) => ({
      src: image.currentSrc,
      naturalWidth: image.naturalWidth,
      naturalHeight: image.naturalHeight,
    }))
    pageCardRegressionFailures.push(`缩略图请求失败后没有恢复：${JSON.stringify(imageState)}`)
  }
  if (pageCardRegressionFailures.length) {
    throw new Error(pageCardRegressionFailures.join('\n'))
  }

  await pageCard.click()
  await page.getByTestId('v2-review-view').waitFor({ state: 'visible', timeout: 20000 })
  await page.locator('.v2-review-toolbar').waitFor({ state: 'visible', timeout: 20000 })
  await waitForLocatorCount(page.locator('.v2-region-card'), 0, '零文本框页面错误生成了文本框')
  await assertText(
    page.locator('.v2-region-sidebar .v2-page-rail-count'),
    '0',
    '审校工作台没有显示零文本框状态',
  )
  const eraseMenu = page.locator('.v2-review-toolbar .v2-erase-menu').first()
  await eraseMenu.hover()
  const eraseMenuButtons = eraseMenu.locator('.v2-erase-menu-popover button')
  await waitForLocatorCount(eraseMenuButtons, 2, '擦除入口没有收束为在线与本地两个动作')
  await eraseMenu.getByRole('button', { name: '在线擦除', exact: true }).waitFor({ state: 'visible', timeout: 20000 })
  const localEraseButton = eraseMenu.getByRole('button', { name: '本地擦除', exact: true })
  await localEraseButton.waitFor({ state: 'visible', timeout: 20000 })
  await localEraseButton.click()

  const eraseWorkspace = page.locator('.v2-erase-workspace-modal')
  await eraseWorkspace.waitFor({ state: 'visible', timeout: 20000 })
  await assertText(eraseWorkspace, '本地擦除', '本地擦除没有进入统一工作区')
  await eraseWorkspace.getByRole('button', { name: /整页处理/ }).waitFor({ state: 'visible', timeout: 20000 })
  await eraseWorkspace.getByRole('button', { name: /指定区域/ }).click()
  for (const toolName of ['点击选中', '框选', '画笔']) {
    await eraseWorkspace.getByRole('button', { name: new RegExp(`^${toolName}`) }).waitFor({ state: 'visible', timeout: 20000 })
  }

  const selectionStage = eraseWorkspace.locator('.v2-selection-erase-stage')
  await selectionStage.waitFor({ state: 'visible', timeout: 20000 })
  const selectionStageBox = await selectionStage.boundingBox()
  if (!selectionStageBox) {
    throw new Error('统一擦除工作区没有可操作的图片画布')
  }
  await selectionStage.click({
    position: {
      x: selectionStageBox.width * 0.5,
      y: selectionStageBox.height * 0.48,
    },
  })
  await eraseWorkspace.getByText('自动选区 1', { exact: true }).waitFor({ state: 'visible', timeout: 20000 })

  await eraseWorkspace.getByRole('button', { name: /^画笔/ }).click()
  await page.mouse.move(
    selectionStageBox.x + selectionStageBox.width * 0.25,
    selectionStageBox.y + selectionStageBox.height * 0.28,
  )
  await page.mouse.down()
  await page.mouse.move(
    selectionStageBox.x + selectionStageBox.width * 0.42,
    selectionStageBox.y + selectionStageBox.height * 0.34,
    { steps: 5 },
  )
  await page.mouse.up()
  await assertText(eraseWorkspace.locator('.v2-selection-erase-count'), '2', '点击选中与画笔没有合并为同一擦除 mask')
  await saveScreenshot(page, 'v2-unified-local-erase-selection.png')
  await eraseWorkspace.getByRole('button', { name: '取消', exact: true }).click()
  await eraseWorkspace.waitFor({ state: 'hidden', timeout: 20000 })

  await eraseMenu.hover()
  await eraseMenu.getByRole('button', { name: '本地擦除', exact: true }).click()
  await eraseWorkspace.waitFor({ state: 'visible', timeout: 20000 })
  await eraseWorkspace.getByRole('button', { name: '生成本地候选', exact: true }).click()
  const localAdvancedModal = page.locator('.v2-local-advanced-preview-modal')
  await localAdvancedModal.waitFor({ state: 'visible', timeout: 20000 })
  await assertText(localAdvancedModal, '自动处理 3 个文字区域', '本地高级擦除预览缺少处理区域摘要')
  await assertText(localAdvancedModal, '2048 精度', '本地高级擦除预览缺少实际推理尺寸')
  await assertText(localAdvancedModal, '1 个低置信度区域未自动擦除', '本地高级擦除预览缺少低置信度提示')
  const localAdvancedImages = localAdvancedModal.locator('.v2-local-advanced-image > img:first-child')
  await waitForLocatorCount(localAdvancedImages, 3, '本地高级擦除预览缺少三张对比图')
  for (let index = 0; index < 3; index += 1) {
    const image = localAdvancedImages.nth(index)
    const imageHandle = await image.elementHandle()
    await page.waitForFunction(
      (element) => element.complete && element.naturalWidth > 0,
      imageHandle,
      { timeout: 20000 },
    )
  }
  await localAdvancedModal.getByRole('checkbox', { name: '显示擦除范围' }).check()
  await localAdvancedModal.locator('.v2-local-advanced-mask-overlay').waitFor({ state: 'visible', timeout: 20000 })
  await saveScreenshot(page, 'v2-local-advanced-preview.png')
  await localAdvancedModal.getByRole('button', { name: '放弃结果', exact: true }).click()
  await localAdvancedModal.waitFor({ state: 'hidden', timeout: 20000 })

  const exportMenu = page.locator('.v2-topbar-actions .v2-export-menu').first()
  const exportTrigger = exportMenu.locator('.v2-dropdown-trigger')
  if (await exportTrigger.isDisabled()) {
    throw new Error('零文本框页面已完成全部 Page Artifact，但导出菜单仍被禁用')
  }
  await exportMenu.hover()
  const exportResultButton = exportMenu.getByRole('button', { name: '导出结果', exact: true })
  await exportResultButton.waitFor({ state: 'visible', timeout: 20000 })
  if (await exportResultButton.isDisabled()) {
    throw new Error('零文本框页面的公开导出动作仍被禁用')
  }
  const downloadStarted = page.waitForEvent('download')
  await exportResultButton.click()
  const download = await downloadStarted
  if (download.suggestedFilename() !== fixture.downloadFilename) {
    throw new Error(`零文本框项目导出文件名异常：${download.suggestedFilename()}`)
  }
  await assertText(page.locator('.v2-topbar-status'), '已开始导出翻译结果压缩包', '导出动作没有用户可见反馈')

  for (const requiredRoute of fixture.requiredRouteKeys) {
    if (!routeHits.get(requiredRoute)) {
      throw new Error(`零文本框 E2E 没有消费公开 fixture route：${requiredRoute}`)
    }
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
      fontCheck: document.fonts.check(
        `16px ${computed.fontFamily.split(',')[0]}`,
        element.textContent || '测试漢字ABC',
      ),
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
    if (
      !metric.fontCheck
      && !metric.fontFaces.some((face) => face.family === expectedFontAlias && face.status === 'loaded')
    ) {
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

async function assertReviewInteractionRedesign(page) {
  const regionCards = page.locator('.v2-region-card')
  await regionCards.first().waitFor({ state: 'visible', timeout: 20000 })
  const initialRegionCount = await regionCards.count()
  if (initialRegionCount < 3) {
    throw new Error(`交互重构夹具缺少足够文本框：${initialRegionCount}`)
  }

  await page.getByTestId('v2-review-page-commands').waitFor({ state: 'visible', timeout: 20000 })
  await page.getByTestId('v2-review-compare-toolbar').waitFor({ state: 'visible', timeout: 20000 })
  await page.getByRole('button', { name: '保存', exact: true }).waitFor({ state: 'visible', timeout: 20000 })

  const compareToolbar = page.getByTestId('v2-review-compare-toolbar')
  const selectedCompareChips = compareToolbar.locator('.v2-compare-chip.active')
  const paneCards = page.locator('.v2-pane-strip .v2-pane-card')
  const selectedCompareCount = await selectedCompareChips.count()
  if (selectedCompareCount < 2 || selectedCompareCount > 3) {
    throw new Error(`对照选择仍需保留现有 2–3 画布能力，实际为 ${selectedCompareCount}`)
  }
  if (await paneCards.count() !== selectedCompareCount) {
    throw new Error('对照开关数量与实际画布数量不一致')
  }

  const filterChips = page.getByTestId('v2-region-filter-chips').locator('button')
  if (await filterChips.count() !== 6) {
    throw new Error(`右侧没有完整保留六个现有筛选：${await filterChips.count()}`)
  }

  await regionCards.first().click()
  const singleCommands = page.getByTestId('v2-review-single-commands')
  await singleCommands.waitFor({ state: 'visible', timeout: 20000 })
  await singleCommands.getByRole('button', { name: '复制全部样式', exact: true }).waitFor({ state: 'visible' })
  const pasteButton = singleCommands.getByRole('button', { name: '粘贴全部样式', exact: true })
  if (!(await pasteButton.isDisabled())) {
    throw new Error('没有样式剪贴板时，粘贴全部样式应明确禁用')
  }

  const activeCard = page.locator('.v2-region-card.active')
  await activeCard.locator('.v2-region-card-body').waitFor({ state: 'visible' })
  if (await activeCard.getByRole('button', { name: '字体应用到本页', exact: true }).count() !== 1) {
    throw new Error('字体整页操作没有明确标注“本页”')
  }
  if (await activeCard.getByRole('button', { name: '字号应用到本页', exact: true }).count() !== 1) {
    throw new Error('字号整页操作没有明确标注“本页”')
  }
  if (await activeCard.locator('.v2-region-copy-actions').count()) {
    throw new Error('当前卡片仍重复显示样式复制/粘贴小图标')
  }

  const sourceFont = await singleCommands.getByTestId('v2-selection-font').inputValue()
  const sourceSize = Number(await singleCommands.locator('input[aria-label="选中框字号"]').inputValue())
  await singleCommands.getByRole('button', { name: '复制全部样式', exact: true }).click()
  const clipboardSummary = singleCommands.getByTestId('v2-style-clipboard-summary')
  await clipboardSummary.waitFor({ state: 'visible', timeout: 20000 })
  await assertText(clipboardSummary, '已复制样式', '样式剪贴板没有持续显示')
  await assertText(clipboardSummary, String(sourceSize), '样式剪贴板没有显示字号')
  if (await pasteButton.isDisabled()) {
    throw new Error('复制样式后，粘贴全部样式仍被禁用')
  }

  await saveScreenshot(page, 'v2-review-interaction-single.png')
  const advancedButton = activeCard.getByRole('button', { name: '更多与高级样式', exact: true })
  await advancedButton.click()
  const advancedSection = activeCard.locator('.v2-region-advanced-section')
  await advancedSection.waitFor({ state: 'visible', timeout: 20000 })
  if (await page.locator('body > .style-advanced-popover').count()) {
    throw new Error('高级样式仍以遮挡画布的全局浮层出现')
  }
  await saveScreenshot(page, 'v2-review-interaction-advanced.png')
  await advancedButton.click()

  const targetCard = regionCards.nth(1)
  await targetCard.click()
  const pasteResponsePromise = page.waitForResponse(
    (response) => isPageCommandResponse(response, 'update_font_size') && response.ok(),
    { timeout: 30000 },
  )
  await singleCommands.getByRole('button', { name: '粘贴全部样式', exact: true }).click()
  const pasteResponse = await pasteResponsePromise
  const pasteCommands = pasteResponse.request().postDataJSON()?.commands || []
  const pastedFontCommand = pasteCommands.find((command) => command.type === 'update_region_font')
  if (!pastedFontCommand?.font_key || (sourceFont && pastedFontCommand.font_key !== sourceFont)) {
    throw new Error(`粘贴全部样式没有沿用来源框字体：source=${sourceFont} commands=${JSON.stringify(pasteCommands)}`)
  }
  if (!pasteCommands.some((command) => command.type === 'update_font_size' && Number(command.font_size) === sourceSize)) {
    throw new Error('粘贴全部样式没有沿用来源框字号')
  }
  if (!pasteCommands.some((command) => ['restore_region', 'disable_region'].includes(command.type))) {
    throw new Error('“全部样式”没有保持现有启用状态语义')
  }

  await regionCards.nth(2).click()
  const activeBatchSourceSize = Number(
    await singleCommands.locator('input[aria-label="选中框字号"]').inputValue(),
  )
  await regionCards.first().click()
  await regionCards.nth(1).click({ modifiers: ['ControlOrMeta'] })
  await regionCards.nth(2).click({ modifiers: ['ControlOrMeta'] })
  const multiCommands = page.getByTestId('v2-review-multi-commands')
  await multiCommands.waitFor({ state: 'visible', timeout: 20000 })
  for (const name of ['启用', '停用', '纵排', '横排', '套用字体', '套用字号']) {
    if (await multiCommands.getByRole('button', { name, exact: true }).count() !== 1) {
      throw new Error(`多选工具栏缺少现有批量命令：${name}`)
    }
  }
  if (await page.locator('.v2-region-card-body').count()) {
    throw new Error('多选状态仍展开单框编辑表单')
  }
  if (await page.locator('.v2-region-batch-bar button, .v2-canvas-selection-toolbar button').count()) {
    throw new Error('多选命令仍在右侧或画布上重复出现')
  }
  if (await regionCards.count() !== initialRegionCount) {
    throw new Error('进入多选后右侧完整文本框列表消失或数量改变')
  }

  const batchSizeResponsePromise = page.waitForResponse(
    (response) => isPageCommandResponse(response, 'update_font_size') && response.ok(),
    { timeout: 30000 },
  )
  await multiCommands.getByRole('button', { name: '套用字号', exact: true }).click()
  const batchSizeResponse = await batchSizeResponsePromise
  const batchSizeCommands = (batchSizeResponse.request().postDataJSON()?.commands || [])
    .filter((command) => command.type === 'update_font_size')
  if (
    batchSizeCommands.length !== 3
    || batchSizeCommands.some((command) => Number(command.font_size) !== activeBatchSourceSize)
  ) {
    throw new Error(`批量套用字号没有使用活动框字号：${JSON.stringify(batchSizeCommands)}`)
  }

  await saveScreenshot(page, 'v2-review-interaction-multi.png')
  await multiCommands.getByRole('button', { name: '清除选择', exact: true }).click()
  await multiCommands.waitFor({ state: 'hidden' })
  if (await regionCards.count() !== initialRegionCount) {
    throw new Error('清除多选后右侧完整文本框列表没有恢复')
  }

  await page.setViewportSize({ width: 1280, height: 900 })
  await page.getByTestId('v2-review-view').waitFor({ state: 'visible', timeout: 20000 })
  await page.locator('.v2-pane-strip').waitFor({ state: 'visible', timeout: 20000 })
  await page.locator('.v2-region-sidebar').waitFor({ state: 'visible', timeout: 20000 })
  const viewportMetrics = await page.evaluate(() => ({
    viewportWidth: window.innerWidth,
    documentWidth: document.documentElement.scrollWidth,
  }))
  if (viewportMetrics.documentWidth > viewportMetrics.viewportWidth + 1) {
    throw new Error(`1280px 工作台出现页面级横向滚动：${JSON.stringify(viewportMetrics)}`)
  }
  if (await regionCards.count() !== initialRegionCount) {
    throw new Error('1280px 工作台下右侧完整文本框列表被隐藏')
  }
  await saveScreenshot(page, 'v2-review-interaction-1280.png')
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
    const fixtureRouteHits = await installWorkspaceFixtureRoutes(page, ZERO_TEXT_REGIONS_FIXTURE)
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

    if (INTERACTION_REDESIGN_ONLY) {
      await page.getByRole('banner').getByRole('button', { name: '项目管理' }).click()
      await page.getByTestId('v2-history-modal').waitFor({ state: 'visible', timeout: 20000 })
      const fixtureCard = page.locator('.v2-history-card', { hasText: FIXTURE_PROJECT_TITLE }).first()
      await fixtureCard.waitFor({ state: 'visible', timeout: 20000 })
      await fixtureCard.getByRole('button', { name: '恢复项目' }).click()
      await page.getByTestId('v2-picker-view').waitFor({ state: 'visible', timeout: 20000 })
      await page.locator('.v2-page-card').first().click()
      await page.getByTestId('v2-review-view').waitFor({ state: 'visible', timeout: 20000 })
      await assertReviewInteractionRedesign(page)
      if (consoleErrors.some((entry) => !isIgnorableConsoleEntry(entry))) {
        throw new Error(`交互重构页面存在控制台错误：\n${consoleErrors.join('\n')}`)
      }
      console.log(JSON.stringify({ projectId: FIXTURE_PROJECT_ID, interactionRedesign: 'passed' }, null, 2))
      return
    }

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

    await assertZeroTextRegionsWorkspace(page, ZERO_TEXT_REGIONS_FIXTURE, fixtureRouteHits)
    await page.getByRole('button', { name: '← 返回' }).click()
    await page.getByTestId('v2-picker-view').waitFor({ state: 'visible', timeout: 20000 })
    await page.getByRole('banner').getByRole('button', { name: '项目管理' }).click()
    await page.getByTestId('v2-history-modal').waitFor({ state: 'visible', timeout: 20000 })

    const fixtureCard = page.locator('.v2-history-card', { hasText: FIXTURE_PROJECT_TITLE }).first()
    await fixtureCard.waitFor({ state: 'visible', timeout: 20000 })
    await fixtureCard.getByRole('button', { name: '恢复项目' }).click()

    await page.getByTestId('v2-picker-view').waitFor({ state: 'visible', timeout: 20000 })
    await page.locator('.v2-section-title', { hasText: FIXTURE_PROJECT_TITLE }).first().waitFor({ state: 'visible', timeout: 20000 })
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
    if (!focusedHandleBox || focusedHandleBox.width > 14 || focusedHandleBox.height > 14) {
      throw new Error(`高倍定位后缩放控制点不再保持固定尺寸：${JSON.stringify(focusedHandleBox)}`)
    }
    if (await page.locator('.v2-pane-card-frame .style-box-settings-button').count()) {
      throw new Error('高级样式入口仍悬浮在画布上')
    }
    const inlineAdvancedButton = page.locator('.v2-region-card.active')
      .getByRole('button', { name: '更多与高级样式', exact: true })
    await inlineAdvancedButton.click()
    await page.locator('.v2-region-card.active .v2-region-advanced-section')
      .waitFor({ state: 'visible', timeout: 20000 })
    await inlineAdvancedButton.click()
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
