import { createRouter, createWebHashHistory } from 'vue-router'

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