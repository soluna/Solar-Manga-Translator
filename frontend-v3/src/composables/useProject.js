/** 项目状态：加载/恢复项目、持有项目视图（所有变更接口都返回完整视图，直接采纳）。 */
import { ref } from 'vue'
import { apiPostJson } from '../api/client.js'

export function useProject() {
  const project = ref(null) // 完整项目视图（build_client_session_payload）
  const loading = ref(false)
  const error = ref('')

  async function loadProject(sessionId) {
    loading.value = true
    error.value = ''
    try {
      const view = await apiPostJson(`/api/projects/${sessionId}/restore`, {}, '恢复项目失败')
      project.value = view
      return view
    } catch (err) {
      error.value = err.message || '加载项目失败'
      throw err
    } finally {
      loading.value = false
    }
  }

  /** 采纳一次 API 响应中的项目视图（所有变更接口均返回）。 */
  function adoptView(view) {
    if (view && typeof view === 'object' && view.session_id) {
      project.value = view
    }
    return project.value
  }

  /** 根据响应结构提取视图：有些接口直接返回视图，有些包在 {project: ...}。 */
  function adoptResponse(response) {
    if (response?.session_id) {
      return adoptView(response)
    }
    if (response?.project?.session_id) {
      return adoptView(response.project)
    }
    if (response?.project?.project_id && !response?.session_id) {
      // 只有摘要（如 PATCH /api/projects/{id}），合并进当前视图。
      if (project.value) {
        project.value = { ...project.value, project: { ...project.value.project, ...response.project } }
      }
    }
    return project.value
  }

  function clear() {
    project.value = null
    error.value = ''
  }

  return { project, loading, error, loadProject, adoptView, adoptResponse, clear }
}