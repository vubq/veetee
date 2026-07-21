import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { managerApi } from "../api/client";
import type { Principal } from "../api/schemas";

export const useAuthStore = defineStore("auth", () => {
  const principal = ref<Principal | null>(null);
  const initialized = ref(false);
  const busy = ref(false);

  const authenticated = computed(() => principal.value !== null);

  managerApi.setUnauthorizedHandler(() => {
    principal.value = null;
  });

  async function initialize(): Promise<void> {
    if (initialized.value) return;
    busy.value = true;
    try {
      principal.value = managerApi.hasAccessToken()
        ? await managerApi.me()
        : await managerApi.refresh();
    } catch {
      managerApi.clearAccessToken();
      principal.value = null;
    } finally {
      busy.value = false;
      initialized.value = true;
    }
  }

  async function login(email: string, password: string, tenantSlug?: string): Promise<void> {
    busy.value = true;
    try {
      const pair = await managerApi.login(email, password, tenantSlug);
      principal.value = pair.principal;
    } finally {
      busy.value = false;
    }
  }

  async function logout(): Promise<void> {
    busy.value = true;
    try {
      await managerApi.logout();
    } finally {
      principal.value = null;
      busy.value = false;
    }
  }

  return { principal, initialized, busy, authenticated, initialize, login, logout };
});
