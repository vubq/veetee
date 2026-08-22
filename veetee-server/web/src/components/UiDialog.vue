<script setup lang="ts">
import { X } from '@lucide/vue'
import { nextTick, onBeforeUnmount, onMounted, ref, useId, useTemplateRef, watch } from 'vue'

const dialogStack: symbol[] = []

function removeFromStack(id: symbol) {
  const index = dialogStack.lastIndexOf(id)
  if (index >= 0) dialogStack.splice(index, 1)
  document.body.classList.toggle('dialog-open', dialogStack.length > 0)
}

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
const dialogId = Symbol('dialog')

function isTopmost() {
  const visiblePanels = document.querySelectorAll<HTMLElement>('.dialog-layer .dialog-panel')
  const topmostPanel = visiblePanels.item(visiblePanels.length - 1)
  return topmostPanel ? topmostPanel === panel.value : dialogStack.at(-1) === dialogId
}

function closeTopmost() {
  if (isTopmost()) emit('close')
}

function focusableElements() {
  return Array.from(panel.value?.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])') ?? [])
}

function onKeydown(event: KeyboardEvent) {
  if (!props.open || !isTopmost()) return
  if (event.key === 'Escape') {
    event.preventDefault()
    event.stopImmediatePropagation()
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
      removeFromStack(dialogId)
      dialogStack.push(dialogId)
      document.body.classList.add('dialog-open')
      await nextTick()
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()))
      const preferredFocus = panel.value?.querySelector<HTMLElement>('[data-dialog-autofocus]:not(:disabled)')
      preferredFocus?.focus() ?? focusableElements()[0]?.focus()
      return
    }
    removeFromStack(dialogId)
    await nextTick()
    if (previousFocus.value?.isConnected) previousFocus.value.focus()
  },
)

onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => {
  document.removeEventListener('keydown', onKeydown)
  removeFromStack(dialogId)
})
</script>

<template>
  <Teleport to="body">
    <Transition name="dialog-fade">
      <div v-if="open" class="dialog-layer" :class="`dialog-layer-${variant}`" role="presentation" @mousedown.self="closeTopmost">
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
