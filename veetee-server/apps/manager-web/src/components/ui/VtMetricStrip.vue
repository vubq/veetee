<script lang="ts">
export interface VtMetricItem {
  label: string;
  value: number | string;
  detail: string;
  tone?: "neutral" | "success" | "warning" | "danger" | "info";
}
</script>

<script setup lang="ts">
defineProps<{ items: VtMetricItem[] }>();
</script>

<template>
  <section class="vt-metric-strip" aria-label="Tóm tắt trạng thái">
    <article v-for="item in items" :key="item.label" :class="`is-${item.tone ?? 'neutral'}`">
      <i aria-hidden="true"></i>
      <div>
        <span>{{ item.label }}</span>
        <p>{{ item.detail }}</p>
      </div>
      <strong>{{ item.value }}</strong>
    </article>
  </section>
</template>
