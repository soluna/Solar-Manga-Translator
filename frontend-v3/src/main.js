import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router.js'
import './assets/theme.css'
import './assets/app.css'

// ---- 主题（深色优先，可切换，记忆在 localStorage）----
const THEME_KEY = 'solar-theme'
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme
}
try {
  const stored = window.localStorage.getItem(THEME_KEY)
  applyTheme(stored === 'light' ? 'light' : 'dark')
} catch {
  applyTheme('dark')
}
export function toggleTheme() {
  const next = document.documentElement.dataset.theme === 'light' ? 'dark' : 'light'
  applyTheme(next)
  try {
    window.localStorage.setItem(THEME_KEY, next)
  } catch {
    /* ignore */
  }
  return next
}

createApp(App).use(router).mount('#app')