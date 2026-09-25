import { afterEach, describe, expect, it, vi } from 'vitest'
import { useDashboard } from './useDashboard'

function jsonResponse(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => 'application/json' },
    json: async () => payload,
    text: async () => JSON.stringify(payload),
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('useDashboard', () => {
  it('opens management login when managed APIs return 401', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({}, 401)))
    const dashboard = useDashboard()

    await dashboard.loadManagedData()

    expect(dashboard.authRequired.value).toBe(true)
    expect(dashboard.loading.value).toBe(false)
    expect(dashboard.toast.text).toBe('')
  })

  it('rejects invalid pairing codes before making a request', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const dashboard = useDashboard()
    dashboard.pairing.code = '123'
    dashboard.pairing.assistant_id = 'default'

    const ok = await dashboard.pairDevice()

    expect(ok).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(dashboard.toast.text).toContain('6 số')
  })

  it('sends dynamic Groq keys and applies runtime without restart', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn(async (url) => {
      if (url === '/health') {
        return jsonResponse({ status: 'ready', readiness: 'ready', degraded_reasons: [] })
      }
      return jsonResponse({
        values: {
          GROQ_API_KEY_1: { configured: true, masked: 'gsk••••••one' },
          'llm.model': 'model-a',
        },
        restart_required: false,
        llm_reloaded: true,
        llm_ready: true,
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const dashboard = useDashboard()
    dashboard.addGroqKey()
    dashboard.groqKeys.value[0].value = '  secret-a  '
    dashboard.groqKeys.value[0].tokenLimit = '50000'
    dashboard.runtimeDraft['llm.model'] = 'model-a'

    const ok = await dashboard.saveRuntime()

    expect(ok).toBe(true)
    const [url, options] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/runtime-config')
    expect(options.method).toBe('PATCH')
    const body = JSON.parse(options.body)
    expect(body.values.GROQ_API_KEY_1).toBe('secret-a')
    expect(body.values['groq.token_limit.GROQ_API_KEY_1']).toBe(50000)
    expect(body.values['llm.model']).toBe('model-a')
    expect(dashboard.restartRequired.value).toBe(false)
    expect(dashboard.health.value?.readiness).toBe('ready')
    expect(dashboard.toast.text).toContain('hot-reload')
  })

  it('rejects an invalid per-key token limit before calling the API', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    const dashboard = useDashboard()
    dashboard.groqKeys.value[0].value = 'secret-a'
    dashboard.groqKeys.value[0].tokenLimit = '-1'

    const ok = await dashboard.saveRuntime()

    expect(ok).toBe(false)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(dashboard.toast.text).toContain('Token limit')
  })

  it('sends TTS fairness tuning as numeric runtime settings', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn(async (url) => {
      if (url === '/health') {
        return jsonResponse({ status: 'ready', readiness: 'ready', degraded_reasons: [] })
      }
      return jsonResponse({
        values: {
          'tts.first_audio_priority_boost': 7.5,
          'tts.scheduler_aging_per_second': 3,
        },
        restart_required: false,
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const dashboard = useDashboard()
    dashboard.runtimeDraft['tts.first_audio_priority_boost'] = '7.5'
    dashboard.runtimeDraft['tts.scheduler_aging_per_second'] = '3'

    const ok = await dashboard.saveRuntime()

    expect(ok).toBe(true)
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body.values['tts.first_audio_priority_boost']).toBe(7.5)
    expect(body.values['tts.scheduler_aging_per_second']).toBe(3)
  })

  it('sends LLM routing policy as numeric hot runtime settings', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn(async (url) => {
      if (url === '/health') {
        return jsonResponse({ status: 'ready', readiness: 'ready', degraded_reasons: [] })
      }
      return jsonResponse({
        values: {
          'llm.routing.discovery_wait_ms': 900,
          'llm.routing.discovery_max_inflight': 2,
        },
        restart_required: false,
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const dashboard = useDashboard()
    dashboard.runtimeDraft['llm.routing.headroom_pct'] = '12.5'
    dashboard.runtimeDraft['llm.routing.max_attempts'] = '4'
    dashboard.runtimeDraft['llm.routing.admission_wait_ms'] = '80'
    dashboard.runtimeDraft['llm.routing.discovery_wait_ms'] = '900'
    dashboard.runtimeDraft['llm.routing.discovery_max_inflight'] = '2'
    dashboard.runtimeDraft['llm.routing.inflight_penalty_s'] = '0.6'
    dashboard.runtimeDraft['llm.routing.latency_ewma_alpha'] = '0.4'
    dashboard.runtimeDraft['llm.routing.latency_jitter_penalty'] = '0.9'

    const ok = await dashboard.saveRuntime()

    expect(ok).toBe(true)
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body.values['llm.routing.headroom_pct']).toBe(12.5)
    expect(body.values['llm.routing.max_attempts']).toBe(4)
    expect(body.values['llm.routing.admission_wait_ms']).toBe(80)
    expect(body.values['llm.routing.discovery_wait_ms']).toBe(900)
    expect(body.values['llm.routing.discovery_max_inflight']).toBe(2)
    expect(body.values['llm.routing.inflight_penalty_s']).toBe(0.6)
    expect(body.values['llm.routing.latency_ewma_alpha']).toBe(0.4)
    expect(body.values['llm.routing.latency_jitter_penalty']).toBe(0.9)
  })

  it('hot-applies speculative TTS prefetch as a boolean runtime setting', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn(async (url) => {
      if (url === '/health') {
        return jsonResponse({ status: 'ready', readiness: 'ready', degraded_reasons: [] })
      }
      return jsonResponse({
        values: { 'tts.speculative_prefetch_enabled': true },
        restart_required: false,
      })
    })
    vi.stubGlobal('fetch', fetchMock)

    const dashboard = useDashboard()
    dashboard.runtimeDraft['tts.speculative_prefetch_enabled'] = true

    const ok = await dashboard.saveRuntime()

    expect(ok).toBe(true)
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body.values['tts.speculative_prefetch_enabled']).toBe(true)
    expect(dashboard.restartRequired.value).toBe(false)
  })

  it('deletes a persisted Groq key explicitly', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn(async (url) => {
      if (url === '/health') {
        return jsonResponse({ status: 'degraded', readiness: 'degraded', degraded_reasons: ['llm_not_warm'] })
      }
      return jsonResponse({ values: {}, restart_required: false, llm_reloaded: true })
    })
    vi.stubGlobal('fetch', fetchMock)

    const dashboard = useDashboard()
    dashboard.groqKeys.value = [{
      envKey: 'GROQ_API_KEY_A',
      value: '',
      configured: true,
      masked: 'gsk••••••old',
    }]
    dashboard.removeGroqKey('GROQ_API_KEY_A')

    const ok = await dashboard.saveRuntime()

    expect(ok).toBe(true)
    const body = JSON.parse(fetchMock.mock.calls[0][1].body)
    expect(body.values.GROQ_API_KEY_A).toBeNull()
    expect(dashboard.groqKeys.value).toHaveLength(1)
  })

  it('never exposes stored secret values through placeholders', () => {
    const dashboard = useDashboard()
    dashboard.runtime.value = {
      GROQ_API_KEY_A: { configured: true, masked: 'abc••••••xyz' },
      HF_TOKEN: { configured: true, masked: 'hf_••••••key' },
    }

    expect(dashboard.secretPlaceholder('GROQ_API_KEY_A')).toBe('abc••••••xyz')
    expect(dashboard.secretPlaceholder('HF_TOKEN')).toBe('hf_••••••key')
    expect(dashboard.runtimeDraft.HF_TOKEN).toBe('')
  })
})
