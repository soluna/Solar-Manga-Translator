import assert from 'node:assert/strict'
import { test } from 'node:test'
import { useGlossary } from '../src/composables/useGlossary.js'

const entry = { id: 'term-a', source: 'アキ', translation: '阿纪', category: '人名',
  replacement: '亚纪', note: '', source_kind: 'system',
  occurrences: [{ page_id: '001.png', region_id: 'region-a', source_text: 'アキです' }] }

test('glossary loads canonical entries and applies the exact previewed draft', async () => {
  let previewed, applied
  const state = useGlossary({
    load: async () => ({ glossary: { entries: [entry] } }),
    preview: async (_id, body) => { previewed = structuredClone(body); return { changes: [{ region_id: 'region-a', before: '亚纪', after: '阿纪' }] } },
    apply: async (_id, body) => { applied = structuredClone(body); return { glossary: { entries: body.entries }, change_count: 1 } },
  })
  await state.load('project-a')
  assert.equal(state.entries.value[0].source, 'アキ')
  assert.equal(state.entries.value[0].occurrences[0].region_id, 'region-a')
  state.entries.value[0].translation = '秋'
  await state.preview()
  assert.equal(state.canApply.value, true)
  await state.apply()
  assert.deepEqual(applied, previewed)
  assert.equal(applied.entries[0].id, 'term-a')
  assert.equal(applied.entries[0].replacement, '亚纪')
  assert.equal(state.dirty.value, false)
})

test('failed saves retain drafts and editing after preview prevents application', async () => {
  let applications = 0
  const state = useGlossary({
    load: async () => ({ glossary: { entries: [entry] } }),
    save: async () => { throw new Error('connection lost') },
    preview: async () => ({ changes: [] }),
    apply: async () => { applications += 1 },
  })
  await state.load('project-a')
  state.entries.value[0].translation = '秋'
  await assert.rejects(state.save(), /connection lost/)
  assert.equal(state.entries.value[0].translation, '秋')
  assert.equal(state.dirty.value, true)
  await state.preview()
  state.entries.value[0].translation = '小秋'
  assert.equal(state.canApply.value, false)
  await assert.rejects(state.apply(), /先预览/)
  assert.equal(applications, 0)
})

test('candidate extraction does not replace drafts; adoption retains evidence', async () => {
  const candidate = { ...entry, id: 'term-b', source: 'ユキ', translation: '雪' }
  const state = useGlossary({
    load: async () => ({ glossary: { entries: [entry] } }),
    extract: async (_id, body) => {
      assert.equal(body.preview_only, true)
      return { glossary: { entries: [entry, candidate] } }
    },
  })
  await state.load('project-a')
  state.entries.value[0].translation = '小秋'
  await state.extract()
  assert.equal(state.entries.value[0].translation, '小秋')
  assert.equal(state.candidates.value.length, 1)
  state.adopt(state.candidates.value[0])
  assert.equal(state.entries.value.length, 2)
  assert.equal(state.entries.value[1].occurrences[0].region_id, 'region-a')
  assert.equal(state.candidates.value.length, 0)
  assert.equal(state.dirty.value, true)
})
