import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import UiSelect from './UiSelect.vue'

describe('UiSelect', () => {
  it('opens, filters and emits the selected value', async () => {
    const wrapper = mount(UiSelect, {
      props: {
        modelValue: '',
        searchable: true,
        options: [
          { value: 'a', label: 'Alpha' },
          { value: 'b', label: 'Beta', description: 'Second option' },
        ],
      },
    })

    await wrapper.get('.ui-select__trigger').trigger('click')
    expect(wrapper.find('.ui-select__popover').exists()).toBe(true)

    const search = wrapper.get('.ui-select__search input')
    await search.setValue('Beta')
    const options = wrapper.findAll('.ui-select__option')
    expect(options).toHaveLength(1)

    await options[0].trigger('click')
    expect(wrapper.emitted('update:modelValue')?.at(-1)).toEqual(['b'])
    expect(wrapper.emitted('change')?.at(-1)).toEqual(['b'])
    expect(wrapper.find('.ui-select__popover').exists()).toBe(false)
  })

  it('does not open while disabled', async () => {
    const wrapper = mount(UiSelect, {
      props: {
        modelValue: '',
        disabled: true,
        options: [{ value: 'a', label: 'Alpha' }],
      },
    })

    await wrapper.get('.ui-select__trigger').trigger('click')
    expect(wrapper.find('.ui-select__popover').exists()).toBe(false)
  })
})
