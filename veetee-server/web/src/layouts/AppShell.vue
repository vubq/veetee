<script setup lang="ts">
import AppFooter from '@/components/AppFooter.vue'
import AppHeader from '@/components/AppHeader.vue'
import AgentsView from '@/views/AgentsView.vue'
</script>

<template>
  <div class="app-shell">
    <AppHeader />
    <AgentsView />
    <AppFooter />
  </div>
</template>
