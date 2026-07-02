import assert from 'node:assert/strict'

import { resolveApplicationDataDir } from '../runtime-paths.mjs'


assert.equal(
  resolveApplicationDataDir({
    platform: 'win32',
    env: { LOCALAPPDATA: 'C:\\Users\\Test\\AppData\\Local' },
    fallbackUserData: 'C:\\Users\\Test\\AppData\\Roaming\\Solar-Manga-Translator',
  }),
  'C:\\Users\\Test\\AppData\\Local\\Solar-Manga-Translator',
)

assert.equal(
  resolveApplicationDataDir({
    platform: 'win32',
    env: {},
    fallbackUserData: 'C:\\Users\\Test\\AppData\\Roaming\\Solar-Manga-Translator',
  }),
  'C:\\Users\\Test\\AppData\\Roaming\\Solar-Manga-Translator',
)

assert.equal(
  resolveApplicationDataDir({
    platform: 'darwin',
    env: {},
    fallbackUserData: '/tmp/solar-manga-user-data',
  }),
  '/tmp/solar-manga-user-data',
)

console.log('Desktop runtime path checks passed.')
