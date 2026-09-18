<script setup>
import { computed, nextTick, onBeforeUnmount, ref, useAttrs } from 'vue'
import { Check, ChevronDown, Search } from '@lucide/vue'

const props = defineProps({
  modelValue: { type: [String, Number, Boolean], default: '' },
  options: { type: Array, default: () => [] },
  label: { type: String, default: '' },
  hint: { type: String, default: '' },
  placeholder: { type: String, default: 'Chọn một giá trị' },
  searchable: Boolean,
  disabled: Boolean,
})
const emit = defineEmits(['update:modelValue', 'change'])
const attrs = useAttrs()
const open = ref(false)
const query = ref('')
const root = ref(null)
const searchInput = ref(null)

const normalized = computed(() => props.options.map(option => {
  if (option && typeof option === 'object') return {
    value: option.value ?? option.id ?? option.name,
    label: option.label ?? option.name ?? String(option.value ?? ''),
    description: option.description || '',
    disabled: Boolean(option.disabled),
  }
  return { value: option, label: String(option), description: '', disabled: false }
}))
const filtered = computed(() => {
  const term = query.value.trim().toLocaleLowerCase('vi')
  if (!term) return normalized.value
  return normalized.value.filter(option => `${option.label} ${option.description}`.toLocaleLowerCase('vi').includes(term))
})
const selected = computed(() => normalized.value.find(option => option.value === props.modelValue))

async function toggle() {
  if (props.disabled) return
  open.value = !open.value
  if (open.value && props.searchable) {
    query.value = ''
    await nextTick()
    searchInput.value?.focus()
  }
}
function choose(option) {
  if (option.disabled) return
  emit('update:modelValue', option.value)
  emit('change', option.value)
  open.value = false
}
function onDocument(event) {
  if (root.value && !root.value.contains(event.target)) open.value = false
}
document.addEventListener('pointerdown', onDocument)
onBeforeUnmount(() => document.removeEventListener('pointerdown', onDocument))
</script>

<template>
  <div ref="root" class="ui-field ui-select" :class="{ 'ui-select--open': open, 'ui-select--disabled': disabled }">
    <span v-if="label" class="ui-field__label">{{ label }}</span>
    <button v-bind="attrs" type="button" class="ui-select__trigger" :disabled="disabled" @click="toggle">
      <span class="ui-select__value" :class="{ 'ui-select__value--placeholder': !selected }">
        <slot name="value" :option="selected">{{ selected?.label || placeholder }}</slot>
      </span>
      <ChevronDown :size="16" class="ui-select__chevron" />
    </button>
    <transition name="select-pop">
      <div v-if="open" class="ui-select__popover">
        <div v-if="searchable" class="ui-select__search">
          <Search :size="15" />
          <input ref="searchInput" v-model="query" type="text" placeholder="Tìm kiếm…" @keydown.esc="open=false">
        </div>
        <div class="ui-select__menu" role="listbox">
          <button
            v-for="option in filtered"
            :key="String(option.value)"
            type="button"
            class="ui-select__option"
            :class="{ 'ui-select__option--selected': option.value === modelValue }"
            :disabled="option.disabled"
            @click="choose(option)"
          >
            <span class="ui-select__option-copy">
              <strong>{{ option.label }}</strong>
              <small v-if="option.description">{{ option.description }}</small>
            </span>
            <Check v-if="option.value === modelValue" :size="16" />
          </button>
          <div v-if="!filtered.length" class="ui-select__empty">Không có kết quả</div>
        </div>
      </div>
    </transition>
    <span v-if="hint" class="ui-field__hint">{{ hint }}</span>
  </div>
</template>
