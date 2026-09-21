<script setup>
import { onMounted, onUnmounted, ref } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { apiFetch, apiGetJson, apiPostJson } from '../api/client.js'
import { dismiss, toast, toastError, toasts } from '../composables/useToast.js'
import { useSettings } from '../composables/useSettings.js'
import {
  ADVANCED_ERASE_DEFAULT_PROMPT,
  SETTINGS_GROUPS,
  fontSourceLabel,
} from '../state/settings-fields.js'
import {
  EMPTY_DIAGNOSTICS,
  EMPTY_RUNTIME,
  diagnosticOverallTone,
  diagnosticTone,
  formatBytes,
  gpuLabel,
  hasLegacyData,
  normalizeDiagnostics,
  normalizeMigration,
  normalizeRemoteDiagnostics,
  normalizeRemoteExecution,
  normalizeRuntime,
  remoteDiagnosticsConnectionText,
  remoteExecutionConnectionText,
} from '../state/app-runtime.js'
import SettingsFields from '../components/SettingsFields.vue'
import ThemeToggle from '../components/ThemeToggle.vue'

const router = useRouter()
const state = useSettings()
const { settings, draft, loading, saving, clearing, validating, changedKeys, dirty } = state
const loadError = ref('')
const navGroups = SETTINGS_GROUPS
const formGroups = SETTINGS_GROUPS.filter(group => group.keys)
const fonts = ref([]), fontsLoading = ref(false), fontsError = ref('')
const appRuntime = ref({ ...EMPTY_RUNTIME })
const appDiagnostics = ref({ ...EMPTY_DIAGNOSTICS })
const migration = ref({})
const runtimeLoading = ref(false), runtimeError = ref('')
const clearingSecret = ref('')

async function loadSettings() {
  loadError.value = ''
  try { await state.load() } catch (err) { loadError.value = err.message }
}
async function loadFonts({ silent = false, forceRefresh = false } = {}) {
  if (!silent) fontsLoading.value = true
  fontsError.value = ''
  try {
    const path = forceRefresh ? `/api/fonts?refresh=${Date.now()}` : '/api/fonts'
    fonts.value = (await apiGetJson(path, '读取字体列表失败')).fonts || []
  }
  catch (err) { fontsError.value = err.message }
  finally { fontsLoading.value = false }
}
async function saveSettings() {
  try { await state.flush({ retryFailedSave: true }); toast('设置已保存。', 'ok') } catch (err) { toastError(err) }
}
function resetDraft() { state.reset() }
async function validateService() {
  try {
    await state.flush({ retryFailedSave: true })
    const result = await state.validate()
    if (result) toast(result.message || (result.ok ? '翻译服务验证通过。' : '翻译服务验证未通过。'), result.ok ? 'ok' : 'error')
  } catch (err) { toastError(err) }
}

async function clearSecret(key) {
  if (clearingSecret.value || !state.secretConfigured(key)) return
  clearingSecret.value = key
  try {
    await state.clearSecrets([key])
    toast(`${key === 'api_key' ? '翻译服务' : key === 'image_cleanup_api_key' ? '图像清理' : '在线擦除'}密钥已清除。`, 'success')
  } catch (err) { toastError(err) }
  finally { clearingSecret.value = '' }
}

async function loadRuntimeData({ silent = true } = {}) {
  runtimeLoading.value = true
  runtimeError.value = ''
  const [runtimeResult, diagnosticsResult, migrationResult] = await Promise.allSettled([
    apiGetJson('/api/app/runtime', '读取应用运行环境失败'),
    apiGetJson('/api/app/diagnostics', '读取运行环境诊断失败'),
    apiGetJson('/api/app/migration-status', '读取旧数据迁移状态失败'),
  ])
  if (runtimeResult.status === 'fulfilled') {
    appRuntime.value = normalizeRuntime(runtimeResult.value, appRuntime.value)
    migration.value = normalizeMigration(appRuntime.value, migration.value)
  } else if (!silent) {
    runtimeError.value = runtimeResult.reason?.message || '读取应用运行环境失败'
  }
  if (diagnosticsResult.status === 'fulfilled') {
    appDiagnostics.value = normalizeDiagnostics(diagnosticsResult.value, appDiagnostics.value)
  } else if (!runtimeError.value && !silent) {
    runtimeError.value = diagnosticsResult.reason?.message || '读取运行环境诊断失败'
  }
  if (migrationResult.status === 'fulfilled') {
    migration.value = normalizeMigration(migrationResult.value, migration.value)
    appRuntime.value = { ...appRuntime.value, migration: migration.value }
  } else if (!runtimeError.value && !silent) {
    runtimeError.value = migrationResult.reason?.message || '读取旧数据迁移状态失败'
  }
  runtimeLoading.value = false
}

