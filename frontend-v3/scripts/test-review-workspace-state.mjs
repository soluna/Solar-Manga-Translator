import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  nextIssueRegion,
  normalizeReviewLocation,
  normalizeReviewRegionFilter,
  reviewLocationStorageKey,
  selectRegionRange,
} from '../src/state/review-workspace-state.js'

test('restored review workspace always leaves one canvas visible and clamps panel state', () => {
  const location = normalizeReviewLocation({
    panes: { frame: false, final: false, src: false, blank: false },
    panelWidth: 900,
    listScrollTop: -20,
  })
  assert.equal(location.panes.frame, true)
  assert.equal(Object.values(location.panes).some(Boolean), true)
  assert.equal(location.panelWidth, 520)
  assert.equal(location.listScrollTop, 0)
})

test('issue navigation and range selection retain canonical order across filters', () => {
  const regions = [
    { id: 'a', issueReason: '' },
    { id: 'b', issueReason: 'OCR 失败' },
    { id: 'c', issueReason: '' },
    { id: 'd', issueReason: '缺少译文' },
  ]
  assert.equal(nextIssueRegion(regions, 'b', { isIssue: region => Boolean(region.issueReason) }).id, 'd')
  assert.equal(nextIssueRegion(regions, 'd', { isIssue: region => Boolean(region.issueReason) }).id, 'b')
  assert.deepEqual([...selectRegionRange(regions, 'd', 'b', ['a'])], ['a', 'b', 'c', 'd'])
  assert.equal(reviewLocationStorageKey('project-a'), 'inkstage-review-location-v1:project-a')
})

test('review location preserves the legacy actionable region filters', () => {
  for (const filter of ['keep-original', 'untranslated', 'font-override']) {
    assert.equal(normalizeReviewRegionFilter(filter), filter)
    assert.equal(normalizeReviewLocation({ filter }).filter, filter)
  }
  assert.equal(normalizeReviewRegionFilter('removed-filter'), 'all')
})
