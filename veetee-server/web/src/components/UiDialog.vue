<script setup lang="ts">
import { X } from '@lucide/vue'
import { nextTick, onBeforeUnmount, onMounted, ref, useId, useTemplateRef, watch } from 'vue'

const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    description?: string
    size?: 'small' | 'medium' | 'large'
    variant?: 'default' | 'compact'
  }>(),
  { description: '', size: 'small', variant: 'default' },
)

const emit = defineEmits<{ close: [] }>()
const panel = useTemplateRef<HTMLElement>('panel')
const titleId = `${useId()}-title`
const descriptionId = `${useId()}-description`
const previousFocus = ref<HTMLElement | null>(null)

function focusableElements() {
  return Array.from(panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])') ?? [])
}

function onKeydown(event: KeyboardEvent) {
  if (!props.open) return
  if (event.key === 'Escape') {
    emit('close')
    return
  }
  if (event.key !== 'Tab') return
  const elements = focusableElements()
  if (elements.length === 0) return
  const first = elements[0]
  const last = elements[elements.length - 1]
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last?.focus()
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first?.focus()
  }
}

watch(
  () => props.open,
  async (open) => {
    if (open) {
      previousFocus.value = document.activeElement as HTMLElement | null
      document.body.classList.add('dialog-open')
      await nextTick()
      focusableElements()[0]?.focus()
      return
    }
    document.body.classList.remove('dialog-open')
    previousFocus.value?.focus()
  },
)

onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  document.body.classList.remove('dialog-open')
})
</script>

<template>
  <Teleport to="body">
    <Transition name="dialog-fade">
      <div v-if="open" class="dialog-layer" :class="`dialog-layer-${variant}`" role="presentation" @mousedown.self="emit('close')">
        <section ref="panel" class="dialog-panel" :class="[`dialog-${size}`, `dialog-${variant}`]" role="dialog" aria-modal="true" :aria-labelledby="titleId" :aria-describedby="description ? descriptionId : undefined">
          <header class="dialog-header">
            <div>
              <h2 :id="titleId">{{ title }}</h2>
              <p v-if="description" :id="descriptionId">{{ description }}</p>
            </div>
            <button class="dialog-close" type="button" aria-label="Đóng" @click="emit('close')">
              <X :size="18" />
            </button>
          </header>
          <div class="dialog-content"><slot /></div>
          <footer v-if="$slots.footer" class="dialog-footer"><slot name="footer" /></footer>
        </section>
      </div>
    </Transition>
  </Teleport>
</template>