async function migrateLegacy(action) {
  if (action === 'migrate_clean' && !window.confirm('将先复制旧数据并验证旧项目，再删除确认属于本应用的旧目录。是否继续？')) return
  await runAdvanced(`migration-${action}`, () => apiPostJson('/api/app/migrate-legacy', { action }, '处理旧数据失败'), {
    onSuccess: payload => {
      migration.value = normalizeMigration(payload, migration.value)
      appRuntime.value = { ...appRuntime.value, migration: migration.value }
      const cleanup = migration.value.cleanup || {}
      const message = action === 'migrate_clean'
        ? (cleanup.status === 'partial' ? '旧项目已迁移，但少量旧目录未能清理。' : `旧项目已迁移并清理 ${cleanup.deleted_paths?.length || 0} 个旧目录。`)
        : action === 'migrate' ? '旧项目数据已迁移；原目录按你的选择保留。' : '已跳过旧数据迁移。'
      toast(message, cleanup.status === 'partial' ? 'warn' : 'success')
    },
  })
}

function resetAdvancedErasePrompt() {
  draft.advanced_erase_selection_prompt = ADVANCED_ERASE_DEFAULT_PROMPT
  toast('已恢复默认 Prompt，请点击保存使其生效。', 'ok')
}
function beforeUnload(event) {
  if (dirty.value || saving.value || clearing.value) { event.preventDefault(); event.returnValue = '' }
}
onBeforeRouteLeave(async () => {
  try { await state.flush(); return true } catch (err) { toastError(err); return false }
})
onMounted(() => {
  loadSettings()
  loadFonts({ silent: true })
  loadRuntimeData({ silent: true })
  loadRemoteStatuses({ silent: true })
  window.addEventListener('beforeunload', beforeUnload)
})
onUnmounted(() => window.removeEventListener('beforeunload', beforeUnload))

// ---- 高级运维 ----
const advBusy = ref('')
const remoteDiag = ref(null)
const remoteExec = ref(null)
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
  runAdvanced('diag-start', () => apiPostJson('/api/app/remote-diagnostics/start', { ttl_minutes: 60 }, '开启远程诊断失败'), {
    onSuccess: (payload) => {
      remoteDiag.value = normalizeRemoteDiagnostics(payload, remoteDiag.value || {})
      toast('远程诊断已开启。', 'success')
    },
  })
}

function stopRemoteDiag() {
  runAdvanced('diag-stop', () => apiPostJson('/api/app/remote-diagnostics/stop', {}, '关闭远程诊断失败'), {
    onSuccess: (payload) => {
      remoteDiag.value = normalizeRemoteDiagnostics(payload, remoteDiag.value || {})
      toast('远程诊断已关闭。', 'success')
    },
  })
}

function remoteExecAction(action) {
  const labelMap = { enable: '启用远程任务节点', 'rotate-token': '轮换令牌', disable: '禁用远程任务节点' }
  runAdvanced(`exec-${action}`, () => apiPostJson(`/api/app/remote-execution/${action}`, {}, `${labelMap[action]}失败`), {
    onSuccess: (payload) => {
      if (action === 'disable') {
        remoteExec.value = normalizeRemoteExecution(payload, remoteExec.value || {})
      } else if (payload && typeof payload === 'object') {
        remoteExec.value = normalizeRemoteExecution(payload, remoteExec.value || {})
      }
      toast(`${labelMap[action]}成功。`, 'success')
    },
  })
}

async function loadRemoteStatuses({ silent = true } = {}) {
  const [diagResult, execResult] = await Promise.allSettled([
    apiGetJson('/api/app/remote-diagnostics', '读取局域网诊断状态失败'),
    apiGetJson('/api/app/remote-execution', '读取远程任务节点状态失败'),
  ])
  if (diagResult.status === 'fulfilled') {
    remoteDiag.value = normalizeRemoteDiagnostics(diagResult.value, remoteDiag.value || {})
  } else if (!silent) {
    toastError(diagResult.reason)
  }
  if (execResult.status === 'fulfilled') {
    remoteExec.value = normalizeRemoteExecution(execResult.value, remoteExec.value || {})
  } else if (!silent) {
    toastError(execResult.reason)
  }
}

