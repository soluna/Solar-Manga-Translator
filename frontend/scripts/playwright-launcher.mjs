import { existsSync } from 'node:fs'

import { chromium } from 'playwright'

function systemChromiumCandidates() {
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE) {
    return [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE]
  }

  if (process.platform === 'darwin') {
    return [
      '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
      '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
      '/Applications/Chromium.app/Contents/MacOS/Chromium',
    ]
  }

  if (process.platform === 'win32') {
    return [
      `${process.env.PROGRAMFILES || 'C:\\Program Files'}\\Google\\Chrome\\Application\\chrome.exe`,
      `${process.env['PROGRAMFILES(X86)'] || 'C:\\Program Files (x86)'}\\Google\\Chrome\\Application\\chrome.exe`,
      `${process.env.PROGRAMFILES || 'C:\\Program Files'}\\Microsoft\\Edge\\Application\\msedge.exe`,
      `${process.env['PROGRAMFILES(X86)'] || 'C:\\Program Files (x86)'}\\Microsoft\\Edge\\Application\\msedge.exe`,
    ]
  }

  return [
    '/usr/bin/google-chrome',
    '/usr/bin/google-chrome-stable',
    '/usr/bin/chromium',
    '/usr/bin/chromium-browser',
    '/snap/bin/chromium',
  ]
}

export async function launchChromium(options = {}) {
  try {
    return await chromium.launch(options)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    const browserMissing = message.includes('Executable doesn\'t exist') || message.includes('playwright install')
    if (!browserMissing) {
      throw error
    }

    for (const executablePath of systemChromiumCandidates()) {
      if (executablePath && existsSync(executablePath)) {
        return chromium.launch({
          ...options,
          executablePath,
        })
      }
    }

    throw error
  }
}
