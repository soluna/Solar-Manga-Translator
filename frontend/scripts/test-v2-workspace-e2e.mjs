import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { promises as fs } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { chromium } from 'playwright'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const frontendDir = path.resolve(__dirname, '..')
const repoRoot = path.resolve(frontendDir, '..')
const backendDir = path.join(repoRoot, 'backend')
const artifactDir = path.join(frontendDir, 'test-artifacts', 'v2-workspace')

const FIXTURE_PROJECT_ID = 'canvas-e2e-fixture'
const FIXTURE_PROJECT_TITLE = 'Canvas E2E Fixture'
const BACKEND_URL = process.env.CANVAS_E2E_BACKEND_URL || 'http://127.0.0.1:8000'
const FRONTEND_URL = process.env.CANVAS_E2E_FRONTEND_URL || 'http://127.0.0.1:5173'

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
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
  const child = spawn(command, args, {
    cwd: options.cwd,
    env: {
      ...process.env,
      ...options.env,
    },
    shell: false,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
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
    const response = await fetch(url)
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
    if (!child || child.killed) continue
    child.kill('SIGTERM')
    await sleep(300)
    if (child.exitCode == null) {
      child.kill('SIGKILL')
    }
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

async function ensureServices() {
  const started = []

  if (!(await httpOk(`${BACKEND_URL}/api/status`))) {
    const python = pickBackendPython()
    const backendProcess = spawnProcess(
      python,
      ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', new URL(BACKEND_URL).port || '8000'],
      {
        cwd: backendDir,
        label: 'backend-v2-e2e',
      }
    )
    started.push(backendProcess)
    await waitForHttp(`${BACKEND_URL}/api/status`, '后端服务')
  }

  if (!(await httpOk(FRONTEND_URL))) {
    const frontendProcess = spawnProcess(
      'npm',
      ['run', 'dev', '--', '--host', '127.0.0.1', '--port', new URL(FRONTEND_URL).port || '5173'],
      {
        cwd: frontendDir,
        label: 'frontend-v2-e2e',
      }
    )
    started.push(frontendProcess)
    await waitForHttp(FRONTEND_URL, '前端服务')
  }

  return started
}

async function saveScreenshot(page, name) {
  const filePath = path.join(artifactDir, name)
  await page.screenshot({ path: filePath, fullPage: false })
  return filePath
}

async function createSupplementFixture() {
  const sourceImage = path.join(frontendDir, 'src', 'assets', 'v2', 'thumb-a.jpg')
  const targetImage = path.join(artifactDir, 'Fixture Page 1.jpg')
  await fs.copyFile(sourceImage, targetImage)
  return targetImage
}

async function assertText(locator, expected, message) {
  const text = (await locator.textContent()) || ''
  if (!text.includes(expected)) {
    throw new Error(`${message}：期望包含 "${expected}"，实际为 "${text}"`)
  }
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

async function main() {
  await fs.mkdir(artifactDir, { recursive: true })

  const started = await ensureServices()
  let browser
  try {
    await ensureFixture()

    browser = await chromium.launch({ headless: true })
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

    await page.getByRole('banner').getByRole('button', { name: '历史项目' }).click()
    await page.getByTestId('v2-history-modal').waitFor({ state: 'visible', timeout: 20000 })
    const historyShot = await saveScreenshot(page, 'v2-history-modal.png')

    const fixtureCard = page.locator('.v2-history-card', { hasText: FIXTURE_PROJECT_TITLE }).first()
    await fixtureCard.waitFor({ state: 'visible', timeout: 20000 })
    await fixtureCard.getByRole('button', { name: '恢复项目' }).click()

    await page.getByTestId('v2-picker-view').waitFor({ state: 'visible', timeout: 20000 })
    await assertText(page.locator('.v2-section-title').first(), FIXTURE_PROJECT_TITLE, '选页页项目标题不正确')
    await page.getByRole('banner').getByRole('button', { name: '新建项目' }).waitFor({ state: 'visible', timeout: 20000 })
    await page.locator('.v2-picker-view .v2-section-actions .v2-primary-button').waitFor({ state: 'visible', timeout: 20000 })
    const pickerShot = await saveScreenshot(page, 'v2-picker.png')

    const pageCards = page.locator('.v2-page-card')
    const pageCardCount = await pageCards.count()
    if (pageCardCount < 2) {
      throw new Error(`选页页缩略图数量异常：${pageCardCount}`)
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
    const reviewShot = await saveScreenshot(page, 'v2-review.png')

    await page.getByRole('button', { name: '下一个对白框' }).click()
    await assertText(page.locator('.v2-region-sidebar-summary strong'), '#2', '框导航没有切换到下一个对白框')

    await page.getByRole('button', { name: '下一页' }).click()
    await assertText(page.locator('.v2-topbar-project-copy span'), '第 2 页', '页导航没有切换到下一页')

    const supplementImage = await createSupplementFixture()
    const supplementUpload = page.waitForResponse((response) => (
      response.url().includes(`/api/projects/${FIXTURE_PROJECT_ID}/base-images`)
      && response.request().method() === 'POST'
      && response.ok()
    ))
    await page.locator('input.v2-hidden-input').nth(1).setInputFiles(supplementImage)
    await supplementUpload

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
  }
}

main().catch((error) => {
  console.error(error)
  process.exit(1)
})
