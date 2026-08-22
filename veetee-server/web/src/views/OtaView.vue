<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  createOtaRelease,
  publishOtaRelease,
  uploadOtaArtifact,
  type ArtifactUploadResponse,
  type FirmwareReleaseSummary,
} from '@/api/controlPlane'
import UiDialog from '@/components/UiDialog.vue'

const selectedFile = ref<File | null>(null)
const uploading = ref(false)
const uploadError = ref('')
const uploadedArtifact = ref<ArtifactUploadResponse | null>(null)

const version = ref('')
const board = ref('')
const chip = ref('')
const partition = ref('')
const force = ref(false)

const creating = ref(false)
const createError = ref('')
const createdRelease = ref<FirmwareReleaseSummary | null>(null)

const publishing = ref(false)
const publishError = ref('')
const showPublishConfirm = ref(false)
const publishSuccess = ref(false)

const isVersionValid = computed(() => /^[0-9]+(?:\.[0-9]+)*$/.test(version.value.trim()))
const releaseFormValid = computed(
  () =>
    Boolean(uploadedArtifact.value)
    && isVersionValid.value
    && board.value.trim().length > 0
    && chip.value.trim().length > 0
    && partition.value.trim().length > 0,
)

function handleFileChange(event: Event) {
  const target = event.target as HTMLInputElement
  if (target.files && target.files.length > 0) {
    selectedFile.value = target.files[0]
    uploadError.value = ''
    uploadedArtifact.value = null
    createdRelease.value = null
    publishSuccess.value = false
    publishError.value = ''
  }
}

async function handleUpload() {
  if (!selectedFile.value || uploading.value) return
  uploading.value = true
  uploadError.value = ''
  try {
    const arrayBuffer = await selectedFile.value.arrayBuffer()
    uploadedArtifact.value = await uploadOtaArtifact(arrayBuffer)
  } catch (error) {
    uploadError.value = error instanceof Error ? error.message : 'Tải lên artifact thất bại.'
  } finally {
    uploading.value = false
  }
}

async function handleCreateRelease() {
  if (!uploadedArtifact.value || !releaseFormValid.value || creating.value) return
  creating.value = true
  createError.value = ''
  publishSuccess.value = false
  try {
    createdRelease.value = await createOtaRelease({
      artifact_id: uploadedArtifact.value.id,
      version: version.value.trim(),
      board: board.value.trim(),
      chip: chip.value.trim(),
      partition: partition.value.trim(),
      force: force.value,
    })
  } catch (error) {
    createError.value = error instanceof Error ? error.message : 'Tạo bản phát hành thất bại.'
  } finally {
    creating.value = false
  }
}

function requestPublish() {
  if (!createdRelease.value || createdRelease.value.published || publishing.value) return
  publishError.value = ''
  showPublishConfirm.value = true
}

async function confirmPublish() {
  if (!createdRelease.value || publishing.value) return
  publishing.value = true
  publishError.value = ''
  try {
    const published = await publishOtaRelease(createdRelease.value.id)
    createdRelease.value = published
    publishSuccess.value = published.published
    showPublishConfirm.value = false
  } catch (error) {
    publishError.value = error instanceof Error ? error.message : 'Xuất bản bản phát hành thất bại.'
  } finally {
    publishing.value = false
  }
}
</script>

