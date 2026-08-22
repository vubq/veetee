<script setup lang="ts">
import {
  AlertCircle,
  AlertTriangle,
  Check,
  Copy,
  KeyRound,
  RefreshCw,
  Search,
  UserPlus,
} from '@lucide/vue'
import { onMounted, ref } from 'vue'

import RoleGate from '@/components/RoleGate.vue'
import UiDialog from '@/components/UiDialog.vue'
import {
  AdminUser,
  ApiError,
  createUser,
  issueUserResetToken,
  listUsers,
  updateUser,
} from '@/api/controlPlane'

const users = ref<AdminUser[]>([])
const totalUsers = ref(0)
const page = ref(1)
const loading = ref(true)
const error = ref('')
const actionError = ref('')
const isForbidden = ref(false)

// Filters
const filterSearch = ref('')
const filterRole = ref<'owner' | 'admin' | ''>('')
const filterStatus = ref<'active' | 'suspended' | ''>('')

// Create User dialog
const createOpen = ref(false)
const newEmail = ref('')
const newRole = ref<'owner' | 'admin'>('admin')
const newStatus = ref<'active' | 'suspended'>('active')
const creating = ref(false)

// One-time Reset Token Modal
const resetTokenModalOpen = ref(false)
const oneTimeResetToken = ref('')
const resetTokenTargetEmail = ref('')
const copied = ref(false)

// Edit User dialog
const editOpen = ref(false)
const editUserObj = ref<AdminUser | null>(null)
const editRole = ref<'owner' | 'admin'>('admin')
const editStatus = ref<'active' | 'suspended'>('active')
const updating = ref(false)

async function loadUsers() {
  loading.value = true
  error.value = ''
  actionError.value = ''
  isForbidden.value = false
  try {
    const res = await listUsers({
      page: page.value,
      limit: 20,
      role: filterRole.value || undefined,
      status: filterStatus.value || undefined,
      search: filterSearch.value.trim() || undefined,
    })
    users.value = res.items
    totalUsers.value = res.total
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      isForbidden.value = true
    } else {
      error.value = err instanceof Error ? err.message : 'Không thể tải danh sách người dùng.'
    }
  } finally {
    loading.value = false
  }
}

async function handleCreateUser() {
  if (!newEmail.value.trim()) return
  creating.value = true
  actionError.value = ''
  try {
    const res = await createUser({
      email: newEmail.value.trim(),
      role: newRole.value,
      status: newStatus.value,
    })
    createOpen.value = false
    newEmail.value = ''
    // Show one time reset token modal
    resetTokenTargetEmail.value = res.user.email
    oneTimeResetToken.value = res.reset_token
    resetTokenModalOpen.value = true
    await loadUsers()
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      actionError.value = 'Cần quyền admin để tạo người dùng mới.'
    } else if (err instanceof ApiError && err.status === 409) {
      actionError.value = 'Email người dùng đã tồn tại.'
    } else {
      actionError.value = err instanceof Error ? err.message : 'Tạo người dùng thất bại.'
    }
  } finally {
    creating.value = false
  }
}

function openEditUser(user: AdminUser) {
  editUserObj.value = user
  editRole.value = user.role
  editStatus.value = user.status
  editOpen.value = true
}

async function handleUpdateUser() {
  if (!editUserObj.value) return
  updating.value = true
  actionError.value = ''
  try {
    const updated = await updateUser(editUserObj.value.id, {
      expected_version: editUserObj.value.version,
      role: editRole.value,
      status: editStatus.value,
    })
    const idx = users.value.findIndex(u => u.id === updated.id)
    if (idx >= 0) users.value[idx] = updated
    editOpen.value = false
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      actionError.value = 'Cần quyền admin để cập nhật người dùng.'
    } else if (err instanceof ApiError && err.status === 409) {
      actionError.value = 'Xung đột phiên bản: Người dùng đã được thay đổi bởi yêu cầu khác. Vui lòng tải lại.'
      await loadUsers()
    } else {
      actionError.value = err instanceof Error ? err.message : 'Cập nhật người dùng thất bại.'
    }
  } finally {
    updating.value = false
  }
}

