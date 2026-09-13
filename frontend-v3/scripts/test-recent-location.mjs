import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  RECENT_LOCATION_STORAGE_KEY,
  forgetRecentPage,
  recentPageFor,
  rememberRecentPage,
} from '../src/state/recent-location.js'

function storageHarness() {
  const values = new Map()
  globalThis.window = { localStorage: {
    getItem: key => values.get(key) || null,
    setItem: (key, value) => values.set(key, String(value)),
  } }
  return values
}

test('recent page state is scoped per project and ignores stale page IDs', () => {
  const values = storageHarness()
  rememberRecentPage('project-a', '002.png')
  rememberRecentPage('project-b', '003.png')
  assert.equal(recentPageFor('project-a', [{ stored_name: '002.png' }]), '002.png')
  assert.equal(recentPageFor('project-a', [{ stored_name: '001.png' }]), '')
  assert.equal(recentPageFor('project-b'), '003.png')
  assert.ok(JSON.parse(values.get(RECENT_LOCATION_STORAGE_KEY))['project-a'])
})

test('forgetting a recent page is recoverable and does not throw without storage', () => {
  storageHarness()
  rememberRecentPage('project-a', '001.png')
  assert.equal(forgetRecentPage('project-a'), true)
  assert.equal(recentPageFor('project-a'), '')
  assert.equal(forgetRecentPage('project-a'), false)
})
