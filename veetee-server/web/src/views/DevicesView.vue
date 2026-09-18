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
    <PageHeader eyebrow="04 / Hardware" title="Devices & Pairing" description="Onboard ESP32, bind Assistant và quản lý credential của từng thiết bị từ một control panel duy nhất." />

    <div class="device-workspace">
      <section class="pair-station">
        <header class="pair-station__head">
          <div class="pair-station__symbol"><KeyRound :size="20" /></div>
          <div>
            <span>NEW HARDWARE</span>
            <h2>Pairing station</h2>
            <p>Nhập mã 6 số từ firmware stock, chọn Assistant và đặt tên thiết bị.</p>
          </div>
        </header>

        <div class="pair-station__code">
          <span>PAIRING CODE</span>
          <UiInput
            :model-value="pairing.code"
            maxlength="6"
            inputmode="numeric"
            placeholder="000000"
            class="pair-code-input"
            @update:model-value="setPairing('code', $event.replace(/\D/g, '').slice(0,6))"
          />
          <small>Mã chỉ có hiệu lực trong vài phút.</small>
        </div>

        <div class="pair-station__fields">
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
            hint="Tách durable memory theo người sở hữu thiết bị."
            @update:model-value="setPairing('owner_id', $event)"
          />
        </div>

        <UiButton variant="primary" :loading="busy" class="w-full pair-station__submit" @click="emit('pair')">
          <template #icon><Plus :size="15" /></template>
          Duyệt & Pair thiết bị
        </UiButton>

        <div class="pair-queue">
          <div class="pair-queue__head">
            <div><span>WAITING ROOM</span><strong>Pending requests</strong></div>
            <UiBadge tone="warning">{{ pendingDevices.length }}</UiBadge>
          </div>

          <div v-if="pendingDevices.length" class="pair-queue__list">
            <button
              v-for="item in pendingDevices"
              :key="item.device_key"
              type="button"
              class="pair-request"
              @click="setPairing('code', item.code)"
            >
              <div>
                <strong>{{ item.code }}</strong>
                <span>{{ item.device_id }}</span>
              </div>
              <small><Clock3 :size="11" /> {{ fmtTime(item.expires_at) }}</small>
            </button>
          </div>
          <div v-else class="pair-queue__empty">
            <RadioTower :size="17" />
            <span>Chưa có thiết bị chờ pair.</span>
          </div>
        </div>
      </section>

      <section class="fleet-workspace">
        <header class="fleet-workspace__head">
          <div>
            <span>PAIRED FLEET</span>
            <h2>Thiết bị đã pair</h2>
            <p>Đổi tên, Assistant binding hoặc revoke credential mà không cần chỉnh firmware.</p>
          </div>
          <div class="fleet-workspace__count">
            <Cpu :size="15" />
            <strong>{{ devices.length }}</strong>
            <span>devices</span>
          </div>
        </header>

        <div v-if="devices.length" class="fleet-list">
          <DeviceRow
            v-for="device in devices"
            :key="`${device.device_id}/${device.client_id}`"
            :device="device"
            :assistants="assistants"
            @update="(item, changes) => emit('update-device', item, changes)"
            @revoke="emit('revoke', $event)"
          />
        </div>

        <UiEmptyState v-else title="Chưa có thiết bị" description="Pair ESP32 đầu tiên để bắt đầu quản lý fleet.">
          <template #icon><Cpu :size="24" /></template>
        </UiEmptyState>
      </section>
    </div>
  </div>
</template>