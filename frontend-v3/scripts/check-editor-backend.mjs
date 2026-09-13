// Invoked by scripts/check_editor_contract.py against FastAPI's real TestClient.
import assert from 'node:assert/strict'
import { createInterface } from 'node:readline'
import { usePageEditor } from '../src/composables/usePageEditor.js'
import { useSettings } from '../src/composables/useSettings.js'
import { SETTINGS_FIELDS } from '../src/state/settings-fields.js'
import { buildImportForm } from '../src/state/project-import.js'
import { brushOperations, eraseRequest, normalizedPoint, selectionBox, previewAttempt } from '../src/state/erase-contract.js'

const input = createInterface({ input: process.stdin })
const pending = new Map()
let sequence = 0
let seedResolve
const seed = new Promise(resolve => { seedResolve = resolve })
input.on('line', line => {
  const response = JSON.parse(line)
  if (response.seed) return seedResolve(response.seed)
  const job = pending.get(response.id)
  pending.delete(response.id)
  if (response.status >= 400) {
    const detail = response.body.detail
    job.reject(Object.assign(new Error(typeof detail === 'string' ? detail : detail.message), {
      status: response.status, document: detail?.document,
    }))
  } else job.resolve(response.body)
})
function request(method, url, body, form) {
  const id = ++sequence
  const result = new Promise((resolve, reject) => pending.set(id, { resolve, reject }))
  process.stdout.write(`${JSON.stringify({ id, method, url, body, form })}\n`)
  return result
}

async function uploadForm(form) {
  const entries = []
  for (const [name, value] of form.entries()) {
    entries.push(typeof value === 'string' ? { name, value } : {
      name, filename: value.name, contentType: value.type || 'application/octet-stream',
      base64: Buffer.from(await value.arrayBuffer()).toString('base64'),
    })
  }
  return request('POST', '/api/upload', undefined, entries)
}

