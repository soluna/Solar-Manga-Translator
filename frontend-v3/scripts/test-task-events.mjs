import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createTaskEvents } from '../src/composables/useTaskEvents.js'

function harness(snapshot = null) {
  const sockets = [], timers = new Map(), requests = [], cancellations = []
  let timerId = 0
  const tasks = createTaskEvents({
    createSocket() {
      const socket = {
        sent: [], send(value) { this.sent.push(JSON.parse(value)) },
        close() { this.onclose?.() },
      }
      sockets.push(socket)
      return socket
    },
    getJson: async (url) => { requests.push(url); return url.includes('/projects/') ? { task: snapshot } : snapshot },
    postJson: async (url, body, message) => { cancellations.push({ url, body, message }); return {} },
    setTimer: (fn) => { timers.set(++timerId, fn); return timerId },
    clearTimer: (id) => timers.delete(id),
  })
  const event = (socket, payload) => socket.onmessage({ data: JSON.stringify(payload) })
  const tick = async () => { const pending = [...timers.values()]; timers.clear(); for (const fn of pending) await fn() }
  return { tasks, sockets, timers, requests, cancellations, event, tick }
}

test('one click starts one task and completion never starts another', async () => {
  const h = harness()
  h.tasks.start('project-a', 'detect')
  assert.equal(h.tasks.busy.value, true)
  h.sockets[0].onopen()
  h.event(h.sockets[0], { event: 'start', task_id: 'task-a', sequence: 1, action: 'detect' })
  h.event(h.sockets[0], { event: 'completed', task_id: 'task-a', sequence: 2 })
  await h.tick()
  assert.equal(h.tasks.busy.value, false)
  assert.equal(h.sockets.length, 1)
  assert.equal(h.timers.size, 0)
  assert.deepEqual(h.sockets.flatMap(s => s.sent), [{ action: 'detect', config: {} }])
})

test('an explicit page batch advances only on completion and stops on cancellation', async () => {
  const h = harness()
  h.tasks.startBatch('project-a', 'rerender', {}, ['page-a', 'page-b', 'page-c'])
  h.sockets[0].onopen()
  h.event(h.sockets[0], { event: 'completed', task_id: 'task-a', sequence: 1 })
  assert.equal(h.tasks.busy.value, true)
  assert.equal(await h.tasks.resume('project-b'), false)
  await h.tick()
  h.sockets[1].onopen()
  assert.deepEqual(h.sockets.map(socket => socket.sent[0].target_stored_name), ['page-a', 'page-b'])
  h.event(h.sockets[1], { event: 'cancelled', task_id: 'task-b', sequence: 1 })
  await h.tick()
  assert.equal(h.tasks.busy.value, false)
  assert.equal(h.sockets.length, 2)
})

test('cancelling between batch pages clears the pending next-page timer', async () => {
  const h = harness()
  h.tasks.startBatch('project-a', 'rerender', {}, ['page-a', 'page-b', 'page-c'])
  h.sockets[0].onopen()
  h.event(h.sockets[0], { event: 'completed', task_id: 'task-a', sequence: 1 })
  assert.equal(h.tasks.taskState.value.eventName, 'batch-next')
  await h.tasks.cancel()
  await h.tick()
  assert.equal(h.tasks.batch.value, null)
  assert.equal(h.tasks.busy.value, false)
  assert.equal(h.sockets.length, 1)
  assert.deepEqual(h.cancellations, [])
})

test('cancelling an active batch page prevents later pages even after a late completion', async () => {
  const h = harness()
  h.tasks.startBatch('project-a', 'rerender', {}, ['page-a', 'page-b'])
  h.sockets[0].onopen()
  h.event(h.sockets[0], { event: 'start', task_id: 'task-a', sequence: 1 })
  await h.tasks.cancel()
  assert.deepEqual(h.cancellations.map(item => item.url), ['/api/tasks/task-a/cancel'])
  h.event(h.sockets[0], { event: 'completed', task_id: 'task-a', sequence: 2 })
  await h.tick()
  assert.equal(h.sockets.length, 1)
  assert.equal(h.tasks.batch.value, null)
})

