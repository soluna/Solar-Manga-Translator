import { spawn } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { setTimeout as delay } from 'node:timers/promises'

import { findAvailablePort } from '../../scripts/local-port.mjs'

const __dirname = dirname(fileURLToPath(import.meta.url))
const desktopDir = resolve(__dirname, '..')
const repoRoot = resolve(desktopDir, '..')
const frontendDir = resolve(repoRoot, 'frontend')

const configuredRendererUrl = process.env.ELECTRON_RENDERER_URL || ''
const frontendPort = configuredRendererUrl
  ? Number(new URL(configuredRendererUrl).port || 5173)
  : await findAvailablePort({
      preferredPort: process.env.FRONTEND_PORT || process.env.VITE_DEV_PORT || 5173,
      host: '127.0.0.1',
    })
const viteUrl = configuredRendererUrl || `http://127.0.0.1:${frontendPort}`

function commandForNpx() {
  return process.platform === 'win32' ? 'npx.cmd' : 'npx'
}

function commandForNpm() {
  return process.platform === 'win32' ? 'npm.cmd' : 'npm'
}

async function waitForUrl(url, timeoutMs = 60000) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(url)
      if (response.ok || response.status < 500) {
        return
      }
    } catch {
      // Keep polling until Vite is ready.
    }
    await delay(500)
  }
  throw new Error(`Timed out waiting for ${url}`)
}

const frontendProcess = spawn(commandForNpm(), [
  'run',
  'dev',
  '--',
  '--host',
  '127.0.0.1',
  '--port',
  String(frontendPort),
  '--strictPort',
], {
  cwd: frontendDir,
  stdio: 'inherit',
  env: {
    ...process.env,
    FRONTEND_PORT: String(frontendPort),
    VITE_DEV_PORT: String(frontendPort),
  },
})

const shutdown = () => {
  if (!frontendProcess.killed) {
    frontendProcess.kill('SIGTERM')
  }
}

process.on('SIGINT', shutdown)
process.on('SIGTERM', shutdown)

try {
  await waitForUrl(viteUrl)
  const electronProcess = spawn(commandForNpx(), ['electron', '.'], {
    cwd: desktopDir,
    stdio: 'inherit',
    env: {
      ...process.env,
      ELECTRON_RENDERER_URL: viteUrl,
    },
  })

  electronProcess.on('exit', (code) => {
    shutdown()
    process.exit(code ?? 0)
  })
} catch (error) {
  shutdown()
  throw error
}
