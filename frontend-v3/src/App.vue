<script setup>
// Solar · InkStage — 全局外壳：仅挂载路由视图；主题切换由各页顶栏调用 main.js 的 toggleTheme。
</script>

<template>
  <router-view v-slot="{ Component }">
    <component :is="Component" />
  </router-view>
</template>