test('cancelling after a start command is sent waits for the backend task ID', async () => {
  const h = harness()
  h.tasks.startBatch('project-a', 'rerender', {}, ['page-a', 'page-b'])
  h.sockets[0].onopen()
  const requested = await h.tasks.cancel()
  assert.equal(requested, false)
  assert.equal(h.tasks.batch.value, null)
  assert.equal(h.tasks.busy.value, true)
  assert.equal(h.tasks.taskState.value.eventName, 'starting')
  assert.equal(h.sockets.length, 1)
  assert.deepEqual(h.cancellations, [])
  h.event(h.sockets[0], { event: 'start', task_id: 'task-a', sequence: 1 })
  assert.equal(await h.tasks.cancel(), true)
  assert.deepEqual(h.cancellations.map(item => item.url), ['/api/tasks/task-a/cancel'])
})

test('a completed snapshot containing duplicate terminal events advances one batch page once', async () => {
  const h = harness({ task_id: 'task-a', status: 'completed', action: 'rerender', events: [
    { event: 'completed', task_id: 'task-a', sequence: 1 },
    { event: 'completed', task_id: 'task-a', sequence: 2 },
  ] })
  h.tasks.startBatch('project-a', 'rerender', {}, ['page-a', 'page-b', 'page-c'])
  h.sockets[0].onopen()
  h.sockets[0].onclose()
  await h.tick()
  await h.tick()
  assert.equal(h.tasks.batch.value.completed, 1)
  assert.equal(h.sockets.length, 2)
  h.sockets[1].onopen()
  assert.equal(h.sockets[1].sent[0].target_stored_name, 'page-b')
})

test('disconnect before acknowledgement recovers the accepted task by ID', async () => {
  const h = harness({ task_id: 'accepted-task', status: 'running', action: 'translate', events: [] })
  h.tasks.start('project-a', 'translate')
  h.sockets[0].onopen()
  h.sockets[0].onclose()
  await h.tick()
  h.sockets[1].onopen()
  assert.deepEqual(h.sockets[1].sent, [{ task_id: 'accepted-task', after_sequence: 0 }])
  assert.equal(h.sockets.flatMap(s => s.sent).filter(p => p.action).length, 1)
})

test('a connection lost before opening retries the unsent command, not an older task', async () => {
  const h = harness({ task_id: 'old-task', status: 'completed', action: 'detect', events: [] })
  h.tasks.start('project-a', 'translate')
  h.sockets[0].onclose()
  await h.tick()
  assert.equal(h.sockets.length, 2)
  h.sockets[1].onopen()
  assert.deepEqual(h.sockets.flatMap(s => s.sent), [{ action: 'translate', config: {} }])
  assert.deepEqual(h.requests, [])
})

test('failed, cancelled and interrupted tasks terminate reconnect intention', async () => {
  for (const event of ['error', 'failed', 'cancelled', 'interrupted']) {
    const h = harness()
    h.tasks.start('project-a', 'translate')
    h.sockets[0].onopen()
    h.event(h.sockets[0], { event, task_id: 'task-a', sequence: 1, message: event })
    await h.tick()
    assert.equal(h.tasks.busy.value, false, event)
    assert.equal(h.sockets.length, 1, event)
  }
})

test('restoring a running project retains sequence and ignores stale socket events', async () => {
  const h = harness({ task_id: 'task-b', status: 'running', action: 'translate-page', metadata: { target_stored_name: '002.png' }, events: [{ event: 'progress', task_id: 'task-b', sequence: 7, current: 1, total: 2 }] })
  await h.tasks.resume('project-b')
  h.sockets[0].onopen()
  assert.deepEqual(h.sockets[0].sent[0], { task_id: 'task-b', after_sequence: 7 })
  h.sockets[0].onclose()
  await h.tick()
  h.sockets[1].onopen()
  h.event(h.sockets[0], { event: 'error', task_id: 'task-b', sequence: 8 })
  assert.equal(h.tasks.busy.value, true)
  assert.equal(h.tasks.taskState.value.activeTaskSequence, 7)
})

test('restoring a project blocks a new start until the existing task lookup completes', async () => {
  let resolve
  const tasks = createTaskEvents({ getJson: () => new Promise(done => { resolve = done }) })
  const lookup = tasks.resume('project-a')
  assert.equal(tasks.busy.value, true)
  assert.throws(() => tasks.start('project-a', 'translate'), /等待或停止/)
  resolve({ task: null })
  await lookup
  assert.equal(tasks.busy.value, false)
})
