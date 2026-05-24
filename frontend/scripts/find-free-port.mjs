import net from 'node:net'

const requestedPort = Number(process.argv[2] || process.env.FRONTEND_PORT || process.env.VITE_DEV_PORT || 5173)
const preferredPort = Number.isFinite(requestedPort) && requestedPort > 0 ? requestedPort : 5173
const host = process.argv[3] || '0.0.0.0'

function canListen(port) {
  return new Promise((resolve) => {
    const server = net.createServer()
    server.unref()
    server.once('error', () => resolve(false))
    server.listen(port, host, () => {
      server.close(() => resolve(true))
    })
  })
}

for (let port = preferredPort; port < preferredPort + 200; port += 1) {
  if (await canListen(port)) {
    process.stdout.write(String(port))
    process.exit(0)
  }
}

const randomServer = net.createServer()
randomServer.unref()
randomServer.listen(0, host, () => {
  const address = randomServer.address()
  const port = typeof address === 'object' && address ? address.port : 0
  randomServer.close(() => {
    process.stdout.write(String(port))
  })
})
