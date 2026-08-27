/**
 * Mock API 插件 —— 仅用于无后端时的视觉/交互验证。
 *
 * 启用：VITE_MOCK_API=1 npm run dev（package.json 的 dev:mock）。
 * 数据：mock-data.mjs；所有 /api/** 请求被拦截。
 * 重要：mock 仅模拟契约形状，不代表后端真实行为。
 *
 * 路径分段说明：seg = url 去掉 /api/ 前缀后按 / 切分，
 * 例如 /api/pages/sid/pid/document → ['pages','sid','pid','document']。
 */
import mockData from './mock-data.mjs'

const DOT_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==',
  'base64',
)

export default function mockApiPlugin() {
  const data = mockData

  return {
    name: 'mock-api',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = String(req.url || '')
        if (!url.startsWith('/api/')) {
          return next()
        }

        const sendJson = (status, body) => {
          res.statusCode = status
          res.setHeader('Content-Type', 'application/json')
          res.end(JSON.stringify(body))
        }

        // ---- 图片与下载 ----
        if (url.includes('/image') || url.includes('/previews/')) {
          res.setHeader('Content-Type', 'image/png')
          res.setHeader('Cache-Control', 'no-store')
          res.end(DOT_PNG)
          return
        }
        if (url.includes('/download/')) {
          res.setHeader('Content-Type', 'application/zip')
          res.end('mock-zip')
          return
        }

        const seg = url.replace(/^\/api\//, '').split('?')[0].split('/').filter(Boolean)
        const resource = seg[0] || ''
        const method = String(req.method || 'GET').toUpperCase()

        // /api/status
        if (resource === 'status') {
          return sendJson(200, { status: 'running', auth_required: false })
        }

        // /api/projects...
        if (resource === 'projects') {
          // GET /api/projects
          if (seg.length === 1 && method === 'GET') {
            return sendJson(200, { projects: data.projects || [] })
          }
          const projectId = seg[1] || ''
          const sub = seg[2] || ''
          // POST /api/projects/{id}/restore
          if (sub === 'restore' && method === 'POST') {
            const view = data.projectViews?.[projectId] || Object.values(data.projectViews || {})[0]
            return sendJson(200, view || { session_id: projectId, images: [], total_images: 0, workflow_stage: 'idle', project: { project_id: projectId } })
          }
          // GET/PUT /api/projects/{id}/glossary
          if (sub === 'glossary') {
            const gloss = data.glossaries?.[projectId] || Object.values(data.glossaries || {})[0] || { entries: [] }
            return sendJson(200, gloss)
          }
          // GET /api/projects/{id}/snapshots
          if (sub === 'snapshots') {
            return sendJson(200, { snapshots: data.snapshots?.[projectId] || [] })
          }
          // PATCH/DELETE /api/projects/{id}
          if (seg.length === 2) {
            return sendJson(200, { ok: true })
          }
          return sendJson(200, { ok: true })
        }

        // /api/pages/{sid}/{pid}/...
        if (resource === 'pages') {
          const sessionId = seg[1] || ''
          const pageId = seg[2] || ''
          const sub = seg[3] || ''
          if (sub === 'document') {
            const doc = data.pageDocuments?.[pageId] || Object.values(data.pageDocuments || {})[0] || { revision: 0, regions: [] }
            return sendJson(200, { document: doc })
          }
          if (sub === 'commands') {
            const view = data.projectViews?.[sessionId] || Object.values(data.projectViews || {})[0]
            return sendJson(200, view || { session_id: sessionId, ok: true })
          }
          if (sub === 'advanced-erase') {
            if (seg[4] === 'suggest-selection') {
              return sendJson(200, { selection: { bbox: [120, 180, 320, 260] } })
            }
            if (seg[4] === 'previews') {
              res.setHeader('Content-Type', 'image/png')
              res.end(DOT_PNG)
              return
            }
            return sendJson(200, { attempt_id: 'mock-attempt' })
          }
          return sendJson(404, { detail: `[mock] 未实现的 pages 端点: ${url}` })
        }

        // /api/app/settings
        if (resource === 'app' && seg[1] === 'settings') {
          return sendJson(200, { settings: data.settings || {} })
        }
        // /api/app/local-models/xxx
        if (resource === 'app' && seg[1] === 'local-models') {
          return sendJson(200, { available: true })
        }
        // /api/app/diagnostics/export
        if (resource === 'app' && seg[1] === 'diagnostics') {
          res.setHeader('Content-Type', 'application/zip')
          res.end('mock-diagnostics')
          return
        }

        // /api/fonts
        if (resource === 'fonts') {
          return sendJson(200, { fonts: data.fonts || [] })
        }
        // /api/tasks/{id} 与 /cancel
        if (resource === 'tasks') {
          return sendJson(200, { task_id: seg[1] || '', status: 'completed' })
        }

        return sendJson(404, { detail: `[mock] 未实现的端点: ${url}` })
      })
    },
  }
}
