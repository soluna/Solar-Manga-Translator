import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'
import { usePageEditor } from '../src/composables/usePageEditor.js'
import { buildStablePageImageUrl } from '../src/state/review-document.js'

const fixture = JSON.parse(readFileSync(new URL('../test-fixtures/page-document.json', import.meta.url)))
const copy = value => JSON.parse(JSON.stringify(value))
const deferred = () => {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}

test('a real backend document exposes independent drafts and saves the canonical region and revision', async () => {
  let server = copy(fixture.document)
  let sent
  const editor = usePageEditor({ api: {
    load: async () => ({ document: copy(server) }),
    command: async (projectId, pageId, body) => {
      sent = { projectId, pageId, ...body }
      const command = body.commands[0]
      server.regions.find(region => region.region_id === command.region_id).translation.edited = command.text
      server.regions[0].translation.resolved = command.text
      server.metadata.revision++
      return { document: copy(server), page_id: pageId, session_id: projectId }
    },
  } })
  await editor.load('project-a', '0001.png')
  const [first, second] = editor.document.value.regions
  assert.equal(first.id, 'region-a')
  assert.equal(second.disabled, true)
  assert.equal(first.direction, 'horizontal')
  assert.equal(editor.draftFor(first).rotation, 12)
  assert.equal(editor.draftFor(first).preserveBackground, true)
  editor.draftFor(first).translation = '人工修订译文'
  assert.notEqual(editor.draftFor(second).translation, '人工修订译文')
  assert.equal(editor.dirty.value, true)
  await editor.saveDraft(first.id)
  assert.equal(sent.projectId, 'project-a')
  assert.equal(sent.pageId, '0001.png')
  assert.equal(sent.expected_page_revision, fixture.document.metadata.revision)
  assert.deepEqual(sent.commands, [{ type: 'update_translation', region_id: 'region-a', text: '人工修订译文' }])
  assert.equal(editor.document.value.regions[0].current_translation, '人工修订译文')
  assert.equal(editor.dirty.value, false)
})

test('leaving waits for an in-flight save and persists newer typing with the next page revision', async () => {
  const started = deferred(), finish = deferred(), requests = []
  const server = copy(fixture.document)
  const editor = usePageEditor({ api: {
    load: async () => ({ document: copy(server) }),
    command: async (_project, _page, body) => {
      requests.push(copy(body))
      if (requests.length === 1) { started.resolve(); await finish.promise }
      server.regions[0].translation.edited = body.commands[0].text
      server.regions[0].translation.resolved = body.commands[0].text
      server.metadata.revision++
      return { document: copy(server) }
    },
  } })
  await editor.load('project-a', '0001.png')
  editor.draftFor('region-a').translation = '第一次输入'
  const firstSave = editor.saveDraft('region-a')
  await started.promise
  editor.draftFor('region-a').translation = '保存期间继续输入'
  const leaving = editor.flush()
  assert.equal(requests.length, 1)
  finish.resolve()
  await firstSave
  await leaving
  assert.equal(requests.length, 2)
  assert.equal(requests[1].expected_page_revision, requests[0].expected_page_revision + 1)
  assert.equal(requests[1].commands[0].text, '保存期间继续输入')
  assert.equal(editor.draftFor('region-a').translation, '保存期间继续输入')
  assert.equal(editor.dirty.value, false)
})

test('a failed navigation flush preserves editable drafts and allows an explicit retry', async () => {
  let fail = true
  const server = copy(fixture.document)
  const editor = usePageEditor({ api: {
    load: async () => ({ document: copy(server) }),
    command: async (_project, _page, body) => {
      if (fail) throw new Error('磁盘写入失败')
      server.regions[0].translation = { edited: body.commands[0].text, resolved: body.commands[0].text }
      server.metadata.revision++
      return { document: copy(server) }
    },
  } })
  await editor.load('project-a', '0001.png')
  editor.draftFor('region-a').translation = '不能丢失的修改'
  await assert.rejects(editor.flush(), /磁盘写入失败/)
  assert.equal(editor.draftFor('region-a').translation, '不能丢失的修改')
  assert.equal(editor.hasUnsaved.value, true)
  assert.equal(editor.error.value, '磁盘写入失败')
  fail = false
  await editor.flush()
  assert.equal(editor.dirty.value, false)
  assert.equal(editor.error.value, '')
})

