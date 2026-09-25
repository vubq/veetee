import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import VoiceConsoleView from './VoiceConsoleView.vue'

describe('VoiceConsoleView LLM readiness', () => {
  it('blocks AI chat and points to Runtime when LLM is not warm', async () => {
    const wrapper = mount(VoiceConsoleView, {
      props: {
        wsState: 'connected',
        chatInput: 'Xin chào',
        health: {
          status: 'degraded',
          readiness: 'degraded',
          degraded_reasons: ['llm_not_warm'],
        },
      },
    })

    expect(wrapper.find('.voice-readiness-alert').text()).toContain('LLM chưa sẵn sàng')
    const send = wrapper.get('button[aria-label="Gửi"]')
    expect(send.attributes('disabled')).toBeDefined()

    await wrapper.get('.voice-readiness-alert .ui-button').trigger('click')
    expect(wrapper.emitted('runtime')).toHaveLength(1)

    await send.trigger('click')
    expect(wrapper.emitted('send')).toBeUndefined()
  })

  it('allows sending when WebSocket and LLM are ready', async () => {
    const wrapper = mount(VoiceConsoleView, {
      props: {
        wsState: 'connected',
        chatInput: 'Xin chào',
        health: {
          status: 'ready',
          readiness: 'ready',
          degraded_reasons: [],
        },
      },
    })

    expect(wrapper.find('.voice-readiness-alert').exists()).toBe(false)
    const send = wrapper.get('button[aria-label="Gửi"]')
    expect(send.attributes('disabled')).toBeUndefined()

    await send.trigger('click')
    expect(wrapper.emitted('send')).toHaveLength(1)
  })

  it('offers an explicit audio unlock action when browser playback is blocked', async () => {
    const wrapper = mount(VoiceConsoleView, {
      props: {
        wsState: 'connected',
        audioStatus: 'Audio bị trình duyệt chặn · bấm “Bật âm thanh”',
        health: {
          status: 'ready',
          readiness: 'ready',
          degraded_reasons: [],
        },
      },
    })

    const button = wrapper.findAll('button').find(item => item.text().includes('Bật âm thanh'))
    expect(button).toBeTruthy()
    await button.trigger('click')
    expect(wrapper.emitted('unlock-audio')).toHaveLength(1)
  })
})
