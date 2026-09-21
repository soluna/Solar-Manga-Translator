/** Project-level action guards shared by the history view and its tests. */

export function projectHasActiveTask(project) {
  return Boolean(project?.is_busy || project?.busy)
}

export function canContinueProject(
  project,
  { restoringId = '', activeTaskBusy = false, activeSessionId = '' } = {},
) {
  const projectId = String(project?.project_id || '').trim()
  if (!projectId || restoringId) return false
  // A task already visible in another project must keep its connection. The
  // same project can be reopened so the shared task bar can reconnect to it.
  return !activeTaskBusy || String(activeSessionId || '').trim() === projectId
}

export function canRestoreProjectSnapshot(
  project,
  { restoringKey = '', snapshotKey = '' } = {},
) {
  if (projectHasActiveTask(project)) return false
  // Restoring any snapshot mutates project storage and creates a new project.
  // Keep the whole history view single-flight so a second click cannot race
  // the first restore, even when it targets a different snapshot.
  return !String(restoringKey || '').trim()
}
