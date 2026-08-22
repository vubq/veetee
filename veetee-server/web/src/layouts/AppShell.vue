<script setup lang="ts">
import { ref } from 'vue'
import AppFooter from '@/components/AppFooter.vue'
import AppHeader from '@/components/AppHeader.vue'
import AgentsView from '@/views/AgentsView.vue'
import OtaView from '@/views/OtaView.vue'

const activeTab = ref<'agents' | 'ota'>('agents')
</script>

<template>
  <div class="app-shell">
    <AppHeader :active-tab="activeTab" @select-tab="activeTab = $event" />
    <AgentsView v-if="activeTab === 'agents'" />
    <OtaView v-else-if="activeTab === 'ota'" />
    <AppFooter />
  </div>
</template>
