import assert from 'node:assert/strict'
import { test } from 'node:test'
import { buildImportForm, readDroppedFiles } from '../src/state/project-import.js'

test('single images and archives use the backend single-file form with review mode', () => {
  for (const name of ['page.png', 'book.zip', 'book.cbz']) {
    const file = new File(['synthetic'], name)
    const form = buildImportForm([file])
    assert.equal(form.get('file').name, name)
    assert.equal(form.get('review_mode'), 'auto')
    assert.equal(form.has('files'), false)
  }
})

test('folder import keeps each image paired with its relative path and ignores non-images', () => {
  const form = buildImportForm([
    { file: new File(['a'], 'same.png'), relativePath: 'Book/chapter1/same.png' },
    { file: new File(['b'], 'same.png'), relativePath: 'Book/chapter2/same.png' },
    { file: new File(['note'], 'README.txt'), relativePath: 'Book/README.txt' },
  ], { folderName: 'Book' })
  assert.equal(form.get('folder_name'), 'Book')
  assert.deepEqual(form.getAll('files').map(f => f.name), ['same.png', 'same.png'])
  assert.deepEqual(form.getAll('relative_paths'), ['Book/chapter1/same.png', 'Book/chapter2/same.png'])
  assert.throws(() => buildImportForm([new File([''], 'README.txt')]), /没有可导入/)
  assert.throws(() => buildImportForm([new File([''], 'a.png'), new File([''], 'b.zip')]), /压缩包请单独/)
})

test('dropped directories include nested files and all reader batches', async () => {
  const fileEntry = name => ({ name, isFile: true, file: resolve => resolve(new File(['image'], name)) })
  const directory = (name, batches) => ({ name, isDirectory: true, createReader() {
    const remaining = [...batches, []]
    return { readEntries: resolve => resolve(remaining.shift() || []) }
  } })
  const entry = directory('Book', [[fileEntry('001.png')], [directory('extra', [[fileEntry('002.png')]])]])
  const result = await readDroppedFiles({ items: [{ webkitGetAsEntry: () => entry }], files: [] })
  assert.equal(result.folderName, 'Book')
  assert.deepEqual(result.files.map(r => r.relativePath), ['Book/001.png', 'Book/extra/002.png'])
  const form = buildImportForm(result.files, result)
  assert.deepEqual(form.getAll('relative_paths'), ['Book/001.png', 'Book/extra/002.png'])
})
