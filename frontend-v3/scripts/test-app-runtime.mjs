import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  normalizeMigration,
  normalizeRemoteDiagnostics,
  normalizeRemoteExecution,
  normalizeRuntime,
  needsOnboarding,
} from '../src/state/app-runtime.js'
import { createStartupOnboardingGuard } from '../src/state/onboarding-navigation.js'
import { fontSourceLabel } from '../src/state/settings-fields.js'

test('runtime and migration normalization keeps nested fallback fields', () => {
  const runtime = normalizeRuntime({ runtime: {
    desktop_mode: true,
    migration: { needed: true, summary: { legacy_bytes: 12 } },
  } }, {
    desktop_mode: false,
    migration: { target: { app_data: '/.runtime' }, cleanup: { status: 'pending' } },
  })
  assert.equal(runtime.desktop_mode, true)
  assert.equal(runtime.migration.needed, true)
  assert.equal(runtime.migration.summary.legacy_bytes, 12)
  assert.equal(runtime.migration.target.app_data, '/.runtime')
  assert.equal(runtime.migration.cleanup.status, 'pending')
  assert.equal(normalizeMigration({ migration: { status: 'skipped' } }, runtime.migration).status, 'skipped')
})

test('remote status reads nested responses and keeps an issued token for a later refresh', () => {
  const started = normalizeRemoteDiagnostics({ remote_diagnostics: {
    active: true, port: 1234, urls: ['http://192.168.1.8:1234'], token: 'diag-token',
  } })
  const refreshed = normalizeRemoteDiagnostics({ remote_diagnostics: {
    active: false, urls: [], port: 0,
  } }, started)
  assert.equal(started.token, 'diag-token')
  assert.equal(refreshed.active, false)
  assert.equal(refreshed.token, 'diag-token')

  const execution = normalizeRemoteExecution({ remote_execution: {
    enabled: true, active: true, token: 'worker-token', tasks: ['cuda-smoke-test'],
  } })
  assert.equal(execution.enabled, true)
  assert.deepEqual(execution.tasks, ['cuda-smoke-test'])
  assert.equal(execution.token, 'worker-token')
})

test('automatic onboarding follows desktop first-run conditions only', () => {
  const base = { runtime: { settings_exists: true }, settings: { translator: 'gemini' }, diagnostics: { gpu: {} } }
  assert.equal(needsOnboarding({ ...base, desktopMode: false }), false)
  assert.equal(needsOnboarding({ ...base, desktopMode: true, runtime: { settings_exists: false }, settings: {} }), true)
  assert.equal(needsOnboarding({ ...base, desktopMode: true }), true)
  assert.equal(needsOnboarding({ ...base, desktopMode: true, settings: { translator: 'gemini', configured_secrets: { api_key: true } } }), false)
  assert.equal(needsOnboarding({ ...base, desktopMode: true, settings: { translator: 'sakura' } }), false)
  assert.equal(needsOnboarding({ ...base, desktopMode: true, diagnostics: { gpu: { status: 'torch_cpu_build' } } }), true)
  assert.equal(needsOnboarding({ ...base, desktopMode: true, acknowledged: true }), true)
  assert.equal(fontSourceLabel('project'), '自定义')
})

test('startup onboarding checks the initial home once and does not loop after returning from onboarding', async () => {
  let checks = 0
  const guard = createStartupOnboardingGuard(async () => { checks += 1; return true })

  assert.deepEqual(await guard({ name: 'home' }), { name: 'onboarding', replace: true })
  assert.equal(await guard({ name: 'onboarding' }), true)
  assert.equal(await guard({ name: 'home' }), true)
  assert.equal(checks, 1)

  const nextAppSession = createStartupOnboardingGuard(async () => { checks += 1; return true })
  assert.deepEqual(await nextAppSession({ name: 'home' }), { name: 'onboarding', replace: true })
  assert.equal(checks, 2)
})

test('startup onboarding does not probe or redirect an initial project deep link', async () => {
  let checks = 0
  const guard = createStartupOnboardingGuard(async () => { checks += 1; return true })

  assert.equal(await guard({ name: 'review' }), true)
  assert.equal(await guard({ name: 'home' }), true)
  assert.equal(checks, 0)
})
