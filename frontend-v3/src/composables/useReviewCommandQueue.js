/** Small, framework independent helpers used by the review editor. */

function normalize(value) {
  return String(value || '').trim()
}

export function createPageRequestGuard(initialPageId = '') {
  let epoch = 0
  let activePageId = normalize(initialPageId)

  function begin(pageId) {
    activePageId = normalize(pageId)
    epoch += 1
    return { pageId: activePageId, epoch }
  }

  function invalidate() {
    epoch += 1
    activePageId = ''
  }

  function isCurrent(identity) {
    return Boolean(identity)
      && Number(identity.epoch) === epoch
      && normalize(identity.pageId) === activePageId
  }

  return {
    begin,
    invalidate,
    isCurrent,
    get current() {
      return { pageId: activePageId, epoch }
    },
  }
}

/**
 * Serialize commands by project.  A page edit changes project-level override
 * maps and therefore cannot safely race another page edit in the same project.
 * Queue jobs are allowed to fail; the next job still runs.
 */
export function createSerialProjectCommandQueue({ onChange = () => {} } = {}) {
  const tails = new Map()
  const pending = new Map()
  let nextJobId = 0

  function emit(projectId) {
    onChange({
      projectId,
      pending: Number(pending.get(projectId) || 0),
      pendingByProject: Object.fromEntries(pending),
    })
  }

  function enqueue(projectId, pageId, executor) {
    const normalizedProjectId = normalize(projectId)
    const normalizedPageId = normalize(pageId)
    if (!normalizedProjectId || !normalizedPageId || typeof executor !== 'function') {
      return Promise.resolve(null)
    }
    const jobId = ++nextJobId
    const previous = tails.get(normalizedProjectId) || Promise.resolve()
    pending.set(normalizedProjectId, Number(pending.get(normalizedProjectId) || 0) + 1)
    emit(normalizedProjectId)

    let trackedPromise
    const run = previous
      .catch(() => {})
      .then(() => executor({
        jobId,
        projectId: normalizedProjectId,
        pageId: normalizedPageId,
      }))
    trackedPromise = run.finally(() => {
      const count = Math.max(0, Number(pending.get(normalizedProjectId) || 0) - 1)
      if (count) pending.set(normalizedProjectId, count)
      else pending.delete(normalizedProjectId)
      if (tails.get(normalizedProjectId) === trackedPromise) tails.delete(normalizedProjectId)
      emit(normalizedProjectId)
    })
    tails.set(normalizedProjectId, trackedPromise)
    return trackedPromise
  }

  function waitForProject(projectId) {
    return tails.get(normalize(projectId)) || Promise.resolve()
  }

  function pendingCount(projectId) {
    return Number(pending.get(normalize(projectId)) || 0)
  }

  function hasPending(projectId) {
    return pendingCount(projectId) > 0
  }

  function clear() {
    tails.clear()
    pending.clear()
  }

  return { enqueue, waitForProject, pendingCount, hasPending, clear }
}
