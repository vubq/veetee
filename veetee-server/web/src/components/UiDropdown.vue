<script setup lang="ts">
import { Check } from '@lucide/vue'
import { onBeforeUnmount, onMounted, ref, useTemplateRef, type Component } from 'vue'

export interface DropdownItem {
  label: string
  value: string
  checked?: boolean
  danger?: boolean
  disabled?: boolean
  icon?: Component
}

defineProps<{
  label: string
  items: DropdownItem[]
  align?: 'start' | 'end'
}>()

const emit = defineEmits<{
  select: [value: string]
}>()

const open = ref(false)
const root = useTemplateRef<HTMLElement>('root')

function select(value: string) {
  emit('select', value)
  open.value = false
}

function closeOnOutside(event: MouseEvent) {
  if (root.value && !root.value.contains(event.target as Node)) open.value = false
}

onMounted(() => document.addEventListener('pointerdown', closeOnOutside))
onBeforeUnmount(() => document.removeEventListener('pointerdown', closeOnOutside))
</script>

<template>
  <div ref="root" class="dropdown-root" @keydown.esc.stop.prevent="open = false">
    <div @click.stop="open = !open">
      <slot name="trigger" :open="open" :close="() => (open = false)" />
    </div>
    <Transition name="menu-fade">
      <div v-if="open" class="dropdown-menu" :class="`align-${align ?? 'end'}`" role="menu" :aria-label="label">
        <button
          v-for="item in items"
          :key="item.value"
          class="dropdown-item"
          :class="{ danger: item.danger }"
          :disabled="item.disabled"
          type="button"
          role="menuitem"
          @click="select(item.value)"
        >
          <component :is="item.icon" v-if="item.icon" class="menu-item-icon" :size="16" stroke-width="1.8" />
          <span>{{ item.label }}</span>
          <Check class="menu-item-check" :class="{ invisible: !item.checked }" :size="15" />
        </button>
      </div>
    </Transition>
  </div>
</template>
