<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { apiFetch, apiGetJson, apiPostJson } from '../api/client.js'
import { dismiss, toast, toastError, toasts } from '../composables/useToast.js'
import { useSettings } from '../composables/useSettings.js'
import { SETTINGS_GROUPS } from '../state/settings-fields.js'
import SettingsFields from '../components/SettingsFields.vue'
import ThemeToggle from '../components/ThemeToggle.vue'

const router = useRouter()
const state = useSettings()
const { settings, draft, loading, saving, validating, changedKeys, dirty } = state
const loadError = ref('')
const navGroups = SETTINGS_GROUPS
const formGroups = SETTINGS_GROUPS.filter(group => group.keys)
const fonts = ref([]), fontsLoading = ref(false), fontsError = ref('')

async function loadSettings() {
  loadError.value = ''
  try { await state.load() } catch (err) { loadError.value = err.message }
}
async function loadFonts({ silent = false } = {}) {
  if (!silent) fontsLoading.value = true
  fontsError.value = ''
  try { fonts.value = (await apiGetJson('/api/fonts', '读取字体列表失败')).fonts || [] }
  catch (err) { fontsError.value = err.message }
  finally { fontsLoading.value = false }
}
async function saveSettings() {
  try { await state.flush(); toast('设置已保存。', 'ok') } catch (err) { toastError(err) }
}
function resetDraft() { state.reset() }
async function validateService() {
  try {
    const result = await state.validate()
    if (result) toast(result.message || (result.ok ? '翻译服务验证通过。' : '翻译服务验证未通过。'), result.ok ? 'ok' : 'error')
  } catch (err) { toastError(err) }
}
function fontSourceLabel(source) {
  return ({ system: '内置', custom: '自定义', user: '自定义', bundled: '内置' })[source] || source || '未知'
}
function beforeUnload(event) {
  if (dirty.value || saving.value) { event.preventDefault(); event.returnValue = '' }
}
onBeforeRouteLeave(async () => {
  try { await state.flush(); return true } catch (err) { toastError(err); return false }
})
onMounted(() => {
  loadSettings()
  loadFonts({ silent: true })
  window.addEventListener('beforeunload', beforeUnload)
})
onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))

// ---- 高级运维 ----
const advBusy = ref('')
const remoteDiag = ref(null) // start 响应（含地址/到期时间等）
const remoteExec = ref(null) // enable / rotate-token 响应（含 token）
const logsText = ref('')

async function runAdvanced(key, request, { onSuccess } = {}) {
  if (advBusy.value) return
  advBusy.value = key
  try {
    const payload = await request()
    if (onSuccess) onSuccess(payload)
    return payload
  } catch (err) {
    toastError(err)
  } finally {
    advBusy.value = ''
  }
}

function startRemoteDiag() {
  runAdvanced('diag-start', () => apiPostJson('/api/app/remote-diagnostics/start', {}, '开启远程诊断失败'), {
    onSuccess: (payload) => {
      remoteDiag.value = payload && typeof payload === 'object' ? payload : { status: 'started' }
      toast('远程诊断已开启。', 'success')
    },
  })
}

function stopRemoteDiag() {
  runAdvanced('diag-stop', () => apiPostJson('/api/app/remote-diagnostics/stop', {}, '关闭远程诊断失败'), {
    onSuccess: () => {
      remoteDiag.value = null
      toast('远程诊断已关闭。', 'success')
    },
  })
}

function remoteExecAction(action) {
  const labelMap = { enable: '启用远程任务节点', 'rotate-token': '轮换令牌', disable: '禁用远程任务节点' }
  runAdvanced(`exec-${action}`, () => apiPostJson(`/api/app/remote-execution/${action}`, {}, `${labelMap[action]}失败`), {
    onSuccess: (payload) => {
      if (action === 'disable') {
        remoteExec.value = null
      } else if (payload && typeof payload === 'object') {
        remoteExec.value = payload
      }
      toast(`${labelMap[action]}成功。`, 'success')
    },
  })
}

