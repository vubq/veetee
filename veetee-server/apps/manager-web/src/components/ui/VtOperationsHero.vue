<script setup lang="ts">
import type { VtIconName } from "./VtIcon.vue";
import VtIcon from "./VtIcon.vue";

defineProps<{
  eyebrow: string;
  title: string;
  description: string;
  value: number | string;
  valueLabel: string;
  valueHint?: string;
  icon: VtIconName;
}>();
</script>

<template>
  <section class="vt-operations-hero">
    <span class="vt-operations-icon"><VtIcon :name="icon" :size="23" /></span>
    <div class="vt-operations-copy">
      <span class="vt-kicker">{{ eyebrow }}</span>
      <h2>{{ title }}</h2>
      <p>{{ description }}</p>
    </div>
    <div class="vt-operations-total">
      <small>{{ valueLabel }}</small>
      <strong>{{ value }}</strong>
      <span v-if="valueHint">{{ valueHint }}</span>
    </div>
  </section>
</template>
