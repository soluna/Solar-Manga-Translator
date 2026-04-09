import { spawn } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const desktopDir = resolve(__dirname, '..')
const repoRoot = resolve(desktopDir, '..')
const frontendDir = resolve(repoRoot, 'frontend')
const args = process.argv.slice(2)

function run(command, commandArgs, options = {}) {
  return new Promise((resolveRun, reject) => {
    const child = spawn(command, commandArgs, {
      stdio: 'inherit',
      shell: false,
      ...options,
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
  })

  const electronBuilderArgs = ['electron-builder', '--win', 'nsis']
  if (args.includes('--dir')) {
    electronBuilderArgs.push('--dir')
  }

  await run(process.platform === 'win32' ? 'npx.cmd' : 'npx', electronBuilderArgs, {
    cwd: desktopDir,
    env: process.env,
  })
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : error)
  process.exit(1)
})
