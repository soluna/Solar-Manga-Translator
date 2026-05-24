import { computed, ref } from 'vue'

function normalizeIds(ids) {
  return (Array.isArray(ids) ? ids : [ids])
    .map((id) => String(id || '').trim())
    .filter(Boolean)
}

export function usePageCommandState() {
  const pageCommandPendingCounts = ref({})
  const regionCommitStates = ref({})

  const hasPendingPageCommands = computed(() => (
    Object.values(pageCommandPendingCounts.value).some((count) => Number(count || 0) > 0)
  ))

  function setPageCommandPending(pageId, delta) {
    const normalizedPageId = String(pageId || '').trim()
    if (!normalizedPageId) {
      return
    }
    const nextState = { ...pageCommandPendingCounts.value }
    const nextCount = Math.max(0, Number(nextState[normalizedPageId] || 0) + delta)
    if (nextCount > 0) {
      nextState[normalizedPageId] = nextCount
    } else {
      delete nextState[normalizedPageId]
    }
    pageCommandPendingCounts.value = nextState
  }

  function isPageCommandPending(pageId) {
    return Number(pageCommandPendingCounts.value[String(pageId || '').trim()] || 0) > 0
  }

  function getCommandRegionIds(commands) {
    const regionIds = new Set()
    for (const command of commands || []) {
      if (!command || typeof command !== 'object') {
        continue
      }
      const singleRegionId = String(command.region_id || '').trim()
      if (singleRegionId) {
        regionIds.add(singleRegionId)
      }
      for (const regionId of command.region_ids || []) {
        const normalizedRegionId = String(regionId || '').trim()
        if (normalizedRegionId) {
          regionIds.add(normalizedRegionId)
        }
      }
    }
    return Array.from(regionIds)
  }

  function setRegionCommitState(regionIds, status, label = '') {
    const normalizedIds = normalizeIds(regionIds)
    if (!normalizedIds.length) {
      return
    }
    const nextState = { ...regionCommitStates.value }
    for (const regionId of normalizedIds) {
      nextState[regionId] = {
        status,
        label,
        updatedAt: Date.now(),
      }
    }
    regionCommitStates.value = nextState
  }

  function clearRegionCommitState(regionIds, allowedStatuses = []) {
    const normalizedIds = normalizeIds(regionIds)
    if (!normalizedIds.length) {
      return
    }
    const allowedSet = new Set((allowedStatuses || []).map((status) => String(status || '')))
    const nextState = { ...regionCommitStates.value }
    for (const regionId of normalizedIds) {
      const currentStatus = String(nextState[regionId]?.status || '')
      if (!allowedSet.size || allowedSet.has(currentStatus)) {
        delete nextState[regionId]
      }
    }
    regionCommitStates.value = nextState
  }

  function getRegionCommitState(region) {
    const regionId = String(region?.id || region || '').trim()
    return regionCommitStates.value[regionId] || null
  }

  function getRegionCommitStatusLabel(region) {
    const commitState = getRegionCommitState(region)
    if (!commitState) {
      return ''
    }
    if (commitState.status === 'dirty') {
      return commitState.label || '有未保存草稿'
    }
    if (commitState.status === 'saving') {
      return commitState.label || '保存中…'
    }
    if (commitState.status === 'failed') {
      return commitState.label || '保存失败，已保留本地编辑'
    }
    return commitState.label || ''
  }

  function getRegionCommitStatusClass(region) {
    const status = getRegionCommitState(region)?.status || ''
    return status ? `is-${status}` : ''
  }

  return {
    pageCommandPendingCounts,
    regionCommitStates,
    hasPendingPageCommands,
    setPageCommandPending,
    isPageCommandPending,
    getCommandRegionIds,
    setRegionCommitState,
    clearRegionCommitState,
    getRegionCommitState,
    getRegionCommitStatusLabel,
    getRegionCommitStatusClass,
  }
}
