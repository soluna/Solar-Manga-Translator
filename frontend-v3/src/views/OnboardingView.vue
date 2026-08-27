<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiGetJson, apiPatchJson, toApiUrl } from '../api/client.js'
import { toasts, dismiss, toast, toastError } from '../composables/useToast.js'
import ThemeToggle from '../components/ThemeToggle.vue'

const router = useRouter()

// ---- 环境检查 ----
const checks = ref([
  { key: 'backend', label: '后端服务', state: 'run', detail: '检测中…' },
  { key: 'translation', label: '翻译服务', state: 'run', detail: '检测中…' },
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
  checks.value.forEach((c) => { c.state = 'run'; c.detail = '检测中…' })
  // 后端
  let online = false
  try {
    const status = await apiGetJson('/api/status', '连接失败')
    online = status?.status === 'running'
    setCheck('backend', online ? 'ok' : 'fail', online ? '运行中' : '未响应')
  } catch {
    setCheck('backend', 'fail', '未连接（请先启动后端）')
  }
  if (!online) {
    setCheck('translation', 'fail', '需后端在线')
    setCheck('fonts', 'fail', '需后端在线')
    setCheck('lama', 'fail', '需后端在线')
    return
  }
  // 翻译服务
  try {
    const { settings } = await apiGetJson('/api/app/settings', '读取设置失败')
    const provider = settings?.translation_service || settings?.translation_provider || ''
    const key = settings?.translation_api_key || settings?.api_key || ''
    const ok = Boolean(provider) && Boolean(key)
    setCheck('translation', ok ? 'ok' : 'fail', ok ? `${provider} 已配置` : '未配置 API Key')
  } catch {
    setCheck('translation', 'fail', '读取失败')
  }
  // 字体
  try {
    const data = await apiGetJson('/api/fonts', '读取字体失败')
    const fonts = data?.fonts || []
    setCheck('fonts', fonts.length ? 'ok' : 'fail', fonts.length ? `${fonts.length} 个可用` : '字体目录为空')
  } catch {
    setCheck('fonts', 'fail', '读取失败')
  }
  // LaMa
  try {
    const data = await apiGetJson('/api/app/local-models/lama-large', '读取失败')
    const ok = Boolean(data?.available ?? data?.downloaded ?? data?.installed)
    setCheck('lama', ok ? 'ok' : 'fail', ok ? '已就绪' : '未下载')
  } catch {
    setCheck('lama', 'fail', '状态未知')
  }
}

function exportDiagnostics() {
  window.open(toApiUrl('/api/app/diagnostics/export'), '_blank')
}

// ---- 翻译服务表单 ----
const provider = ref('gemini')
const targetLang = ref('简体中文')
const apiKey = ref('')
const saving = ref(false)

const PROVIDERS = ['gemini', '豆包 Ark', 'OpenAI Compatible', 'deepseek', '自定义']
const LANGS = ['简体中文', '繁体中文', '英语', '日语', '韩语']

const allGreen = computed(() => checks.value.every((c) => c.state === 'ok'))

async function saveAndStart() {
  if (saving.value) return
  saving.value = true
  try {
    const patch = {}
    if (provider.value) patch.translation_service = provider.value
    if (targetLang.value) patch.target_language = targetLang.value
    if (apiKey.value) patch.translation_api_key = apiKey.value
    if (Object.keys(patch).length) {
      await apiPatchJson('/api/app/settings', patch, '保存设置失败')
    }
    try {
      window.localStorage.setItem('solar-v3-onboarded', '1')
    } catch { /* ignore */ }
    toast('设置已保存，欢迎使用。', 'ok')
    router.push('/')
  } catch (err) {
    toastError(err)
  } finally {
    saving.value = false
  }
}

function skip() {
  try {
    window.localStorage.setItem('solar-v3-onboarded', '1')
  } catch { /* ignore */ }
  router.push('/')
}

onMounted(runChecks)
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
          <i :class="{ done: apiKey || allGreen }"></i>
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
          </section>

          <section class="panel" style="padding:16px">
            <h3 style="font-size:var(--fs-strong);font-weight:700;margin-bottom:12px">翻译服务</h3>
            <div class="form-grid">
              <label class="field">
                <span>翻译引擎</span>
                <select v-model="provider">
                  <option v-for="p in PROVIDERS" :key="p" :value="p">{{ p }}</option>
                </select>
              </label>
              <label class="field">
                <span>目标语言</span>
                <select v-model="targetLang">
                  <option v-for="l in LANGS" :key="l" :value="l">{{ l }}</option>
                </select>
              </label>
              <label class="field span2">
                <span>API Key</span>
                <input v-model="apiKey" type="password" placeholder="粘贴你的 API Key" />
              </label>
            </div>
            <p class="inline-note" style="margin-top:10px">设置会保存到本机配置文件，不会上传到服务器。</p>
          </section>
        </div>
        <div class="modal-foot">
          <button class="btn btn-ghost" type="button" @click="skip">跳过，稍后再说</button>
          <button class="btn btn-primary" type="button" :disabled="saving" @click="saveAndStart">
            {{ saving ? '保存中…' : '保存并开始' }}
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
.onboard-wrap {
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 48px 20px;
  min-height: calc(100vh - 60px);
}
.onboard-wrap .modal.onboard {
  position: static;
  max-width: 640px;
  width: 100%;
}
.c-ico.ok { color: #3ecfc0; }
.c-ico.fail { color: #e05656; }
.c-ico.run { color: #e8a33d; }
.toast-stack { position: fixed; right: 20px; bottom: 20px; display: flex; flex-direction: column; gap: 8px; z-index: 100; }
.toast { padding: 10px 16px; border-radius: 10px; background: var(--surface-3, #232838); border: 1px solid var(--line, rgba(255,255,255,.1)); color: var(--text-1, #e8eaf0); font-size: 13px; cursor: pointer; max-width: 360px; }
.toast.is-error { border-color: #e05656; }
.toast.is-warn { border-color: #e8a33d; }
.toast.is-ok { border-color: #3ecfc0; }
</style>