const SUPPORTED_SFNT_SIGNATURES = new Set([
  0x00010000,
  0x4f54544f, // OTTO / OpenType CFF
  0x74727565, // true
  0x74797031, // typ1
])

function asUint8Array(value) {
  if (value instanceof Uint8Array) {
    return new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
  }
  if (value instanceof ArrayBuffer) {
    return new Uint8Array(value)
  }
  throw new TypeError('字体数据必须是 ArrayBuffer 或 Uint8Array。')
}

function readTag(bytes, offset) {
  return String.fromCharCode(bytes[offset], bytes[offset + 1], bytes[offset + 2], bytes[offset + 3])
}

function parseSfnt(bytes) {
  if (bytes.byteLength < 12) {
    throw new Error('字体文件头不完整。')
  }
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength)
  const signature = view.getUint32(0, false)
  if (!SUPPORTED_SFNT_SIGNATURES.has(signature)) {
    throw new Error('暂不支持该字体容器格式。')
  }
  const tableCount = view.getUint16(4, false)
  const directoryEnd = 12 + tableCount * 16
  if (!tableCount || directoryEnd > bytes.byteLength) {
    throw new Error('字体表目录不完整。')
  }
  const tables = []
  for (let index = 0; index < tableCount; index += 1) {
    const recordOffset = 12 + index * 16
    const offset = view.getUint32(recordOffset + 8, false)
    const length = view.getUint32(recordOffset + 12, false)
    if (offset > bytes.byteLength || length > bytes.byteLength - offset) {
      throw new Error(`字体表 ${readTag(bytes, recordOffset)} 越界。`)
    }
    tables.push({
      tag: readTag(bytes, recordOffset),
      offset,
      length,
    })
  }
  return { signature, tables }
}

function align4(value) {
  return (value + 3) & ~3
}

function tableChecksum(bytes, offset, length) {
  let sum = 0
  const paddedLength = align4(length)
  for (let index = 0; index < paddedLength; index += 4) {
    const b0 = index < length ? bytes[offset + index] : 0
    const b1 = index + 1 < length ? bytes[offset + index + 1] : 0
    const b2 = index + 2 < length ? bytes[offset + index + 2] : 0
    const b3 = index + 3 < length ? bytes[offset + index + 3] : 0
    const word = (((b0 << 24) >>> 0) + (b1 << 16) + (b2 << 8) + b3) >>> 0
    sum = (sum + word) >>> 0
  }
  return sum
}

function writeSfntSearchFields(view, tableCount) {
  let maximumPowerOfTwo = 1
  let entrySelector = 0
  while (maximumPowerOfTwo * 2 <= tableCount) {
    maximumPowerOfTwo *= 2
    entrySelector += 1
  }
  const searchRange = maximumPowerOfTwo * 16
  view.setUint16(4, tableCount, false)
  view.setUint16(6, searchRange, false)
  view.setUint16(8, entrySelector, false)
  view.setUint16(10, tableCount * 16 - searchRange, false)
}

export function getSfntTableTags(value) {
  return parseSfnt(asUint8Array(value)).tables.map((table) => table.tag)
}

export function sanitizeSfntFontForBrowser(value) {
  const source = asUint8Array(value)
  const { tables } = parseSfnt(source)
  const droppedTags = new Set(['DSIG', 'VORG', 'vhea', 'vmtx'])
  const keptTables = tables
    .filter((table) => !droppedTags.has(table.tag))
    .sort((left, right) => left.tag < right.tag ? -1 : left.tag > right.tag ? 1 : 0)
  if (!keptTables.length) {
    throw new Error('字体没有可用字形表。')
  }

  let outputLength = 12 + keptTables.length * 16
  for (const table of keptTables) {
    outputLength = align4(outputLength) + align4(table.length)
  }

  const output = new Uint8Array(outputLength)
  output.set(source.subarray(0, 4), 0)
  const outputView = new DataView(output.buffer)
  writeSfntSearchFields(outputView, keptTables.length)

  let tableOffset = 12 + keptTables.length * 16
  let headOffset = -1
  for (let index = 0; index < keptTables.length; index += 1) {
    const table = keptTables[index]
    tableOffset = align4(tableOffset)
    output.set(source.subarray(table.offset, table.offset + table.length), tableOffset)
    if (table.tag === 'head' && table.length >= 12) {
      headOffset = tableOffset
      outputView.setUint32(headOffset + 8, 0, false)
    }

    const recordOffset = 12 + index * 16
    for (let tagIndex = 0; tagIndex < 4; tagIndex += 1) {
      output[recordOffset + tagIndex] = table.tag.charCodeAt(tagIndex)
    }
    outputView.setUint32(recordOffset + 4, tableChecksum(output, tableOffset, table.length), false)
    outputView.setUint32(recordOffset + 8, tableOffset, false)
    outputView.setUint32(recordOffset + 12, table.length, false)
    tableOffset += align4(table.length)
  }

  if (headOffset >= 0) {
    const checksumAdjustment = (0xb1b0afba - tableChecksum(output, 0, output.byteLength)) >>> 0
    outputView.setUint32(headOffset + 8, checksumAdjustment, false)
  }
  return output.buffer
}
