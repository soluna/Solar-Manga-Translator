// Keep the documented mock command usable in Windows cmd as well as POSIX shells.
import { spawn } from 'node:child_process'
import { fileURLToPath } from 'node:url'
const child = spawn(process.execPath, [fileURLToPath(new URL('../node_modules/vite/bin/vite.js', import.meta.url)), ...process.argv.slice(2)], {
  stdio: 'inherit', env: { ...process.env, VITE_MOCK_API: '1' },
})
child.on('error', error => { console.error(error.message); process.exitCode = 1 })
child.on('exit', code => { process.exitCode = code ?? 1 })
