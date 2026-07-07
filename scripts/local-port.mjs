import net from 'node:net'

export function normalizePort(value, fallbackPort = 5173) {
  const port = Number(value)
  return Number.isInteger(port) && port > 0 && port <= 65535 ? port : fallbackPort
}

export async function canListenOnHost(port, host = '127.0.0.1') {
  return await new Promise((resolve) => {
    const server = net.createServer()
    server.unref()
    server.once('error', () => resolve(false))
    server.listen({ host, port }, () => {
      server.close(() => resolve(true))
    })
  })
}

export async function findAvailablePort({
  preferredPort = 5173,
  fallbackPort = 5173,
  host = '127.0.0.1',
  scanLimit = 200,
  blockedPorts = new Set(),
} = {}) {
  if (Number(preferredPort) === 0) {
    return await findUnblockedEphemeralPort(host, blockedPorts)
  }

  const startPort = normalizePort(preferredPort, fallbackPort)
  const blocked = new Set([...blockedPorts].map((port) => normalizePort(port, 0)).filter(Boolean))
  const maxPort = Math.min(65535, startPort + Math.max(1, scanLimit) - 1)

  for (let port = startPort; port <= maxPort; port += 1) {
    if (!blocked.has(port) && await canListenOnHost(port, host)) {
      return port
    }
  }

  return await findUnblockedEphemeralPort(host, blocked)
}

async function findEphemeralPort(host) {
  return await new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.once('error', reject)
    server.listen({ host, port: 0 }, () => {
      const address = server.address()
      const port = typeof address === 'object' && address ? address.port : 0
      server.close((error) => {
        if (error) {
          reject(error)
          return
        }
        resolve(port)
      })
    })
  })
}

async function findUnblockedEphemeralPort(host, blockedPorts = new Set()) {
  const blocked = new Set([...blockedPorts].map((port) => normalizePort(port, 0)).filter(Boolean))
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const port = await findEphemeralPort(host)
    if (!blocked.has(port)) {
      return port
    }
  }

  throw new Error(`Unable to find an available local port on ${host}.`)
}
