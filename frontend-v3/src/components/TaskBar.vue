<script setup>
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useTaskEvents } from '../composables/useTaskEvents.js'
import { toastError, toast } from '../composables/useToast.js'
const route = useRoute()
const tasks = useTaskEvents()
const { taskState, busy, connected, sessionId } = tasks
const cancelling = ref(false)
const labels = { syncing: '正在同步', starting: '正在启动', running: '处理中', start: '处理中', progress: '处理中',
  'batch-next': '准备下一页', completed: '已完成', failed: '失败', error: '连接或任务失败', cancelled: '已取消', interrupted: '已中断' }
const actions = { detect: '识别', translate: '翻译', 'resume-translate': '继续翻译', 'translate-page': '本页翻译', rerender: '重新嵌字' }
const location = computed(() => taskState.value.activeTaskTargetStoredName
  ? `/review/${encodeURIComponent(sessionId.value)}/${encodeURIComponent(taskState.value.activeTaskTargetStoredName)}`
  : `/pages/${encodeURIComponent(sessionId.value)}`)
const progress = computed(() => {
  const value = taskState.value.progress
  return value?.total > 0 ? `${value.current || 0} / ${value.total}` : ''
})
watch(() => route.params.sessionId, id => { if (id) tasks.resume(String(id)) }, { immediate: true })
async function cancel() {
  if (cancelling.value) return
  cancelling.value = true
  try {
    const requested = await tasks.cancel()
    toast(requested ? '已请求取消，等待后台停止。' : '任务已发送，正在确认后台任务；确认后可取消。', requested ? 'ok' : 'warn')
  } catch (error) { toastError(error) }
  finally { cancelling.value = false }
}
</script>

<template>
  <div v-if="taskState.eventName" class="task-bar" role="status" aria-live="polite">
    <span :class="{ spin: busy }" aria-hidden="true"></span>
    <strong>{{ actions[taskState.activeAction] || '后台任务' }} · {{ labels[taskState.eventName] || '处理中' }}</strong>
    <span v-if="tasks.batch.value">第 {{ tasks.batch.value.completed + 1 }} / {{ tasks.batch.value.total }} 页</span>
    <span class="task-message">{{ taskState.statusMessage }} {{ progress }}</span>
    <router-link :to="location">返回任务项目</router-link>
    <button v-if="busy" class="btn btn-ghost btn-sm" :disabled="cancelling" aria-label="取消当前任务及后续批量页面" @click="cancel">{{ cancelling ? '正在取消…' : '取消' }}</button>
    <button v-if="!connected && taskState.eventName !== 'completed'" class="btn btn-ghost btn-sm" @click="tasks.resume(sessionId)">重新同步</button>
    <button v-if="!busy" class="icon-btn" aria-label="关闭任务状态" @click="tasks.reset()">×</button>
  </div>
</template>

<style scoped>
.task-bar { flex: none; min-height: 36px; display: flex; align-items: center; gap: 12px; padding: 5px 16px; border-bottom: 1px solid var(--border-strong); background: var(--bg-elevated); color: var(--text-2); font-size: 12px; }
.task-bar strong { flex: none; color: var(--text-1); }
.task-message { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.task-bar a { flex: none; }
</style>
