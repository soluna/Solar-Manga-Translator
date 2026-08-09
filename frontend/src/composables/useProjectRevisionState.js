import { ref } from 'vue'

function payloadGeneration(payload) {
  if (!Object.prototype.hasOwnProperty.call(payload || {}, 'project_head_generation')) {
    return null
  }
  const generation = Number(payload?.project_head_generation)
  return Number.isInteger(generation) && generation >= 0 ? generation : null
}

export function useProjectRevisionState() {
  const projectHeadGeneration = ref(0)
  const projectHeadRevisionId = ref('')

  function shouldApplyProjectPayload(payload, activeProjectId) {
    const incomingProjectId = String(payload?.session_id || '').trim()
    const currentProjectId = String(activeProjectId || '').trim()
    if (!currentProjectId || incomingProjectId !== currentProjectId) {
      return true
    }
    const generation = payloadGeneration(payload)
    if (generation == null) {
      return true
    }
    if (generation < projectHeadGeneration.value) {
      return false
    }
    const revisionId = String(payload?.project_head_revision_id || '').trim()
    return !(
      generation === projectHeadGeneration.value
      && revisionId
      && projectHeadRevisionId.value
      && revisionId !== projectHeadRevisionId.value
    )
  }

  function isPayloadForActiveProject(payload, activeProjectId) {
    const incomingProjectId = String(payload?.session_id || '').trim()
    const currentProjectId = String(activeProjectId || '').trim()
    return !incomingProjectId || !currentProjectId || incomingProjectId === currentProjectId
  }

  function recordProjectRevision(payload) {
    const generation = payloadGeneration(payload)
    if (generation == null) {
      return true
    }
    const revisionId = String(payload?.project_head_revision_id || '').trim()
    if (generation < projectHeadGeneration.value) {
      return false
    }
    if (
      generation === projectHeadGeneration.value
      && revisionId
      && projectHeadRevisionId.value
      && revisionId !== projectHeadRevisionId.value
    ) {
      return false
    }
    projectHeadGeneration.value = generation
    projectHeadRevisionId.value = revisionId
    return true
  }

  function resetProjectRevision() {
    projectHeadGeneration.value = 0
    projectHeadRevisionId.value = ''
  }

  return {
    projectHeadGeneration,
    projectHeadRevisionId,
    isPayloadForActiveProject,
    shouldApplyProjectPayload,
    recordProjectRevision,
    resetProjectRevision,
  }
}
