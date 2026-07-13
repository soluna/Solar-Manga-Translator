import assert from 'node:assert/strict'

import {
  resolveRegionFontSizeValue,
  resolveRegionRenderFontSize,
} from '../src/region-typography.js'


assert.equal(resolveRegionFontSizeValue({ detectedValue: 34 }), 34)
assert.equal(resolveRegionRenderFontSize({ detectedValue: 34 }), 34)

assert.equal(
  resolveRegionFontSizeValue({ detectedValue: 34, explicitValue: 35 }),
  35,
)
assert.equal(
  resolveRegionRenderFontSize({ detectedValue: 34, explicitValue: 35 }),
  35,
)

assert.equal(
  resolveRegionFontSizeValue({ detectedValue: 34, explicitValue: 35, draftValue: '36' }),
  '36',
)
assert.equal(
  resolveRegionRenderFontSize({ detectedValue: 34, explicitValue: 35, draftValue: '36' }),
  36,
)

assert.equal(resolveRegionRenderFontSize({ detectedValue: 2 }), 8)
assert.equal(resolveRegionRenderFontSize({ detectedValue: 'not-a-number' }), 12)

console.log('Region typography tests passed.')