try {
  const { projectId, pageId, regionId, imageBase64, archiveBase64 } = await seed
  const makeImage = name => new File([Buffer.from(imageBase64, 'base64')], name, { type: 'image/png' })
  const imports = [
    { files: [makeImage('single.png')], count: 1 },
    { files: [makeImage('001.png'), makeImage('002.png')], count: 2 },
    { files: [
      { file: makeImage('same.png'), relativePath: 'Book/chapter1/same.png' },
      { file: makeImage('same.png'), relativePath: 'Book/chapter2/same.png' },
    ], options: { folderName: 'Book' }, count: 2 },
    ...['zip', 'cbz'].map(extension => ({
      files: [new File([Buffer.from(archiveBase64, 'base64')], `book.${extension}`, { type: 'application/zip' })], count: 2,
    })),
  ]
  const importIds = new Set()
  for (const item of imports) {
    const view = await uploadForm(buildImportForm(item.files, item.options))
    assert.ok(view.session_id)
    assert.equal(importIds.has(view.session_id), false)
    importIds.add(view.session_id)
    assert.equal(view.images.length, item.count)
    assert.equal(new Set(view.images.map(image => image.stored_name)).size, item.count)
    for (const image of view.images) {
      const result = await request('GET', `/api/pages/${view.session_id}/${image.stored_name}/document`)
      assert.deepEqual(result.document.dimensions, { width: 600, height: 900 })
      assert.equal(result.document.page_id, image.stored_name)
    }
  }
  const settingsApi = {
    load: () => request('GET', '/api/app/settings'),
    save: body => request('PATCH', '/api/app/settings', body),
    validate: body => request('POST', '/api/app/settings/validate', body),
  }
  const preferences = useSettings(settingsApi)
  await preferences.load()
  for (const [key, field] of Object.entries(SETTINGS_FIELDS)) {
    assert.ok(Object.hasOwn(preferences.draft, key), `Backend must supply setting ${key}`)
    if (field.type === 'number') assert.equal(typeof preferences.draft[key], 'number')
    if (field.type === 'boolean') assert.equal(typeof preferences.draft[key], 'boolean')
  }
  Object.assign(preferences.draft, { translator: 'openai-compatible', target_lang: 'ENG',
    api_key: 'synthetic-key-only', openai_base_url: 'https://example.test/v1', openai_model: 'synthetic-model' })
  await preferences.flush()
  assert.equal(preferences.secretConfigured('api_key'), true)
  assert.equal(preferences.draft.api_key, '')
  const validation = await preferences.validate()
  assert.equal(validation.ok, true)
  assert.equal(validation.translator, 'openai-compatible')
  const reloaded = useSettings(settingsApi)
  await reloaded.load()
  assert.equal(reloaded.draft.openai_model, 'synthetic-model')
  assert.equal(reloaded.draft.target_lang, 'ENG')
  reloaded.draft.render_alignment = 'left'
  await reloaded.flush()
  assert.equal(reloaded.secretConfigured('api_key'), true)
  assert.equal(typeof (await request('GET', '/api/app/local-models/lama-large')).model.downloaded, 'boolean')

  const endpoint = `/api/pages/${projectId}/${pageId}`
  const editor = usePageEditor({ api: {
    load: () => request('GET', `${endpoint}/document`),
    command: (_project, _page, body) => request('POST', `${endpoint}/commands`, body),
  } })
  await editor.load(projectId, pageId)
  const original = { ...editor.draftFor(regionId) }
  Object.assign(editor.draftFor(regionId), { translation: '仅用于接口验证的文字', fontSize: 36,
    direction: 'horizontal', fontStyle: 'rounded', rotation: 8, fgColor: '#19232d', preserveBackground: true })
  await editor.flush()
  assert.equal(editor.dirty.value, false)
  const saved = await request('GET', `${endpoint}/document`)
  assert.equal(saved.document.regions[0].translation.resolved, '仅用于接口验证的文字')
  assert.equal(saved.document.regions[0].style.font_size_override, 36)
  assert.equal(saved.document.regions[0].style.font_style_override, 'rounded')
  assert.equal(saved.document.regions[0].flags.preserve_background, true)
  await editor.undo()
  assert.deepEqual({ ...editor.draftFor(regionId) }, original)
  await editor.redo()
  assert.equal(editor.draftFor(regionId).translation, '仅用于接口验证的文字')
  assert.equal(editor.draftFor(regionId).fontSize, 36)

  await editor.execute([{ type: 'disable_region', region_id: regionId }])
  assert.equal(editor.draftFor(regionId).enabled, false)
  await editor.execute([{ type: 'restore_region', region_id: regionId }])
  assert.equal(editor.draftFor(regionId).translation, '仅用于接口验证的文字')
  assert.equal(editor.draftFor(regionId).fontSize, 36)
  assert.equal(editor.draftFor(regionId).preserveBackground, true)

  const created = await editor.execute([{ type: 'create_region', bbox: [300, 250, 500, 420] }])
  const secondId = created.created_region_id
  assert.ok(secondId)
  await editor.undo()
  assert.equal(editor.document.value.regions.length, 1)
  await editor.redo()
  assert.ok(editor.document.value.regions.some(region => region.id === secondId))

  await editor.execute([{ type: 'delete_manual_region', region_id: regionId }])
  assert.equal(editor.document.value.regions.some(region => region.id === regionId), false)
  await editor.undo()
  assert.equal(editor.draftFor(regionId).translation, '仅用于接口验证的文字')
  assert.equal(editor.draftFor(regionId).fontSize, 36)

  const merged = await editor.execute([{ type: 'merge_regions', region_ids: [regionId, secondId] }])
  assert.ok(merged.created_region_id)
  assert.ok(editor.document.value.regions.some(region => region.id === merged.created_region_id), 'Merged region must be visible immediately')
  assert.equal(editor.draftFor(regionId).enabled, false)
  await editor.undo()
  assert.equal(editor.draftFor(regionId).enabled, true)
  assert.equal(editor.draftFor(regionId).translation, '仅用于接口验证的文字')
  assert.equal(editor.draftFor(regionId).fontSize, 36)
  await editor.redo()
  assert.ok(editor.document.value.regions.some(region => region.id === merged.created_region_id), JSON.stringify(editor.document.value.canonical))
  await editor.undo()

  const restored = await request('POST', `/api/projects/${projectId}/restore`, {})
  assert.equal(restored.images.length, 1)
  await editor.load(projectId, pageId)
  assert.equal(editor.draftFor(regionId).translation, '仅用于接口验证的文字')
  assert.equal(editor.dirty.value, false)

  const dimensions = { w: 600, h: 900 }
  const brush = brushOperations([{ points: [[200, 300]], radius: 10 }], 'paint', '#102030')
  await request('POST', `${endpoint}/brush-edit`, { operations: brush })
  assert.deepEqual((await request('GET', `${endpoint}/base-image`)).center, [16, 32, 48])
  assert.deepEqual((await request('GET', `${endpoint}/base-image`)).outside, [255, 255, 255])
  const selected = await request('POST', `${endpoint}/advanced-erase/suggest-selection`, {
    point: normalizedPoint([200, 300], dimensions), config: { use_gpu: false },
  })
  const bbox = selectionBox(selected.selection, dimensions)
  assert.ok(bbox[0] <= 200 && bbox[2] >= 200)
  assert.ok(bbox[1] <= 300 && bbox[3] >= 300)
  const erased = await request('POST', `${endpoint}/advanced-erase`, eraseRequest({ provider: 'local', scope: 'selection',
    marks: [{ bbox }, { points: [[200, 300]], radius: 10 }], dimensions, maskMode: 'region', config: { use_gpu: false } }))
  assert.equal(erased.advanced_erase.selection_count, 2)
  assert.equal(erased.advanced_erase.selection_stroke_count, 1)
  assert.equal(erased.advanced_erase.mask_mode, 'selection')
  assert.deepEqual((await request('GET', `${endpoint}/base-image`)).center, [255, 255, 255])
  const preview = previewAttempt(await request('POST', `${endpoint}/advanced-erase`, eraseRequest({ provider: 'local', scope: 'full', config: { use_gpu: false } })))
  const applied = await request('POST', `${endpoint}/advanced-erase`, { action: 'local-advanced-apply', attempt_id: preview.attempt_id, config: { use_gpu: false } })
  assert.equal(applied.advanced_erase.attempt_id, preview.attempt_id)
  assert.deepEqual((await request('GET', `${endpoint}/base-image`)).outside, [255, 255, 255])
  await editor.load(projectId, pageId)
  const beforeSource = editor.draftFor(regionId).sourceText
  editor.draftFor(regionId).sourceText = '人工修正原文'
  await editor.flush()
  await editor.undo()
  assert.equal(editor.draftFor(regionId).sourceText, beforeSource)
  await editor.redo()
  assert.equal(editor.draftFor(regionId).sourceText, '人工修正原文')
  await editor.execute([{ type: 'retry_region_ocr', region_id: regionId }])
  assert.equal(editor.draftFor(regionId).sourceText, '合成 OCR 原文')
  await editor.undo()
  assert.equal(editor.draftFor(regionId).sourceText, '人工修正原文')
  await editor.redo()
  assert.equal(editor.draftFor(regionId).sourceText, '合成 OCR 原文')
  await editor.execute([{ type: 'retry_region_ocr', region_id: regionId }])
  assert.equal(editor.draftFor(regionId).sourceText, '合成 OCR 原文')
  assert.equal(editor.document.value.regions.find(region => region.id === regionId).recognition_status, 'failed')
  await editor.undo()
  assert.equal(editor.document.value.regions.find(region => region.id === regionId).recognition_status, 'ready')
  await editor.execute([{ type: 'set_review_status', status: 'reviewed' }])
  await request('POST', `/api/projects/${projectId}/restore`, {})
  await editor.load(projectId, pageId)
  assert.equal(editor.document.value.canonical.metadata.review.status, 'reviewed')
  await request('POST', `${endpoint}/brush-edit`, { operations: brush })
  await editor.load(projectId, pageId)
  assert.equal(editor.document.value.canonical.metadata.review.status, 'unreviewed')
  process.stdout.write(`${JSON.stringify({ complete: true, assertions: 'settings, editor history, source/OCR, human review, restore, brush, local selection and preview/apply' })}\n`)
} finally {
  input.close()
}
