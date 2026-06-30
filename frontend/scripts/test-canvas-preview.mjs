import { createServer } from 'node:http'
import { promises as fs } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { launchChromium } from './playwright-launcher.mjs'

const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)
const frontendDir = path.resolve(__dirname, '..')
const smokeHtmlPath = path.join(frontendDir, 'public', 'dev-canvas-preview-smoke.html')
const artifactDir = path.join(frontendDir, 'test-artifacts')

async function listAvailableFonts() {
  return [
    {
      id: 'test:system-sans',
      name: 'system-sans',
      label: 'System Sans (测试)',
      source: 'system',
      local_family: 'Arial',
      url: '',
    },
    {
      id: 'test:system-serif',
      name: 'system-serif',
      label: 'System Serif (测试)',
      source: 'system',
      local_family: 'Times New Roman',
      url: '',
    },
  ]
}

function writeJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  })
  response.end(JSON.stringify(payload))
}

function writeNotFound(response) {
  response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' })
  response.end('Not Found')
}

function writeHtml(response, html) {
  response.writeHead(200, {
    'Content-Type': 'text/html; charset=utf-8',
    'Cache-Control': 'no-store',
  })
  response.end(html)
}

function writeNoContent(response) {
  response.writeHead(204)
  response.end()
}

async function createSmokeServer() {
  const fonts = await listAvailableFonts()
  const html = await fs.readFile(smokeHtmlPath, 'utf8')

  const server = createServer(async (request, response) => {
    try {
      const requestUrl = new URL(request.url || '/', 'http://127.0.0.1')
      const pathname = requestUrl.pathname

      if (pathname === '/' || pathname === '/dev-canvas-preview-smoke.html') {
        writeHtml(response, html)
        return
      }

      if (pathname === '/favicon.ico') {
        writeNoContent(response)
        return
      }

      if (pathname === '/api/fonts') {
        writeJson(response, 200, { fonts })
        return
      }

      writeNotFound(response)
    } catch (error) {
      response.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' })
      response.end(error instanceof Error ? error.stack || error.message : String(error))
    }
  })

  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })

  const address = server.address()
  if (!address || typeof address === 'string') {
    throw new Error('测试服务启动失败：无法获取监听地址。')
  }

  return {
    server,
    fonts,
    baseUrl: `http://127.0.0.1:${address.port}`,
  }
}

async function readLog(locator) {
  const raw = (await locator.textContent()) || ''
  return JSON.parse(raw)
}

async function waitForJsonLog(page, predicate, timeoutMessage) {
  await page.waitForFunction(
    ({ selector, predicateSource }) => {
      const element = document.querySelector(selector)
      if (!element) return false
      try {
        const payload = JSON.parse(element.textContent || '')
        const predicate = new Function('payload', `return (${predicateSource})(payload)`)
        return Boolean(predicate(payload))
      } catch {
        return false
      }
    },
    {
      selector: '#log',
      predicateSource: predicate.toString(),
    },
    { timeout: 15000 }
  ).catch(() => {
    throw new Error(timeoutMessage)
  })
}

async function main() {
  await fs.mkdir(artifactDir, { recursive: true })

  const { server, fonts, baseUrl } = await createSmokeServer()
  if (fonts.length < 2) {
    server.close()
    throw new Error('可用字体不足，至少需要 2 个字体文件才能运行画布预览回归。')
  }

  let browser
  try {
    browser = await launchChromium({ headless: true })
    const page = await browser.newPage({ viewport: { width: 1600, height: 1200 } })
    const browserConsole = []
    page.on('console', (message) => {
      browserConsole.push(`[${message.type()}] ${message.text()}`)
    })
    const smokeUrl = `${baseUrl}/dev-canvas-preview-smoke.html?apiBase=${encodeURIComponent(baseUrl)}`

    await page.goto(smokeUrl, { waitUntil: 'networkidle' })
    try {
      await waitForJsonLog(
        page,
        (payload) => payload.fontCheckA === true && payload.fontCheckB === true,
        '初始字体加载未成功。'
      )
    } catch (error) {
      const rawLog = await page.locator('#log').textContent().catch(() => '')
      throw new Error(
        `${error instanceof Error ? error.message : error}\n` +
        `当前 smoke log:\n${rawLog || '<empty>'}\n` +
        `浏览器 console:\n${browserConsole.join('\n') || '<empty>'}`
      )
    }

    const logLocator = page.locator('#log')
    const initial = await readLog(logLocator)
    if (initial.writingModeA !== 'vertical-rl' || initial.writingModeB !== 'vertical-rl') {
      throw new Error(`初始竖排状态不正确：${JSON.stringify(initial)}`)
    }

    const initialMetricB = Number(initial.metricB || 0)
    const initialFamilyB = String(initial.fontFamilyB || '')
    const initialFontBValue = await page.inputValue('#fontB')

    const optionValues = await page.locator('#fontB option').evaluateAll((options) =>
      options.map((option) => option.value)
    )
    const switchFontId = optionValues.find((value) => value !== initialFontBValue) || optionValues[0]
    if (!switchFontId) {
      throw new Error('无法找到可切换的目标字体。')
    }

    await page.selectOption('#fontB', switchFontId)
    await waitForJsonLog(
      page,
      (payload) => payload.fontCheckB === true && payload.selectedB,
      '切换字体后未得到新的预览输出。'
    )

    const afterFontSwitch = await readLog(logLocator)
    const metricChanged = Number(afterFontSwitch.metricB || 0) !== initialMetricB
    const familyChanged = String(afterFontSwitch.fontFamilyB || '') !== initialFamilyB
    if (!metricChanged && !familyChanged) {
      throw new Error(`字体切换后预览未变化：${JSON.stringify(afterFontSwitch)}`)
    }

    await page.selectOption('#direction', 'horizontal')
    await waitForJsonLog(
      page,
      (payload) => payload.writingModeA === 'horizontal-tb' && payload.writingModeB === 'horizontal-tb',
      '方向切换后未变成横排。'
    )

    const afterDirectionSwitch = await readLog(logLocator)
    if (afterDirectionSwitch.writingModeA !== 'horizontal-tb' || afterDirectionSwitch.writingModeB !== 'horizontal-tb') {
      throw new Error(`横排状态不正确：${JSON.stringify(afterDirectionSwitch)}`)
    }

    await page.screenshot({
      path: path.join(artifactDir, 'canvas-preview-smoke.png'),
      fullPage: true,
    })

    console.log(JSON.stringify({
      ok: true,
      baseUrl,
      initial,
      afterFontSwitch,
      afterDirectionSwitch,
      screenshot: path.join(artifactDir, 'canvas-preview-smoke.png'),
    }, null, 2))
  } finally {
    await browser?.close()
    await new Promise((resolve) => server.close(resolve))
  }
}

main().catch((error) => {
  console.error(error instanceof Error ? error.stack || error.message : String(error))
  process.exitCode = 1
})
