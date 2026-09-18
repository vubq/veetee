<script setup>
defineProps({
  label: { type: String, default: '' },
  value: { type: [String, Number], default: '—' },
  detail: { type: String, default: '' },
  tone: { type: String, default: 'neutral' },
})
</script>

<template>
  <div class="ui-stat-card" :class="`ui-stat-card--${tone}`">
    <div class="ui-stat-card__icon"><slot name="icon" /></div>
    <div class="ui-stat-card__copy">
      <span class="ui-stat-card__label">{{ label }}</span>
      <strong class="ui-stat-card__value">{{ value }}</strong>
      <small class="ui-stat-card__detail">{{ detail }}</small>
    </div>
  </div>
</template>
