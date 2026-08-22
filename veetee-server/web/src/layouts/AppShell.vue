<script setup lang="ts">
import { ref } from 'vue'
import AppFooter from '@/components/AppFooter.vue'
import AppHeader, { type ConsoleTab } from '@/components/AppHeader.vue'
import AgentsView from '@/views/AgentsView.vue'
import ProvidersView from '@/views/ProvidersView.vue'
import KnowledgeView from '@/views/KnowledgeView.vue'
import CorrectionsView from '@/views/CorrectionsView.vue'
import ToolsDeviceMcpView from '@/views/ToolsDeviceMcpView.vue'
import OtaView from '@/views/OtaView.vue'
import UsersView from '@/views/UsersView.vue'
import SettingsQuotaView from '@/views/SettingsQuotaView.vue'
import AuditView from '@/views/AuditView.vue'

const activeTab = ref<ConsoleTab>('agents')
</script>

<template>
  <div class="app-shell">
    <AppHeader :active-tab="activeTab" @select-tab="activeTab = $event" />
    <AgentsView v-if="activeTab === 'agents'" />
    <ProvidersView v-else-if="activeTab === 'providers'" />
    <KnowledgeView v-else-if="activeTab === 'knowledge'" />
    <CorrectionsView v-else-if="activeTab === 'corrections'" />
    <ToolsDeviceMcpView v-else-if="activeTab === 'tools'" />
    <OtaView v-else-if="activeTab === 'ota'" />
    <UsersView v-else-if="activeTab === 'users'" />
    <SettingsQuotaView v-else-if="activeTab === 'settings-quota'" />
    <AuditView v-else-if="activeTab === 'audit'" />
    <AppFooter />
  </div>
</template>
