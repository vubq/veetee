<script setup lang="ts">
import { ref } from "vue";
import { useI18n } from "vue-i18n";

import { ApiError, managerApi } from "../api/client";
import { useAuthStore } from "../stores/auth";
import { VtBrandMark, VtButton, VtField, VtInput, VtThemeSelector } from "./ui";

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
        <VtBrandMark size="lg" />
        <span><b>veetee</b><small>{{ t("brand.operations") }}</small></span>
      </a>
      <div>
        <span class="eyebrow">{{ t("login.eyebrow") }}</span>
        <h1>{{ t("login.headingLead") }}<br /><em>{{ t("login.headingAccent") }}</em></h1>
        <p>{{ t("login.description") }}</p>
      </div>
      <div class="login-stack">
        <span><i></i> Manager API <b>{{ managerApi.baseUrl }}</b></span>
        <span><i></i> {{ t("login.voiceLoop") }} <b>Silero → Zipformer → CLIProxyAPI → VieNeu</b></span>
      </div>
    </section>

    <form class="login-card" @submit.prevent="submit">
      <div class="login-card-heading">
        <span class="modal-kicker">{{ t("login.workspaceAccess") }}</span>
        <VtThemeSelector />
      </div>
      <h2>{{ t("login.title") }}</h2>
      <p>{{ t("login.security") }}</p>
      <VtField :label="t('login.email')" required>
        <VtInput v-model="email" type="email" autocomplete="username" required />
      </VtField>
      <VtField :label="t('login.password')" required>
        <VtInput v-model="password" type="password" autocomplete="current-password" minlength="8" required />
      </VtField>
      <VtField :label="t('login.workspace')" :hint="t('login.workspaceHint')">
        <VtInput v-model="tenantSlug" autocomplete="organization" />
      </VtField>
      <p v-if="errorMessage" class="form-error" role="alert">{{ errorMessage }}</p>
      <VtButton type="submit" :busy="auth.busy">
        {{ auth.busy ? t("login.submitting") : t("login.submit") }}
      </VtButton>
      <small>{{ t("login.sourceNotice") }}</small>
    </form>
  </main>
</template>
