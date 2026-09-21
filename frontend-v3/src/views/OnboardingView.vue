<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { apiFetch, apiGetJson } from '../api/client.js'
import { useSettings } from '../composables/useSettings.js'
import { SETTINGS_GROUPS } from '../state/settings-fields.js'
import SettingsFields from '../components/SettingsFields.vue'
import { toasts, dismiss, toast, toastError } from '../composables/useToast.js'
import ThemeToggle from '../components/ThemeToggle.vue'
import {
  EMPTY_DIAGNOSTICS,
  EMPTY_RUNTIME,
  diagnosticOverallTone,
  diagnosticTone,
  gpuLabel,
  normalizeDiagnostics,
  normalizeRuntime,
} from '../state/app-runtime.js'

const router = useRouter()
const state = useSettings()
const { settings, draft, loading, saving, validating, dirty } = state
const formKeys = SETTINGS_GROUPS[0].keys
const configError = ref('正在读取配置…')
const runtimeError = ref('')
const appRuntime = ref({ ...EMPTY_RUNTIME })
const appDiagnostics = ref({ ...EMPTY_DIAGNOSTICS })
const validation = ref({ ok: null, message: '' })
let checkEpoch = 0
let allowLeave = false

// ---- 环境检查 ----
const checks = ref([
  { key: 'backend', label: '后端服务', state: 'run', detail: '检测中…' },
  { key: 'translation', label: '翻译配置', state: 'run', detail: '检测中…' },
  { key: 'fonts', label: '字体目录', state: 'run', detail: '检测中…' },
  { key: 'lama', label: 'LaMa 擦字模型', state: 'run', detail: '检测中…' },
])

function setCheck(key, state, detail) {
  const item = checks.value.find((c) => c.key === key)
  if (item) {
    item.state = state
    item.detail = detail
  }
}

async function runChecks() {
  const epoch = ++checkEpoch
  const current = () => epoch === checkEpoch
  checks.value.forEach((c) => { c.state = 'run'; c.detail = '检测中…' })
  // 后端
  let online = false
  try {
    const status = await apiGetJson('/api/status', '连接失败')
    if (!current()) return
    online = status?.status === 'running'
    setCheck('backend', online ? 'ok' : 'fail', online ? '运行中' : '未响应')
  } catch {
    if (!current()) return
    setCheck('backend', 'fail', '未连接（请先启动后端）')
  }
  if (!current()) return
  if (!online) {
    configError.value = '请先连接后端再保存设置。'
    setCheck('translation', 'fail', '需后端在线')
    setCheck('fonts', 'fail', '需后端在线')
    setCheck('lama', 'fail', '需后端在线')
    return
  }
  // Runtime, diagnostics and settings are independent reads. Diagnostics is
  // deliberately fetched from the real endpoint so GPU and environment
  // guidance reflects the backend that will execute the workflow.
  const [runtimeResult, diagnosticsResult, settingsResult] = await Promise.allSettled([
    apiGetJson('/api/app/runtime', '读取应用运行环境失败'),
    apiGetJson('/api/app/diagnostics', '读取运行环境诊断失败'),
    state.load(),
  ])
  if (!current()) return
  runtimeError.value = ''
  if (runtimeResult.status === 'fulfilled') {
    const bridgeRuntime = typeof window !== 'undefined' ? window.mangaDesktop?.runtime || {} : {}
    appRuntime.value = normalizeRuntime(runtimeResult.value, { ...EMPTY_RUNTIME, ...bridgeRuntime })
  } else {
    runtimeError.value = runtimeResult.reason?.message || '读取应用运行环境失败'
  }
  if (diagnosticsResult.status === 'fulfilled') {
    appDiagnostics.value = normalizeDiagnostics(diagnosticsResult.value, appDiagnostics.value)
  } else if (!runtimeError.value) {
    runtimeError.value = diagnosticsResult.reason?.message || '读取运行环境诊断失败'
  }
  // 翻译服务
  if (settingsResult.status === 'fulfilled') {
    configError.value = ''
    const provider = draft.translator || ''
    const hasApiKey = state.secretConfigured('api_key') || Boolean(String(draft.api_key || '').trim())
    const ok = Boolean(provider) && hasApiKey
    setCheck('translation', ok ? 'ok' : 'fail', ok ? `${provider} 已配置，连接待验证` : '请填写翻译服务和 API Key')
  } else {
    configError.value = settingsResult.reason?.message || '读取设置失败'
    setCheck('translation', 'fail', '读取失败')
  }
  // 字体
  try {
    const data = await apiGetJson('/api/fonts', '读取字体失败')
    if (!current()) return
    const fonts = data?.fonts || []
    setCheck('fonts', fonts.length ? 'ok' : 'fail', fonts.length ? `${fonts.length} 个可用` : '字体目录为空')
  } catch {
    if (!current()) return
    setCheck('fonts', 'fail', '读取失败')
  }
  // LaMa
  try {
    const data = await apiGetJson('/api/app/local-models/lama-large', '读取失败')
    if (!current()) return
    const model = data?.model || {}
    const downloaded = Boolean(model.downloaded) && Number(model.size_bytes) > 0
    const partial = Boolean(model.partial_downloaded) || Number(model.partial_size_bytes) > 0
    setCheck('lama', downloaded ? 'ok' : 'fail', downloaded
      ? '已下载，首次使用会校验'
      : partial ? '下载未完成' : '未下载')
  } catch {
    if (!current()) return
    setCheck('lama', 'fail', '状态未知')
  }
}

