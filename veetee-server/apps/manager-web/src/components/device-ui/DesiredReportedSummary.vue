<script setup lang="ts">
import { computed } from "vue";

import type { Device } from "../../api/schemas";
import {
  deliveryLabel,
  deliveryTone,
  summarizeDeviceDelivery,
} from "../../utils/device-delivery";
import { VtBadge, VtIcon } from "../ui";

const props = defineProps<{ device: Device }>();
const summary = computed(() => summarizeDeviceDelivery(props.device));
const icon = computed(() => summary.value.state === "synced" ? "check" : summary.value.state === "unmanaged" ? "telemetry" : "warning");
</script>

<template>
  <section class="delivery-state" data-device-delivery :data-delivery-state="summary.state">
    <div class="delivery-state-banner" :class="`is-${summary.state}`">
      <span><VtIcon :name="icon" :size="21" /></span>
      <div>
        <b>{{ summary.title }}</b>
        <p>{{ summary.description }}</p>
      </div>
      <VtBadge :tone="deliveryTone(summary.state)">{{ deliveryLabel(summary.state) }}</VtBadge>
    </div>

    <div class="delivery-subsystems">
      <article v-for="item in summary.subsystems" :key="item.id" class="vt-panel" :data-subsystem="item.id">
        <header>
          <span class="delivery-subsystem-icon"><VtIcon :name="item.id === 'ui' ? 'display' : 'resource'" :size="19" /></span>
          <div><small>{{ item.id === "ui" ? "DISPLAY DELIVERY" : "RESOURCE DELIVERY" }}</small><h3>{{ item.label }}</h3></div>
          <VtBadge :tone="deliveryTone(item.state)">{{ deliveryLabel(item.state) }}</VtBadge>
        </header>
        <dl>
          <div><dt>Desired</dt><dd>{{ item.desiredVersion ?? "Chưa đặt" }}</dd></div>
          <div><dt>Active</dt><dd>{{ item.currentVersion ?? "Chưa report" }}</dd></div>
          <div><dt>Phase</dt><dd>{{ item.phase ?? "—" }}</dd></div>
        </dl>
        <p>{{ item.message }}</p>
      </article>
    </div>

    <div class="state-revision-note">
      <span><small>Desired revision</small><b>v{{ device.desiredState.version }}</b></span>
      <i></i>
      <span><small>Report sequence</small><b>#{{ device.reportedState.version }}</b></span>
      <p>Hai giá trị dùng cho versioning và idempotency riêng; không cần bằng nhau.</p>
    </div>

    <details class="state-raw-details">
      <summary>Dữ liệu state kỹ thuật <VtIcon name="chevron" :size="16" /></summary>
      <div class="state-grid">
        <article class="vt-panel state-card">
          <header><span class="vt-kicker">DESIRED STATE</span><VtBadge tone="info">revision {{ device.desiredState.version }}</VtBadge></header>
          <pre>{{ JSON.stringify(device.desiredState.state, null, 2) }}</pre>
        </article>
        <article class="vt-panel state-card">
          <header><span class="vt-kicker">REPORTED STATE</span><VtBadge tone="neutral">sequence {{ device.reportedState.version }}</VtBadge></header>
          <pre>{{ JSON.stringify(device.reportedState.state, null, 2) }}</pre>
        </article>
      </div>
    </details>
  </section>
</template>
