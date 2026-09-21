export function pageStatus(page, task = null) {
  if (task?.busy && (!task.pageId || task.pageId === page.stored_name)) {
    return { text: ({ detect: '识别中', translate: '翻译中', 'resume-translate': '翻译中', 'translate-page': '翻译中', rerender: '嵌字中' })[task.action] || '处理中', cls: 'is-accent' }
  }
  const caps = page?.artifact_state?.capabilities
  if (!caps) return { text: '状态待同步', cls: 'is-warn' }
  if (caps.can_export) return { text: '已嵌字', cls: 'is-ok' }
  if (caps.final_stale) return { text: '需重新嵌字', cls: 'is-warn' }
  if (caps.can_render) return { text: '待嵌字', cls: 'is-accent' }
  if (caps.can_translate) return { text: '待翻译', cls: '' }
  if (caps.recognition_ready && !caps.blank_ready) return { text: '待生成空页', cls: 'is-warn' }
  return { text: '待识别', cls: '' }
}

export function hasPartialTranslation(pages) {
  const list = Array.isArray(pages) ? pages : []
  const translated = list.filter(page => Boolean(page?.artifact_state?.capabilities?.translation_ready)).length
  return translated > 0 && translated < list.length
}

export function projectReadiness(pages, { pauseAfterDetection = true, workflowStage = 'idle' } = {}) {
  const all = key => pages.length > 0 && pages.every(page => page.artifact_state?.capabilities?.[key])
  const any = key => pages.some(page => page.artifact_state?.capabilities?.[key])
  const stage = String(workflowStage || '').trim().toLowerCase()
  return { recognized: all('recognition_ready'), translated: all('translation_ready'), rendered: all('can_export'),
    canTranslate: any('can_translate') || (!pauseAfterDetection && stage === 'idle' && pages.length > 0),
    canRender: any('can_render'), blankReady: all('blank_ready') }
}
