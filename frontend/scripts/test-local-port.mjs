import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

import { findAvailablePort } from '../../scripts/local-port.mjs'

const execFileAsync = promisify(execFile)
const __filename = fileURLToPath(import.meta.url)
const __dirname = path.dirname(__filename)

async function listenOnLoopback(port = 0) {
  return await new Promise((resolve, reject) => {
    const server = net.createServer()
    server.once('error', reject)
    server.listen({ host: '127.0.0.1', port }, () => {
      const address = server.address()
      const actualPort = typeof address === 'object' && address ? address.port : port
      resolve({ server, port: actualPort })
    })
  })
}

async function closeServer(server) {
  await new Promise((resolve, reject) => {
    server.close((error) => {
      if (error) {
        reject(error)
        return
      }
      resolve()
    })
  })
}

const busy = await listenOnLoopback()
try {
  const { stdout } = await execFileAsync(
    process.execPath,
    [path.join(__dirname, 'find-free-port.mjs'), String(busy.port)],
    { cwd: path.resolve(__dirname, '..') },
  )
  const selectedPort = Number(stdout.trim())

  assert.notEqual(
    selectedPort,
    busy.port,
    'find-free-port must not return a port already bound on 127.0.0.1',
  )
  assert.ok(selectedPort > busy.port, 'find-free-port should scan upward from the requested port')
} finally {
  await closeServer(busy.server)
}

const randomPort = await findAvailablePort({ preferredPort: 0, host: '127.0.0.1' })
assert.ok(randomPort > 0 && randomPort <= 65535, 'preferredPort=0 should request an ephemeral port')

const blocked = await listenOnLoopback()
try {
  const selectedPort = await findAvailablePort({
    preferredPort: blocked.port,
    host: '127.0.0.1',
    blockedPorts: new Set([blocked.port + 1]),
  })
  assert.notEqual(selectedPort, blocked.port, 'busy preferred port should be skipped')
  assert.notEqual(selectedPort, blocked.port + 1, 'explicitly blocked ports should be skipped')
} finally {
  await closeServer(blocked.server)
}

console.log('Local port selection tests passed.')
