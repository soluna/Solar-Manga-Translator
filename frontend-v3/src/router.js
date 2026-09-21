import { createRouter, createWebHashHistory } from 'vue-router'
import { apiGetJson } from './api/client.js'
import {
  EMPTY_DIAGNOSTICS,
  EMPTY_RUNTIME,
  needsOnboarding,
  normalizeDiagnostics,
  normalizeRuntime,
} from './state/app-runtime.js'
import { createStartupOnboardingGuard } from './state/onboarding-navigation.js'

/**
 * Probe the same persisted state used by the desktop shell on a startup home
 * route. Project deep links never call this gate.
 */
export async function shouldAutoOpenOnboarding() {
  const bridgeRuntime = typeof window !== 'undefined' ? window.mangaDesktop?.runtime || {} : {}
  try {
    const runtimePayload = await apiGetJson('/api/app/runtime', '读取应用运行环境失败')
    const runtime = normalizeRuntime(runtimePayload, { ...EMPTY_RUNTIME, ...bridgeRuntime })
    if (!runtime.desktop_mode) return false
    const [settingsResult, diagnosticsResult] = await Promise.allSettled([
      apiGetJson('/api/app/settings', '读取设置失败'),
      apiGetJson('/api/app/diagnostics', '读取运行环境诊断失败'),
    ])
    if (settingsResult.status !== 'fulfilled') return false
    const settings = settingsResult.value?.settings || {}
    const diagnostics = diagnosticsResult.status === 'fulfilled'
      ? normalizeDiagnostics(diagnosticsResult.value, EMPTY_DIAGNOSTICS)
      : EMPTY_DIAGNOSTICS
    return needsOnboarding({
      desktopMode: true,
      runtime,
      settings,
      diagnostics,
    })
  } catch {
    // A browser without a backend must continue to the normal home page. The
    // explicit /onboarding route remains available when the user asks for it.
    return false
  }
}

const routes = [
  { path: '/', name: 'home', component: () => import('./views/HomeView.vue') },
  { path: '/pages/:sessionId', name: 'pages', component: () => import('./views/PagesView.vue') },
  { path: '/review/:sessionId/:pageId', name: 'review', component: () => import('./views/ReviewView.vue') },
  { path: '/erase/:sessionId/:pageId', name: 'erase', component: () => import('./views/EraseView.vue') },
  { path: '/projects', name: 'projects', component: () => import('./views/ProjectsView.vue') },
  { path: '/glossary/:sessionId', name: 'glossary', component: () => import('./views/GlossaryView.vue') },
  { path: '/settings', name: 'settings', component: () => import('./views/SettingsView.vue') },
  { path: '/onboarding', name: 'onboarding', component: () => import('./views/OnboardingView.vue') },
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach(createStartupOnboardingGuard(shouldAutoOpenOnboarding))
