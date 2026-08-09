import assert from 'node:assert/strict'

import { usePageCommandState } from '../src/composables/usePageCommandState.js'

const state = usePageCommandState()
const events = []
let active = 0
let maximumActive = 0

function command(label, release) {
  return async () => {
    active += 1
    maximumActive = Math.max(maximumActive, active)
    events.push(`start:${label}`)
    await release
    events.push(`end:${label}`)
    active -= 1
    return label
  }
}

let releaseFirst
const firstGate = new Promise((resolve) => {
  releaseFirst = resolve
})
const first = state.executePageCommand(
  'project-a',
  'page-1.png',
  command('first', firstGate),
)
const second = state.executePageCommand(
  'project-a',
  'page-2.png',
  command('second', Promise.resolve()),
)

await new Promise((resolve) => setImmediate(resolve))
assert.deepEqual(events, ['start:first'])
assert.equal(state.isPageCommandPending('page-1.png'), true)
assert.equal(state.isPageCommandPending('page-2.png'), true)
releaseFirst()
assert.deepEqual(await Promise.all([first, second]), ['first', 'second'])
assert.deepEqual(events, [
  'start:first',
  'end:first',
  'start:second',
  'end:second',
])
assert.equal(maximumActive, 1)
assert.equal(state.hasPendingPageCommands.value, false)

let releaseParallel
const parallelGate = new Promise((resolve) => {
  releaseParallel = resolve
})
active = 0
maximumActive = 0
const projectA = state.executePageCommand(
  'project-a',
  'page-3.png',
  command('project-a', parallelGate),
)
const projectB = state.executePageCommand(
  'project-b',
  'page-4.png',
  command('project-b', parallelGate),
)
await new Promise((resolve) => setImmediate(resolve))
assert.equal(maximumActive, 2)
releaseParallel()
await Promise.all([projectA, projectB])

await assert.rejects(
  state.executePageCommand('project-c', 'page-5.png', async () => {
    throw new Error('expected failure')
  }),
  /expected failure/,
)
assert.equal(
  await state.executePageCommand(
    'project-c',
    'page-6.png',
    async () => 'recovered',
  ),
  'recovered',
)

console.log('page command state tests passed')
