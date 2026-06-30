import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron'
import { spawn } from 'node:child_process'
import { randomBytes } from 'node:crypto'
import { createWriteStream, existsSync, mkdirSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'
import net from 'node:net'
import { setTimeout as delay } from 'node:timers/promises'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(__dirname, '..')
const isWindows = process.platform === 'win32'

let mainWindow = null
let backendProcess = null
let backendRuntime = null
let apiToken = ''
let rendererAccessPolicy = null

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

async function waitForBackendReady(baseUrl, token, timeoutMs = 60000) {
  const startedAt = Date.now()
  while (Date.now() - startedAt < timeoutMs) {
    try {
      const response = await fetch(`${baseUrl}/api/status`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
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

function resolveBackendLaunch(userDataDir, port, token) {
  const logsDir = join(userDataDir, 'logs')
  const env = {
    ...process.env,
    APP_DESKTOP_MODE: '1',
    APP_VERSION: app.getVersion(),
    APP_DATA_DIR: userDataDir,
    APP_LOG_DIR: logsDir,
    APP_BACKEND_HOST: '127.0.0.1',
    APP_BACKEND_PORT: String(port),
    APP_API_TOKEN: token,
    APP_RUNTIME_PATCHES_PREPARED: app.isPackaged ? '1' : '0',
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
  apiToken = randomBytes(32).toString('base64url')
  const backendBaseUrl = `http://127.0.0.1:${port}`
  const launch = resolveBackendLaunch(userDataDir, port, apiToken)
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

  await waitForBackendReady(backendBaseUrl, apiToken)

  backendRuntime = {
    desktop_mode: true,
    apiBaseUrl: backendBaseUrl,
    apiToken,
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
  apiToken = ''

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

function isLoopbackHostname(hostname) {
  return ['127.0.0.1', 'localhost', '::1', '[::1]'].includes(String(hostname || '').toLowerCase())
}

function assertAllowedDevRendererUrl(value) {
  let parsed
  try {
    parsed = new URL(value)
  } catch {
    throw new Error(`无效的渲染器地址: ${value}`)
  }
  if (!['http:', 'https:'].includes(parsed.protocol) || !isLoopbackHostname(parsed.hostname)) {
    throw new Error('开发态渲染器地址必须使用本机回环地址。')
  }
  return parsed.toString()
}

function isPathInside(candidatePath, rootPath) {
  const relativePath = relative(resolve(rootPath), resolve(candidatePath))
  return relativePath === '' || (
    Boolean(relativePath)
    && !relativePath.startsWith('..')
    && !isAbsolute(relativePath)
  )
}

function createRendererAccessPolicy(target) {
  if (target.type === 'url') {
    return {
      type: 'url',
      origin: new URL(target.value).origin,
    }
  }
  return {
    type: 'file',
    root: dirname(resolve(target.value)),
  }
}

function isAllowedRendererNavigation(url) {
  if (!rendererAccessPolicy) {
    return false
  }
  let parsed
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  if (rendererAccessPolicy.type === 'url') {
    return parsed.origin === rendererAccessPolicy.origin
  }
  return parsed.protocol === 'file:' && isPathInside(fileURLToPath(parsed), rendererAccessPolicy.root)
}

function isSafeExternalUrl(url) {
  try {
    const parsed = new URL(url)
    return ['https:', 'http:'].includes(parsed.protocol)
  } catch {
    return false
  }
}

function isTrustedIpcEvent(event) {
  const senderUrl = event.senderFrame?.url || event.sender?.getURL?.() || ''
  return isAllowedRendererNavigation(senderUrl)
}

function sanitizeRevealPath(targetPath) {
  if (!backendRuntime?.dataDir || typeof targetPath !== 'string') {
    return null
  }
  const resolvedTarget = resolve(targetPath)
  return isPathInside(resolvedTarget, backendRuntime.dataDir) ? resolvedTarget : null
}

function resolveRendererTarget() {
  if (!app.isPackaged && process.env.ELECTRON_RENDERER_URL) {
    return {
      type: 'url',
      value: assertAllowedDevRendererUrl(process.env.ELECTRON_RENDERER_URL),
    }
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
    title: 'Solar-Manga-Translator',
    webPreferences: {
      preload: join(__dirname, 'preload.mjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
      navigateOnDragDrop: false,
    },
  })

  const target = resolveRendererTarget()
  rendererAccessPolicy = createRendererAccessPolicy(target)

  mainWindow.webContents.session.setPermissionCheckHandler(() => false)
  mainWindow.webContents.session.setPermissionRequestHandler((_webContents, _permission, callback) => {
    callback(false)
  })
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isSafeExternalUrl(url)) {
      void shell.openExternal(url)
    }
    return { action: 'deny' }
  })
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (isAllowedRendererNavigation(url)) {
      return
    }
    event.preventDefault()
    if (isSafeExternalUrl(url)) {
      void shell.openExternal(url)
    }
  })
  mainWindow.webContents.on('will-attach-webview', (event) => {
    event.preventDefault()
  })

  if (target.type === 'url') {
    await mainWindow.loadURL(target.value)
  } else {
    await mainWindow.loadFile(target.value)
  }
}

ipcMain.handle('desktop:get-runtime', async (event) => (
  isTrustedIpcEvent(event) ? backendRuntime : null
))
ipcMain.handle('desktop:reveal-path', async (event, targetPath) => {
  if (!isTrustedIpcEvent(event)) {
    return false
  }
  const safePath = sanitizeRevealPath(targetPath)
  if (!safePath) {
    return false
  }
  shell.showItemInFolder(safePath)
  return true
})
ipcMain.handle('desktop:open-user-fonts', async (event) => {
  if (!isTrustedIpcEvent(event)) {
    return { ok: false, path: '', error: '不受信任的渲染器来源。' }
  }
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
    await dialog.showErrorBox('Solar-Manga-Translator 启动失败', message)
    stopBackend()
    app.quit()
  }
})