test('late page loads and saves cannot replace the newly selected page or its drafts', async () => {
  const oldLoad = deferred(), oldSave = deferred(), started = deferred()
  const original = copy(fixture.document)
  const second = { ...copy(original), page_id: '0002.png' }
  const editor = usePageEditor({ api: {
    load: async (_project, page) => ({ document: page === '0001.png' ? await oldLoad.promise : second }),
    command: async () => { started.resolve(); return oldSave.promise },
  } })
  const loadFirst = editor.load('project-a', '0001.png')
  await editor.load('project-a', '0002.png')
  editor.draftFor('region-a').translation = '第二页草稿'
  oldLoad.resolve(original)
  await loadFirst
  assert.equal(editor.document.value.page_id, '0002.png')
  assert.equal(editor.draftFor('region-a').translation, '第二页草稿')
  await editor.load('project-a', '0001.png')
  editor.draftFor('region-a').translation = '第一页草稿'
  const saving = editor.saveDraft('region-a')
  await started.promise
  await editor.load('project-a', '0002.png')
  oldSave.resolve({ document: { ...original, metadata: { revision: original.metadata.revision + 1 } } })
  await saving
  assert.equal(editor.document.value.page_id, '0002.png')
  assert.equal(editor.draftFor('region-a').translation, '第二页草稿')
})

test('translation and typography edits undo and redo together using saved server values', async () => {
  const server = copy(fixture.document)
  const initial = copy(server.regions[0])
  const editor = usePageEditor({ api: {
    load: async () => ({ document: copy(server) }),
    command: async (_project, _page, { commands }) => {
      for (const command of commands) {
        const region = server.regions.find(item => item.region_id === command.region_id)
        if (command.type === 'update_translation') region.translation = { ...region.translation, edited: command.text, resolved: command.text }
        if (command.type === 'update_font_size') region.style = { ...region.style, font_size: command.font_size, font_size_override: command.font_size }
      }
      server.metadata.revision++
      return { document: copy(server) }
    },
  } })
  await editor.load('project-a', '0001.png')
  Object.assign(editor.draftFor('region-a'), { translation: '调整后的文字', fontSize: 36 })
  await editor.flush()
  assert.equal(editor.canUndo.value, true)
  await editor.undo()
  assert.equal(editor.draftFor('region-a').translation, initial.translation.resolved)
  assert.equal(editor.draftFor('region-a').fontSize, initial.style.font_size)
  assert.equal(editor.canRedo.value, true)
  await editor.redo()
  assert.equal(editor.draftFor('region-a').translation, '调整后的文字')
  assert.equal(editor.draftFor('region-a').fontSize, 36)
  assert.equal(editor.dirty.value, false)
})

test('a malformed document cannot silently turn regions into one undefined selection', async () => {
  const malformed = copy(fixture.document)
  delete malformed.regions[0].region_id
  malformed.regions[0].id = 'legacy-id'
  const editor = usePageEditor({ api: { load: async () => ({ document: malformed }) } })
  await assert.rejects(editor.load('project-a', '0001.png'), /文本区域.*标识/)
  assert.equal(editor.document.value, null)
})

test('a revision conflict retains the draft but invalidates history based on superseded server content', async () => {
  const server = copy(fixture.document)
  let conflict = false
  const editor = usePageEditor({ api: {
    load: async () => ({ document: copy(server) }),
    command: async (_project, _page, body) => {
      if (conflict) {
        server.metadata.revision++
        server.regions[0].translation = { edited: '外部修改', resolved: '外部修改' }
        throw Object.assign(new Error('页面已更新'), { status: 409, document: copy(server) })
      }
      server.regions[0].translation = { edited: body.commands[0].text, resolved: body.commands[0].text }
      server.metadata.revision++
      return { document: copy(server) }
    },
  } })
  await editor.load('project-a', '0001.png')
  editor.draftFor('region-a').translation = '已保存修改'
  await editor.flush()
  assert.equal(editor.canUndo.value, true)
  editor.draftFor('region-a').translation = '冲突中的草稿'
  conflict = true
  await assert.rejects(editor.flush(), /页面已更新/)
  assert.equal(editor.draftFor('region-a').translation, '冲突中的草稿')
  assert.equal(editor.document.value.regions[0].current_translation, '外部修改')
  assert.equal(editor.canUndo.value, false)
  assert.equal(editor.dirty.value, true)
})

test('editing text does not reload unchanged image artifacts; a new blank artifact changes only its own URL', () => {
  const artifact = copy(fixture.page_artifact)
  const initial = copy(fixture.document)
  const edited = copy(initial)
  edited.metadata.revision++
  const url = (kind, document, state = artifact) => buildStablePageImageUrl({ sessionId: 'project-a',
    pageId: '0001.png', kind, document, artifact: state, maxSide: 1280 })
  assert.equal(url('source-image', initial), url('source-image', edited))
  assert.equal(url('base-image', initial), url('base-image', edited))
  const newer = copy(artifact)
  newer.artifacts.blank.revision++
  assert.notEqual(url('base-image', edited), url('base-image', edited, newer))
  assert.equal(url('source-image', edited), url('source-image', edited, newer))
})
