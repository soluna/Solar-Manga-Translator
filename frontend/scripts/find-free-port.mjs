import { findAvailablePort } from '../../scripts/local-port.mjs'

const requestedPort = Number(process.argv[2] || process.env.FRONTEND_PORT || process.env.VITE_DEV_PORT || 5173)
const preferredPort = Number.isFinite(requestedPort) && requestedPort > 0 ? requestedPort : 5173
const host = process.argv[3] || process.env.FRONTEND_HOST || '127.0.0.1'

const port = await findAvailablePort({
  preferredPort,
  host,
})

process.stdout.write(String(port))
