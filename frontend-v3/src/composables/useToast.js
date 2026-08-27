/** 极简 toast：模块级单例，任何组件可 push；由各视图自行渲染 <ToastStack />。 */
import { reactive } from 'vue'

export const toasts = reactive([])

let nextId = 1

export function toast(message, kind = 'info', timeout = 4200) {
  const id = nextId++
  toasts.push({ id, message: String(message || ''), kind })
  if (timeout > 0) {
    setTimeout(() => dismiss(id), timeout)
  }
  return id
}

export function dismiss(id) {
  const index = toasts.findIndex((item) => item.id === id)
  if (index >= 0) {
    toasts.splice(index, 1)
  }
}

export function toastError(error) {
  toast(error?.message || '操作失败', 'error', 6000)
}