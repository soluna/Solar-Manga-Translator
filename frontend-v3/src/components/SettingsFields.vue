<script setup>
import { computed } from 'vue'
import { SETTINGS_FIELDS, visibleSettingKeys } from '../state/settings-fields.js'
const props = defineProps({ draft: { type: Object, required: true }, keys: { type: Array, required: true },
  configuredSecrets: { type: Object, default: () => ({}) }, fonts: { type: Array, default: () => [] } })
const visible = computed(() => visibleSettingKeys(props.keys, props.draft))
function options(key) {
  const field = SETTINGS_FIELDS[key]
  let values = field.type === 'font' ? props.fonts.map(font => ({ value: font.id, label: font.label || font.id })) : field.options
  if (!values) return null
  const current = props.draft[key]
  if (current && !values.some(option => option.value === current)) values = [...values, { value: current, label: current }]
  return values
}
</script>

<template>
  <div class="form-grid">
    <label v-for="key in visible" :key="key" :class="SETTINGS_FIELDS[key].type === 'boolean' ? 'check-row' : ['field', { span2: SETTINGS_FIELDS[key].wide || ['secret', 'textarea'].includes(SETTINGS_FIELDS[key].type) }]">
      <template v-if="SETTINGS_FIELDS[key].type === 'boolean'">
        <input v-model="draft[key]" type="checkbox" /><span>{{ SETTINGS_FIELDS[key].label }}</span>
      </template>
      <template v-else>
        <span>{{ SETTINGS_FIELDS[key].label }}</span>
        <select v-if="options(key)" v-model="draft[key]">
          <option v-for="option in options(key)" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
        <input v-else-if="SETTINGS_FIELDS[key].type === 'secret'" v-model="draft[key]" type="password" autocomplete="new-password"
          :placeholder="configuredSecrets[key] ? '已保存密钥；留空保留，填写可替换' : '填写 API Key'" />
        <input v-else-if="SETTINGS_FIELDS[key].type === 'number'" v-model.number="draft[key]" type="number"
          :min="SETTINGS_FIELDS[key].min" :max="SETTINGS_FIELDS[key].max" :step="SETTINGS_FIELDS[key].step || 1" />
        <textarea v-else-if="SETTINGS_FIELDS[key].type === 'textarea'" v-model="draft[key]" rows="3" />
        <input v-else v-model="draft[key]" type="text" />
      </template>
    </label>
  </div>
</template>
