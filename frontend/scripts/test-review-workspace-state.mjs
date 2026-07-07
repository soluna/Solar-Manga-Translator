import assert from 'node:assert/strict'

import {
  mergeRegionCount,
  resolveSelectedReviewPage,
} from '../src/review-workspace-state.js'

const pendingPage = resolveSelectedReviewPage({
  selectedPageKey: '002.png',
  inspectionPages: [
    { stored_name: '001.png', name: '第一页', regions: [{ id: 'a' }] },
  ],
  pageEntries: [
    { stored_name: '001.png', name: '第一页', sourceUrl: '/source/001.png', blankUrl: '/blank/001.png', regionCount: 1 },
    { stored_name: '002.png', name: '第二页', sourceUrl: '/source/002.png', blankUrl: '/blank/002.png', finalUrl: '/final/002.png', regionCount: 3 },
  ],
})

assert.equal(pendingPage.stored_name, '002.png')
assert.equal(pendingPage.name, '第二页')
assert.equal(pendingPage.source_image_url, '/source/002.png')
assert.equal(pendingPage.base_image_url, '/blank/002.png')
assert.equal(pendingPage.translated_image_url, '/final/002.png')
assert.deepEqual(pendingPage.regions, [])

const loadedPage = resolveSelectedReviewPage({
  selectedPageKey: '001.png',
  inspectionPages: [
    { stored_name: '001.png', name: '第一页', regions: [{ id: 'a' }] },
  ],
  pageEntries: [],
})

assert.equal(loadedPage.stored_name, '001.png')
assert.deepEqual(loadedPage.regions, [{ id: 'a' }])

assert.equal(mergeRegionCount(0, 5), 5)
assert.equal(mergeRegionCount(4, 0), 4)
assert.equal(mergeRegionCount(4, 6), 6)

console.log('Review workspace state tests passed.')