async function exportDiagnostics() {
  try {
    const response = await apiFetch('/api/app/diagnostics/export')
    if (!response.ok) throw new Error('导出诊断包失败')
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `solar-diagnostics-${Date.now()}.zip`
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
    toast('诊断包已开始下载。', 'ok')
  } catch (err) { toastError(err) }
}

const allGreen = computed(() => checks.value.every(c => c.state === 'ok'))
const diagnosticsTone = computed(() => diagnosticOverallTone(appDiagnostics.value.checks))
async function validateService() {
  try {
    await state.flush({ retryFailedSave: true })
    const result = await state.validate()
    if (!result) return
    validation.value = { ok: Boolean(result.ok), message: String(result.message || (result.ok ? '连接成功' : '连接失败')) }
    setCheck('translation', result.ok ? 'ok' : 'fail', result.message || (result.ok ? '连接成功' : '连接失败'))
    toast(result.message || (result.ok ? '连接成功' : '连接失败'), result.ok ? 'ok' : 'error')
  } catch (err) {
    validation.value = { ok: false, message: err.message }
    toastError(err)
  }
}
async function saveAndStart() {
  if (saving.value || loading.value || validating.value || configError.value) return
  try {
    // Flush first so validation observes exactly the configuration persisted by
    // the server. A failed flush or validation leaves this route and its draft
    // in place for a retry.
    await state.flush({ retryFailedSave: true })
    const result = await state.validate()
    validation.value = { ok: Boolean(result?.ok), message: String(result?.message || (result?.ok ? '连接成功' : '连接失败')) }
    if (!result?.ok) {
      setCheck('translation', 'fail', validation.value.message)
      toast(validation.value.message, 'error')
      return
    }
    toast('设置已保存，欢迎使用。', 'ok')
    router.push('/')
  } catch (err) { toastError(err) }
}

function skip() {
  if (!leaveSafely()) return
  allowLeave = true
  router.push('/')
}

function leaveSafely() {
  if (allowLeave) {
    allowLeave = false
    return true
  }
  if (saving.value) {
    toast('设置正在保存，请稍候。', 'warn')
    return false
  }
  return !dirty.value || window.confirm('有未保存的设置，离开将丢失这些修改。确定离开吗？')
}

function beforeUnload(event) {
  if (dirty.value || saving.value) { event.preventDefault(); event.returnValue = '' }
}

onBeforeRouteLeave(leaveSafely)
onMounted(() => {
  runChecks()
  window.addEventListener('beforeunload', beforeUnload)
})
onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))
</script>

