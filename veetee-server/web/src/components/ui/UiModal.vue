<script setup>
import { onBeforeUnmount, onMounted } from 'vue'
import { X } from '@lucide/vue'
import UiButton from './UiButton.vue'

const props = defineProps({
  open: Boolean,
  title: { type: String, default: '' },
  description: { type: String, default: '' },
  size: { type: String, default: 'md' },
  closeOnBackdrop: { type: Boolean, default: true },
})
const emit = defineEmits(['close'])
function onKeydown(event) {
  if (props.open && event.key === 'Escape') emit('close')
}
onMounted(() => document.addEventListener('keydown', onKeydown))
onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div v-if="open" class="ui-modal" role="dialog" aria-modal="true" @mousedown.self="closeOnBackdrop && emit('close')">
        <div class="ui-modal__panel" :class="`ui-modal__panel--${size}`">
          <header class="ui-modal__header">
            <div>
              <h2>{{ title }}</h2>
              <p v-if="description">{{ description }}</p>
            </div>
            <UiButton variant="ghost" size="sm" icon-only aria-label="Đóng" @click="emit('close')">
              <template #icon><X :size="18" /></template>
            </UiButton>
          </header>
          <div class="ui-modal__body"><slot /></div>
          <footer v-if="$slots.footer" class="ui-modal__footer"><slot name="footer" /></footer>
        </div>
      </div>
    </transition>
  </Teleport>
</template>
