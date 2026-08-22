import { readonly, ref } from 'vue'

import { login as apiLogin, logout as apiLogout } from '@/api/controlPlane'

const authenticated = ref(false)

export function useAuth() {
  async function login(email: string, password: string) {
    await apiLogin(email, password)
    authenticated.value = true
  }

  function logout() {
    apiLogout()
    authenticated.value = false
  }

  return { authenticated: readonly(authenticated), login, logout }
}