function openDirectory(kind) {
  const labelMap = { 'open-logs': '日志目录', 'open-data-directory': '数据目录', 'open-user-fonts': '字库文件夹' }
  runAdvanced(kind, () => apiPostJson(`/api/app/${kind}`, {}, `打开${labelMap[kind]}失败`), {
    onSuccess: (payload) => {
      if (!payload?.ok) throw new Error(payload?.error || `系统文件管理器未能打开${labelMap[kind]}。`)
      toast(`已打开${labelMap[kind]}。`, 'success')
    },
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

async function copyText(value, successMessage, emptyMessage) {
  const textValue = String(value || '').trim()
  if (!textValue) {
    toast(emptyMessage, 'warn')
    return
  }
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(textValue)
    } else {
      const input = document.createElement('textarea')
      input.value = textValue
      input.setAttribute('readonly', '')
      input.style.position = 'fixed'
      input.style.opacity = '0'
      document.body.appendChild(input)
      input.select()
      if (!document.execCommand('copy')) throw new Error('剪贴板不可用')
      input.remove()
    }
    toast(successMessage, 'success')
  } catch (err) {
    toastError(err)
  }
}

function copyRemoteDiag() {
  copyText(
    remoteDiagnosticsConnectionText(remoteDiag.value),
    '局域网诊断连接信息已复制，可直接发送给 Codex。',
    '当前没有可复制的连接信息，请开启或刷新诊断访问。',
  )
}

function copyRemoteExec() {
  copyText(
    remoteExecutionConnectionText(remoteExec.value),
    '远程任务节点连接信息已复制，可直接发送给 Codex。',
    '任务节点尚未运行，请先启用后再复制连接信息。',
  )
}

/** Keep status panels readable while retaining the full payload for copying. */
function summarizePayload(payload) {
  if (!payload || typeof payload !== 'object') return []
  const interesting = ['active', 'enabled', 'persistent', 'read_only', 'port', 'urls', 'expires_at', 'remaining_seconds', 'token', 'tasks', 'worker_root']
  return interesting
    .filter(key => Object.hasOwn(payload, key))
    .map(key => ({
      key,
      value: Array.isArray(payload[key]) ? payload[key].join(', ') : String(payload[key] ?? ''),
    }))
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
          :disabled="!dirty || saving || clearing || !!loadError"
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
            <!-- 运行环境与旧数据 -->
            <section class="settings-group" id="settings-runtime">
              <div class="group-head">
                <h3>应用运行环境</h3>
                <span>{{ appRuntime.desktop_mode ? '桌面版本地运行时' : '浏览器模式' }}</span>
              </div>
              <div class="group-body">
                <div v-if="runtimeLoading" class="inline-note">正在读取运行环境…</div>
                <p v-if="runtimeError" class="inline-note is-error">{{ runtimeError }}</p>
                <template v-else>
                  <div class="kv"><span>设置状态</span><strong>{{ saving ? '正在保存设置…' : state.error.value ? '连接待修复' : '设置已载入' }}</strong></div>
                  <div v-if="appRuntime.settings_path" class="kv"><span>设置文件</span><strong :title="appRuntime.settings_path" class="mono">{{ appRuntime.settings_path }}</strong></div>
                  <div v-if="appRuntime.data_dir" class="kv"><span>统一存储根目录</span><strong :title="appRuntime.data_dir" class="mono">{{ appRuntime.data_dir }}</strong></div>
                  <div v-if="appRuntime.cache_dir" class="kv"><span>缓存目录</span><strong :title="appRuntime.cache_dir" class="mono">{{ appRuntime.cache_dir }}</strong></div>
                  <div v-if="appRuntime.temp_dir" class="kv"><span>临时目录</span><strong :title="appRuntime.temp_dir" class="mono">{{ appRuntime.temp_dir }}</strong></div>
                  <div class="kv"><span>GPU / CUDA</span><strong :title="gpuLabel(appDiagnostics)">{{ gpuLabel(appDiagnostics) }}</strong></div>
                  <p v-if="appDiagnostics.gpu?.action" class="inline-note is-error">{{ appDiagnostics.gpu.action }}</p>
                  <div v-if="appDiagnostics.disk" class="kv"><span>可用磁盘空间</span><strong>{{ formatBytes(appDiagnostics.disk.free_bytes) }}</strong></div>
                  <div v-if="appRuntime.logs_dir" class="kv"><span>日志目录</span><strong :title="appRuntime.logs_dir" class="mono">{{ appRuntime.logs_dir }}</strong></div>
                  <div v-if="appRuntime.font_root" class="kv"><span>字体目录</span><strong :title="appRuntime.font_root" class="mono">{{ appRuntime.font_root }}</strong></div>
                  <div v-if="appDiagnostics.checks?.length" class="runtime-check-list">
                    <div v-for="check in appDiagnostics.checks" :key="check.id" class="kv">
                      <span>{{ check.label }}</span>
                      <strong :class="'runtime-' + diagnosticTone(check.status)" :title="check.message">{{ check.message || check.status }}</strong>
                    </div>
                  </div>
                  <div class="inline-actions">
                    <button class="btn btn-secondary" :disabled="runtimeLoading || !!advBusy" @click="loadRuntimeData({ silent: false })">
                      <span v-if="runtimeLoading" class="spin" />
                      <template v-else>重新检查</template>
                    </button>
                    <button class="btn btn-secondary" :disabled="!!advBusy" @click="openDirectory('open-data-directory')">打开统一数据目录</button>
                    <button class="btn btn-secondary" :disabled="!!advBusy" @click="openDirectory('open-logs')">打开日志目录</button>
                    <button class="btn btn-secondary" :disabled="!!advBusy" @click="exportDiagnostics">导出诊断包</button>
                  </div>
                  <div v-if="hasLegacyData(migration)" class="migration-box">
                    <div class="kv"><span>旧版数据</span><strong>{{ migration.status === 'skipped' ? '已跳过，可重新处理' : '检测到可迁移数据' }}</strong></div>
                    <p class="inline-note">迁移会先复制并验证旧项目；只有明确选择“迁移并清理”才会删除本应用确认拥有的旧目录。</p>
                    <div class="inline-actions">
                      <button class="btn btn-secondary" :disabled="!!advBusy" @click="migrateLegacy('migrate')">迁移并保留原目录</button>
                      <button class="btn btn-danger" :disabled="!!advBusy" @click="migrateLegacy('migrate_clean')">迁移并清理旧目录</button>
                      <button class="btn btn-ghost" :disabled="!!advBusy" @click="migrateLegacy('skip')">跳过本次迁移</button>
                    </div>
                    <p v-if="migration.cleanup?.failed_paths?.length" class="inline-note is-error">仍有目录未能清理：{{ migration.cleanup.failed_paths.join('、') }}</p>
                  </div>
                </template>
              </div>
            </section>
            <section v-for="group in formGroups" :id="`settings-${group.id}`" :key="group.id" class="settings-group">
              <div class="group-head"><h3>{{ group.label }}</h3></div>
              <div class="group-body">
                <SettingsFields :draft="draft" :keys="group.keys" :configured-secrets="settings.configured_secrets" :fonts="fonts" :clear-busy="clearingSecret" @clear-secret="clearSecret" />
                <template v-if="group.id === 'translation'">
                  <div class="inline-actions">
                    <button class="btn btn-secondary" :disabled="validating || saving || clearing" @click="validateService">{{ validating ? '验证中…' : '验证当前配置' }}</button>
                    <button class="btn btn-ghost" :disabled="!dirty || saving || clearing" @click="resetDraft">放弃改动</button>
                  </div>
                  <p class="inline-note">验证会使用当前填写的配置向所选服务发送测试文本。密钥保存在本机，留空保留已有密钥。</p>
                </template>
                <template v-if="group.id === 'cleanup'">
                  <div class="inline-actions">
                    <button class="btn btn-ghost" type="button" :disabled="saving || clearing" @click="resetAdvancedErasePrompt">恢复默认 Prompt</button>
                  </div>
                  <p class="inline-note">在线清理与在线擦除会把所需图片发送给你选择的服务商；本地 LaMa 不需要在线擦除密钥。在线擦除目前使用 Ark / Seedream。</p>
                </template>
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
                  <button class="btn btn-secondary" :disabled="fontsLoading" @click="loadFonts({ forceRefresh: true })">
                    <span v-if="fontsLoading" class="spin" />
                    <template v-else>刷新字库</template>
                  </button>
                  <button class="btn btn-secondary" :disabled="!!advBusy" @click="openDirectory('open-user-fonts')">打开字库文件夹</button>
                </div>
                <p v-if="fontsError" class="inline-note is-error">{{ fontsError }}</p>
                <p v-else class="inline-note">共 {{ fonts.length }} 款字体</p>
                <div v-if="fonts.length" class="font-list">
                  <div v-for="font in fonts" :key="font.id" class="mark-item">
                    <span class="tag" :class="{ 'is-accent': ['custom', 'project', 'user'].includes(font.source) }">{{ fontSourceLabel(font.source) }}</span>
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
                      <button v-if="!remoteDiag?.active" class="btn btn-secondary" :disabled="!!advBusy" @click="startRemoteDiag">
                        <span v-if="advBusy === 'diag-start'" class="spin" />
                        <template v-else>开启</template>
                      </button>
                      <button v-else class="btn btn-secondary" :disabled="!!advBusy" @click="stopRemoteDiag">
                        <span v-if="advBusy === 'diag-stop'" class="spin" />
                        <template v-else>关闭</template>
                      </button>
                      <button v-if="remoteDiag?.active" class="btn btn-ghost" :disabled="!!advBusy" @click="copyRemoteDiag">复制连接信息</button>
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
                      <button class="btn btn-ghost" :disabled="!!advBusy || !remoteExec?.enabled" @click="copyRemoteExec">复制连接信息</button>
                      <button class="btn btn-secondary" :disabled="!!advBusy || !remoteExec?.enabled" @click="remoteExecAction('rotate-token')">轮换令牌</button>
                      <button class="btn btn-danger" :disabled="!!advBusy || !remoteExec?.enabled" @click="remoteExecAction('disable')">禁用</button>
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