function openDirectory(kind) {
  const labelMap = { 'open-logs': '日志目录', 'open-data-directory': '数据目录', 'open-user-fonts': '字库文件夹' }
  runAdvanced(kind, () => apiPostJson(`/api/app/${kind}`, {}, `打开${labelMap[kind]}失败`), {
    onSuccess: () => toast(`已请求打开${labelMap[kind]}。`, 'success'),
  })
}

function tailLogs() {
  runAdvanced('logs-tail', () => apiGetJson('/api/app/logs/tail', '读取日志失败'), {
    onSuccess: (payload) => {
      const lines = payload?.lines ?? payload?.logs ?? payload?.tail ?? payload
      logsText.value = Array.isArray(lines) ? lines.join('\n') : typeof lines === 'string' ? lines : JSON.stringify(lines, null, 2)
      toast('已读取最新日志。', 'success')
    },
  })
}

async function exportDiagnostics() {
  if (advBusy.value) return
  advBusy.value = 'export'
  try {
    const response = await apiFetch(`/api/app/diagnostics/export`)
    if (!response.ok) throw new Error('导出诊断包失败')
    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `solar-diagnostics-${Date.now()}.zip`
    a.click()
    URL.revokeObjectURL(url)
    toast('诊断包已开始下载。', 'success')
  } catch (err) {
    toastError(err)
  } finally {
    advBusy.value = ''
  }
}

/** 展示响应里的关键信息（地址 / 令牌 / 状态）。 */
function summarizePayload(payload) {
  if (!payload || typeof payload !== 'object') return []
  const interesting = ['status', 'url', 'address', 'token', 'expires_at', 'expires_in', 'enabled', 'message']
  return Object.entries(payload)
    .filter(([k]) => interesting.includes(k))
    .map(([k, v]) => ({ key: k, value: String(v) }))
}

// ---- 导航 ----
const activeGroup = ref('translation')

