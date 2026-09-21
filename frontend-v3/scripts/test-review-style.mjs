import assert from 'node:assert/strict'
import { test } from 'node:test'
import {
  buildReviewStyleCommands,
  resolveReviewDirection,
  resolveReviewFontKey,
  reviewStyleSnapshot,
  sourceCropStyle,
} from '../src/state/review-style.js'

const fonts = [
  { id: 'system:sans.otf', name: 'SourceHanSansSC-Regular-2.otf', label: '思源黑体' },
  { id: 'system:rounded.otf', name: 'Rounded.otf', label: '圆体' },
]

const region = {
  id: 'region-a',
  bbox: [40, 30, 100, 180],
  direction: 'auto',
  font_style: 'rounded',
  font_family: 'SourceHanSansSC-Regular-2.otf',
  font_key_override: '',
  font_size: 24,
  disabled: true,
  preserve_background: true,
  fg_color: [10, 20, 30],
  bg_color: [240, 240, 240],
}

test('style snapshot carries effective font, direction, enabled state and advanced fields', () => {
  const snapshot = reviewStyleSnapshot(region, {
    draft: {
      enabled: false,
      fontKey: '',
      fontStyle: '',
      fontSize: 24,
      rotation: 8,
      strokeWidth: 0.4,
      letterSpacing: 1.1,
      lineSpacing: 1.2,
      fgColor: '#112233',
      bgColor: '#ffffff',
      preserveBackground: true,
    },
    config: { target_lang: 'CHS', style_font_rounded_key: 'system:rounded.otf' },
    fonts,
  })
  assert.deepEqual(snapshot, {
    enabled: false,
    direction: 'vertical',
    fontKey: 'system:rounded.otf',
    fontSize: 24,
    fontStyle: 'rounded',
    advanced: {
      rotation: 8,
      stroke_width: 0.4,
      letter_spacing: 1.1,
      line_spacing: 1.2,
      fg_color: [17, 34, 51],
      bg_color: [255, 255, 255],
      preserve_background: true,
    },
  })
})

test('Chinese without a layout override keeps the vertical default over horizontal detection', () => {
  assert.equal(resolveReviewDirection(
    { ...region, direction: 'horizontal', bbox: [0, 0, 180, 80] },
    { target_lang: 'CHS', translation_region_layout_overrides: {} },
  ), 'vertical')
  assert.equal(resolveReviewDirection(
    { ...region, direction: 'horizontal', bbox: [0, 0, 80, 180] },
    { target_lang: 'CHT' },
  ), 'vertical')
})

test('non-Chinese uses detected direction when it disagrees with bbox geometry', () => {
  assert.equal(resolveReviewDirection(
    { ...region, direction: 'vertical', bbox: [0, 0, 180, 80] },
    { target_lang: 'ENG' },
  ), 'vertical')
  assert.equal(resolveReviewDirection(
    { ...region, direction: 'horizontal', bbox: [0, 0, 80, 180] },
    { target_lang: 'ENG' },
  ), 'horizontal')
})

test('a project layout direction override wins over language defaults and detection', () => {
  assert.equal(resolveReviewDirection(
    { ...region, direction: 'vertical', bbox: [0, 0, 80, 180] },
    { target_lang: 'CHS', translation_region_layout_overrides: { 'region-a': { direction: 'horizontal' } } },
  ), 'horizontal')
  assert.equal(resolveReviewDirection(
    { ...region, direction: 'horizontal', bbox: [0, 0, 180, 80] },
    {
      target_lang: 'ENG',
      translation_region_layout_overrides: { 'region-a': { direction: 'horizontal' } },
      overrides: { translation_region_layout_overrides: { 'region-a': { direction: 'vertical' } } },
    },
  ), 'vertical')
})

test('the latest layout override map suppresses stale config overrides when empty, deleted, or auto', () => {
  const latestMaps = [
    {},
    { 'another-region': { direction: 'vertical' } },
    { 'region-a': { direction: 'auto' } },
  ]
  for (const latest of latestMaps) {
    assert.equal(resolveReviewDirection(
      { ...region, direction: 'vertical' },
      {
        target_lang: 'ENG',
        translation_region_layout_overrides: { 'region-a': { direction: 'horizontal' } },
        overrides: { translation_region_layout_overrides: latest },
      },
    ), 'vertical')
  }

  assert.equal(resolveReviewDirection(
    { ...region, direction: 'vertical' },
    {
      target_lang: 'ENG',
      translation_region_layout_overrides: { 'region-a': { direction: 'horizontal' } },
      overrides: {},
    },
  ), 'horizontal')
})

test('style snapshots use a direction draft that has not finished saving', () => {
  const snapshot = reviewStyleSnapshot(
    { ...region, direction: 'vertical', bbox: [0, 0, 180, 80] },
    { draft: { direction: 'horizontal' }, config: { target_lang: 'ENG' } },
  )
  assert.equal(snapshot.direction, 'horizontal')
})

test('style snapshots honor an explicit direction intent that matches detection', () => {
  const snapshot = reviewStyleSnapshot(
    { ...region, direction: 'horizontal', bbox: [0, 0, 180, 80] },
    {
      draft: { direction: 'horizontal', directionIntent: 'horizontal' },
      config: { target_lang: 'CHS' },
    },
  )
  assert.equal(snapshot.direction, 'horizontal')
})

test('an explicit auto direction intent previews defaults while a saved override is being cleared', () => {
  const snapshot = reviewStyleSnapshot(
    { ...region, direction: 'horizontal', bbox: [0, 0, 180, 80] },
    {
      draft: { direction: 'auto', directionIntent: 'auto' },
      config: {
        target_lang: 'CHS',
        translation_region_layout_overrides: { 'region-a': { direction: 'horizontal' } },
      },
    },
  )
  assert.equal(snapshot.direction, 'vertical')
})

test('style commands apply copied enabled state and keep auto font reset representable', () => {
  const commands = buildReviewStyleCommands('region-a', {
    enabled: false,
    direction: 'vertical',
    fontKey: 'system:rounded.otf',
    fontSize: 24,
    fontStyle: 'rounded',
    advanced: { rotation: 0, stroke_width: 0.2, letter_spacing: 1, line_spacing: 1, fg_color: [0, 0, 0], bg_color: [255, 255, 255], preserve_background: false },
  })
  assert.equal(commands[0].type, 'disable_region')
  assert.deepEqual(commands.slice(1, 5), [
    { type: 'update_text_direction', region_id: 'region-a', direction: 'vertical' },
    { type: 'update_region_font', region_id: 'region-a', font_key: 'system:rounded.otf' },
    { type: 'update_font_size', region_id: 'region-a', font_size: 24 },
    { type: 'update_font_style', region_id: 'region-a', style: 'rounded' },
  ])
  assert.equal(buildReviewStyleCommands('region-a', { enabled: true, fontSize: null })[3].font_size, null)
})

test('source crop uses page coordinates to map the original image into the region', () => {
  assert.deepEqual(sourceCropStyle([100, 50, 300, 250], 1000, 800), {
    width: '500%', height: '400%', left: '-50%', top: '-25%',
  })
})