async function handleIssueResetToken(user: AdminUser) {
  actionError.value = ''
  try {
    const res = await issueUserResetToken(user.id)
    resetTokenTargetEmail.value = user.email
    oneTimeResetToken.value = res.reset_token
    resetTokenModalOpen.value = true
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      actionError.value = 'Cần quyền admin để cấp token đặt lại mật khẩu.'
    } else {
      actionError.value = err instanceof Error ? err.message : 'Cấp token thất bại.'
    }
  }
}

async function copyToken() {
  if (!oneTimeResetToken.value) return
  await navigator.clipboard.writeText(oneTimeResetToken.value)
  copied.value = true
  setTimeout(() => (copied.value = false), 2000)
}

onMounted(() => loadUsers())
</script>

<template>
  <div class="page-container console-view">
    <div class="page-header">
      <div>
        <h1>Quản lý người dùng</h1>
        <p class="subtitle">Danh sách người dùng, phân quyền Owner/Admin và quản lý trạng thái tài khoản.</p>
      </div>
      <div class="header-actions">
        <button class="primary-button" type="button" :disabled="isForbidden" @click="createOpen = true">
          <UserPlus :size="16" />
          <span>Thêm người dùng</span>
        </button>
        <button class="icon-button" type="button" title="Tải lại" :disabled="loading" @click="loadUsers">
          <RefreshCw :size="16" :class="{ 'spin': loading }" />
        </button>
      </div>
    </div>

    <RoleGate v-if="isForbidden" />

    <div v-else-if="loading" class="state-card loading-card">
      <RefreshCw :size="24" class="spin" />
      <p>Đang tải danh sách người dùng...</p>
    </div>

    <div v-else-if="error" class="state-card error-card">
      <AlertCircle :size="24" />
      <p>{{ error }}</p>
      <button class="primary-button" type="button" @click="loadUsers">Thử lại</button>
    </div>

    <div v-else class="view-content">
      <div v-if="actionError" class="alert-box is-error" role="alert">
        <AlertCircle :size="16" />
        <span>{{ actionError }}</span>
      </div>

      <!-- Filters -->
      <div class="filter-bar">
        <div class="search-box">
          <Search :size="16" />
          <input
            v-model="filterSearch"
            type="text"
            class="text-input search-input"
            placeholder="Tìm theo email..."
            @keydown.enter="loadUsers"
          />
        </div>
        <select v-model="filterRole" class="text-input select-input" @change="loadUsers">
          <option value="">-- Tất cả vai trò --</option>
          <option value="owner">Owner</option>
          <option value="admin">Admin</option>
        </select>
        <select v-model="filterStatus" class="text-input select-input" @change="loadUsers">
          <option value="">-- Tất cả trạng thái --</option>
          <option value="active">Hoạt động (Active)</option>
          <option value="suspended">Tạm khóa (Suspended)</option>
        </select>
      </div>

      <div class="card">
        <div class="table-container">
          <table class="data-table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Vai trò</th>
                <th>Trạng thái</th>
                <th>Phiên bản</th>
                <th class="actions-col">Thao tác</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="user in users" :key="user.id">
                <td class="font-medium">{{ user.email }}</td>
                <td>
                  <span class="badge" :class="user.role === 'owner' ? 'kind-badge' : 'enabled-badge'">
                    {{ user.role.toUpperCase() }}
                  </span>
                </td>
                <td>
                  <span class="badge" :class="user.status === 'active' ? 'enabled-badge' : 'disabled-badge'">
                    {{ user.status === 'active' ? 'Hoạt động' : 'Tạm khóa' }}
                  </span>
                </td>
                <td>v{{ user.version }}</td>
                <td class="actions-col">
                  <button class="secondary-button compact" type="button" @click="openEditUser(user)">
                    Sửa
                  </button>
                  <button class="secondary-button compact" type="button" title="Tạo token đặt lại mật khẩu" @click="handleIssueResetToken(user)">
                    <KeyRound :size="14" />
                    <span>Reset Token</span>
                  </button>
                </td>
              </tr>
              <tr v-if="users.length === 0">
                <td colspan="5" class="text-center text-muted">Không tìm thấy người dùng nào.</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <!-- Create User Dialog -->
    <UiDialog :open="createOpen" title="Thêm người dùng mới" @close="createOpen = false">
      <form class="dialog-form" @submit.prevent="handleCreateUser">
        <div class="form-group">
          <label for="u-email">Email <span class="required">*</span></label>
          <input id="u-email" v-model="newEmail" type="email" class="text-input" required placeholder="user@example.test" />
        </div>
        <div class="form-group">
          <label for="u-role">Vai trò</label>
          <select id="u-role" v-model="newRole" class="text-input select-input">
            <option value="admin">Admin</option>
            <option value="owner">Owner</option>
          </select>
        </div>
        <div class="form-group">
          <label for="u-status">Trạng thái</label>
          <select id="u-status" v-model="newStatus" class="text-input select-input">
            <option value="active">Hoạt động (Active)</option>
            <option value="suspended">Tạm khóa (Suspended)</option>
          </select>
        </div>
      </form>
      <template #footer>
        <button class="secondary-button" type="button" @click="createOpen = false">Hủy</button>
        <button class="primary-button" type="button" :disabled="creating || !newEmail.trim()" @click="handleCreateUser">
          {{ creating ? 'Đang tạo...' : 'Tạo mới' }}
        </button>
      </template>
    </UiDialog>

    <!-- Edit User Dialog -->
    <UiDialog :open="editOpen" title="Cập nhật người dùng" @close="editOpen = false">
      <form class="dialog-form" @submit.prevent="handleUpdateUser">
        <div class="form-group">
          <label>Email: <strong>{{ editUserObj?.email }}</strong></label>
        </div>
        <div class="form-group">
          <label for="eu-role">Vai trò</label>
          <select id="eu-role" v-model="editRole" class="text-input select-input">
            <option value="admin">Admin</option>
            <option value="owner">Owner</option>
          </select>
        </div>
        <div class="form-group">
          <label for="eu-status">Trạng thái</label>
          <select id="eu-status" v-model="editStatus" class="text-input select-input">
            <option value="active">Hoạt động (Active)</option>
            <option value="suspended">Tạm khóa (Suspended)</option>
          </select>
        </div>
      </form>
      <template #footer>
        <button class="secondary-button" type="button" @click="editOpen = false">Hủy</button>
        <button class="primary-button" type="button" :disabled="updating" @click="handleUpdateUser">
          {{ updating ? 'Đang lưu...' : 'Lưu thay đổi' }}
        </button>
      </template>
    </UiDialog>

    <!-- One-time Reset Token Modal -->
    <UiDialog :open="resetTokenModalOpen" title="Token đặt lại mật khẩu khởi tạo" size="medium" @close="resetTokenModalOpen = false">
      <div class="confirm-modal-body" data-testid="one-time-token-modal">
        <div class="alert-box is-warning">
          <AlertTriangle :size="20" />
          <div>
            <strong>TOKEN NÀY CHỈ HIỂN THỊ MỘT LẦN DUY NHẤT</strong>
            <p>Vui lòng sao chép và lưu lại token này ngay. Token sẽ KHÔNG BAO GIỜ được hiển thị lại và KHÔNG được lưu trong hệ thống.</p>
          </div>
        </div>

        <div class="confirm-details">
          <p><strong>Người dùng:</strong> {{ resetTokenTargetEmail }}</p>
          <div class="token-copy-box">
            <input type="text" readonly class="text-input font-mono readonly-token" :value="oneTimeResetToken" />
            <button class="primary-button compact" type="button" @click="copyToken">
              <Check v-if="copied" :size="15" />
              <Copy v-else :size="15" />
              <span>{{ copied ? 'Đã sao chép' : 'Sao chép' }}</span>
            </button>
          </div>
        </div>
      </div>
      <template #footer>
        <button class="primary-button" type="button" @click="resetTokenModalOpen = false">Đã lưu token & Đóng</button>
      </template>
    </UiDialog>
  </div>
</template>
