<script setup>
import { toggleTheme } from '../main.js'
</script>

<template>
  <button class="icon-btn" type="button" title="切换深浅主题" @click="toggleTheme">
    <span class="theme-icon">🌙</span>
  </button>
</template>

<style scoped>
.theme-icon {
  font-size: 15px;
  display: inline-block;
  vertical-align: middle;
}
</style>