<script setup lang="ts">
import {
  AlertCircle,
  BookOpen,
  Check,
  FileText,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from '@lucide/vue'
import { onMounted, ref } from 'vue'

import UiDialog from '@/components/UiDialog.vue'
import {
  assignAgentDataset,
  createDataset,
  deleteDataset,
  deleteDocument,
  getDocumentChunks,
  listAgentDatasets,
  listAgents,
  listDatasets,
  listDocuments,
  searchKnowledge,
  unassignAgentDataset,
  uploadDocument,
  type AgentSummary,
  type KnowledgeChunk,
  type KnowledgeDataset,
  type KnowledgeDocument,
  type SearchResultItem,
} from '@/api/controlPlane'

const datasets = ref<KnowledgeDataset[]>([])
const loading = ref(true)
const error = ref('')
const actionError = ref('')

const selectedDatasetId = ref<string | null>(null)
const documents = ref<KnowledgeDocument[]>([])
const loadingDocs = ref(false)

// Chunks modal
const selectedDocId = ref<string | null>(null)
const docChunks = ref<KnowledgeChunk[]>([])
const chunksOpen = ref(false)
const loadingChunks = ref(false)

// Create dataset dialog
const createOpen = ref(false)
const newDatasetName = ref('')
const newDatasetDesc = ref('')
const creating = ref(false)

// Upload document
const uploading = ref(false)
const uploadFile = ref<File | null>(null)

// Search test
const searchQuery = ref('')
const searchLimit = ref(5)
const searchResults = ref<SearchResultItem[]>([])
const searching = ref(false)
const searchDone = ref(false)

// Agent assignment
const agents = ref<AgentSummary[]>([])
const selectedAgentId = ref<string>('')
const assignedDatasetIds = ref<Set<string>>(new Set())
const loadingAgentDatasets = ref(false)

async function loadData() {
  loading.value = true
  error.value = ''
  actionError.value = ''
  try {
    const [dsList, agentList] = await Promise.all([
      listDatasets(),
      listAgents().catch(() => []),
    ])
    datasets.value = dsList
    agents.value = agentList
    if (dsList.length > 0 && !selectedDatasetId.value) {
      selectDataset(dsList[0].id)
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Không thể tải kho kiến thức.'
  } finally {
    loading.value = false
  }
}

async function selectDataset(id: string) {
  selectedDatasetId.value = id
  loadingDocs.value = true
  documents.value = []
  try {
    documents.value = await listDocuments(id)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể tải danh sách tài liệu.'
  } finally {
    loadingDocs.value = false
  }
}

async function handleCreateDataset() {
  if (!newDatasetName.value.trim()) return
  creating.value = true
  actionError.value = ''
  try {
    const created = await createDataset({
      name: newDatasetName.value.trim(),
      description: newDatasetDesc.value.trim(),
    })
    datasets.value.push(created)
    createOpen.value = false
    newDatasetName.value = ''
    newDatasetDesc.value = ''
    selectDataset(created.id)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể tạo tập dữ liệu.'
  } finally {
    creating.value = false
  }
}

async function handleDeleteDataset(id: string) {
  if (!confirm('Bạn có chắc muốn xóa tập dữ liệu này?')) return
  actionError.value = ''
  try {
    await deleteDataset(id)
    datasets.value = datasets.value.filter(d => d.id !== id)
    if (selectedDatasetId.value === id) {
      selectedDatasetId.value = datasets.value[0]?.id || null
      if (selectedDatasetId.value) selectDataset(selectedDatasetId.value)
      else documents.value = []
    }
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể xóa tập dữ liệu.'
  }
}

function handleFileSelect(e: Event) {
  const target = e.target as HTMLInputElement
  if (target.files && target.files[0]) {
    uploadFile.value = target.files[0]
  }
}

async function handleUploadDocument() {
  if (!selectedDatasetId.value || !uploadFile.value) return
  uploading.value = true
  actionError.value = ''
  try {
    const file = uploadFile.value
    const arrayBuffer = await file.arrayBuffer()
    const uploaded = await uploadDocument(
      selectedDatasetId.value,
      file.name,
      arrayBuffer,
      file.type === 'text/markdown' || file.name.toLowerCase().endsWith('.md')
        ? 'text/markdown'
        : 'text/plain',
    )
    documents.value.push(uploaded)
    uploadFile.value = null
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Tải lên tài liệu thất bại.'
  } finally {
    uploading.value = false
  }
}

async function handleDeleteDocument(docId: string) {
  if (!confirm('Bạn có chắc muốn xóa tài liệu này?')) return
  actionError.value = ''
  try {
    await deleteDocument(docId)
    documents.value = documents.value.filter(d => d.id !== docId)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể xóa tài liệu.'
  }
}

async function openChunks(docId: string) {
  selectedDocId.value = docId
  chunksOpen.value = true
  loadingChunks.value = true
  docChunks.value = []
  try {
    docChunks.value = await getDocumentChunks(docId)
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể tải đoạn tài liệu.'
  } finally {
    loadingChunks.value = false
  }
}

async function handleSearch() {
  if (!searchQuery.value.trim() || datasets.value.length === 0) return
  searching.value = true
  searchDone.value = false
  try {
    const ids = selectedDatasetId.value ? [selectedDatasetId.value] : datasets.value.map(d => d.id)
    const res = await searchKnowledge({
      dataset_ids: ids,
      query: searchQuery.value.trim(),
      limit: searchLimit.value,
    })
    searchResults.value = res.results
    searchDone.value = true
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Tìm kiếm thất bại.'
  } finally {
    searching.value = false
  }
}

async function onAgentSelect(agentId: string) {
  selectedAgentId.value = agentId
  if (!agentId) return
  loadingAgentDatasets.value = true
  try {
    const assigned = await listAgentDatasets(agentId)
    assignedDatasetIds.value = new Set(assigned.map(d => d.id))
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể tải tập dữ liệu của trợ lý.'
  } finally {
    loadingAgentDatasets.value = false
  }
}

async function toggleAgentDataset(datasetId: string) {
  if (!selectedAgentId.value) return
  const isAssigned = assignedDatasetIds.value.has(datasetId)
  try {
    if (isAssigned) {
      await unassignAgentDataset(selectedAgentId.value, datasetId)
      assignedDatasetIds.value.delete(datasetId)
    } else {
      await assignAgentDataset(selectedAgentId.value, datasetId)
      assignedDatasetIds.value.add(datasetId)
    }
  } catch (err) {
    actionError.value = err instanceof Error ? err.message : 'Không thể liên kết tập dữ liệu.'
  }
}

onMounted(() => loadData())
</script>

<template>
  <div class="page-container console-view">
    <div class="page-header">
      <div>
        <h1>Kho kiến thức</h1>
        <p class="subtitle">Quản lý tập dữ liệu RAG, tài liệu nguồn và liên kết với trợ lý.</p>
      </div>
      <div class="header-actions">
        <button class="primary-button" type="button" @click="createOpen = true">
          <Plus :size="16" />
          <span>Tạo tập dữ liệu</span>
        </button>
        <button class="icon-button" type="button" title="Tải lại" :disabled="loading" @click="loadData">
          <RefreshCw :size="16" :class="{ 'spin': loading }" />
        </button>
      </div>
    </div>

    <div v-if="loading" class="state-card loading-card">
      <RefreshCw :size="24" class="spin" />
      <p>Đang tải kho kiến thức...</p>
    </div>

    <div v-else-if="error" class="state-card error-card">
      <AlertCircle :size="24" />
      <p>{{ error }}</p>
      <button class="primary-button" type="button" @click="loadData">Thử lại</button>
    </div>

    <div v-else class="view-content knowledge-layout">
      <div v-if="actionError" class="alert-box is-error" role="alert">
        <AlertCircle :size="16" />
        <span>{{ actionError }}</span>
      </div>

      <div class="knowledge-grid">
        <!-- Datasets Sidebar / Selection -->
        <div class="card dataset-list-card">
          <div class="card-header">
            <h2>Tập dữ liệu</h2>
            <span class="badge">{{ datasets.length }}</span>
          </div>

          <div v-if="datasets.length === 0" class="empty-state">
            <BookOpen :size="28" />
            <p>Chưa có tập dữ liệu nào.</p>
          </div>

          <div v-else class="dataset-menu">
            <div
              v-for="d in datasets"
              :key="d.id"
              class="dataset-menu-item"
              :class="{ 'is-active': selectedDatasetId === d.id }"
              role="button"
              tabindex="0"
              @click="selectDataset(d.id)"
              @keydown.enter="selectDataset(d.id)"
              @keydown.space.prevent="selectDataset(d.id)"
            >
              <div class="dataset-item-info">
                <strong>{{ d.name }}</strong>
                <span v-if="d.description" class="dataset-item-desc">{{ d.description }}</span>
              </div>
              <button
                class="icon-button compact delete-btn"
                type="button"
                title="Xóa tập dữ liệu"
                @click.stop="handleDeleteDataset(d.id)"
              >
                <Trash2 :size="14" />
              </button>
            </div>
          </div>
        </div>

        <!-- Selected Dataset Content -->
        <div class="card dataset-detail-card">
          <template v-if="selectedDatasetId">
            <div class="card-header">
              <h2>Tài liệu trong tập dữ liệu</h2>
            </div>

            <!-- Upload File -->
            <div class="upload-section">
              <input
                type="file"
                data-testid="document-file-input"
                class="file-input"
                @change="handleFileSelect"
              />
              <button
                class="primary-button compact"
                type="button"
                data-testid="upload-document-btn"
                :disabled="!uploadFile || uploading"
                @click="handleUploadDocument"
              >
                <Upload :size="15" />
                <span>{{ uploading ? 'Đang tải lên...' : 'Tải lên tài liệu' }}</span>
              </button>
            </div>

            <div v-if="loadingDocs" class="state-card loading-card compact">
              <RefreshCw :size="18" class="spin" />
              <p>Đang tải tài liệu...</p>
            </div>

            <div v-else-if="documents.length === 0" class="empty-state">
              <FileText :size="28" />
              <p>Chưa có tài liệu nào trong tập này.</p>
            </div>

            <div v-else class="table-container">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>Tên file</th>
                    <th>Kích thước</th>
                    <th>Trạng thái</th>
                    <th>Số đoạn (Chunks)</th>
                    <th class="actions-col">Thao tác</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="doc in documents" :key="doc.id">
                    <td class="font-medium">{{ doc.filename }}</td>
                    <td>{{ (doc.byte_size / 1024).toFixed(1) }} KB</td>
                    <td>
                      <span class="badge" :class="doc.status === 'indexed' || doc.status === 'ready' ? 'enabled-badge' : 'kind-badge'">
                        {{ doc.status }}
                      </span>
                    </td>
                    <td>{{ doc.chunk_count }}</td>
                    <td class="actions-col">
                      <button class="secondary-button compact" type="button" @click="openChunks(doc.id)">
                        Xem chunks
                      </button>
                      <button class="icon-button compact" type="button" title="Xóa tài liệu" @click="handleDeleteDocument(doc.id)">
                        <Trash2 :size="15" />
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </template>

          <div v-else class="empty-state">
            <BookOpen :size="32" />
            <p>Vui lòng chọn hoặc tạo mới một tập dữ liệu.</p>
          </div>
        </div>
      </div>

      <!-- Retrieval Search Test Section -->
      <div class="card search-test-card">
        <div class="card-header">
          <h2>Thử nghiệm Truy vấn Kho kiến thức (RAG Search)</h2>
        </div>
        <div class="search-form">
          <div class="form-row">
            <input
              v-model="searchQuery"
              type="text"
              class="text-input search-input"
              placeholder="Nhập câu hỏi / từ khóa tìm kiếm..."
              @keydown.enter="handleSearch"
            />
            <button class="primary-button" type="button" :disabled="searching || !searchQuery.trim()" @click="handleSearch">
              <Search :size="16" />
              <span>{{ searching ? 'Đang tìm...' : 'Tìm kiếm' }}</span>
            </button>
          </div>
        </div>

        <div v-if="searchDone" class="search-results">
          <h3>Kết quả tìm kiếm ({{ searchResults.length }})</h3>
          <div v-if="searchResults.length === 0" class="empty-state compact">
            <p>Không tìm thấy kết quả phù hợp.</p>
          </div>
          <div v-else class="results-list">
            <div v-for="res in searchResults" :key="res.chunk_id" class="result-card">
              <div class="result-header">
                <span class="score-pill">Score: {{ res.score.toFixed(3) }}</span>
                <span v-if="res.filename" class="file-tag">{{ res.filename }}</span>
              </div>
              <p class="result-content">{{ res.content }}</p>
            </div>
          </div>
        </div>
      </div>

      <!-- Agent Dataset Assignment Section -->
      <div class="card agent-assign-card">
        <div class="card-header">
          <h2>Liên kết Tập dữ liệu với Trợ lý</h2>
        </div>
        <div class="assign-body">
          <div class="form-group select-group">
            <label>Chọn trợ lý:</label>
            <select :value="selectedAgentId" class="text-input select-input" @change="onAgentSelect(($event.target as HTMLSelectElement).value)">
              <option value="">-- Chọn trợ lý --</option>
              <option v-for="ag in agents" :key="ag.id" :value="ag.id">{{ ag.name }}</option>
            </select>
          </div>

          <div v-if="selectedAgentId" class="datasets-toggle-list">
            <div v-if="loadingAgentDatasets" class="state-card loading-card compact">
              <RefreshCw :size="16" class="spin" />
              <p>Đang tải tập dữ liệu đã liên kết...</p>
            </div>
            <div v-else class="toggle-grid">
              <button
                v-for="ds in datasets"
                :key="ds.id"
                class="assign-toggle-btn"
                :class="{ 'is-assigned': assignedDatasetIds.has(ds.id) }"
                type="button"
                @click="toggleAgentDataset(ds.id)"
              >
                <Check v-if="assignedDatasetIds.has(ds.id)" :size="16" />
                <span>{{ ds.name }}</span>
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Create Dataset Dialog -->
    <UiDialog :open="createOpen" title="Tạo tập dữ liệu mới" @close="createOpen = false">
      <form class="dialog-form" @submit.prevent="handleCreateDataset">
        <div class="form-group">
          <label for="ds-name">Tên tập dữ liệu <span class="required">*</span></label>
          <input id="ds-name" v-model="newDatasetName" type="text" class="text-input" required placeholder="Ví dụ: Tài liệu sản phẩm" />
        </div>
        <div class="form-group">
          <label for="ds-desc">Mô tả</label>
          <textarea id="ds-desc" v-model="newDatasetDesc" class="text-input textarea-input" rows="3" placeholder="Mô tả nội dung tập dữ liệu..."></textarea>
        </div>
      </form>
      <template #footer>
        <button class="secondary-button" type="button" @click="createOpen = false">Hủy</button>
        <button class="primary-button" type="button" :disabled="creating || !newDatasetName.trim()" @click="handleCreateDataset">
          {{ creating ? 'Đang tạo...' : 'Tạo mới' }}
        </button>
      </template>
    </UiDialog>

    <!-- Chunks View Dialog -->
    <UiDialog :open="chunksOpen" title="Danh sách các đoạn (Chunks)" size="medium" @close="chunksOpen = false">
      <div v-if="loadingChunks" class="state-card loading-card">
        <RefreshCw :size="20" class="spin" />
        <p>Đang tải đoạn tài liệu...</p>
      </div>
      <div v-else-if="docChunks.length === 0" class="empty-state">
        <p>Chưa có chunk nào.</p>
      </div>
      <div v-else class="chunks-list">
        <div v-for="c in docChunks" :key="c.id" class="chunk-item">
          <div class="chunk-meta">
            <span>#{{ c.ordinal }}</span>
            <span>{{ c.token_estimate }} tokens ước tính</span>
          </div>
          <p class="chunk-content">{{ c.content }}</p>
        </div>
      </div>
      <template #footer>
        <button class="secondary-button" type="button" @click="chunksOpen = false">Đóng</button>
      </template>
    </UiDialog>
  </div>
</template>
