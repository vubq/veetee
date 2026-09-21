import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AssistantsView from './AssistantsView.vue'

const assistants = [
  { id: 'a', name: 'Alpha', model: 'model-a', voice: 'Voice A', enabled: true, device_count: 1 },
  { id: 'b', name: 'Beta', model: 'model-b', voice: 'Voice B', enabled: true, device_count: 0 },
]

describe('AssistantsView', () => {
  it('filters assistants by name/model/voice', async () => {
    const wrapper = mount(AssistantsView, { props: { assistants } })
    expect(wrapper.findAll('.xz-agent-card')).toHaveLength(2)

    await wrapper.get('.xz-search input').setValue('model-b')

    const cards = wrapper.findAll('.xz-agent-card')
    expect(cards).toHaveLength(1)
    expect(cards[0].text()).toContain('Beta')
  })

  it('emits add-device from primary action and create from split menu', async () => {
    const wrapper = mount(AssistantsView, { props: { assistants } })

    await wrapper.get('.xz-primary-action:not(.xz-primary-action--menu)').trigger('click')
    expect(wrapper.emitted('add-device')).toHaveLength(1)

    await wrapper.get('.xz-primary-action--menu').trigger('click')
    await wrapper.get('.xz-action-menu button').trigger('click')
    expect(wrapper.emitted('create')).toHaveLength(1)
  })
})
