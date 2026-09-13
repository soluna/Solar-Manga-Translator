<script setup>
import TaskBar from './components/TaskBar.vue'
import { isMockMode } from './api/client.js'
</script>

<template>
  <div class="app-shell">
    <div v-if="isMockMode" class="demo-notice" role="status">演示模式 · 合成数据供浏览，保存、导入、处理和导出需连接真实后端。</div>
    <TaskBar />
    <div class="route-host">
      <router-view v-slot="{ Component }"><component :is="Component" /></router-view>
    </div>
  </div>
</template>

<style>
.app-shell { height: 100dvh; display: flex; flex-direction: column; min-height: 0; }
.demo-notice { flex: none; padding: 6px 16px; background: var(--bg-elevated); color: var(--text-1); border-bottom: 1px solid var(--border-strong); font-size: 12px; }
.route-host { flex: 1; min-height: 0; display: flex; }
.route-host > .app { width: 100%; height: 100%; min-height: 0; }
</style>
