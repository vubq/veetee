<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";

import { ApiError, managerApi } from "../api/client";
import { useAuthStore } from "../stores/auth";

const auth = useAuthStore();
const { t } = useI18n();
const email = ref("");
const password = ref("");
const tenantSlug = ref("");
const errorMessage = ref("");

async function submit(): Promise<void> {
  errorMessage.value = "";
  try {
    await auth.login(email.value.trim(), password.value, tenantSlug.value.trim() || undefined);
  } catch (error) {
    errorMessage.value =
      error instanceof ApiError ? error.message : t("login.connectionError");
  }
}
</script>

<template>
  <main class="login-page">
    <div class="ambient ambient-one"></div>
    <div class="ambient ambient-two"></div>
    <section class="login-story">
      <a class="brand" href="#" aria-label="Veetee Manager">
        <span class="brand-mark" aria-hidden="true"><i></i><i></i></span>
        <span><b>veetee</b><small>robot operations</small></span>
      </a>
      <div>
        <span class="eyebrow">{{ t("login.eyebrow") }}</span>
        <h1>{{ t("login.headingLead") }}<br /><em>{{ t("login.headingAccent") }}</em></h1>
        <p>{{ t("login.description") }}</p>
      </div>
      <div class="login-stack">
        <span><i></i> Manager API <b>{{ managerApi.baseUrl }}</b></span>
        <span><i></i> Voice loop <b>Silero → Zipformer → 9Router → VieNeu</b></span>
      </div>
    </section>

    <form class="login-card" @submit.prevent="submit">
      <span class="modal-kicker">{{ t("login.workspaceAccess") }}</span>
      <h2>{{ t("login.title") }}</h2>
      <p>{{ t("login.security") }}</p>
      <label>{{ t("login.email") }}<input v-model="email" type="email" autocomplete="username" required /></label>
      <label>{{ t("login.password") }}<input v-model="password" type="password" autocomplete="current-password" minlength="8" required /></label>
      <label>{{ t("login.workspace") }} <small>{{ t("login.workspaceHint") }}</small><input v-model="tenantSlug" autocomplete="organization" /></label>
      <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>
      <button class="button button-primary" type="submit" :disabled="auth.busy">
        {{ auth.busy ? t("login.submitting") : t("login.submit") }}
      </button>
      <small>{{ t("login.sourceNotice") }}</small>
    </form>
  </main>
</template>
