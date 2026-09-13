/** A selected project accepts only current responses and merges page command results. */
import { ref } from 'vue'
import { apiPostJson } from '../api/client.js'

export function useProject({ request = apiPostJson } = {}) {
  const project = ref(null) // 完整项目视图（build_client_session_payload）
  const loading = ref(false)
  const error = ref('')
  let activeProjectId = ''
  let requestEpoch = 0

  async function loadProject(sessionId) {
    const id = String(sessionId || '')
    if (!id) throw new Error('缺少项目 ID')
    const epoch = ++requestEpoch
    if (activeProjectId !== id) project.value = null
    activeProjectId = id
    loading.value = true
    error.value = ''
    try {
      const view = await request(`/api/projects/${encodeURIComponent(id)}/restore`, {}, '恢复项目失败')
      if (epoch !== requestEpoch || activeProjectId !== id) return null
      if (String(view?.session_id || '') !== id) throw new Error('返回的项目与所选项目不一致')
      return adoptView(view)
    } catch (err) {
      if (epoch !== requestEpoch) return null
      error.value = err.message || '加载项目失败'
      throw err
    } finally {
      if (epoch === requestEpoch) loading.value = false
    }
  }

  /** Full project views and compact page responses share the same head guard. */
  function adoptView(view) {
    if (!activeProjectId || !view || typeof view !== 'object') return project.value
    const incomingId = String(view.session_id || view.project?.project_id || '')
    if (incomingId && incomingId !== activeProjectId) return project.value
    const current = project.value
    const generation = Number(view.project_head_generation)
    const previousGeneration = Number(current?.project_head_generation)
    if (Number.isInteger(generation) && Number.isInteger(previousGeneration)) {
      if (generation < previousGeneration) return current
      if (generation === previousGeneration && view.project_head_revision_id
        && current.project_head_revision_id && view.project_head_revision_id !== current.project_head_revision_id) return current
    }
    const next = { ...current, ...view, session_id: activeProjectId }
    if (view.project) next.project = { ...current?.project, ...view.project }
    const pageId = String(view.page_id || view.page_artifact?.page_id || '')
    if (pageId && view.page_artifact) {
      next.page_artifacts = { ...current?.page_artifacts, ...view.page_artifacts, [pageId]: view.page_artifact }
    }
    if (Array.isArray(next.images) && next.page_artifacts) {
      next.images = next.images.map(image => next.page_artifacts[image.stored_name]
        ? { ...image, artifact_state: next.page_artifacts[image.stored_name] } : image)
      if (next.images.some(image => !next.page_artifacts[image.stored_name]?.capabilities?.can_export)) {
        next.download_url = ''
      }
    }
    project.value = next
    return project.value
  }

  /** A project summary must not replace its images or artifact map. */
  function adoptResponse(response) {
    if (response?.session_id) {
      return adoptView(response)
    }
    if (response?.project?.session_id) {
      return adoptView(response.project)
    }
    if (response?.project?.project_id && !response?.session_id) {
      // 只有摘要（如 PATCH /api/projects/{id}），合并进当前视图。
      return adoptView(response)
    }
    if (response?.page_artifact && response?.page_id) return adoptView(response)
    return project.value
  }

  function clear() {
    requestEpoch += 1
    activeProjectId = ''
    project.value = null
    error.value = ''
    loading.value = false
  }

  return { project, loading, error, loadProject, adoptView, adoptResponse, clear }
}
