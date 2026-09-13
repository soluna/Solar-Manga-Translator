/**
 * Pure helpers for aggregating the backend's base-image upload result.
 * The endpoint accepts one image or archive per request; the UI combines a
 * batch of sequential requests into one explicit matched/unmatched/invalid
 * report without treating a failed file as a successful import.
 */
export function emptyBaseImageUploadSummary() {
  return {
    matched: 0,
    unmatched: 0,
    invalid: 0,
    failed: 0,
    matchedFiles: [],
    unmatchedFiles: [],
    invalidFiles: [],
    failedFiles: [],
    errors: [],
  }
}

function count(value) {
  const result = Number(value)
  return Number.isFinite(result) && result >= 0 ? Math.floor(result) : 0
}

function names(value) {
  return Array.isArray(value)
    ? value.map(item => String(item || '').trim()).filter(Boolean)
    : []
}

/** Merge one successful `base_image_upload` payload into an existing summary. */
export function mergeBaseImageUploadSummary(summary, payload) {
  const next = summary || emptyBaseImageUploadSummary()
  const result = payload?.base_image_upload || payload
  if (!result || typeof result !== 'object') return next
  next.matched += count(result.matched_count)
  next.unmatched += count(result.unmatched_count)
  next.invalid += count(result.invalid_count)
  next.matchedFiles.push(...names(result.matched_files))
  next.unmatchedFiles.push(...names(result.unmatched_files))
  next.invalidFiles.push(...names(result.invalid_files))
  if (Array.isArray(result.matched_pages)) {
    next.matchedFiles.push(...result.matched_pages.map(item => item?.uploaded_name || item?.page_name))
  }
  return dedupeSummary(next)
}

/** Keep transport/auth/server failures separate from backend's image classification. */
export function mergeBaseImageUploadFailure(summary, fileName, message = '') {
  const next = summary || emptyBaseImageUploadSummary()
  const name = String(fileName || '未命名文件').trim() || '未命名文件'
  const text = String(message || '')
  next.failed += 1
  next.failedFiles.push(name)
  next.errors.push({ file: name, message: text || '上传失败' })
  return dedupeSummary(next)
}

function dedupe(values) {
  return [...new Set(values.map(value => String(value || '').trim()).filter(Boolean))]
}

function dedupeSummary(summary) {
  summary.matchedFiles = dedupe(summary.matchedFiles)
  summary.unmatchedFiles = dedupe(summary.unmatchedFiles)
  summary.invalidFiles = dedupe(summary.invalidFiles)
  summary.failedFiles = dedupe(summary.failedFiles)
  return summary
}

export function baseImageUploadMessage(summary) {
  const value = summary || emptyBaseImageUploadSummary()
  const failed = value.failed ? `，失败 ${value.failed}` : ''
  return `无字图上传完成：匹配 ${value.matched}，未匹配 ${value.unmatched}，无效 ${value.invalid}${failed}。`
}
