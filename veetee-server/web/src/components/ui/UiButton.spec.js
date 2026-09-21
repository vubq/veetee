import { h } from 'vue'
import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import UiButton from './UiButton.vue'

describe('UiButton', () => {
  it('replaces the icon with a spinner while loading', async () => {
    const wrapper = mount(UiButton, {
      props: { variant: 'primary', iconOnly: true, loading: false },
      slots: {
        icon: () => h('span', { class: 'test-send-icon' }),
      },
    })

    expect(wrapper.find('.test-send-icon').exists()).toBe(true)
    expect(wrapper.find('.ui-button__spinner').exists()).toBe(false)

    await wrapper.setProps({ loading: true })

    expect(wrapper.find('.test-send-icon').exists()).toBe(false)
    expect(wrapper.find('.ui-button__spinner').exists()).toBe(true)
    expect(wrapper.classes()).toContain('ui-button--loading')
    expect(wrapper.attributes('aria-busy')).toBe('true')
    expect(wrapper.attributes('disabled')).toBeDefined()
  })
})