<template>
  <div class="app onboard-page">
    <header class="topbar">
      <a class="topbar-brand" href="#/">
        <span class="brand-mark">S</span>
        <span class="brand-word">Solar<small>MANGA&nbsp;STUDIO</small></span>
      </a>
      <div class="topbar-spacer"></div>
      <div class="topbar-actions">
        <ThemeToggle />
      </div>
    </header>

    <main class="onboard-wrap">
      <div class="modal onboard">
        <div class="modal-head">
          <div>
            <span class="kicker">首次启动</span>
            <h2 class="section-title" style="margin-top:3px">先完成基础设置</h2>
          </div>
          <button class="icon-btn" type="button" data-tip="关闭" aria-label="关闭" @click="skip">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
          </button>
        </div>
        <div class="onboard-steps" style="margin-top:14px">
          <i :class="{ done: checks.every(c => c.state !== 'run') }"></i>
          <i :class="{ done: state.secretConfigured('api_key') || draft.api_key || allGreen }"></i>
          <i></i>
        </div>
        <div class="modal-body" style="display:flex;flex-direction:column;gap:16px">
          <section class="panel" style="padding:16px">
            <h3 style="font-size:var(--fs-strong);font-weight:700;margin-bottom:8px">运行环境检查</h3>
            <div class="check-list">
              <div v-for="c in checks" :key="c.key" class="check-item">
                <span class="c-ico" :class="c.state === 'ok' ? 'ok' : c.state === 'fail' ? 'fail' : 'run'">
                  {{ c.state === 'ok' ? '✓' : c.state === 'fail' ? '✕' : '…' }}
                </span>
                <span>{{ c.label }}</span>
                <small>{{ c.detail }}</small>
              </div>
            </div>
            <div class="inline-actions" style="margin-top:12px">
              <button class="btn btn-secondary" type="button" @click="runChecks">重新检查</button>
              <button class="btn btn-ghost" type="button" @click="exportDiagnostics">导出诊断包</button>
            </div>
            <div v-if="runtimeError || appDiagnostics.checks.length || appDiagnostics.gpu?.action" class="diagnostics-note">
              <div v-if="runtimeError" class="inline-note is-error">{{ runtimeError }}</div>
              <div v-else class="diagnostics-summary">
                <div class="check-item">
                  <span>GPU / CUDA</span>
                  <strong :class="'runtime-' + diagnosticsTone">{{ gpuLabel(appDiagnostics) }}</strong>
                </div>
                <p v-if="appDiagnostics.gpu?.action" class="inline-note is-error">{{ appDiagnostics.gpu.action }}</p>
                <div v-for="check in appDiagnostics.checks" :key="check.id" class="check-item">
                  <span>{{ check.label }}</span>
                  <small :class="'runtime-' + diagnosticTone(check.status)">{{ check.message || check.status }}</small>
                </div>
              </div>
            </div>
          </section>

          <section class="panel" style="padding:16px">
            <h3 style="font-size:var(--fs-strong);font-weight:700;margin-bottom:12px">翻译服务</h3>
            <p v-if="loading" class="inline-note">正在读取设置…</p>
            <p v-else-if="configError" class="inline-note">{{ configError }}</p>
            <SettingsFields v-else :draft="draft" :keys="formKeys" :configured-secrets="settings.configured_secrets" />
            <button class="btn btn-secondary" style="margin-top:12px" :disabled="loading || saving || validating || !!configError" @click="validateService">{{ validating ? '验证中…' : '验证当前配置' }}</button>
            <p v-if="validation.message" class="inline-note" :class="validation.ok ? 'is-success' : 'is-error'">{{ validation.message }}</p>
            <p class="inline-note" style="margin-top:10px">密钥保存在本机。验证和在线翻译会向所选服务商发送文本；输入框留空会保留已保存的密钥。</p>
          </section>
        </div>
        <div class="modal-foot">
          <button class="btn btn-ghost" type="button" @click="skip">跳过，稍后再说</button>
          <button class="btn btn-primary" type="button" :disabled="saving || loading || validating || !!configError" @click="saveAndStart">
            {{ saving ? '保存中…' : validating ? '验证中…' : '保存并开始' }}
          </button>
        </div>
      </div>
    </main>

    <div class="toast-stack">
      <div v-for="t in toasts" :key="t.id" class="toast" :class="`is-${t.kind}`" @click="dismiss(t.id)">{{ t.message }}</div>
    </div>
  </div>
</template>

<style scoped>
.onboard-page { height: 100%; min-height: 0; }
.onboard-wrap {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 32px 20px;
}
.onboard-wrap .modal.onboard {
  position: static;
  max-width: 640px;
  width: 100%;
}
.c-ico.ok { color: var(--ok); }
.c-ico.fail { color: var(--danger); }
.c-ico.run { color: var(--warn); }
.diagnostics-note { margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--border); }
.diagnostics-summary { display: flex; flex-direction: column; gap: 5px; }
.diagnostics-summary .check-item { padding: 4px 0; }
.runtime-ok { color: var(--ok); }
.runtime-warn { color: var(--warn); }
.runtime-fail { color: var(--danger); }
.toast-stack { position: fixed; right: 20px; bottom: 20px; display: flex; flex-direction: column; gap: 8px; z-index: 100; }
.toast { padding: 10px 16px; border-radius: 10px; background: var(--bg-elevated); border: 1px solid var(--border-strong); color: var(--text-1); font-size: 13px; cursor: pointer; max-width: 360px; }
.toast.is-error { border-color: var(--danger); }
.toast.is-warn { border-color: var(--warn); }
.toast.is-ok { border-color: var(--accent); }
</style>
