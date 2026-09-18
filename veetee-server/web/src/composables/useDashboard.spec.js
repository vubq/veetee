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

  it('sends only non-empty secrets and selected runtime values', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn(async () => jsonResponse({
      values: {
        GROQ_API_KEY_A: { configured: true, masked: 'abc••••••xyz' },
        'llm.model': 'model-a',
      },
      restart_required: true,
    }))
    vi.stubGlobal('fetch', fetchMock)
    const dashboard = useDashboard()
    dashboard.runtimeDraft.GROQ_API_KEY_A = '  secret-a  '
    dashboard.runtimeDraft.GROQ_API_KEY_B = ''
    dashboard.runtimeDraft['llm.model'] = 'model-a'

    const ok = await dashboard.saveRuntime()

    expect(ok).toBe(true)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    const [, options] = fetchMock.mock.calls[0]
    expect(options.method).toBe('PATCH')
    const body = JSON.parse(options.body)
    expect(body.values.GROQ_API_KEY_A).toBe('secret-a')
    expect(body.values['llm.model']).toBe('model-a')
    expect(body.values).not.toHaveProperty('GROQ_API_KEY_B')
    expect(dashboard.restartRequired.value).toBe(true)
  })

  it('never exposes stored secret values through placeholders', () => {
    const dashboard = useDashboard()
    dashboard.runtime.value = {
      GROQ_API_KEY_A: { configured: true, masked: 'abc••••••xyz' },
    }

    expect(dashboard.secretPlaceholder('GROQ_API_KEY_A')).toBe('abc••••••xyz')
    expect(dashboard.runtimeDraft.GROQ_API_KEY_A).toBe('')
  })
})
