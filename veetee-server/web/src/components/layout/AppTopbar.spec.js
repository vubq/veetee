import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AppTopbar from './AppTopbar.vue'

describe('AppTopbar', () => {
  it('renders degraded runtime state instead of a healthy badge', () => {
    const wrapper = mount(AppTopbar, {
      props: {
        activeView: 'overview',
        health: {
          status: 'degraded',
          readiness: 'degraded',
          degraded_reasons: ['llm_not_warm'],
        },
      },
    })
    expect(wrapper.text()).toContain('degraded')
    expect(wrapper.text()).toContain('Mission Control')
  })
})
