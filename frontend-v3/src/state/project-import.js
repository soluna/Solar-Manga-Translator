const IMAGE = /\.(png|jpe?g|webp|bmp|gif)$/i
const ARCHIVE = /\.(zip|cbz)$/i

/** Build the multipart contract used by image, archive and folder import. */
export function buildImportForm(files, { folderName = '' } = {}) {
  const records = Array.from(files || []).map(item => ({
    file: item.file || item,
    path: item.relativePath || item.webkitRelativePath || item.file?.name || item.name,
  }))
  const supported = records.filter(({ file }) => IMAGE.test(file.name) || ARCHIVE.test(file.name))
  if (!supported.length) throw new Error('没有可导入的图片。请选择图片或单个 ZIP / CBZ 压缩包。')
  const form = new FormData()
  form.append('review_mode', 'auto')
  if (supported.length === 1 && !folderName) {
    form.append('file', supported[0].file)
    return form
  }
  if (supported.some(({ file }) => ARCHIVE.test(file.name))) {
    throw new Error('压缩包请单独导入；文件夹和多选导入仅支持图片。')
  }
  for (const { file, path } of supported) {
    form.append('files', file)
    form.append('relative_paths', path || file.name)
  }
  form.append('folder_name', folderName || '图片导入')
  return form
}

/** Capture browser drop handles synchronously, then exhaust each directory reader. */
export async function readDroppedFiles(transfer) {
  const handles = Array.from(transfer?.items || []).map(item => ({
    entry: item.webkitGetAsEntry?.() || null,
    file: item.getAsFile?.() || null,
  }))
  const fallback = Array.from(transfer?.files || [])
  if (!handles.some(handle => handle.entry)) return { files: fallback, folderName: '' }
  async function read(entry, prefix = '') {
    const path = `${prefix}${entry.name}`
    if (entry.isFile) {
      const file = await new Promise((resolve, reject) => entry.file(resolve, reject))
      return [{ file, relativePath: path }]
    }
    if (!entry.isDirectory) return []
    const reader = entry.createReader()
    const records = []
    while (true) {
      const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject))
      if (!batch.length) break
      for (const child of batch) records.push(...await read(child, `${path}/`))
    }
    return records
  }
  const files = []
  for (const handle of handles) {
    if (handle.entry) files.push(...await read(handle.entry))
    else if (handle.file) files.push(handle.file)
  }
  const folders = handles.filter(handle => handle.entry?.isDirectory)
  return { files, folderName: folders.length === 1 && handles.length === 1 ? folders[0].entry.name : folders.length ? '拖拽导入' : '' }
}
