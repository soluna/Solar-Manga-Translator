import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const devApiTarget = process.env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'
const devPort = Number(process.env.VITE_DEV_PORT || process.env.FRONTEND_PORT || 5173)

export default defineConfig(({ command }) => ({
  base: command === 'build' ? './' : '/',
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    port: Number.isFinite(devPort) && devPort > 0 ? devPort : 5173,
    strictPort: false,
    proxy: {
      '/api': devApiTarget,
      '/output': devApiTarget
    }
  }
}))
