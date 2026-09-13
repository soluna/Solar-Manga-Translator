import { reactive } from 'vue'
import { apiFetch } from '../api/client.js'
import { sanitizeSfntFontForBrowser } from '../state/browser-font.js'

const families = reactive({}), failures = reactive({}), pending = new Map()
let sequence = 0

export function useFontPreview() {
  async function load(font) {
    if (!font?.id || !font.url || families[font.id] || failures[font.id]) return
    if (pending.has(font.id)) return pending.get(font.id)
    const request = (async () => {
      try {
        const response = await apiFetch(font.url)
        if (!response.ok) throw new Error('字体文件加载失败')
        const family = `inkstage-font-${++sequence}`
        const face = new FontFace(family, sanitizeSfntFontForBrowser(await response.arrayBuffer()))
        await face.load()
        globalThis.document.fonts.add(face)
        families[font.id] = family
      } catch { failures[font.id] = true }
      finally { pending.delete(font.id) }
    })()
    pending.set(font.id, request)
    return request
  }
  return { families, failures, load }
}
