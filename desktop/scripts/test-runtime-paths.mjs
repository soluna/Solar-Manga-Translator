import assert from 'node:assert/strict'

import { resolveApplicationDataDir } from '../runtime-paths.mjs'


assert.equal(
  resolveApplicationDataDir({
    platform: 'win32',
    env: { LOCALAPPDATA: 'C:\\Users\\Test\\AppData\\Local' },
    projectDir: 'E:\\Projects\\manga-translator',
  }),
  'E:\\Projects\\manga-translator\\.runtime',
)

assert.equal(
  resolveApplicationDataDir({
    platform: 'win32',
    env: { APP_DATA_DIR: 'D:\\MangaData' },
    projectDir: 'E:\\Projects\\manga-translator',
  }),
  'D:\\MangaData',
)

assert.equal(
  resolveApplicationDataDir({
    platform: 'darwin',
    env: {},
    projectDir: '/tmp/manga-translator',
  }),
  '/tmp/manga-translator/.runtime',
)

console.log('Desktop runtime path checks passed.')
