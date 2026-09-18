import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import UiInput from './UiInput.vue'

describe('UiInput', () => {
  it('emits model updates while typing and a native change event on commit', async () => {
    const wrapper = mount(UiInput, { props: { modelValue: 'old' } })
    const input = wrapper.get('input')

    await input.setValue('new')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['new'])

    await input.trigger('change')
    const change = wrapper.emitted('change')?.at(-1)?.[0]
    expect(change?.target?.value).toBe('new')
  })
})
