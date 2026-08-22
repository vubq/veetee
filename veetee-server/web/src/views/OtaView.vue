<script setup lang="ts">
import { computed, ref } from 'vue'
import { FileUp, RadioTower, RotateCcw } from '@lucide/vue'

import {
  ApiError, changeRollout, createRelease, getOtaSummary, listArtifacts, listReleases,
  listRollouts, publishRelease, rollbackRollout, uploadArtifact,
  type OtaArtifact, type OtaRelease, type OtaRollout, type OtaSummary,
} from '@/api/controlPlane'
import ConfirmDialog from '@/components/ConfirmDialog.vue'

const summary = ref<OtaSummary | null>(null)
const artifacts = ref<OtaArtifact[]>([])
const releases = ref<OtaRelease[]>([])
const rollouts = ref<OtaRollout[]>([])
const loading = ref(true)
const busy = ref(false)
const error = ref('')
const adminRequired = ref(false)
const file = ref<File | null>(null)
const hash = ref('')
const hashing = ref(false)
const upload = ref({ signature: '', board: '', chip: '', partition: '', provenance: '' })
const release = ref({ version: '', artifact_id: '', channel: 'stable', min_current_version: '', provenance: '', rollback_target_id: '' })
const publishPercentages = ref<Record<string, number>>({})
const confirmation = ref<{ action: 'pause' | 'resume' | 'kill'; rollout: OtaRollout } | null>(null)
const rollback = ref({ rollout_id: '', scope: 'rollout' as 'rollout' | 'cohort' | 'device', target: '' })

const selectedArtifact = computed(() => artifacts.value.find((item) => item.id === release.value.artifact_id))

function showError(reason: unknown, fallback: string) {
  if (reason instanceof ApiError && reason.status === 403) adminRequired.value = true
  error.value = reason instanceof Error ? reason.message : fallback
}

async function load() {
  loading.value = true; error.value = ''; adminRequired.value = false
  try {
    const result = await Promise.all([getOtaSummary(), listArtifacts(), listReleases(), listRollouts()])
    ;[summary.value, artifacts.value, releases.value, rollouts.value] = result
  } catch (reason) { showError(reason, 'Không tải được dữ liệu OTA.') }
  finally { loading.value = false }
}

async function selectFile(event: Event) {
  file.value = (event.target as HTMLInputElement).files?.[0] ?? null
  hash.value = ''
  if (!file.value) return
  hashing.value = true
  try {
    const digest = await crypto.subtle.digest('SHA-256', await file.value.arrayBuffer())
    hash.value = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
  } finally { hashing.value = false }
}

async function submitArtifact() {
  if (!file.value || !hash.value) return
  busy.value = true; error.value = ''
  try {
    await uploadArtifact({ file: file.value, sha256: hash.value, ...upload.value })
    file.value = null; hash.value = ''; upload.value = { signature: '', board: '', chip: '', partition: '', provenance: '' }
    await load()
  } catch (reason) { showError(reason, 'Không tải lên được artifact.') }
  finally { busy.value = false }
}

async function submitRelease() {
  if (!selectedArtifact.value) return
  busy.value = true; error.value = ''
  try {
    await createRelease({
      version: release.value.version, artifact_id: release.value.artifact_id,
      board: selectedArtifact.value.board, chip: selectedArtifact.value.chip,
      partition: selectedArtifact.value.partition, channel: release.value.channel,
      min_current_version: release.value.min_current_version, provenance: release.value.provenance,
      rollback_target_id: release.value.rollback_target_id || null, is_published: false,
    })
    release.value = { version: '', artifact_id: '', channel: 'stable', min_current_version: '', provenance: '', rollback_target_id: '' }
    await load()
  } catch (reason) { showError(reason, 'Không tạo được release.') }
  finally { busy.value = false }
}

async function publish(item: OtaRelease) {
  busy.value = true; error.value = ''
  try { await publishRelease(item.id, publishPercentages.value[item.id] ?? 100); await load() }
  catch (reason) { showError(reason, 'Không publish được release.') }
  finally { busy.value = false }
}

async function confirmRollout() {
  if (!confirmation.value) return
  busy.value = true; error.value = ''
  try { await changeRollout(confirmation.value.rollout.id, confirmation.value.action); confirmation.value = null; await load() }
  catch (reason) { showError(reason, 'Không đổi được trạng thái rollout.') }
  finally { busy.value = false }
}

async function submitRollback() {
  if (!rollback.value.rollout_id) return
  busy.value = true; error.value = ''
  const body: { scope: 'rollout' | 'cohort' | 'device'; device_id?: string; cohort?: string } = { scope: rollback.value.scope }
  if (rollback.value.scope === 'device') body.device_id = rollback.value.target.trim()
  if (rollback.value.scope === 'cohort') body.cohort = rollback.value.target.trim()
  try { await rollbackRollout(rollback.value.rollout_id, body); rollback.value = { rollout_id: '', scope: 'rollout', target: '' }; await load() }
  catch (reason) { showError(reason, 'Không kích hoạt được rollback.') }
  finally { busy.value = false }
}

