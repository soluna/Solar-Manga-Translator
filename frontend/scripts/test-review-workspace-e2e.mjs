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
const artifactDir = path.join(frontendDir, 'test-artifacts', 'review-workspace')

const FIXTURE_PROJECT_ID = 'canvas-e2e-fixture'
const FIXTURE_PROJECT_TITLE = 'Canvas E2E Fixture'
const BACKEND_URL = process.env.CANVAS_E2E_BACKEND_URL || 'http://127.0.0.1:8000'
const FRONTEND_URL = process.env.CANVAS_E2E_FRONTEND_URL || 'http://127.0.0.1:5173'
const PLAYWRIGHT_TIMEOUT = Number(process.env.CANVAS_E2E_TIMEOUT_MS || 20000)

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

function createLogger(prefix, output = process.stdout) {
  return (chunk) => {
    const text = chunk.toString()
    if (!text.trim()) return
    output.write(`[${prefix}] ${text}`)
  }
}

async function pathExists(targetPath) {
  try {
    await fs.access(targetPath)
    return true
  } catch {
    return false
  }
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

async function ensureFixture() {
  const python = pickBackendPython()
  const fixtureScript = path.join(repoRoot, 'scripts', 'create_canvas_test_fixture.py')
  await new Promise((resolve, reject) => {
    const child = spawnProcess(python, [fixtureScript, '--project-id', FIXTURE_PROJECT_ID], {
      cwd: repoRoot,
      label: 'fixture',
    })
    child.once('error', reject)
    child.once('exit', (code) => {
      if (code === 0) {
        resolve()
        return
      }
      reject(new Error(`创建画布测试夹具失败，退出码 ${code}`))
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
        label: 'backend-e2e',
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
        label: 'frontend-e2e',
      }
    )
    started.push(frontendProcess)
    await waitForHttp(FRONTEND_URL, '前端服务')
  }

  return started
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

async function fetchPageDocument(pageId) {
  const response = await fetch(`${BACKEND_URL}/api/pages/${FIXTURE_PROJECT_ID}/${pageId}/document`)
  const payload = await response.json()
  if (!response.ok) {
    throw new Error(payload.detail || `读取页面文档失败：${pageId}`)
  }
  return payload.document
}

function findRegion(document, regionId) {
  return (document.regions || []).find((region) => String(region.region_id || '') === regionId)
}

async function waitForRegionBBox(pageId, regionId, predicate, description) {
  const start = Date.now()
  while (Date.now() - start < PLAYWRIGHT_TIMEOUT) {
    const document = await fetchPageDocument(pageId)
    const region = findRegion(document, regionId)
    if (region && predicate(region.bbox)) {
      return region.bbox
    }
    await sleep(180)
  }
  throw new Error(`等待文本框 bbox 更新超时：${description}`)
}

async function restoreFixtureProject(page) {
  const historyToggle = page.getByRole('button', { name: /历史记录/ }).first()
  if (await historyToggle.isVisible()) {
    await historyToggle.click()
  }
  const fixtureCard = page.locator('.project-history-item', { hasText: FIXTURE_PROJECT_TITLE }).first()
  await fixtureCard.waitFor({ state: 'visible', timeout: PLAYWRIGHT_TIMEOUT })
  await fixtureCard.getByRole('button', { name: '继续编辑' }).click()
  await page.locator('.review-canvas-zone').waitFor({ state: 'visible', timeout: PLAYWRIGHT_TIMEOUT })
}

async function selectRegion(page, index = 0) {
  const region = page.locator('.review-canvas-pane-main .style-box').nth(index)
  await region.waitFor({ state: 'visible', timeout: PLAYWRIGHT_TIMEOUT })
  await region.scrollIntoViewIfNeeded()
  await region.click({ force: true })
  await page.waitForFunction(
    (idx) => {
      const boxes = document.querySelectorAll('.review-canvas-pane-main .style-box')
      return Boolean(boxes[idx]?.classList.contains('active'))
    },
    index,
    { timeout: PLAYWRIGHT_TIMEOUT }
  )
  return region
}

async function testMove(page) {
  const pageId = '0001.jpg'
  const regionId = 'fixture-0001-r1'
  const before = findRegion(await fetchPageDocument(pageId), regionId)?.bbox
  if (!before) throw new Error('移动测试前无法找到 fixture-0001-r1')

  const region = await selectRegion(page, 0)
  const box = await region.boundingBox()
  if (!box) {
    throw new Error('无法读取主画布选中框位置')
  }

  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 36, box.y + box.height / 2 + 22, { steps: 8 })
  await page.mouse.up()

  const after = await waitForRegionBBox(
    pageId,
    regionId,
    (bbox) => bbox[0] !== before[0] && bbox[1] !== before[1],
    '拖动文本框后应该更新位置'
  )
  if (after[0] <= before[0] || after[1] <= before[1]) {
    throw new Error(`移动后的 bbox 不符合预期：before=${before.join(',')} after=${after.join(',')}`)
  }
}

async function testResize(page) {
  const pageId = '0001.jpg'
  const regionId = 'fixture-0001-r1'
  const before = findRegion(await fetchPageDocument(pageId), regionId)?.bbox
  if (!before) throw new Error('缩放测试前无法找到 fixture-0001-r1')

  await selectRegion(page, 0)
  const handle = page.locator('.review-canvas-pane-main .style-box.active .style-box-handle.handle-se').first()
  await handle.waitFor({ state: 'visible', timeout: PLAYWRIGHT_TIMEOUT })
  const box = await handle.boundingBox()
  if (!box) {
    throw new Error('无法读取缩放手柄位置')
  }

  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width / 2 + 28, box.y + box.height / 2 + 42, { steps: 8 })
  await page.mouse.up()

  const after = await waitForRegionBBox(
    pageId,
    regionId,
    (bbox) => (bbox[2] - bbox[0]) > (before[2] - before[0]) && (bbox[3] - bbox[1]) > (before[3] - before[1]),
    '拖动缩放手柄后应该放大框体'
  )
  if ((after[2] - after[0]) <= (before[2] - before[0])) {
    throw new Error(`缩放后的宽度没有增加：before=${before.join(',')} after=${after.join(',')}`)
  }
}

async function testKeyboardNudge(page) {
  const pageId = '0001.jpg'
  const regionId = 'fixture-0001-r1'
  await selectRegion(page, 0)

  const beforeSingle = findRegion(await fetchPageDocument(pageId), regionId)?.bbox
  if (!beforeSingle) throw new Error('微调测试前无法找到 fixture-0001-r1')
  await page.keyboard.press('ArrowRight')
  const afterSingle = await waitForRegionBBox(
    pageId,
    regionId,
    (bbox) => bbox[0] === beforeSingle[0] + 1 && bbox[2] === beforeSingle[2] + 1,
    '方向键右移 1px'
  )

  await page.keyboard.press('Control+ArrowDown')
  await waitForRegionBBox(
    pageId,
    regionId,
    (bbox) => bbox[1] === afterSingle[1] + 5 && bbox[3] === afterSingle[3] + 5,
    'Ctrl + 方向键下移 5px'
  )
}

async function testViewportPersistence(page) {
  const mainPane = page.locator('.review-canvas-pane-main .review-canvas-shell').first()
  await mainPane.waitFor({ state: 'visible', timeout: PLAYWRIGHT_TIMEOUT })
  const shellBox = await mainPane.boundingBox()
  if (!shellBox) {
    throw new Error('无法读取主画布尺寸，无法测试视口记忆')
  }

  await page.mouse.move(shellBox.x + shellBox.width / 2, shellBox.y + shellBox.height / 2)
  await page.mouse.wheel(0, -400)
  await sleep(300)

  const before = await page.locator('.review-canvas-pane-main .review-canvas-stage').evaluate((node) => getComputedStyle(node).transform)
  await page.locator('.review-page-tile', { hasText: '2' }).first().click()
  await page.locator('.review-page-tile.active', { hasText: '2' }).first().waitFor({ state: 'visible', timeout: PLAYWRIGHT_TIMEOUT })
  await page.locator('.review-page-tile', { hasText: '1' }).first().click()
  await page.locator('.review-page-tile.active', { hasText: '1' }).first().waitFor({ state: 'visible', timeout: PLAYWRIGHT_TIMEOUT })
  const after = await page.locator('.review-canvas-pane-main .review-canvas-stage').evaluate((node) => getComputedStyle(node).transform)

  if (before !== after) {
    throw new Error(`切页后视口没有恢复：before=${before} after=${after}`)
  }
}

async function testCompareAspect(page) {
  const readRatio = async () => page.locator('.review-canvas-pane-compare img').evaluate((img) => ({
    natural: img.naturalWidth / Math.max(img.naturalHeight, 1),
    rendered: img.getBoundingClientRect().width / Math.max(img.getBoundingClientRect().height, 1),
  }))

  const fixedRatio = await readRatio()
  await page.getByRole('button', { name: '自适应' }).click()
  await sleep(300)
  const autoRatio = await readRatio()
  await page.getByRole('button', { name: 'Fixed' }).click()
  await sleep(300)
  const resetRatio = await readRatio()

  const ratios = [fixedRatio, autoRatio, resetRatio]
  for (const ratio of ratios) {
    if (Math.abs(ratio.natural - ratio.rendered) > 0.03) {
      throw new Error(`结果对照图比例异常：natural=${ratio.natural} rendered=${ratio.rendered}`)
    }
  }
}

async function main() {
  await fs.mkdir(artifactDir, { recursive: true })
  await ensureFixture()
  const startedProcesses = await ensureServices()

  let browser
  let page
  let browserConsole = []
  try {
    browser = await chromium.launch({ headless: true })
    page = await browser.newPage({ viewport: { width: 1720, height: 1180 } })
    page.on('console', (message) => {
      browserConsole.push(`[${message.type()}] ${message.text()}`)
    })
    page.on('pageerror', (error) => {
      browserConsole.push(`[pageerror] ${error.message}`)
    })

    await page.goto(FRONTEND_URL, { waitUntil: 'networkidle' })
    await restoreFixtureProject(page)

    await testMove(page)
    await testResize(page)
    await testKeyboardNudge(page)
    await testViewportPersistence(page)
    await testCompareAspect(page)

    await page.screenshot({
      path: path.join(artifactDir, 'review-workspace-e2e-success.png'),
      fullPage: true,
    })
    await fs.writeFile(
      path.join(artifactDir, 'review-workspace-e2e-console.log'),
      browserConsole.join('\n'),
      'utf8'
    )
    console.log('Canvas review workspace E2E passed.')
  } catch (error) {
    if (page) {
      await page.screenshot({
        path: path.join(artifactDir, 'review-workspace-e2e-failure.png'),
        fullPage: true,
      }).catch(() => {})
    }
    await fs.writeFile(
      path.join(artifactDir, 'review-workspace-e2e-console.log'),
      browserConsole.join('\n'),
      'utf8'
    ).catch(() => {})
    console.error(error instanceof Error ? error.stack || error.message : String(error))
    throw error
  } finally {
    if (browser) {
      await browser.close()
    }
    await stopProcesses(startedProcesses)
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
})
