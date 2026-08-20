<script setup lang="ts">
import { Check, ChevronDown } from '@lucide/vue'
import { computed, ref, type Component } from 'vue'

export interface SelectOption {
  label: string
  value: string
  icon?: Component
}

const props = defineProps<{
  modelValue: string
  label: string
  options: SelectOption[]
}>()

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()
const open = ref(false)
const selected = computed(() => props.options.find((option) => option.value === props.modelValue))
</script>

<template>
  <div class="select-root">
    <button class="select-trigger" type="button" role="combobox" :aria-label="label" :aria-expanded="open" @click="open = !open">
      <span class="select-value"><component :is="selected?.icon" v-if="selected?.icon" :size="16" stroke-width="1.8" />{{ selected?.label }}</span><ChevronDown :size="16" />
    </button>
    <Transition name="menu-fade">
      <div v-if="open" class="select-list" role="listbox">
        <button
          v-for="option in options"
          :key="option.value"
          type="button"
          role="option"
          :aria-selected="option.value === modelValue"
          @click="emit('update:modelValue', option.value); open = false"
        >
          <component :is="option.icon" v-if="option.icon" class="menu-item-icon" :size="16" stroke-width="1.8" />
          <span>{{ option.label }}</span>
          <Check class="menu-item-check" :class="{ invisible: option.value !== modelValue }" :size="15" />
        </button>
      </div>
    </Transition>
  </div>
</template>
