import { join, resolve, win32 } from 'node:path'

export const RUNTIME_DATA_DIR_NAME = '.runtime'

export function resolveApplicationDataDir({
  platform = process.platform,
  env = process.env,
  projectDir = '',
} = {}) {
  const explicitDataDir = String(env.APP_DATA_DIR || '').trim()
  if (explicitDataDir) {
    return platform === 'win32'
      ? win32.resolve(explicitDataDir)
      : resolve(explicitDataDir)
  }

  const root = String(projectDir || '').trim()
  if (!root) {
    throw new Error('无法确定项目目录。')
  }
  if (platform === 'win32') {
    return win32.join(root, RUNTIME_DATA_DIR_NAME)
  }
  return join(root, RUNTIME_DATA_DIR_NAME)
}
