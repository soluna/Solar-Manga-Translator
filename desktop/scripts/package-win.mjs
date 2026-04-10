import { spawn } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const desktopDir = resolve(__dirname, '..')
const repoRoot = resolve(desktopDir, '..')
const frontendDir = resolve(repoRoot, 'frontend')
const args = process.argv.slice(2)

function quoteWindowsArg(value) {
  const stringValue = String(value ?? '')
  if (!stringValue.length) {
    return '""'
  }
  if (!/[ \t"]/u.test(stringValue)) {
    return stringValue
  }
  return `"${stringValue.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\+)$/g, '$1$1')}"`
}

function run(command, commandArgs, options = {}) {
  const useWindowsCommandWrapper = process.platform === 'win32' && options.windowsCommandWrapper !== false
  const spawnCommand = useWindowsCommandWrapper ? 'cmd.exe' : command
  const spawnArgs = useWindowsCommandWrapper
    ? ['/d', '/s', '/c', [quoteWindowsArg(command), ...(commandArgs || []).map(quoteWindowsArg)].join(' ')]
    : commandArgs

  return new Promise((resolveRun, reject) => {
    const child = spawn(spawnCommand, spawnArgs, {
      stdio: 'inherit',
      shell: false,
      ...options,
    })
    child.on('error', (error) => {
      reject(new Error(`无法启动命令：${command} ${(commandArgs || []).join(' ')}\n${error instanceof Error ? error.message : error}`))
    })
    child.on('exit', (code) => {
      if (code === 0) {
        resolveRun()
        return
      }
      reject(new Error(`${command} ${commandArgs.join(' ')} exited with code ${code}`))
    })
  })
}

async function main() {
  if (process.platform !== 'win32') {
    throw new Error('Windows 安装包请在 Windows 机器上构建。')
  }

  await run(process.platform === 'win32' ? 'npm.cmd' : 'npm', ['run', 'build'], {
    cwd: frontendDir,
    env: process.env,
  })

  await run(process.execPath, [resolve(desktopDir, 'scripts', 'stage-runtime.mjs')], {
    cwd: desktopDir,
    env: process.env,
    windowsCommandWrapper: false,
  })

  const electronBuilderArgs = ['electron-builder', '--win', 'nsis']
  if (args.includes('--dir')) {
    electronBuilderArgs.push('--dir')
  }

  const electronBuilderCli = resolve(desktopDir, 'node_modules', 'electron-builder', 'cli.js')
  await run(process.execPath, [electronBuilderCli, ...electronBuilderArgs.slice(1)], {
    cwd: desktopDir,
    env: process.env,
    windowsCommandWrapper: false,
  })
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error)
  process.exit(1)
})
