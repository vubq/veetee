import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AppTopbar from './AppTopbar.vue'

describe('AppTopbar', () => {
  it('renders Xiaozhi-style navigation and degraded runtime state', () => {
    const wrapper = mount(AppTopbar, {
      props: {
        activeView: 'assistants',
        health: {
          status: 'degraded',
          readiness: 'degraded',
          degraded_reasons: ['llm_not_warm'],
        },
      },
    })
    expect(wrapper.text()).toContain('VeeTee')
    expect(wrapper.text()).toContain('Assistants')
    expect(wrapper.text()).toContain('Voice Console')
    expect(wrapper.text()).toContain('degraded')
  })
})