<template>
  <main class="page-container ota-view">
    <div class="page-header" style="margin-bottom: 24px;">
      <h1>Firmware OTA</h1>
      <p class="muted">Tải lên artifact binary, tạo bản phát hành rồi xuất bản cho thiết bị tương thích.</p>
    </div>

    <div class="ota-card-grid">
      <!-- Bước 1: Tải lên artifact -->
      <section class="ota-step-card">
        <div class="ota-step-header">
          <span class="ota-step-number">1</span>
          <h2>Tải artifact binary</h2>
        </div>

        <div class="auth-field" style="margin-bottom: 16px;">
          <span>Chọn file firmware binary (.bin)</span>
          <input
            type="file"
            accept=".bin,application/octet-stream"
            data-testid="ota-file-input"
            :disabled="uploading"
            @change="handleFileChange"
          />
        </div>

        <p v-if="selectedFile" class="muted" style="margin-bottom: 12px; font-size: 13px;">
          Tệp đã chọn: <strong>{{ selectedFile.name }}</strong> ({{ selectedFile.size }} byte)
        </p>

        <div v-if="uploadError" class="auth-error" style="margin-bottom: 12px;" role="alert" data-testid="ota-upload-error">
          {{ uploadError }}
        </div>

        <button
          type="button"
          class="button button-primary"
          :disabled="!selectedFile || uploading"
          data-testid="ota-upload-btn"
          @click="handleUpload"
        >
          {{ uploading ? 'Đang tải artifact...' : 'Tải artifact' }}
        </button>

        <div v-if="uploadedArtifact" class="ota-info-box" style="margin-top: 16px;" data-testid="ota-artifact-info">
          <strong style="color: var(--primary);">Artifact đã sẵn sàng:</strong>
          <p>ID: {{ uploadedArtifact.id }}</p>
          <p>Kích thước: {{ uploadedArtifact.size }} byte</p>
          <p>SHA-256: {{ uploadedArtifact.sha256 }}</p>
        </div>
      </section>

      <!-- Bước 2: Tạo release -->
      <section class="ota-step-card">
        <div class="ota-step-header">
          <span class="ota-step-number">2</span>
          <h2>Tạo bản phát hành</h2>
        </div>

        <p v-if="!uploadedArtifact" class="ota-file-hint">Cần tải lên artifact ở bước 1 trước khi tạo bản phát hành.</p>

        <form @submit.prevent="handleCreateRelease">
          <div class="ota-form-grid">
            <label class="auth-field">
              <span>Phiên bản (ví dụ 1.0.0)</span>
              <input v-model="version" type="text" required :disabled="!uploadedArtifact || creating" data-testid="ota-version" />
            </label>

            <label class="auth-field">
              <span>Bo mạch (Board)</span>
              <input v-model="board" type="text" required maxlength="128" :disabled="!uploadedArtifact || creating" data-testid="ota-board" />
            </label>

            <label class="auth-field">
              <span>Chip</span>
              <input v-model="chip" type="text" required maxlength="64" :disabled="!uploadedArtifact || creating" data-testid="ota-chip" />
            </label>

            <label class="auth-field">
              <span>Phân vùng</span>
              <input v-model="partition" type="text" required maxlength="64" :disabled="!uploadedArtifact || creating" data-testid="ota-partition" />
            </label>
          </div>

          <p v-if="uploadedArtifact && !isVersionValid" class="field-help" role="alert">
            Phiên bản chỉ gồm các số cách nhau bằng dấu chấm, ví dụ 1.0.0.
          </p>

          <div style="margin-bottom: 16px;">
            <label style="display: flex; align-items: center; gap: 8px; font-size: 14px; cursor: pointer;">
              <input v-model="force" type="checkbox" :disabled="!uploadedArtifact || creating" data-testid="ota-force" />
              <span>Cập nhật cả thiết bị đang dùng phiên bản mới hơn (Force)</span>
            </label>
          </div>

          <div v-if="createError" class="auth-error" style="margin-bottom: 12px;" role="alert" data-testid="ota-create-error">
            {{ createError }}
          </div>

          <button
            type="submit"
            class="button button-primary"
            :disabled="!releaseFormValid || creating"
            data-testid="ota-create-release-btn"
          >
            {{ creating ? 'Đang tạo bản phát hành...' : 'Tạo bản phát hành' }}
          </button>
        </form>

        <div v-if="createdRelease" class="ota-info-box" style="margin-top: 16px;" data-testid="ota-release-info">
          <strong style="color: var(--primary);">Bản phát hành đã tạo:</strong>
          <p>Release ID: {{ createdRelease.id }}</p>
          <p>Phiên bản: {{ createdRelease.version }} ({{ createdRelease.board }})</p>
          <p>Trạng thái: {{ createdRelease.published ? 'Đã xuất bản' : 'Chưa xuất bản' }}</p>
        </div>
      </section>

      <!-- Bước 3: Publish -->
      <section class="ota-step-card">
        <div class="ota-step-header">
          <span class="ota-step-number">3</span>
          <h2>Xuất bản</h2>
        </div>

        <p v-if="!createdRelease" class="ota-file-hint">Cần tạo bản phát hành ở bước 2 trước khi xuất bản.</p>

        <div v-if="publishSuccess" class="ota-info-box is-success" data-testid="ota-publish-success">
          <strong>Xuất bản thành công!</strong>
          <p>Bản phát hành {{ createdRelease?.version }} cho {{ createdRelease?.board }} đã được xuất bản.</p>
        </div>

        <div v-if="publishError" class="auth-error" style="margin-bottom: 12px;" role="alert" data-testid="ota-publish-error">
          {{ publishError }}
        </div>

        <button
          type="button"
          class="button button-primary"
          :disabled="!createdRelease || createdRelease.published || publishing"
          data-testid="ota-publish-btn"
          @click="requestPublish"
        >
          {{ createdRelease?.published ? 'Đã xuất bản' : publishing ? 'Đang xuất bản...' : 'Xuất bản bản phát hành' }}
        </button>
      </section>
    </div>

    <!-- Xác nhận publish -->
    <UiDialog
      :open="showPublishConfirm"
      title="Xác nhận xuất bản bản phát hành?"
      :description="`Bản phát hành ${createdRelease?.version ?? ''} cho board ${createdRelease?.board ?? ''} sẽ hiển thị tới các thiết bị phù hợp.`"
      variant="compact"
      @close="showPublishConfirm = false"
    >
      <p class="confirmation-copy">Sau khi xuất bản, thiết bị liên kết sẽ nhận bản cập nhật trong lần kiểm tra OTA kế tiếp.</p>
      <template #footer>
        <button class="button button-outline" type="button" :disabled="publishing" data-testid="ota-publish-cancel" @click="showPublishConfirm = false">
          Hủy
        </button>
        <button class="button button-primary" type="button" :disabled="publishing" data-testid="ota-publish-confirm" @click="confirmPublish">
          {{ publishing ? 'Đang xuất bản...' : 'Xác nhận xuất bản' }}
        </button>
      </template>
    </UiDialog>
  </main>
</template>
