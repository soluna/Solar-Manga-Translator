const clone = value => JSON.parse(JSON.stringify(value))

function restoreRegionCommands(region) {
  if (!region || typeof region !== 'object' || !region.region_id) return []
  const id = region.region_id, style = region.style || {}, flags = region.flags || {}
  return [
    { type: 'restore_region', region_id: id },
    { type: 'update_translation', region_id: id, text: region.translation?.edited || '' },
    { type: 'update_region_bbox', region_id: id, bbox: clone(region.bbox) },
    { type: 'update_font_size', region_id: id, font_size: style.font_size_override ?? null },
    { type: 'update_text_direction', region_id: id, direction: region.direction || 'auto' },
    { type: 'update_region_font', region_id: id, font_key: style.font_key_override || '' },
    { type: 'update_font_style', region_id: id, style: style.font_style_override || '' },
    { type: 'update_region_style', region_id: id, rotation: style.rotation, stroke_width: style.stroke_width,
      letter_spacing: style.letter_spacing, line_spacing: style.line_spacing,
      fg_color: clone(style.fg_color), bg_color: clone(style.bg_color), preserve_background: Boolean(flags.preserve_background) },
    { type: 'set_keep_original', region_id: id, enabled: Boolean(flags.keep_original) },
    ...(flags.disabled ? [{ type: 'disable_region', region_id: id }] : []),
  ]
}

function reviewStateFrom(document) {
  const candidate = document?.metadata?.review
    || document?.review_state
    || (document?.review_status ? { status: document.review_status } : null)
  if (candidate && typeof candidate === 'object') return candidate
  return { status: 'unreviewed' }
}

/** Derive undo from the last committed document, never from an optimistic preview. */
export function pageCommandHistory(commands, before, response = {}, label = '编辑文本区域') {
  const regions = new Map((before?.regions || []).map(region => [region.region_id, region]))
  const undo = [], redo = clone(commands)
  for (let index = commands.length - 1; index >= 0; index--) {
    const command = commands[index], region = regions.get(command.region_id)
    const base = { type: command.type, region_id: command.region_id }
    switch (command.type) {
      case 'update_translation': undo.push({ ...base, text: region?.translation?.edited || '' }); break
      case 'update_font_size': undo.push({ ...base, font_size: region?.style?.font_size_override ?? null }); break
      case 'update_region_font': undo.push({ ...base, font_key: region?.style?.font_key_override || '' }); break
      case 'update_font_style': undo.push({ ...base, style: region?.style?.font_style_override || '' }); break
      case 'update_text_direction': undo.push({ ...base, direction: region?.direction || 'auto' }); break
      case 'update_region_bbox': undo.push({ ...base, bbox: clone(region.bbox) }); break
      case 'set_keep_original': undo.push({ ...base, enabled: Boolean(region?.flags?.keep_original) }); break
      case 'disable_region': undo.push(...restoreRegionCommands(region)); break
      case 'restore_region': undo.push({ ...base, type: region?.flags?.disabled ? 'disable_region' : 'restore_region' }); break
      case 'update_region_style': {
        const previous = {}
        for (const key of Object.keys(command)) {
          if (key === 'preserve_background') previous[key] = Boolean(region?.flags?.preserve_background)
          else if (Object.hasOwn(region?.style || {}, key)) previous[key] = clone(region.style[key])
        }
        undo.push({ ...base, ...previous })
        break
      }
      case 'create_region':
      case 'duplicate_region':
      case 'merge_regions': {
        if (!response.created_region_payload?.id) return null
        undo.push({ type: 'delete_manual_region', region_id: response.created_region_payload.id })
        if (command.type === 'merge_regions') {
          for (const id of command.region_ids) {
            undo.push(...restoreRegionCommands(regions.get(id)))
          }
          redo.splice(index, 1, { type: 'restore_manual_region', payload: clone(response.created_region_payload) },
            ...command.region_ids.map(id => ({ type: 'disable_region', region_id: id })))
        } else redo[index] = { type: 'restore_manual_region', payload: clone(response.created_region_payload) }
        break
      }
      case 'delete_manual_region':
        if (!response.deleted_region_payload?.id) return null
        undo.push({ type: 'restore_manual_region', payload: clone(response.deleted_region_payload) })
        undo.push(...restoreRegionCommands(region))
        break
      case 'restore_manual_region': undo.push({ type: 'delete_manual_region', region_id: command.payload.id }); break
      case 'update_source_text':
      case 'retry_region_ocr':
      case 'restore_region_ocr_snapshot': {
        const pair = response.ocr_snapshots?.[command.region_id]
        const previous = pair?.before || response.previous_ocr_snapshot
        const next = pair?.after || response.ocr_snapshot
        if (previous?.region_id !== command.region_id || next?.region_id !== command.region_id) return null
        undo.push({ type: 'restore_region_ocr_snapshot', region_id: command.region_id, snapshot: clone(previous) })
        redo[index] = { type: 'restore_region_ocr_snapshot', region_id: command.region_id, snapshot: clone(next) }
        break
      }
      case 'set_review_status': {
        const previous = reviewStateFrom(before)
        undo.push({ type: 'set_review_status', status: previous.status,
          ...(previous.reviewer ? { reviewer: previous.reviewer } : {}) })
        break
      }
      default: return null
    }
  }
  return { label, undo, redo }
}