function formatBytes(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KiB` : `${(bytes / 1024 / 1024).toFixed(1)} MiB`
}

void load()
</script>

<template>
  <main class="page-container main-content ota-page">
    <section class="page-toolbar"><div class="toolbar-inner"><div class="page-title-group"><span class="page-title-icon"><RadioTower :size="19" /></span><div><h1>OTA</h1><p>Artifact, release và rollout firmware</p></div></div><button class="button button-outline" type="button" :disabled="loading" @click="load">Làm mới</button></div></section>
    <div v-if="loading" class="empty-state">Đang tải OTA Console...</div>
    <div v-else-if="adminRequired" class="empty-state admin-state"><h2>Cần quyền admin</h2><p>Tài khoản hiện tại không có quyền quản lý OTA fleet.</p></div>
    <template v-else>
      <p v-if="error" class="inline-alert error">{{ error }}</p>
      <section v-if="summary" class="summary-grid" aria-label="Tổng quan OTA">
        <article><strong>{{ summary.total_devices }}</strong><span>Thiết bị</span></article><article><strong>{{ summary.bound_devices }}</strong><span>Đã liên kết</span></article><article><strong>{{ summary.total_releases }}</strong><span>Release</span></article><article><strong>{{ summary.active_rollouts }}</strong><span>Rollout đang chạy</span></article><article><strong>{{ summary.total_reports }}</strong><span>Báo cáo OTA</span></article>
      </section>

      <section class="console-panel">
        <div class="panel-heading"><div><h2>Artifact</h2><p>Trình duyệt tính SHA-256; chữ ký Ed25519 tách rời được tạo bên ngoài bằng private key không đưa vào Console.</p></div><FileUp :size="20" /></div>
        <form class="console-form" @submit.prevent="submitArtifact">
          <label class="wide"><span>Firmware binary</span><input type="file" required accept=".bin,application/octet-stream" @change="selectFile" /><small v-if="hashing">Đang tính SHA-256...</small><code v-else-if="hash" class="hash-value">{{ hash }}</code></label>
          <label><span>Board</span><input v-model="upload.board" class="text-input" required maxlength="64" /></label><label><span>Chip</span><input v-model="upload.chip" class="text-input" required maxlength="64" /></label><label><span>Partition</span><input v-model="upload.partition" class="text-input" required maxlength="64" /></label>
          <label class="wide"><span>Detached signature hex (128 ký tự)</span><input v-model="upload.signature" class="text-input mono" required pattern="[0-9a-fA-F]{128}" maxlength="128" autocomplete="off" /></label>
          <label class="wide"><span>Provenance</span><input v-model="upload.provenance" class="text-input" required maxlength="512" /></label>
          <button class="button button-primary" type="submit" :disabled="busy || hashing || !hash">Tải artifact</button>
        </form>
        <div class="table-scroll" role="region" aria-label="Danh sách artifact" tabindex="0"><table class="data-table ota-table"><thead><tr><th>Tệp</th><th>Target</th><th>Kích thước</th><th>SHA-256</th><th>Key</th><th>Provenance</th></tr></thead><tbody><tr v-for="item in artifacts" :key="item.id"><td>{{ item.file_name }}</td><td>{{ item.board }} · {{ item.chip }} · {{ item.partition }}</td><td>{{ formatBytes(item.file_size) }}</td><td><code>{{ item.sha256.slice(0, 12) }}…</code></td><td>{{ item.signature_key_id }}</td><td>{{ item.provenance }}</td></tr><tr v-if="!artifacts.length"><td colspan="6">Chưa có artifact.</td></tr></tbody></table></div>
      </section>

      <section class="console-panel">
        <div class="panel-heading"><div><h2>Release</h2><p>Target được lấy chính xác từ artifact đã chọn.</p></div></div>
        <form class="console-form" @submit.prevent="submitRelease">
          <label><span>Artifact</span><select v-model="release.artifact_id" class="text-input" required><option value="">Chọn artifact</option><option v-for="item in artifacts" :key="item.id" :value="item.id">{{ item.file_name }} · {{ item.board }}</option></select></label>
          <label><span>SemVer</span><input v-model="release.version" class="text-input" required placeholder="1.2.3" maxlength="64" /></label><label><span>Kênh</span><input v-model="release.channel" class="text-input" required maxlength="64" /></label><label><span>Phiên bản tối thiểu</span><input v-model="release.min_current_version" class="text-input" placeholder="Tùy chọn" maxlength="64" /></label>
          <label><span>Rollback target</span><select v-model="release.rollback_target_id" class="text-input"><option value="">Không có</option><option v-for="item in releases.filter((candidate) => candidate.is_published)" :key="item.id" :value="item.id">{{ item.version }} · {{ item.channel }}</option></select></label>
          <label class="wide"><span>Provenance</span><input v-model="release.provenance" class="text-input" required maxlength="512" /></label><button class="button button-primary" type="submit" :disabled="busy || !selectedArtifact">Tạo release</button>
        </form>
        <div class="table-scroll" role="region" aria-label="Danh sách release" tabindex="0"><table class="data-table ota-table"><thead><tr><th>Version</th><th>Target</th><th>Kênh</th><th>Trạng thái</th><th>Rollout %</th><th>Thao tác</th></tr></thead><tbody><tr v-for="item in releases" :key="item.id"><td>{{ item.version }}</td><td>{{ item.board }} · {{ item.chip }} · {{ item.partition }}</td><td>{{ item.channel }}</td><td><span class="neutral-badge">{{ item.is_published ? 'Đã publish' : 'Nháp' }}</span></td><td><input v-model.number="publishPercentages[item.id]" class="percent-input" type="number" min="0" max="100" :placeholder="'100'" /></td><td><button class="button button-outline" type="button" :disabled="busy" @click="publish(item)">{{ item.is_published ? 'Cập nhật rollout' : 'Publish' }}</button></td></tr><tr v-if="!releases.length"><td colspan="6">Chưa có release.</td></tr></tbody></table></div>
      </section>

      <section class="console-panel">
        <div class="panel-heading"><div><h2>Rollout và rollback</h2><p>Dừng, tiếp tục, kill và rollback đều cần xác nhận hoặc scope rõ ràng.</p></div><RotateCcw :size="20" /></div>
        <div class="table-scroll" role="region" aria-label="Danh sách rollout" tabindex="0"><table class="data-table ota-table"><thead><tr><th>Release</th><th>Kênh</th><th>Phạm vi</th><th>Phần trăm</th><th>Trạng thái</th><th>Thao tác</th></tr></thead><tbody><tr v-for="item in rollouts" :key="item.id"><td>{{ releases.find((releaseItem) => releaseItem.id === item.release_id)?.version || item.release_id }}</td><td>{{ item.channel }}</td><td>{{ item.kind === 'rollback' ? `rollback · ${item.rollback_scope}` : item.kind }}</td><td>{{ item.cohort_percentage }}%</td><td><span class="neutral-badge">{{ item.status }}</span></td><td class="row-actions"><button v-if="item.status === 'active'" class="button button-outline" type="button" @click="confirmation = { action: 'pause', rollout: item }">Tạm dừng</button><button v-if="item.status === 'paused'" class="button button-outline" type="button" @click="confirmation = { action: 'resume', rollout: item }">Tiếp tục</button><button v-if="item.status !== 'killed'" class="danger-action" type="button" @click="confirmation = { action: 'kill', rollout: item }">Kill</button></td></tr><tr v-if="!rollouts.length"><td colspan="6">Chưa có rollout.</td></tr></tbody></table></div>
        <form class="rollback-form" @submit.prevent="submitRollback"><label><span>Rollout nguồn</span><select v-model="rollback.rollout_id" class="text-input" required><option value="">Chọn rollout</option><option v-for="item in rollouts.filter((candidate) => candidate.kind === 'release')" :key="item.id" :value="item.id">{{ releases.find((releaseItem) => releaseItem.id === item.release_id)?.version || item.id }}</option></select></label><label><span>Scope</span><select v-model="rollback.scope" class="text-input"><option value="rollout">Toàn rollout</option><option value="cohort">Cohort</option><option value="device">Thiết bị</option></select></label><label v-if="rollback.scope !== 'rollout'"><span>{{ rollback.scope === 'device' ? 'Device ID' : 'Cohort' }}</span><input v-model="rollback.target" class="text-input" required maxlength="128" /></label><button class="button button-danger" type="submit" :disabled="busy">Kích hoạt rollback</button></form>
      </section>

      <section v-if="summary?.devices_by_board_version_cohort.length" class="console-panel"><div class="panel-heading"><div><h2>Phân bố thiết bị</h2></div></div><div class="table-scroll" role="region" aria-label="Phân bố thiết bị" tabindex="0"><table class="data-table"><thead><tr><th>Board</th><th>Version</th><th>Cohort</th><th>Số lượng</th></tr></thead><tbody><tr v-for="group in summary.devices_by_board_version_cohort" :key="`${group.board}-${group.version}-${group.cohort}`"><td>{{ group.board || 'Chưa ghi nhận' }}</td><td>{{ group.version || 'Chưa ghi nhận' }}</td><td>{{ group.cohort || 'Chưa có' }}</td><td>{{ group.count }}</td></tr></tbody></table></div></section>
    </template>
    <ConfirmDialog :open="Boolean(confirmation)" :title="`${confirmation?.action === 'kill' ? 'Kill' : confirmation?.action === 'pause' ? 'Tạm dừng' : 'Tiếp tục'} rollout?`" message="Thao tác thay đổi eligibility OTA của các thiết bị trong rollout." :confirm-label="confirmation?.action === 'kill' ? 'Kill rollout' : 'Xác nhận'" :danger="confirmation?.action === 'kill'" :busy="busy" @close="confirmation = null" @confirm="confirmRollout" />
  </main>
</template>
