import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import { spawn } from 'node:child_process'
import { createWriteStream, existsSync, mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import net from 'node:net'
import { setTimeout as delay } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(__dirname, '..')
const isWindows = process.platform === 'win32'

let mainWindow = null
let backendProcess = null
let backendRuntime = null

function ensureDir(path) {
  mkdirSync(path, { recursive: true })
}

function createLogStream(path) {
  ensureDir(dirname(path))
  return createWriteStream(path, { flags: 'a' })
}

async function findFreePort() {
  return await new Promise((resolvePort, reject) => {
    const server = net.createServer()
    server.unref()
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close((error) => {
        if (error) {
          reject(error)
          return
        }
        resolvePort(port)
      })
    })
  })
}

async function waitForBackendReady(baseUrl, timeoutMs = 60000) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(`${baseUrl}/api/status`)
      if (response.ok) {
        return
      }
    } catch {
      // Ignore polling errors while backend starts.
    }
    await delay(500)
  }
  throw new Error('Backend API did not become ready in time.')
}

function resolveRepoPath(...segments) {
  return join(repoRoot, ...segments)
}

function resolvePackagedResourcePath(...segments) {
  return join(process.resourcesPath, ...segments)
}

function detectPythonExecutable() {
  const candidates = [
    process.env.MANGA_TRANSLATOR_PYTHON,
    resolveRepoPath('backend', 'venv', 'Scripts', 'python.exe'),
    resolveRepoPath('backend', '.venv-mac', 'bin', 'python3'),
    resolveRepoPath('backend', '.venv-mac', 'bin', 'python'),
  ].filter(Boolean)

  for (const candidate of candidates) {
    if (existsSync(candidate)) {
      return { command: candidate, prefixArgs: [] }
    }
  }

  return isWindows
    ? { command: 'py', prefixArgs: ['-3'] }
    : { command: 'python3', prefixArgs: [] }
}

function resolveBackendLaunch(userDataDir, port) {
  const logsDir = join(userDataDir, 'logs')
  const env = {
    ...process.env,
    APP_DESKTOP_MODE: '1',
    APP_VERSION: app.getVersion(),
    APP_DATA_DIR: userDataDir,
    APP_LOG_DIR: logsDir,
    APP_BACKEND_HOST: '127.0.0.1',
    APP_BACKEND_PORT: String(port),
  }

  if (app.isPackaged) {
    const command = resolvePackagedResourcePath('python-runtime', 'Scripts', 'python.exe')
    const script = resolvePackagedResourcePath('backend-source', 'desktop_server.py')
    const codeDir = resolvePackagedResourcePath('backend-source')
    return {
      command,
      args: [script],
      env: {
        ...env,
        APP_CODE_DIR: codeDir,
      },
      logsDir,
    }
  }

  const python = detectPythonExecutable()
  const codeDir = resolveRepoPath('backend')
  const script = resolveRepoPath('backend', 'desktop_server.py')
  return {
    command: python.command,
    args: [...python.prefixArgs, script],
    env: {
      ...env,
      APP_CODE_DIR: codeDir,
    },
    logsDir,
  }
}

async function startBackend() {
  const userDataDir = app.getPath('userData')
  const port = await findFreePort()
  const backendBaseUrl = `http://127.0.0.1:${port}`
  const launch = resolveBackendLaunch(userDataDir, port)
  ensureDir(launch.logsDir)

  const commandLooksLikePath = launch.command.includes('/') || launch.command.includes('\\')
  if (commandLooksLikePath && !existsSync(launch.command)) {
    throw new Error(`找不到后端 Python runtime: ${launch.command}`)
  }
  if (!existsSync(launch.args.at(-1))) {
    throw new Error(`找不到后端启动脚本: ${launch.args.at(-1)}`)
  }

  const stdoutStream = createLogStream(join(launch.logsDir, 'backend-stdout.log'))
  const stderrStream = createLogStream(join(launch.logsDir, 'backend-stderr.log'))

  backendProcess = spawn(launch.command, launch.args, {
    cwd: launch.env.APP_CODE_DIR,
    env: launch.env,
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
  })

  backendProcess.stdout?.pipe(stdoutStream)
  backendProcess.stderr?.pipe(stderrStream)
  backendProcess.on('exit', (code, signal) => {
    if (code && code !== 0) {
      console.error(`Backend exited with code ${code} (${signal || 'no signal'})`)
    }
  })

  await waitForBackendReady(backendBaseUrl)

  backendRuntime = {
    desktop_mode: true,
    apiBaseUrl: backendBaseUrl,
    appVersion: app.getVersion(),
    dataDir: userDataDir,
    logsDir: launch.logsDir,
    backendLogPath: join(launch.logsDir, 'backend.log'),
  }
  process.env.MANGA_TRANSLATOR_DESKTOP_RUNTIME = JSON.stringify(backendRuntime)
}

function stopBackend() {
  if (!backendProcess) {
    return
  }

  const processToStop = backendProcess
  backendProcess = null

  if (isWindows) {
    const killer = spawn('taskkill', ['/pid', String(processToStop.pid), '/t', '/f'], {
      windowsHide: true,
      stdio: 'ignore',
    })
    killer.unref()
    return
  }

  try {
    processToStop.kill('SIGTERM')
  } catch {
    // Ignore shutdown errors.
  }
}

function resolveRendererTarget() {
  if (process.env.ELECTRON_RENDERER_URL) {
    return { type: 'url', value: process.env.ELECTRON_RENDERER_URL }
  }

  if (!app.isPackaged) {
    const localBuiltIndex = resolveRepoPath('frontend', 'dist', 'index.html')
    if (existsSync(localBuiltIndex)) {
      return { type: 'file', value: localBuiltIndex }
    }
    return { type: 'url', value: 'http://127.0.0.1:5173' }
  }

  return {
    type: 'file',
    value: resolvePackagedResourcePath('frontend-dist', 'index.html'),
  }
}

async function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1600,
    height: 980,
    minWidth: 1260,
    minHeight: 760,
    backgroundColor: '#eef2f7',
    title: 'Manga Translator',
    webPreferences: {
      preload: join(__dirname, 'preload.mjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
  })

  const target = resolveRendererTarget()
  if (target.type === 'url') {
    await mainWindow.loadURL(target.value)
  } else {
    await mainWindow.loadFile(target.value)
  }
}

ipcMain.handle('desktop:get-runtime', async () => backendRuntime)
ipcMain.handle('desktop:reveal-path', async (_event, targetPath) => {
  if (!targetPath) {
    return false
  }
  shell.showItemInFolder(targetPath)
  return true
})
ipcMain.handle('desktop:open-user-fonts', async () => {
  const fontsDir = join(app.getPath('userData'), 'fonts')
  ensureDir(fontsDir)
  const error = await shell.openPath(fontsDir)
  return {
    ok: !error,
    path: fontsDir,
    error,
  }
})

app.on('window-all-closed', () => {
  app.quit()
})

app.on('before-quit', () => {
  stopBackend()
})

app.whenReady().then(async () => {
  try {
    await startBackend()
    await createMainWindow()
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown startup error'
    await dialog.showErrorBox('Manga Translator 启动失败', message)
    stopBackend()
    app.quit()
  }
})
