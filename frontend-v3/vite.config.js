import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import mockApiPlugin from './plugins/mock-api.js'

const devApiTarget = process.env.VITE_DEV_PROXY_TARGET || 'http://127.0.0.1:8000'
const devPort = Number(process.env.VITE_DEV_PORT || process.env.FRONTEND_V3_PORT || 5273)

export default defineConfig(({ command }) => ({
  base: command === 'build' ? './' : '/',
  plugins: [vue(), ...(process.env.VITE_MOCK_API === '1' ? [mockApiPlugin()] : [])],
  server: {
    host: '127.0.0.1',
    port: Number.isFinite(devPort) && devPort > 0 ? devPort : 5273,
    strictPort: false,
    proxy: {
      '/api': devApiTarget,
      '/output': devApiTarget
    }
  }
}))