<script setup lang="ts">
import { onMounted } from "vue";
import { useI18n } from "vue-i18n";

import LoginView from "./components/LoginView.vue";
import ManagerShell from "./components/ManagerShell.vue";
import { useAuthStore } from "./stores/auth";

const auth = useAuthStore();
const { t } = useI18n();
onMounted(() => auth.initialize());
</script>

<template>
  <div v-if="!auth.initialized" class="boot-screen" aria-live="polite">
    <span class="brand-mark" aria-hidden="true"><i></i><i></i></span>
    <b>veetee</b>
    <small>{{ t("boot") }}</small>
  </div>
  <ManagerShell v-else-if="auth.authenticated && auth.principal" />
  <LoginView v-else />
</template>
