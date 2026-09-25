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
  const groqKeys = ref([{
    envKey: 'GROQ_API_KEY_1',
    value: '',
    configured: false,
    masked: '',
  }])
  const removedGroqKeys = new Set()
  const voices = ref([])
  const models = ref([])
  const toast = reactive({ text: '', tone: 'info' })
  let toastTimer = null

  const assistantDraft = reactive({ id: '', name: '', base_prompt: '', voice: '', model: '', enabled: true })
  const pairing = reactive({ code: '', assistant_id: '', name: '', owner_id: '' })
  const runtimeDraft = reactive({
    DEEPGRAM_API_KEY: '',
    HF_TOKEN: '',
    'llm.model': '',
    'tts.voice': '',
    'asr.device': '',
    'server.barge_in_policy': '',
    'latency.target_first_audio_ms': '600',
    'latency.first_token_timeout_ms': '1800',
    'latency.total_turn_timeout_ms': '15000',
    'asr.endpointing_ms': '250',
    'asr.min_silence_duration_ms': '192',
    'asr.speculative_inference_enabled': true,
    'asr.speculative_start_silence_ms': '64',
    'asr.speculative_min_confidence': '0.95',
    'asr.speculative_llm_enabled': false,
    'asr.speculative_llm_min_confidence': '0.95',
    'asr.min_speech_duration_ms': '160',
    'asr.speech_start_frames': '2',
    'asr.pre_speech_pad_ms': '512',
    'asr.vad_threshold': '0.5',
    'asr.vad_threshold_low': '0.3',
    'asr.vad_end_threshold': '0.3',
    'tts.send_ahead_ms': '120',
    'tts.stream_queue_max_chunks': '4',
    'tts.first_audio_priority_boost': '5',
    'tts.scheduler_aging_per_second': '2',
    'tts.admission_timeout_ms': '5000',
    'tts.speculative_prefetch_enabled': false,
    'llm.routing.headroom_pct': '10',
    'llm.routing.max_attempts': '2',
    'llm.routing.admission_wait_ms': '60',
    'llm.routing.discovery_wait_ms': '750',
    'llm.routing.discovery_max_inflight': '1',
    'llm.routing.inflight_penalty_s': '0.4',
    'llm.routing.latency_ewma_alpha': '0.3',
    'llm.routing.latency_jitter_penalty': '0.75',
    'memory.lookup_timeout_ms': '20',
    'conversation.history_turns': '6',
    'tools.max_parallel_read_only': '2',
    'tools.max_llm_rounds_per_turn': '3',
    'llm.speech_segmentation.min_segment_chars': '',
    'llm.speech_segmentation.clause_target_chars': '',
    'llm.speech_segmentation.clause_min_chars': '',
    'llm.speech_segmentation.first_clause_min_chars': '',
    'llm.speech_segmentation.first_clause_min_words': '',
    'llm.speech_segmentation.hard_max_segment_chars': '',
    'llm.speech_segmentation.hard_cut_search_back': '',
    'llm.speech_segmentation.hard_cut_search_forward': '',
    'llm.speech_segmentation.first_segment_min_chars': '',
    'llm.speech_segmentation.first_segment_min_words': '',
    'llm.speech_segmentation.first_soft_cut_chars': '',
    'llm.speech_segmentation.first_soft_cut_min_words': '',
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

  function nextGroqEnvKey() {
    const used = new Set([
      ...Object.keys(runtime.value || {}).filter(key => key.startsWith('GROQ_API_KEY_')),
      ...groqKeys.value.map(item => item.envKey),
      ...removedGroqKeys,
    ])
    let index = 1
    while (used.has(`GROQ_API_KEY_${index}`)) index += 1
    return `GROQ_API_KEY_${index}`
  }

  function newGroqKeyRow() {
    return {
      envKey: nextGroqEnvKey(),
      value: '',
      configured: false,
      masked: '',
    }
  }

  function hydrateRuntimeDraft() {
    runtimeDraft['llm.model'] = publicRuntimeValue('llm.model', diagnostics.value?.llm?.model || '')
    runtimeDraft['tts.voice'] = publicRuntimeValue('tts.voice', diagnostics.value?.tts?.voice || '')
    runtimeDraft['asr.device'] = publicRuntimeValue('asr.device', diagnostics.value?.asr?.device || '')
    runtimeDraft['server.barge_in_policy'] = publicRuntimeValue('server.barge_in_policy', diagnostics.value?.server?.barge_in_policy || 'client_only')
    runtimeDraft['latency.target_first_audio_ms'] = String(publicRuntimeValue(
      'latency.target_first_audio_ms',
      diagnostics.value?.profile?.latency?.target_first_audio_ms ?? 600,
    ))
    runtimeDraft['latency.first_token_timeout_ms'] = String(publicRuntimeValue(
      'latency.first_token_timeout_ms',
      diagnostics.value?.profile?.latency?.first_token_timeout_ms ?? 1800,
    ))
    runtimeDraft['latency.total_turn_timeout_ms'] = String(publicRuntimeValue(
      'latency.total_turn_timeout_ms',
      diagnostics.value?.profile?.latency?.total_turn_timeout_ms ?? 15000,
    ))
    runtimeDraft['asr.endpointing_ms'] = String(publicRuntimeValue(
      'asr.endpointing_ms',
      diagnostics.value?.profile?.asr?.endpointing_ms ?? 250,
    ))
    runtimeDraft['asr.min_silence_duration_ms'] = String(publicRuntimeValue(
      'asr.min_silence_duration_ms',
      diagnostics.value?.profile?.asr?.min_silence_duration_ms ?? 192,
    ))
    runtimeDraft['asr.speculative_inference_enabled'] = Boolean(publicRuntimeValue(
      'asr.speculative_inference_enabled',
      diagnostics.value?.profile?.asr?.speculative_inference_enabled ?? true,
    ))
    runtimeDraft['asr.speculative_start_silence_ms'] = String(publicRuntimeValue(
      'asr.speculative_start_silence_ms',
      diagnostics.value?.profile?.asr?.speculative_start_silence_ms ?? 64,
    ))
    runtimeDraft['asr.speculative_min_confidence'] = String(publicRuntimeValue(
      'asr.speculative_min_confidence',
      diagnostics.value?.profile?.asr?.speculative_min_confidence ?? 0.95,
    ))
    runtimeDraft['asr.speculative_llm_enabled'] = Boolean(publicRuntimeValue(
      'asr.speculative_llm_enabled',
      diagnostics.value?.profile?.asr?.speculative_llm_enabled ?? false,
    ))
    runtimeDraft['asr.speculative_llm_min_confidence'] = String(publicRuntimeValue(
      'asr.speculative_llm_min_confidence',
      diagnostics.value?.profile?.asr?.speculative_llm_min_confidence ?? 0.95,
    ))
    runtimeDraft['asr.min_speech_duration_ms'] = String(publicRuntimeValue(
      'asr.min_speech_duration_ms',
      diagnostics.value?.profile?.asr?.min_speech_duration_ms ?? 160,
    ))
    runtimeDraft['asr.speech_start_frames'] = String(publicRuntimeValue(
      'asr.speech_start_frames',
      diagnostics.value?.profile?.asr?.speech_start_frames ?? 2,
    ))
    runtimeDraft['asr.pre_speech_pad_ms'] = String(publicRuntimeValue(
      'asr.pre_speech_pad_ms',
      diagnostics.value?.profile?.asr?.pre_speech_pad_ms ?? 512,
    ))
    runtimeDraft['asr.vad_threshold'] = String(publicRuntimeValue(
      'asr.vad_threshold',
      diagnostics.value?.profile?.asr?.vad_threshold ?? 0.5,
    ))
    runtimeDraft['asr.vad_threshold_low'] = String(publicRuntimeValue(
      'asr.vad_threshold_low',
      diagnostics.value?.profile?.asr?.vad_threshold_low ?? 0.3,
    ))
    runtimeDraft['asr.vad_end_threshold'] = String(publicRuntimeValue(
      'asr.vad_end_threshold',
      diagnostics.value?.profile?.asr?.vad_end_threshold ?? 0.3,
    ))
    runtimeDraft['tts.send_ahead_ms'] = String(publicRuntimeValue(
      'tts.send_ahead_ms',
      diagnostics.value?.profile?.tts?.send_ahead_ms ?? 120,
    ))
    runtimeDraft['tts.stream_queue_max_chunks'] = String(publicRuntimeValue(
      'tts.stream_queue_max_chunks',
      diagnostics.value?.profile?.tts?.stream_queue_max_chunks ?? 4,
    ))
    runtimeDraft['tts.first_audio_priority_boost'] = String(publicRuntimeValue(
      'tts.first_audio_priority_boost',
      diagnostics.value?.profile?.tts?.first_audio_priority_boost ?? 5,
    ))
    runtimeDraft['tts.scheduler_aging_per_second'] = String(publicRuntimeValue(
      'tts.scheduler_aging_per_second',
      diagnostics.value?.profile?.tts?.scheduler_aging_per_second ?? 2,
    ))
    runtimeDraft['tts.admission_timeout_ms'] = String(publicRuntimeValue(
      'tts.admission_timeout_ms',
      diagnostics.value?.profile?.tts?.admission_timeout_ms ?? 5000,
    ))
    runtimeDraft['tts.speculative_prefetch_enabled'] = Boolean(publicRuntimeValue(
      'tts.speculative_prefetch_enabled',
      diagnostics.value?.profile?.tts?.speculative_prefetch_enabled ?? false,
    ))
    const routing = diagnostics.value?.profile?.routing || {}
    for (const [key, fallback] of Object.entries({
      'llm.routing.headroom_pct': routing.headroom_pct ?? 10,
      'llm.routing.max_attempts': routing.max_attempts ?? 2,
      'llm.routing.admission_wait_ms': routing.admission_wait_ms ?? 60,
      'llm.routing.discovery_wait_ms': routing.discovery_wait_ms ?? 750,
      'llm.routing.discovery_max_inflight': routing.discovery_max_inflight ?? 1,
      'llm.routing.inflight_penalty_s': routing.inflight_penalty_s ?? 0.4,
      'llm.routing.latency_ewma_alpha': routing.latency_ewma_alpha ?? 0.3,
      'llm.routing.latency_jitter_penalty': routing.latency_jitter_penalty ?? 0.75,
    })) {
      runtimeDraft[key] = String(publicRuntimeValue(key, fallback))
    }
    runtimeDraft['memory.lookup_timeout_ms'] = String(publicRuntimeValue(
      'memory.lookup_timeout_ms',
      diagnostics.value?.profile?.memory?.lookup_timeout_ms ?? 20,
    ))
    runtimeDraft['conversation.history_turns'] = String(publicRuntimeValue(
      'conversation.history_turns',
      diagnostics.value?.conversation?.history_turns ?? 6,
    ))
    runtimeDraft['tools.max_parallel_read_only'] = String(publicRuntimeValue(
      'tools.max_parallel_read_only',
      diagnostics.value?.profile?.tools?.max_parallel_read_only ?? 2,
    ))
    runtimeDraft['tools.max_llm_rounds_per_turn'] = String(publicRuntimeValue(
      'tools.max_llm_rounds_per_turn',
      diagnostics.value?.profile?.tools?.max_llm_rounds_per_turn ?? 3,
    ))
    const segmentation = diagnostics.value?.profile?.speech_segmentation || {}
    for (const key of [
      'min_segment_chars',
      'clause_target_chars',
      'clause_min_chars',
      'first_clause_min_chars',
      'first_clause_min_words',
      'hard_max_segment_chars',
      'hard_cut_search_back',
      'hard_cut_search_forward',
      'first_segment_min_chars',
      'first_segment_min_words',
      'first_soft_cut_chars',
      'first_soft_cut_min_words',
    ]) {
      const runtimeKey = `llm.speech_segmentation.${key}`
      runtimeDraft[runtimeKey] = String(publicRuntimeValue(
        runtimeKey,
        segmentation[key] ?? '',
      ))
    }
    runtimeDraft.DEEPGRAM_API_KEY = ''
    runtimeDraft.HF_TOKEN = ''

    removedGroqKeys.clear()
    const configuredRows = Object.entries(runtime.value || {})
      .filter(([key, value]) => key.startsWith('GROQ_API_KEY_') && value && typeof value === 'object')
      .sort(([a], [b]) => a.localeCompare(b, undefined, { numeric: true }))
      .map(([envKey, value]) => ({
        envKey,
        value: '',
        configured: Boolean(value.configured),
        masked: value.masked || '',
      }))
    if (configuredRows.length) {
      groqKeys.value = configuredRows
    } else {
      groqKeys.value = [{
        envKey: 'GROQ_API_KEY_1',
        value: '',
        configured: false,
        masked: '',
      }]
    }
  }

  function addGroqKey() {
    groqKeys.value.push(newGroqKeyRow())
  }

  function updateGroqKey(envKey, value) {
    const row = groqKeys.value.find(item => item.envKey === envKey)
    if (row) row.value = value
  }

  function removeGroqKey(envKey) {
    const index = groqKeys.value.findIndex(item => item.envKey === envKey)
    if (index < 0) return
    const row = groqKeys.value[index]
    if (row.configured) removedGroqKeys.add(row.envKey)
    groqKeys.value.splice(index, 1)
    if (!groqKeys.value.length) groqKeys.value.push(newGroqKeyRow())
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

    for (const row of groqKeys.value) {
      const value = String(row.value || '').trim()
      if (value) values[row.envKey] = value
    }
    for (const key of removedGroqKeys) values[key] = null

    for (const key of ['DEEPGRAM_API_KEY', 'HF_TOKEN']) {
      const value = String(runtimeDraft[key] || '').trim()
      if (value) values[key] = value
    }
    const runtimeFallbacks = {
      'llm.model': diagnostics.value?.llm?.model || '',
      'tts.voice': diagnostics.value?.tts?.voice || '',
      'asr.device': diagnostics.value?.asr?.device || '',
      'server.barge_in_policy': diagnostics.value?.server?.barge_in_policy || 'client_only',
    }
    for (const key of ['llm.model', 'tts.voice', 'asr.device', 'server.barge_in_policy']) {
      const draftValue = runtimeDraft[key]
      const currentValue = publicRuntimeValue(key, runtimeFallbacks[key])
      if (draftValue !== '' && draftValue !== currentValue) values[key] = draftValue
    }

    const numericFallbacks = {
      'latency.target_first_audio_ms': diagnostics.value?.profile?.latency?.target_first_audio_ms ?? 600,
      'latency.first_token_timeout_ms': diagnostics.value?.profile?.latency?.first_token_timeout_ms ?? 1800,
      'latency.total_turn_timeout_ms': diagnostics.value?.profile?.latency?.total_turn_timeout_ms ?? 15000,
      'asr.endpointing_ms': diagnostics.value?.profile?.asr?.endpointing_ms ?? 250,
      'asr.min_silence_duration_ms': diagnostics.value?.profile?.asr?.min_silence_duration_ms ?? 192,
      'asr.speculative_start_silence_ms': diagnostics.value?.profile?.asr?.speculative_start_silence_ms ?? 64,
      'asr.speculative_min_confidence': diagnostics.value?.profile?.asr?.speculative_min_confidence ?? 0.95,
      'asr.speculative_llm_min_confidence': diagnostics.value?.profile?.asr?.speculative_llm_min_confidence ?? 0.95,
      'asr.min_speech_duration_ms': diagnostics.value?.profile?.asr?.min_speech_duration_ms ?? 160,
      'asr.speech_start_frames': diagnostics.value?.profile?.asr?.speech_start_frames ?? 2,
      'asr.pre_speech_pad_ms': diagnostics.value?.profile?.asr?.pre_speech_pad_ms ?? 512,
      'asr.vad_threshold': diagnostics.value?.profile?.asr?.vad_threshold ?? 0.5,
      'asr.vad_threshold_low': diagnostics.value?.profile?.asr?.vad_threshold_low ?? 0.3,
      'asr.vad_end_threshold': diagnostics.value?.profile?.asr?.vad_end_threshold ?? 0.3,
      'tts.send_ahead_ms': diagnostics.value?.profile?.tts?.send_ahead_ms ?? 120,
      'tts.stream_queue_max_chunks': diagnostics.value?.profile?.tts?.stream_queue_max_chunks ?? 4,
      'tts.first_audio_priority_boost': diagnostics.value?.profile?.tts?.first_audio_priority_boost ?? 5,
      'tts.scheduler_aging_per_second': diagnostics.value?.profile?.tts?.scheduler_aging_per_second ?? 2,
      'tts.admission_timeout_ms': diagnostics.value?.profile?.tts?.admission_timeout_ms ?? 5000,
      'llm.routing.headroom_pct': diagnostics.value?.profile?.routing?.headroom_pct ?? 10,
      'llm.routing.max_attempts': diagnostics.value?.profile?.routing?.max_attempts ?? 2,
      'llm.routing.admission_wait_ms': diagnostics.value?.profile?.routing?.admission_wait_ms ?? 60,
      'llm.routing.discovery_wait_ms': diagnostics.value?.profile?.routing?.discovery_wait_ms ?? 750,
      'llm.routing.discovery_max_inflight': diagnostics.value?.profile?.routing?.discovery_max_inflight ?? 1,
      'llm.routing.inflight_penalty_s': diagnostics.value?.profile?.routing?.inflight_penalty_s ?? 0.4,
      'llm.routing.latency_ewma_alpha': diagnostics.value?.profile?.routing?.latency_ewma_alpha ?? 0.3,
      'llm.routing.latency_jitter_penalty': diagnostics.value?.profile?.routing?.latency_jitter_penalty ?? 0.75,
      'memory.lookup_timeout_ms': diagnostics.value?.profile?.memory?.lookup_timeout_ms ?? 20,
      'conversation.history_turns': diagnostics.value?.conversation?.history_turns ?? 6,
      'tools.max_parallel_read_only': diagnostics.value?.profile?.tools?.max_parallel_read_only ?? 2,
      'tools.max_llm_rounds_per_turn': diagnostics.value?.profile?.tools?.max_llm_rounds_per_turn ?? 3,
      'llm.speech_segmentation.min_segment_chars': diagnostics.value?.profile?.speech_segmentation?.min_segment_chars,
      'llm.speech_segmentation.clause_target_chars': diagnostics.value?.profile?.speech_segmentation?.clause_target_chars,
      'llm.speech_segmentation.clause_min_chars': diagnostics.value?.profile?.speech_segmentation?.clause_min_chars,
      'llm.speech_segmentation.first_clause_min_chars': diagnostics.value?.profile?.speech_segmentation?.first_clause_min_chars,
      'llm.speech_segmentation.first_clause_min_words': diagnostics.value?.profile?.speech_segmentation?.first_clause_min_words,
      'llm.speech_segmentation.hard_max_segment_chars': diagnostics.value?.profile?.speech_segmentation?.hard_max_segment_chars,
      'llm.speech_segmentation.hard_cut_search_back': diagnostics.value?.profile?.speech_segmentation?.hard_cut_search_back,
      'llm.speech_segmentation.hard_cut_search_forward': diagnostics.value?.profile?.speech_segmentation?.hard_cut_search_forward,
      'llm.speech_segmentation.first_segment_min_chars': diagnostics.value?.profile?.speech_segmentation?.first_segment_min_chars,
      'llm.speech_segmentation.first_segment_min_words': diagnostics.value?.profile?.speech_segmentation?.first_segment_min_words,
      'llm.speech_segmentation.first_soft_cut_chars': diagnostics.value?.profile?.speech_segmentation?.first_soft_cut_chars ?? 0,
      'llm.speech_segmentation.first_soft_cut_min_words': diagnostics.value?.profile?.speech_segmentation?.first_soft_cut_min_words ?? 3,
    }
    const speculativeFallback = diagnostics.value?.profile?.asr?.speculative_inference_enabled ?? true
    const speculativeDraft = Boolean(runtimeDraft['asr.speculative_inference_enabled'])
    if (speculativeDraft !== Boolean(publicRuntimeValue(
      'asr.speculative_inference_enabled',
      speculativeFallback,
    ))) {
      values['asr.speculative_inference_enabled'] = speculativeDraft
    }
    const speculativeLlmFallback = diagnostics.value?.profile?.asr?.speculative_llm_enabled ?? false
    const speculativeLlmDraft = Boolean(runtimeDraft['asr.speculative_llm_enabled'])
    if (speculativeLlmDraft !== Boolean(publicRuntimeValue(
      'asr.speculative_llm_enabled',
      speculativeLlmFallback,
    ))) {
      values['asr.speculative_llm_enabled'] = speculativeLlmDraft
    }
    const speculativeTtsFallback = diagnostics.value?.profile?.tts?.speculative_prefetch_enabled ?? false
    const speculativeTtsDraft = Boolean(runtimeDraft['tts.speculative_prefetch_enabled'])
    if (speculativeTtsDraft !== Boolean(publicRuntimeValue(
      'tts.speculative_prefetch_enabled',
      speculativeTtsFallback,
    ))) {
      values['tts.speculative_prefetch_enabled'] = speculativeTtsDraft
    }

    for (const [key, fallback] of Object.entries(numericFallbacks)) {
      const raw = String(runtimeDraft[key] ?? '').trim()
      if (!raw) continue
      const parsed = Number(raw)
      if (Number.isFinite(parsed) && parsed !== Number(publicRuntimeValue(key, fallback))) {
        values[key] = parsed
      }
    }

    busy.value = true
    try {
      const data = await api('/api/runtime-config', { method: 'PATCH', body: JSON.stringify({ values }) })
      runtime.value = data.values || {}
      restartRequired.value = false
      hydrateRuntimeDraft()
      await loadPublicData()
      if (data.llm_reloaded && data.llm_ready) {
        notify('Đã lưu và hot-reload LLM thành công.', 'success')
      } else if (data.llm_reloaded && data.llm_ready === false) {
        notify('Đã lưu. LLM hiện chưa có key khả dụng.', 'warning')
      } else {
        notify('Đã lưu và áp dụng ngay.', 'success')
      }
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
    health, diagnostics, assistants, devices, pendingDevices, runtime, restartRequired, groqKeys, voices, models,
    assistantDraft, pairing, runtimeDraft, toast,
    pairedActiveDevices, onlineDevices,
    notify, fmtTime, publicRuntimeValue, secretPlaceholder,
    login, loadPublicData, loadManagedData, refreshAll,
    prepareNewAssistant, prepareEditAssistant, saveAssistant, toggleAssistant, deleteAssistant,
    pairDevice, updateDevice, revokeDevice, addGroqKey, updateGroqKey, removeGroqKey, saveRuntime,
  }
}
