import { computed, reactive, ref } from 'vue'

export function useDashboard() {
  const loading = ref(false)
  const busy = ref(false)
  const authRequired = ref(false)
  const managerToken = ref('')
  const loginError = ref('')
  const loginBusy = ref(false)
  const health = ref(null)
  const diagnostics = ref(null)
  const assistants = ref([])
  const devices = ref([])
  const pendingDevices = ref([])
  const runtime = ref({})
  const restartRequired = ref(false)
  const voices = ref([])
  const models = ref([])
  const toast = reactive({ text: '', tone: 'info' })
  let toastTimer = null

  const assistantDraft = reactive({ id: '', name: '', base_prompt: '', voice: '', model: '', enabled: true })
  const pairing = reactive({ code: '', assistant_id: '', name: '', owner_id: '' })
  const runtimeDraft = reactive({
    GROQ_API_KEY_A: '', GROQ_API_KEY_B: '', GROQ_API_KEY_C: '', GROQ_API_KEY_D: '',
    DEEPGRAM_API_KEY: '', HF_TOKEN: '', 'llm.model': '', 'tts.voice': '', 'asr.device': '',
    'server.barge_in_policy': '',
  })

  const pairedActiveDevices = computed(() => devices.value.filter(device => !device.revoked))
  const onlineDevices = computed(() => devices.value.filter(device => device.online && !device.revoked))

  function notify(text, tone = 'info') {
    toast.text = text
    toast.tone = tone
    clearTimeout(toastTimer)
    toastTimer = setTimeout(() => { toast.text = '' }, 3600)
  }

  function fmtTime(value) {
    if (!value) return '—'
    const date = new Date(Number(value) * 1000)
    return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString('vi-VN')
  }

  function publicRuntimeValue(key, fallback = '') {
    const value = runtime.value?.[key]
    if (value && typeof value === 'object') return ''
    return value ?? fallback
  }

  function secretPlaceholder(key) {
    const value = runtime.value?.[key]
    return value && typeof value === 'object'
      ? (value.masked || (value.configured ? 'Đã cấu hình' : 'Chưa cấu hình'))
      : 'Nhập secret mới'
  }

  async function api(url, options = {}) {
    const response = await fetch(url, {
      credentials: 'same-origin',
      cache: 'no-store',
      ...options,
      headers: {
        ...(options.body && !(options.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}),
        ...(options.headers || {}),
      },
    })
    if (response.status === 401) {
      authRequired.value = true
      throw new Error('AUTH_REQUIRED')
    }
    const type = response.headers.get('content-type') || ''
    const payload = type.includes('application/json') ? await response.json() : await response.text()
    if (!response.ok) throw new Error(payload?.error || `HTTP ${response.status}`)
    return payload
  }

  async function login() {
    if (!managerToken.value.trim()) return
    loginBusy.value = true
    loginError.value = ''
    try {
      const response = await fetch('/api/session', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { Authorization: `Bearer ${managerToken.value.trim()}` },
      })
      if (!response.ok) throw new Error('Token quản trị không hợp lệ')
      managerToken.value = ''
      authRequired.value = false
      await loadManagedData()
      notify('Đăng nhập quản trị thành công', 'success')
    } catch (error) {
      loginError.value = error.message || String(error)
    } finally {
      loginBusy.value = false
    }
  }

  async function loadPublicData() {
    try {
      const response = await fetch('/health', { cache: 'no-store' })
      if (response.ok) health.value = await response.json()
    } catch (_) {}
  }

  function hydrateRuntimeDraft() {
    runtimeDraft['llm.model'] = publicRuntimeValue('llm.model', diagnostics.value?.llm?.model || '')
    runtimeDraft['tts.voice'] = publicRuntimeValue('tts.voice', diagnostics.value?.tts?.voice || '')
    runtimeDraft['asr.device'] = publicRuntimeValue('asr.device', diagnostics.value?.asr?.device || '')
    runtimeDraft['server.barge_in_policy'] = publicRuntimeValue('server.barge_in_policy', diagnostics.value?.server?.barge_in_policy || 'client_only')
    for (const key of ['GROQ_API_KEY_A', 'GROQ_API_KEY_B', 'GROQ_API_KEY_C', 'GROQ_API_KEY_D', 'DEEPGRAM_API_KEY', 'HF_TOKEN']) runtimeDraft[key] = ''
  }

  async function loadManagedData() {
    loading.value = true
    try {
      const [diag, assistantsData, devicesData, pendingData, runtimeData, voiceData, modelData] = await Promise.all([
        api('/api/diagnostics'),
        api('/api/assistants'),
        api('/api/devices'),
        api('/api/devices/pending'),
        api('/api/runtime-config'),
        api('/api/voice'),
        api('/api/model'),
      ])
      diagnostics.value = diag
      assistants.value = assistantsData.assistants || []
      devices.value = (devicesData.devices || []).map(device => ({ ...device, last_seen_label: fmtTime(device.last_seen_at) }))
      pendingDevices.value = pendingData.pending || []
      runtime.value = runtimeData.values || {}
      restartRequired.value = Boolean(runtimeData.restart_required)
      voices.value = voiceData.voices || []
      models.value = modelData.models || []
      if (!pairing.assistant_id && assistants.value.length) pairing.assistant_id = assistants.value[0].id
      hydrateRuntimeDraft()
    } catch (error) {
      if (error.message !== 'AUTH_REQUIRED') notify(`Không tải được dữ liệu: ${error.message}`, 'error')
    } finally {
      loading.value = false
    }
  }

  async function refreshAll() {
    await loadPublicData()
    await loadManagedData()
  }

  function prepareNewAssistant() {
    Object.assign(assistantDraft, {
      id: '',
      name: '',
      base_prompt: '',
      voice: '',
      model: '',
      enabled: true,
    })
  }

  function prepareEditAssistant(item) {
    Object.assign(assistantDraft, {
      id: item.id,
      name: item.name || '',
      base_prompt: item.base_prompt || '',
      voice: item.voice || '',
      model: item.model || '',
      enabled: item.enabled !== false,
    })
  }

  async function saveAssistant() {
    if (!assistantDraft.name.trim()) {
      notify('Tên Assistant không được trống', 'error')
      return false
    }
    busy.value = true
    try {
      const body = JSON.stringify({
        name: assistantDraft.name.trim(),
        base_prompt: assistantDraft.base_prompt.trim(),
        voice: assistantDraft.voice,
        model: assistantDraft.model,
        enabled: assistantDraft.enabled,
      })
      if (assistantDraft.id) await api(`/api/assistants/${encodeURIComponent(assistantDraft.id)}`, { method: 'PATCH', body })
      else await api('/api/assistants', { method: 'POST', body })
      await loadManagedData()
      notify('Đã lưu Assistant', 'success')
      return true
    } catch (error) {
      notify(error.message, 'error')
      return false
    } finally {
      busy.value = false
    }
  }

  async function toggleAssistant(item) {
    try {
      await api(`/api/assistants/${encodeURIComponent(item.id)}`, {
        method: 'PATCH',
        body: JSON.stringify({ enabled: !item.enabled }),
      })
      await loadManagedData()
    } catch (error) {
      notify(error.message, 'error')
    }
  }

  async function deleteAssistant(item) {
    try {
      await api(`/api/assistants/${encodeURIComponent(item.id)}`, { method: 'DELETE' })
      await loadManagedData()
      notify('Đã xóa Assistant', 'success')
      return true
    } catch (error) {
      notify(error.message, 'error')
      return false
    }
  }

  async function pairDevice() {
    if (!/^\d{6}$/.test(pairing.code)) {
      notify('Mã pairing phải đúng 6 số', 'error')
      return false
    }
    if (!pairing.assistant_id) {
      notify('Hãy chọn Assistant', 'error')
      return false
    }
    busy.value = true
    try {
      await api('/api/devices/pair', { method: 'POST', body: JSON.stringify(pairing) })
      pairing.code = ''
      pairing.name = ''
      pairing.owner_id = ''
      await loadManagedData()
      notify('Pair thiết bị thành công', 'success')
      return true
    } catch (error) {
      notify(error.message, 'error')
      return false
    } finally {
      busy.value = false
    }
  }

  async function updateDevice(device, changes) {
    try {
      const path = `/api/devices/${encodeURIComponent(device.device_id)}/${encodeURIComponent(device.client_id)}`
      await api(path, { method: 'PATCH', body: JSON.stringify(changes) })
      await loadManagedData()
      notify('Đã cập nhật thiết bị', 'success')
    } catch (error) {
      notify(error.message, 'error')
    }
  }

  async function revokeDevice(device) {
    try {
      const path = `/api/devices/${encodeURIComponent(device.device_id)}/${encodeURIComponent(device.client_id)}/revoke`
      await api(path, { method: 'POST' })
      await loadManagedData()
      notify('Đã revoke thiết bị', 'success')
      return true
    } catch (error) {
      notify(error.message, 'error')
      return false
    }
  }

  async function saveRuntime() {
    const values = {}
    for (const key of ['GROQ_API_KEY_A', 'GROQ_API_KEY_B', 'GROQ_API_KEY_C', 'GROQ_API_KEY_D', 'DEEPGRAM_API_KEY', 'HF_TOKEN']) {
      if (runtimeDraft[key].trim()) values[key] = runtimeDraft[key].trim()
    }
    for (const key of ['llm.model', 'tts.voice', 'asr.device', 'server.barge_in_policy']) {
      if (runtimeDraft[key] !== '') values[key] = runtimeDraft[key]
    }
    busy.value = true
    try {
      const data = await api('/api/runtime-config', { method: 'PATCH', body: JSON.stringify({ values }) })
      runtime.value = data.values || {}
      restartRequired.value = Boolean(data.restart_required)
      hydrateRuntimeDraft()
      notify(restartRequired.value ? 'Đã lưu. Một số thay đổi cần restart server.' : 'Đã lưu và áp dụng.', 'success')
      return true
    } catch (error) {
      notify(error.message, 'error')
      return false
    } finally {
      busy.value = false
    }
  }

  return {
    loading, busy, authRequired, managerToken, loginError, loginBusy,
    health, diagnostics, assistants, devices, pendingDevices, runtime, restartRequired, voices, models,
    assistantDraft, pairing, runtimeDraft, toast,
    pairedActiveDevices, onlineDevices,
    notify, fmtTime, publicRuntimeValue, secretPlaceholder,
    login, loadPublicData, loadManagedData, refreshAll,
    prepareNewAssistant, prepareEditAssistant, saveAssistant, toggleAssistant, deleteAssistant,
    pairDevice, updateDevice, revokeDevice, saveRuntime,
  }
}
