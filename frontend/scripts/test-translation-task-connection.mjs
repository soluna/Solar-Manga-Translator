import assert from 'node:assert/strict'

import { useTranslationTaskConnection } from '../src/composables/useTranslationTaskConnection.js'

function ref(value) {
  return { value }
}

function createFakeSocket() {
  return {
    sent: [],
    closed: false,
    send(payload) {
      this.sent.push(JSON.parse(payload))
    },
    close() {
      this.closed = true
      this.onclose?.()
    },
  }
}

function createHarness(overrides = {}) {
  const sockets = []
  const timers = []
  const handledEvents = []
  const requests = []
  const refs = {
    sessionId: ref('project-a'),
    translating: ref(true),
    status: ref(''),
    errorMessage: ref(''),
    activeTaskId: ref(''),
    activeTaskSequence: ref(0),
    activeAction: ref('translate'),
    activeTaskTargetStoredName: ref(''),
    activeTaskPhase: ref({ label: '旧阶段' }),
    progress: ref({ current: 9, total: 9 }),
  }
  const taskConnection = useTranslationTaskConnection({
    refs,
    createApiWebSocket(path) {
      const socket = createFakeSocket()
      socket.path = path
      sockets.push(socket)
      return socket
    },
    buildRuntimeConfig() {
      return { translator: 'mock' }
    },
    async apiFetch(url, init = {}) {
      requests.push({ url, init })
      return overrides.apiFetch
        ? overrides.apiFetch(url, init)
        : {
            ok: true,
            async json() {
              return {}
            },
          }
    },
    toApiUrl(path) {
      return `http://api.test${path}`
    },
    async handleTaskEvent(event) {
      handledEvents.push(event)
      if (event.sequence) {
        refs.activeTaskSequence.value = Number(event.sequence)
      }
    },
    clearTaskError() {
      refs.errorMessage.value = ''
    },
    getBackendLogHint() {
      return '请查看日志。'
    },
    setTimeoutFn(callback, delayMs) {
      timers.push({ callback, delayMs })
      return timers.length
    },
    clearTimeoutFn(timerId) {
      timers[timerId - 1].cleared = true
    },
    maxReconnectAttempts: 2,
  })

  return { refs, sockets, timers, handledEvents, requests, taskConnection }
}

const startHarness = createHarness()
startHarness.taskConnection.connect({
  action: 'translate-page',
  pageTargetStoredName: '001.png',
  taskId: '',
})
assert.equal(startHarness.sockets[0].path, '/ws/translate/project-a')
startHarness.sockets[0].onopen()
assert.deepEqual(startHarness.sockets[0].sent[0], {
  action: 'translate-page',
  config: { translator: 'mock' },
  target_stored_name: '001.png',
})

const subscribeHarness = createHarness()
subscribeHarness.refs.activeTaskSequence.value = 7
subscribeHarness.taskConnection.connect({ taskId: 'task-a' })
subscribeHarness.sockets[0].onopen()
assert.deepEqual(subscribeHarness.sockets[0].sent[0], {
  task_id: 'task-a',
  after_sequence: 7,
})

const closeHarness = createHarness()
const closingSocket = closeHarness.taskConnection.connect()
closeHarness.taskConnection.closeSocket(closingSocket)
assert.equal(closingSocket.closed, true)
assert.equal(closeHarness.taskConnection.hasSocket(), false)
assert.equal(closeHarness.timers.length, 0)

const reconnectHarness = createHarness({
  apiFetch: async () => ({
    ok: true,
    async json() {
      return {
        task: {
          task_id: 'task-restored',
          action: 'resume-translate',
          status: 'running',
          metadata: { target_stored_name: '002.png' },
          events: [
            { event: 'task', sequence: 1 },
            { event: 'progress', sequence: 2 },
          ],
        },
      }
    },
  }),
})
const restored = await reconnectHarness.taskConnection.reconnect()
assert.equal(restored, true)
assert.equal(reconnectHarness.refs.activeTaskId.value, 'task-restored')
assert.equal(reconnectHarness.refs.activeAction.value, 'resume-translate')
assert.equal(reconnectHarness.refs.activeTaskTargetStoredName.value, '002.png')
assert.equal(reconnectHarness.handledEvents.length, 2)
assert.equal(reconnectHarness.sockets.length, 1)
reconnectHarness.sockets[0].onopen()
assert.deepEqual(reconnectHarness.sockets[0].sent[0], {
  task_id: 'task-restored',
  after_sequence: 2,
})

const reconnectDelayHarness = createHarness()
reconnectDelayHarness.taskConnection.scheduleReconnect()
assert.equal(reconnectDelayHarness.refs.status.value, '任务仍在后台运行，正在重新连接（1/2）…')
assert.equal(reconnectDelayHarness.timers[0].delayMs, 1000)

const cancelHarness = createHarness()
cancelHarness.refs.activeTaskId.value = 'task-cancel'
const cancelled = await cancelHarness.taskConnection.cancelActiveTask()
assert.equal(cancelled, true)
assert.equal(cancelHarness.refs.status.value, '任务仍在后台运行，正在重新连接（1/2）…')
assert.deepEqual(cancelHarness.requests[0], {
  url: 'http://api.test/api/tasks/task-cancel/cancel',
  init: { method: 'POST' },
})
assert.equal(cancelHarness.timers.length, 1)

const exhaustedHarness = createHarness()
exhaustedHarness.taskConnection.scheduleReconnect()
exhaustedHarness.taskConnection.scheduleReconnect()
exhaustedHarness.taskConnection.scheduleReconnect()
assert.equal(exhaustedHarness.refs.translating.value, false)
assert.equal(exhaustedHarness.refs.status.value, '任务连接恢复失败。')
assert.match(exhaustedHarness.refs.errorMessage.value, /无法重新连接后台任务/)
assert.deepEqual(exhaustedHarness.refs.progress.value, { current: 9, total: 9 })

console.log('Translation task connection tests passed.')
