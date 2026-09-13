/** Read-only synthetic preview. Unsupported work must never report success. */
import mockData from './mock-data.mjs'
import { readFileSync } from 'node:fs'

// 页面占位图：真实比例（800×1200）的 SVG，供自由画布测量自然尺寸
function pageSvg(label, bg) {
  const lines = []
  for (let x = 100; x < 800; x += 100) lines.push(`<line x1="${x}" y1="0" x2="${x}" y2="1200" stroke="#b9b2a4" stroke-width="1"/>`)
  for (let y = 100; y < 1200; y += 100) lines.push(`<line x1="0" y1="${y}" x2="800" y2="${y}" stroke="#b9b2a4" stroke-width="1"/>`)
  return Buffer.from(
    `<svg xmlns="http://www.w3.org/2000/svg" width="800" height="1200" viewBox="0 0 800 1200">` +
    `<rect width="800" height="1200" fill="${bg}"/>${lines.join('')}` +
    `<text x="400" y="620" font-size="72" text-anchor="middle" fill="#6b6558" font-family="sans-serif">${label}</text>` +
    `</svg>`,
  )
}

export default function mockApiPlugin() {
  return {
    name: 'mock-api',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = String(req.url || '')
        if (!url.startsWith('/api/')) return next()
        const send = (status, body) => {
          res.statusCode = status
          res.setHeader('Content-Type', 'application/json')
          res.setHeader('Cache-Control', 'no-store')
          res.end(JSON.stringify(body))
        }
        let seg
        try { seg = url.split('?')[0].slice(5).split('/').filter(Boolean).map(decodeURIComponent) }
        catch { return send(400, { detail: '无效的请求路径。' }) }
        const [resource, id, sub, operation] = seg
        const method = String(req.method || 'GET').toUpperCase()
        const unsupported = () => send(501, { detail: '演示模式不执行保存、导入、模型处理或导出。请连接真实后端。' })
        const missing = () => send(404, { detail: '演示数据中不存在此项目、页面或端点。' })
        const view = mockData.projectViews[id]
        // Restoring the demo view is the sole POST supported in read-only mode.
        if (resource === 'projects' && seg.length === 3 && sub === 'restore' && method === 'POST') {
          return view ? send(200, view) : missing()
        }
        if (method !== 'GET' || resource === 'download') return unsupported()
        if (resource === 'status') return send(200, { status: 'running', auth_required: false })
        if (resource === 'projects') {
          if (seg.length === 1) return send(200, { projects: mockData.projects })
          if (!view) return missing()
          if (sub === 'task') return send(200, { task: null })
          if (sub === 'glossary') return send(200, { glossary: mockData.glossaries[id] })
          if (sub === 'snapshots') return send(200, { snapshots: [] })
          return missing()
        }
        if (resource === 'pages') {
          if (!view || !view.images.some(image => image.stored_name === sub)) return missing()
          const document = mockData.pageDocuments[sub]
          if (operation === 'document') return send(200, { document })
          if (['source-image', 'base-image', 'preview-image', 'translated-image'].includes(operation)) {
            if (operation === 'translated-image' && !document.translated_image) return missing()
            if (operation === 'base-image' && !document.base_image) return missing()
            const label = operation === 'translated-image' ? '合成嵌字示例' : operation === 'base-image' ? '合成空页示例' : '合成原图示例'
            res.setHeader('Content-Type', 'image/svg+xml')
            res.setHeader('Cache-Control', 'no-store')
            res.end(pageSvg(label, '#e8e4dc'))
            return
          }
          return missing()
        }
        if (resource === 'app' && id === 'settings' && seg.length === 2) return send(200, { settings: mockData.settings })
        if (resource === 'app' && id === 'local-models') return send(200, { model: { downloaded: false, size_bytes: 0, partial_downloaded: false, partial_size_bytes: 0 } })
        if (resource === 'fonts') {
          if (seg.length === 1) return send(200, { fonts: mockData.fonts })
          const font = mockData.fonts.find(font => font.url === url.split('?')[0])
          if (!font) return missing()
          res.setHeader('Content-Type', 'font/otf')
          res.end(readFileSync(new URL(`../../fonts/system/${font.name}`, import.meta.url)))
          return
        }
        return missing()
      })
    },
  }
}
