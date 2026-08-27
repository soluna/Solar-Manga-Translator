<script setup>
/**
 * 设置 — 分组设置表单 + 服务验证 + 高级运维
 * GET   /api/app/settings            → { settings: {...} }（按 key 通用渲染，未知字段进「其他设置」）
 * PATCH /api/app/settings            → 仅提交改动字段 → { settings }
 * POST  /api/app/settings/validate   → 校验翻译服务
 * GET   /api/fonts                   → { fonts: [{name, source, url}] }
 * 高级：remote-diagnostics start|stop / remote-execution enable|rotate-token|disable
 *       open-logs / open-data-directory / open-user-fonts / logs/tail / diagnostics/export
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { apiFetch, apiGetJson, apiPatchJson, apiPostJson } from '../api/client.js'
import { dismiss, toast, toastError, toasts } from '../composables/useToast.js'
import ThemeToggle from '../components/ThemeToggle.vue'

const router = useRouter()

// ---- 数据 ----
const loading = ref(true)
const loadError = ref('')
const settings = ref({}) // 服务端版本（脏检查基准）
const draft = reactive({}) // 编辑副本
const saving = ref(false)
const validating = ref(false)

const fonts = ref([])
const fontsLoading = ref(false)
const fontsError = ref('')

// ---- 分组定义（顺序即导航顺序）----
const GROUPS = [
  {
    id: 'translation',
    label: '翻译服务',
    desc: '控制翻译引擎、模型与密钥',
    keys: ['translation_service', 'translation_provider', 'translation_model', 'translation_api_key', 'language'],
  },
  {
    id: 'detect',
    label: '检测与识别',
    desc: '文字检测与 OCR 相关参数',
    keys: ['detect_model', 'ocr'],
  },
  {
    id: 'render',
    label: '渲染与嵌字',
    desc: '嵌字渲染与输出格式',
    keys: ['rendering_backend', 'rerender_output_format', 'font_family', 'render_font_family'],
  },
  { id: 'fonts', label: '字体', desc: '本机可用字库' },
  { id: 'advanced', label: '高级', desc: '远程诊断 / 运维操作' },
]

const KNOWN_KEYS = new Set(GROUPS.flatMap((g) => g.keys || []))

const FIELD_LABELS = {
  translation_service: '翻译引擎',
  translation_provider: '服务 Provider',
  translation_model: '模型名称',
  translation_api_key: 'API Key',
  language: '目标语言',
  detect_model: '检测模型',
  ocr: 'OCR 引擎',
  rendering_backend: '渲染后端',
  rerender_output_format: '结果输出格式',
  font_family: '默认字体',
  render_font_family: '嵌字字体',
}

const PROVIDER_OPTIONS = ['gemini', 'openai', 'deepseek', 'doubao', 'groq', 'claude', 'custom_openai']
const SELECT_OPTIONS = {
  translation_service: PROVIDER_OPTIONS,
  translation_provider: PROVIDER_OPTIONS,
  language: ['简体中文', '繁体中文', '英语', '日语', '韩语'],
  rerender_output_format: ['webp', 'png', 'jpg'],
  rendering_backend: ['manga-translator', 'opencv'],
}

// ---- 加载 ----
async function loadSettings() {
  loading.value = true
  loadError.value = ''
  try {
    const payload = await apiGetJson('/api/app/settings', '读取设置失败')
    const next = payload?.settings && typeof payload.settings === 'object' ? payload.settings : {}
    settings.value = next
    syncDraft(next)
  } catch (err) {
    loadError.value = err.message || '读取设置失败'
  } finally {
    loading.value = false
  }
}

function syncDraft(source) {
  Object.keys(draft).forEach((k) => delete draft[k])
  Object.entries(source).forEach(([k, v]) => {
    draft[k] = v ?? ''
  })
}

async function loadFonts({ silent = false } = {}) {
  if (!silent) fontsLoading.value = true
  fontsError.value = ''
  try {
    const payload = await apiGetJson('/api/fonts', '读取字体列表失败')
    fonts.value = Array.isArray(payload.fonts) ? payload.fonts : []
  } catch (err) {
    fontsError.value = err.message || '读取字体列表失败'
  } finally {
    fontsLoading.value = false
  }
}

onMounted(() => {
  loadSettings()
  loadFonts({ silent: true })
})

// ---- 字段渲染规则 ----
function labelOf(key) {
  return FIELD_LABELS[key] || key
}

function optionsOf(key) {
  const base = SELECT_OPTIONS[key]
  if (!base) return null
  const current = String(draft[key] ?? '')
  return current && !base.includes(current) ? [...base, current] : base
}

function isSecret(key) {
  return /api[_-]?key|token|secret/i.test(key)
}

function isBoolean(key) {
  return typeof settings.value[key] === 'boolean'
}

function isNumber(key) {
  return typeof settings.value[key] === 'number'
}

function isLongText(key) {
  return typeof settings.value[key] === 'string' && settings.value[key].length > 60
}

function isWide(key) {
  return isSecret(key) || isLongText(key) || /endpoint|url|prompt/i.test(key)
}

function groupFields(groupId) {
  const group = GROUPS.find((g) => g.id === groupId)
  const keys = Object.keys(settings.value)
  if (groupId === 'other') {
    return keys.filter((k) => !KNOWN_KEYS.has(k))
  }
  if (!group?.keys) return []
  return group.keys.filter((k) => keys.includes(k))
}

const otherKeys = computed(() => Object.keys(settings.value).filter((k) => !KNOWN_KEYS.has(k)))

const navGroups = computed(() => {
  const list = GROUPS.map((g) => ({ id: g.id, label: g.label }))
  if (otherKeys.value.length) {
    list.splice(list.length - 1, 0, { id: 'other', label: '其他设置' })
  }
  return list
})

// ---- 脏检查与保存 ----
const changedKeys = computed(() => {
  const keys = new Set([...Object.keys(settings.value), ...Object.keys(draft)])
  return [...keys].filter((k) => !isEqual(draft[k], settings.value[k]))
})

function isEqual(a, b) {
  return String(a ?? '') === String(b ?? '')
}

const dirty = computed(() => changedKeys.value.length > 0)

async function saveSettings() {
  if (saving.value || !dirty.value) return
  saving.value = true
  try {
    const patch = {}
    changedKeys.value.forEach((k) => {
      patch[k] = draft[k]
    })
    const payload = await apiPatchJson('/api/app/settings', patch, '保存设置失败')
    if (payload?.settings && typeof payload.settings === 'object') {
      settings.value = payload.settings
      syncDraft(payload.settings)
    } else {
      settings.value = { ...settings.value, ...patch }
    }
    toast('设置已保存。', 'success')
  } catch (err) {
    toastError(err)
  } finally {
    saving.value = false
  }
}

function resetDraft() {
  syncDraft(settings.value)
}

// ---- 翻译服务验证 ----
async function validateService() {
  if (validating.value) return
  validating.value = true
  try {
    const payload = await apiPostJson('/api/app/settings/validate', {}, '验证翻译服务失败')
    const ok = payload?.ok ?? payload?.valid ?? true
    const message = payload?.message || payload?.detail || ''
    const issues = Array.isArray(payload?.issues) ? payload.issues : []
    if (ok === false || issues.length) {
      const text = [message, ...issues.map((i) => (typeof i === 'string' ? i : i?.message))].filter(Boolean).join('；')
      toast(text || '验证未通过，请检查翻译服务配置。', 'error', 6000)
    } else {
      toast(message || '翻译服务验证通过。', 'success')
    }
  } catch (err) {
    toastError(err)
  } finally {
    validating.value = false
  }
}

// ---- 字体组操作 ----
function fontSourceLabel(source) {
  const map = { system: '系统', user: '自定义', bundled: '内置' }
  return map[source] || source || '未知'
}

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
            <!-- 翻译服务 -->
            <section class="settings-group" :id="'settings-translation'">
              <div class="group-head">
                <h3>翻译服务</h3>
                <span>控制翻译引擎、模型与密钥</span>
              </div>
              <div class="group-body">
                <div class="form-grid">
                  <template v-for="key in groupFields('translation')" :key="key">
                    <label v-if="!isBoolean(key)" class="field" :class="{ span2: isWide(key) }">
                      <span>{{ labelOf(key) }}</span>
                      <select v-if="optionsOf(key)" v-model="draft[key]">
                        <option v-for="opt in optionsOf(key)" :key="opt" :value="opt">{{ opt }}</option>
                      </select>
                      <input v-else-if="isSecret(key)" v-model="draft[key]" type="password" autocomplete="off" placeholder="粘贴你的 API Key" />
                      <input v-else-if="isNumber(key)" v-model.number="draft[key]" type="number" />
                      <textarea v-else-if="isLongText(key)" v-model="draft[key]" rows="3" />
                      <input v-else v-model="draft[key]" type="text" />
                    </label>
                    <label v-else class="check-row">
                      <input v-model="draft[key]" type="checkbox" />
                      <span>{{ labelOf(key) }}</span>
                    </label>
                  </template>
                </div>
                <div class="inline-actions">
                  <button class="btn btn-secondary" :disabled="validating" @click="validateService">
                    <span v-if="validating" class="spin" />
                    <template v-else>验证</template>
                  </button>
                  <button class="btn btn-ghost" :disabled="!dirty" @click="resetDraft">放弃改动</button>
                </div>
                <p class="inline-note">验证使用当前已保存的配置；请先保存再验证新填写的密钥。</p>
              </div>
            </section>

            <!-- 检测与识别 -->
            <section class="settings-group" :id="'settings-detect'">
              <div class="group-head">
                <h3>检测与识别</h3>
                <span>文字检测与 OCR 相关参数</span>
              </div>
              <div class="group-body">
                <div class="form-grid">
                  <template v-for="key in groupFields('detect')" :key="key">
                    <label v-if="!isBoolean(key)" class="field" :class="{ span2: isWide(key) }">
                      <span>{{ labelOf(key) }}</span>
                      <select v-if="optionsOf(key)" v-model="draft[key]">
                        <option v-for="opt in optionsOf(key)" :key="opt" :value="opt">{{ opt }}</option>
                      </select>
                      <input v-else-if="isNumber(key)" v-model.number="draft[key]" type="number" />
                      <input v-else v-model="draft[key]" type="text" />
                    </label>
                    <label v-else class="check-row">
                      <input v-model="draft[key]" type="checkbox" />
                      <span>{{ labelOf(key) }}</span>
                    </label>
                  </template>
                </div>
                <p v-if="!groupFields('detect').length" class="inline-note">后端未返回检测与识别相关字段。</p>
              </div>
            </section>

            <!-- 渲染与嵌字 -->
            <section class="settings-group" :id="'settings-render'">
              <div class="group-head">
                <h3>渲染与嵌字</h3>
                <span>嵌字渲染与输出格式</span>
              </div>
              <div class="group-body">
                <div class="form-grid">
                  <template v-for="key in groupFields('render')" :key="key">
                    <label v-if="!isBoolean(key)" class="field" :class="{ span2: isWide(key) }">
                      <span>{{ labelOf(key) }}</span>
                      <select v-if="optionsOf(key)" v-model="draft[key]">
                        <option v-for="opt in optionsOf(key)" :key="opt" :value="opt">{{ opt }}</option>
                      </select>
                      <input v-else-if="isNumber(key)" v-model.number="draft[key]" type="number" />
                      <input v-else v-model="draft[key]" type="text" />
                    </label>
                    <label v-else class="check-row">
                      <input v-model="draft[key]" type="checkbox" />
                      <span>{{ labelOf(key) }}</span>
                    </label>
                  </template>
                </div>
                <p v-if="!groupFields('render').length" class="inline-note">后端未返回渲染相关字段。</p>
              </div>
            </section>

            <!-- 其他设置（未知字段原样展示） -->
            <section v-if="otherKeys.length" class="settings-group" :id="'settings-other'">
              <div class="group-head">
                <h3>其他设置</h3>
                <span>后端返回的其余字段</span>
              </div>
              <div class="group-body">
                <div class="form-grid">
                  <template v-for="key in otherKeys" :key="key">
                    <label v-if="!isBoolean(key)" class="field" :class="{ span2: isWide(key) }">
                      <span>{{ labelOf(key) }}</span>
                      <input v-if="isSecret(key)" v-model="draft[key]" type="password" autocomplete="off" />
                      <input v-else-if="isNumber(key)" v-model.number="draft[key]" type="number" />
                      <textarea v-else-if="isLongText(key)" v-model="draft[key]" rows="3" />
                      <input v-else v-model="draft[key]" type="text" />
                    </label>
                    <label v-else class="check-row">
                      <input v-model="draft[key]" type="checkbox" />
                      <span>{{ labelOf(key) }}</span>
                    </label>
                  </template>
                </div>
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
                  <div v-for="font in fonts" :key="`${font.source}:${font.name}`" class="mark-item">
                    <span class="tag" :class="{ 'is-accent': font.source === 'user' }">{{ fontSourceLabel(font.source) }}</span>
                    <span>{{ font.name }}</span>
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
