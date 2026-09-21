import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  canContinueProject,
  canRestoreProjectSnapshot,
  projectHasActiveTask,
} from '../src/state/project-actions.js'

test('busy history projects can be reopened while another project keeps its task connection', () => {
  const project = { project_id: 'project-a', is_busy: true }
  assert.equal(projectHasActiveTask(project), true)
  assert.equal(canContinueProject(project), true)
  assert.equal(canContinueProject(project, { activeTaskBusy: true, activeSessionId: 'project-a' }), true)
  assert.equal(canContinueProject(project, { activeTaskBusy: true, activeSessionId: 'project-b' }), false)
})

test('snapshot restore is blocked while the source project task is active', () => {
  const project = { project_id: 'project-a', busy: true }
  assert.equal(canRestoreProjectSnapshot(project, { snapshotKey: 'project-a:s1' }), false)
  assert.equal(canRestoreProjectSnapshot({ project_id: 'project-a' }, { restoringKey: 'project-a:s1', snapshotKey: 'project-a:s1' }), false)
  assert.equal(canRestoreProjectSnapshot({ project_id: 'project-a' }, { restoringKey: 'project-a:s2', snapshotKey: 'project-a:s1' }), false)
  assert.equal(canRestoreProjectSnapshot({ project_id: 'project-a' }, { snapshotKey: 'project-a:s1' }), true)
})
