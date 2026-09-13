import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  baseImageUploadMessage,
  emptyBaseImageUploadSummary,
  mergeBaseImageUploadFailure,
  mergeBaseImageUploadSummary,
} from '../src/state/base-image-upload.js'

test('base image upload feedback preserves server counts and separates request failures', () => {
  let summary = emptyBaseImageUploadSummary()
  summary = mergeBaseImageUploadSummary(summary, {
    base_image_upload: {
      matched_count: 2,
      matched_pages: [{ uploaded_name: '001.png' }, { uploaded_name: '002.png' }],
      unmatched_count: 1,
      unmatched_files: ['notes.png'],
      invalid_count: 1,
      invalid_files: ['broken.jpg'],
    },
  })
  summary = mergeBaseImageUploadFailure(summary, 'missing.png', '没有找到可匹配的页面文件名')
  assert.deepEqual(summary, {
    matched: 2, unmatched: 1, invalid: 1, failed: 1,
    matchedFiles: ['001.png', '002.png'],
    unmatchedFiles: ['notes.png'],
    invalidFiles: ['broken.jpg'],
    failedFiles: ['missing.png'],
    errors: [{ file: 'missing.png', message: '没有找到可匹配的页面文件名' }],
  })
  assert.equal(baseImageUploadMessage(summary), '无字图上传完成：匹配 2，未匹配 1，无效 1，失败 1。')
})

test('a failed archive request is one failed upload, not one invalid image', () => {
  const summary = mergeBaseImageUploadFailure(
    emptyBaseImageUploadSummary(), 'book.cbz', '请求超时',
  )
  assert.equal(summary.failed, 1)
  assert.equal(summary.invalid, 0)
  assert.deepEqual(summary.failedFiles, ['book.cbz'])
})
