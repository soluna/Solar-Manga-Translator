import assert from 'node:assert/strict'
import { test } from 'node:test'

test('desktop requests and image URLs use the shell backend exactly once', async () => {
  const requests = []
  globalThis.window = {
    mangaDesktop: { runtime: { apiBaseUrl: 'http://127.0.0.1:8123/', apiToken: 'test-only-token' } },
    localStorage: { getItem: () => '' },
    location: { origin: 'file://' },
    fetch: async (url, init) => { requests.push({ url, init }); return new Response('{}') },
  }
  const client = await import(`../src/api/client.js?desktop=${Date.now()}`)
  assert.equal(client.toApiUrl('/api/status'), 'http://127.0.0.1:8123/api/status')
  assert.equal(client.toApiUrl('http://127.0.0.1:8123/api/status'), 'http://127.0.0.1:8123/api/status')
  await client.apiFetch('/api/status')
  assert.equal(requests[0].url, 'http://127.0.0.1:8123/api/status')
  assert.equal(requests[0].init.headers.get('Authorization'), 'Bearer test-only-token')
  assert.equal(client.toWebSocketUrl('/ws/translate/demo'), 'ws://127.0.0.1:8123/ws/translate/demo')
  assert.equal(client.withCacheBust('/api/image', 'revision-7'), client.withCacheBust('/api/image', 'revision-7'))
  assert.match(client.withCacheBust('/api/image', 'revision-7'), /revision-7/)
})

test('relative development requests and WebSockets stay on the proxied origin', async () => {
  globalThis.window = {
    localStorage: { getItem: () => '' }, location: { origin: 'http://127.0.0.1:5273' },
    fetch: async () => new Response('{}'),
  }
  const client = await import(`../src/api/client.js?web=${Date.now()}`)
  assert.equal(client.toApiUrl('/api/status'), '/api/status')
  assert.equal(client.toWebSocketUrl('/ws/translate/demo'), 'ws://127.0.0.1:5273/ws/translate/demo')
  assert.equal(client.withCacheBust('/api/image', 2), 'http://127.0.0.1:5273/api/image?_=2')
})
