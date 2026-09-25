import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import RuntimeView from './RuntimeView.vue'

const baseProps = {
  draft: {
    HF_TOKEN: '',
    'llm.model': 'openai/gpt-oss-20b',
    'tts.voice': 'Trúc Ly',
    'asr.device': 'cuda',
    'server.barge_in_policy': 'client_only',
  },
  runtime: {},
  models: ['openai/gpt-oss-20b'],
  voices: [{ name: 'Trúc Ly' }],
  secretPlaceholder: () => 'Nhập secret mới',
}

describe('RuntimeView', () => {
  it('shows one initial Groq key row and provider-separated credentials', async () => {
    const wrapper = mount(RuntimeView, {
      props: {
        ...baseProps,
        groqKeys: [{
          envKey: 'GROQ_API_KEY_1',
          value: '',
          configured: false,
          masked: '',
        }],
      },
    })

    expect(wrapper.findAll('.groq-key-row')).toHaveLength(1)
    expect(wrapper.text()).toContain('LLM · Groq')
    expect(wrapper.text()).toContain('Models · Hugging Face')
    expect(wrapper.find('.restart-alert').exists()).toBe(false)

    await wrapper.get('.groq-add-key').trigger('click')
    expect(wrapper.emitted('add-groq-key')).toHaveLength(1)

    await wrapper.get('.groq-key-row .ui-button--danger-ghost').trigger('click')
    expect(wrapper.emitted('remove-groq-key')?.[0]).toEqual(['GROQ_API_KEY_1'])
  })

  it('renders all previously configured Groq keys without exposing raw values', () => {
    const groqKeys = ['A', 'B', 'C', 'D'].map(id => ({
      envKey: `GROQ_API_KEY_${id}`,
      value: '',
      configured: true,
      masked: `gsk••••••${id}`,
    }))
    const wrapper = mount(RuntimeView, {
      props: {
        ...baseProps,
        runtime: Object.fromEntries(groqKeys.map(item => [
          item.envKey,
          { configured: true, masked: item.masked },
        ])),
        groqKeys,
      },
    })

    expect(wrapper.findAll('.groq-key-row')).toHaveLength(4)
    expect(wrapper.text()).toContain('4 configured')
    for (const item of groqKeys) {
      expect(wrapper.html()).not.toContain('raw-secret')
      expect(wrapper.find(`input[placeholder="${item.masked}"]`).exists()).toBe(true)
    }
  })
})
