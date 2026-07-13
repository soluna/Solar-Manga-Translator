import assert from 'node:assert/strict'
import { promises as fs } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { getSfntTableTags, sanitizeSfntFontForBrowser } from '../src/font-preview.js'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const repoRoot = path.resolve(__dirname, '..', '..')
const fixtureFontPath = path.join(repoRoot, 'fonts', 'system', 'SourceHanSansSC-Regular-2.otf')
const sourceFont = await fs.readFile(fixtureFontPath)
const sourceTags = getSfntTableTags(sourceFont)

assert.ok(sourceTags.includes('vhea'), '字体夹具应包含垂直度量表')
assert.ok(sourceTags.includes('vmtx'), '字体夹具应包含垂直度量数据')

const sanitizedFont = sanitizeSfntFontForBrowser(sourceFont)
const sanitizedTags = getSfntTableTags(sanitizedFont)

assert.ok(sanitizedTags.includes('CFF '), '清理后必须保留字形数据')
assert.ok(sanitizedTags.includes('cmap'), '清理后必须保留字符映射')
assert.ok(!sanitizedTags.includes('DSIG'), '修改后的无效签名必须移除')
assert.ok(!sanitizedTags.includes('VORG'), '不兼容的垂直原点表必须移除')
assert.ok(!sanitizedTags.includes('vhea'), '浏览器拒绝的垂直度量头必须移除')
assert.ok(!sanitizedTags.includes('vmtx'), '浏览器拒绝的垂直度量数据必须移除')
assert.ok(sanitizedFont.byteLength < sourceFont.byteLength, '清理结果应移除不兼容表')

console.log('Font preview sanitizer tests passed.')
