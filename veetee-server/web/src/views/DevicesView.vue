<script setup>
import { Clock3, Cpu, KeyRound, Plus, RadioTower } from '@lucide/vue'
import DeviceRow from '../components/device/DeviceRow.vue'
import PageHeader from '../components/layout/PageHeader.vue'
import UiBadge from '../components/ui/UiBadge.vue'
import UiButton from '../components/ui/UiButton.vue'
import UiEmptyState from '../components/ui/UiEmptyState.vue'
import UiInput from '../components/ui/UiInput.vue'
import UiSelect from '../components/ui/UiSelect.vue'

const props = defineProps({
  devices: { type: Array, default: () => [] },
  pendingDevices: { type: Array, default: () => [] },
  assistants: { type: Array, default: () => [] },
  pairing: { type: Object, required: true },
  busy: Boolean,
  fmtTime: { type: Function, required: true },
})
const emit = defineEmits(['pair', 'update-device', 'revoke', 'update-pairing'])
const assistantOptions = () => props.assistants.map(item => ({ value: item.id, label: item.name, description: item.model || 'Global model' }))
function setPairing(key, value) { emit('update-pairing', key, value) }
</script>

<template>
  <div class="workspace-view devices-view">
    <PageHeader
      eyebrow="Thiết bị"
      title="Devices & Pairing"
      description="Pair ESP32, gán Assistant và quản lý credential của từng thiết bị."
    >
      <template #actions>
        <UiBadge :tone="pendingDevices.length ? 'warning' : 'neutral'" dot>
          {{ pendingDevices.length }} pending
        </UiBadge>
      </template>
    </PageHeader>

    <div class="devices-layout">
      <section class="section-card pairing-card">
        <header class="section-card__header">
          <div class="section-card__title-with-icon">
            <span><KeyRound :size="18" /></span>
            <div>
              <h2>Pair thiết bị mới</h2>
              <p>Nhập mã 6 số hiển thị trên firmware.</p>
            </div>
          </div>
        </header>

        <div class="pairing-card__body">
          <UiInput
            :model-value="pairing.code"
            label="Pairing code"
            maxlength="6"
            inputmode="numeric"
            placeholder="000000"
            class="pair-code-input"
            hint="Mã pairing chỉ có hiệu lực trong thời gian ngắn."
            @update:model-value="setPairing('code', $event.replace(/\D/g, '').slice(0,6))"
          />

          <UiSelect
            :model-value="pairing.assistant_id"
            label="Assistant"
            :options="assistantOptions()"
            searchable
            placeholder="Chọn Assistant"
            @update:model-value="setPairing('assistant_id', $event)"
          />

          <UiInput
            :model-value="pairing.name"
            label="Tên thiết bị"
            placeholder="Robot phòng khách"
            @update:model-value="setPairing('name', $event)"
          />

          <UiInput
            :model-value="pairing.owner_id"
            label="Memory owner"
            placeholder="Để trống = owner mặc định"
            maxlength="128"
            hint="Dùng để tách durable memory theo người sở hữu."
            @update:model-value="setPairing('owner_id', $event)"
          />

          <UiButton variant="primary" :loading="busy" class="w-full" @click="emit('pair')">
            <template #icon><Plus :size="16" /></template>
            Xác nhận pair
          </UiButton>
        </div>

        <div class="pending-panel">
          <div class="pending-panel__header">
            <div>
              <strong>Yêu cầu đang chờ</strong>
              <small>Chọn một request để điền mã tự động.</small>
            </div>
            <UiBadge :tone="pendingDevices.length ? 'warning' : 'neutral'">{{ pendingDevices.length }}</UiBadge>
          </div>

          <div v-if="pendingDevices.length" class="pending-list">
            <button
              v-for="item in pendingDevices"
              :key="item.device_key"
              type="button"
              class="pending-item"
              @click="setPairing('code', item.code)"
            >
              <span class="pending-item__code">{{ item.code }}</span>
              <span class="pending-item__device">{{ item.device_id }}</span>
              <small><Clock3 :size="13" /> {{ fmtTime(item.expires_at) }}</small>
            </button>
          </div>

          <div v-else class="pending-empty">
            <RadioTower :size="18" />
            <span>Chưa có thiết bị chờ pair.</span>
          </div>
        </div>
      </section>

      <section class="section-card devices-card">
        <header class="section-card__header">
          <div class="section-card__title-with-icon">
            <span><Cpu :size="18" /></span>
            <div>
              <h2>Thiết bị đã pair</h2>
              <p>Đổi tên, binding, memory owner hoặc revoke credential.</p>
            </div>
          </div>
          <UiBadge tone="neutral">{{ devices.length }} devices</UiBadge>
        </header>

        <div v-if="devices.length" class="device-list">
          <DeviceRow
            v-for="device in devices"
            :key="`${device.device_id}/${device.client_id}`"
            :device="device"
            :assistants="assistants"
            @update="(item, changes) => emit('update-device', item, changes)"
            @revoke="emit('revoke', $event)"
          />
        </div>

        <UiEmptyState
          v-else
          title="Chưa có thiết bị"
          description="Khi ESP32 gửi yêu cầu pairing, request sẽ xuất hiện ở cột bên trái."
        >
          <template #icon><Cpu :size="24" /></template>
        </UiEmptyState>
      </section>
    </div>
  </div>
</template>