function scrollToGroup(id) {
  activeGroup.value = id
  const el = document.getElementById(`settings-${id}`)
  if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function goHome() {
  router.push('/')
}

function goBack() {
  if (window.history.length > 1 && window.history.state?.back) {
    router.back()
  } else {
    router.push('/')
  }
}
</script>

<template>
  <div class="app">
    <!-- 顶部命令栏 -->
    <header class="topbar">
      <a class="topbar-brand" title="回到首页" @click="goHome">
        <span class="brand-mark">S</span>
        <span class="brand-word">Solar<small>MANGA&nbsp;STUDIO</small></span>
      </a>
      <div class="topbar-divider"></div>
      <button class="btn btn-ghost btn-sm" data-tip="返回上一页" @click="goBack">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
        返回
      </button>
      <div class="topbar-title"><strong>设置</strong></div>
      <div class="topbar-spacer"></div>
      <div class="topbar-actions">
        <button
          class="btn btn-primary btn-sm"
          :disabled="!dirty || saving || !!loadError"
          data-tip="仅保存改动字段"
          @click="saveSettings"
        >
          <span v-if="saving" class="spin" />
          <template v-else>保存{{ dirty ? `（${changedKeys.length}）` : '' }}</template>
        </button>
        <ThemeToggle />
      </div>
    </header>

    <!-- 后端未连接空态 -->
    <main v-if="loadError && !loading" class="pages-view">
      <div class="pages-inner">
        <div class="card empty">
          <strong>后端未连接</strong>
          <p>{{ loadError }}</p>
          <button class="btn btn-secondary" @click="loadSettings">重试</button>
        </div>
      </div>
    </main>

    <div v-else class="settings-view">
      <!-- 左侧分组导航 -->
      <aside class="settings-nav">
        <span class="kicker">设置</span>
        <button
          v-for="group in navGroups"
          :key="group.id"
          :class="{ active: activeGroup === group.id }"
          @click="scrollToGroup(group.id)"
        >
          {{ group.label }}
        </button>
      </aside>

      <!-- 右侧设置内容 -->
      <main class="settings-body">
        <div class="settings-body-inner">
          <div v-if="loading" class="card empty">
            <span class="spin spin-lg" />
            <strong>正在读取设置…</strong>
          </div>

          <template v-else>
            <section v-for="group in formGroups" :id="`settings-${group.id}`" :key="group.id" class="settings-group">
              <div class="group-head"><h3>{{ group.label }}</h3></div>
              <div class="group-body">
                <SettingsFields :draft="draft" :keys="group.keys" :configured-secrets="settings.configured_secrets" :fonts="fonts" />
                <template v-if="group.id === 'translation'">
                  <div class="inline-actions">
                    <button class="btn btn-secondary" :disabled="validating || saving" @click="validateService">{{ validating ? '验证中…' : '验证当前配置' }}</button>
                    <button class="btn btn-ghost" :disabled="!dirty || saving" @click="resetDraft">放弃改动</button>
                  </div>
                  <p class="inline-note">验证会使用当前填写的配置向所选服务发送测试文本。密钥保存在本机，留空保留已有密钥。</p>
                </template>
                <p v-if="group.id === 'cleanup'" class="inline-note">在线清理与在线擦除会把所需图片发送给你选择的服务商；本地 LaMa 不需要在线擦除密钥。在线擦除目前使用 Ark / Seedream。</p>
              </div>
            </section>

            <!-- 字体 -->
            <section class="settings-group" :id="'settings-fonts'">
              <div class="group-head">
                <h3>字体</h3>
                <span>本机可用字库</span>
              </div>
              <div class="group-body">
                <div class="inline-actions">
                  <button class="btn btn-secondary" :disabled="fontsLoading" @click="loadFonts()">
                    <span v-if="fontsLoading" class="spin" />
                    <template v-else>刷新字库</template>
                  </button>
                  <button class="btn btn-secondary" :disabled="!!advBusy" @click="openDirectory('open-user-fonts')">打开字库文件夹</button>
                </div>
                <p v-if="fontsError" class="inline-note is-error">{{ fontsError }}</p>
                <p v-else class="inline-note">共 {{ fonts.length }} 款字体</p>
                <div v-if="fonts.length" class="font-list">
                  <div v-for="font in fonts" :key="font.id" class="mark-item">
                    <span class="tag" :class="{ 'is-accent': font.source === 'user' }">{{ fontSourceLabel(font.source) }}</span>
                    <span>{{ font.label || font.id }}</span>
                    <span class="num">{{ font.source }}</span>
                  </div>
                </div>
                <p v-else-if="!fontsLoading && !fontsError" class="inline-note">暂无可用字体。</p>
              </div>
            </section>

            <!-- 高级 -->
            <section class="settings-group" :id="'settings-advanced'">
              <div class="group-head">
                <h3>高级</h3>
                <span>远程诊断与运维操作</span>
              </div>
              <div class="group-body" style="background: transparent; border: none; padding: 0; gap: 14px">
                <!-- 局域网远程诊断 -->
                <details class="advanced-fold" open>
                  <summary>局域网远程诊断 <span class="fold-hint">默认关闭 · 只读</span></summary>
                  <div class="fold-body">
                    <p class="inline-note">开启后允许局域网内设备读取脱敏日志与项目状态，用于远程排查故障；不开放任何修改权限。</p>
                    <div v-if="remoteDiag">
                      <div v-for="item in summarizePayload(remoteDiag)" :key="item.key" class="kv">
                        <span>{{ item.key }}</span><strong class="mono">{{ item.value }}</strong>
                      </div>
                    </div>
                    <div v-else class="kv"><span>服务状态</span><strong>已关闭</strong></div>
                    <div class="inline-actions">
                      <button v-if="!remoteDiag" class="btn btn-secondary" :disabled="!!advBusy" @click="startRemoteDiag">
                        <span v-if="advBusy === 'diag-start'" class="spin" />
                        <template v-else>开启</template>
                      </button>
                      <button v-else class="btn btn-secondary" :disabled="!!advBusy" @click="stopRemoteDiag">
                        <span v-if="advBusy === 'diag-stop'" class="spin" />
                        <template v-else>关闭</template>
                      </button>
                    </div>
                  </div>
                </details>

                <!-- 持久远程任务节点 -->
                <details class="advanced-fold">
                  <summary>持久远程任务节点 <span class="fold-hint">默认关闭 · 高风险</span></summary>
                  <div class="fold-body">
                    <p class="inline-note is-error">令牌具备本机执行权限，只发给可信对象。</p>
                    <div v-if="remoteExec">
                      <div v-for="item in summarizePayload(remoteExec)" :key="item.key" class="kv">
                        <span>{{ item.key }}</span><strong class="mono">{{ item.value }}</strong>
                      </div>
                    </div>
                    <div v-else class="kv"><span>服务状态</span><strong>已关闭</strong></div>
                    <div class="inline-actions">
                      <button class="btn btn-secondary" :disabled="!!advBusy" @click="remoteExecAction('enable')">启用</button>
                      <button class="btn btn-secondary" :disabled="!!advBusy || !remoteExec" @click="remoteExecAction('rotate-token')">轮换令牌</button>
                      <button class="btn btn-danger" :disabled="!!advBusy || !remoteExec" @click="remoteExecAction('disable')">禁用</button>
                    </div>
                  </div>
                </details>

                <!-- 目录与日志 -->
                <details class="advanced-fold">
                  <summary>目录与日志 <span class="fold-hint">数据 / 日志 / 诊断包</span></summary>
                  <div class="fold-body">
                    <div class="inline-actions">
                      <button class="btn btn-secondary" :disabled="!!advBusy" @click="openDirectory('open-data-directory')">打开数据目录</button>
                      <button class="btn btn-secondary" :disabled="!!advBusy" @click="openDirectory('open-logs')">打开日志目录</button>
                      <button class="btn btn-secondary" :disabled="!!advBusy" @click="tailLogs">
                        <span v-if="advBusy === 'logs-tail'" class="spin" />
                        <template v-else>查看最新日志</template>
                      </button>
                      <button class="btn btn-secondary" :disabled="!!advBusy" @click="exportDiagnostics">
                        <span v-if="advBusy === 'export'" class="spin" />
                        <template v-else>导出诊断包</template>
                      </button>
                    </div>
                    <pre v-if="logsText" class="logs-tail mono-scroll">{{ logsText }}</pre>
                  </div>
                </details>
              </div>
            </section>
          </template>
        </div>
      </main>
    </div>

    <!-- Toast 栈 -->
    <div class="toast-stack">
      <TransitionGroup name="toast">
        <div v-for="item in toasts" :key="item.id" class="toast" :class="`is-${item.kind}`" @click="dismiss(item.id)">
          {{ item.message }}
        </div>
      </TransitionGroup>
    </div>
  </div>
</template>

<style scoped>
.spin {
  display: inline-block; width: 12px; height: 12px;
  border: 2px solid var(--border-strong); border-top-color: currentColor;
  border-radius: 50%; animation: sv-spin .8s linear infinite; vertical-align: -2px;
}
.spin-lg { width: 22px; height: 22px; border-width: 2.5px; }
@keyframes sv-spin { to { transform: rotate(360deg); } }

.font-list { display: flex; flex-direction: column; gap: 5px; max-height: 260px; overflow-y: auto; }

.logs-tail {
  margin: 0;
  max-height: 240px;
  overflow: auto;
  padding: 10px 12px;
  background: var(--bg-inset);
  border: 1px solid var(--border);
  border-radius: var(--r-s);
  font-family: var(--font-mono);
  font-size: var(--fs-micro);
  color: var(--text-2);
  white-space: pre-wrap;
  word-break: break-all;
}

/* Toast 栈 */
.toast-stack {
  position: fixed; right: 18px; bottom: 18px; z-index: 200;
  display: flex; flex-direction: column; gap: 8px; max-width: min(420px, calc(100vw - 36px));
  pointer-events: none;
}
.toast {
  pointer-events: auto;
  padding: 10px 14px;
  border-radius: var(--r-m);
  background: var(--bg-elevated);
  border: 1px solid var(--border-strong);
  box-shadow: var(--shadow-pop);
  font-size: var(--fs-sub);
  color: var(--text-1);
  cursor: pointer;
  display: flex; align-items: center; gap: 8px;
}
.toast::before { content: ""; width: 7px; height: 7px; border-radius: 50%; flex: none; background: var(--accent); }
.toast.is-success::before { background: var(--ok); }
.toast.is-error { border-color: var(--danger); }
.toast.is-error::before { background: var(--danger); }
.toast-enter-active, .toast-leave-active { transition: all var(--t-med) var(--ease); }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translateY(6px); }
</style>
