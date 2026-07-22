#include "ota/resource_reconciler.h"

#include <algorithm>
#include <array>
#include <cinttypes>
#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <iterator>

#include "board/board_config.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_partition.h"
#include "esp_psram.h"
#include "network/endpoint_url.h"
#include "psa/crypto.h"
#include "sdkconfig.h"

namespace veetee::ota {
namespace {

constexpr char kTag[] = "veetee_resources";
constexpr std::size_t kMaximumManifestBytes = 32768;
constexpr std::uint32_t kNotificationRetryMs = 100;
constexpr std::size_t kDownloadBufferBytes = 8192;
constexpr std::uint32_t kEraseChunkBytes = 64U * 1024U;
constexpr std::uint32_t kProgressCheckpointBytes = 256U * 1024U;
constexpr std::uint64_t kBoardFlashBytes = 16ULL * 1024ULL * 1024ULL;
constexpr SupportedResourceRuntime kWakeSupportedRuntimes[] = {
    {.kind = "model_pack", .runtime = "esp-sr", .runtime_abi = 1},
};
constexpr SupportedResourceRuntime kUiSupportedRuntimes[] = {
    {.kind = "display_assets", .runtime = "veetee-ui", .runtime_abi = 1},
};

struct ResourceProfile {
    const char* manifest_kind;
    const char* content_type;
    const char* partition_prefix;
    const char* nvs_namespace;
    const char* default_version;
    const char* task_name;
    std::uint8_t partition_subtype_base;
    std::uint32_t resource_abi;
    std::uint32_t ui_abi;
    const SupportedResourceRuntime* runtimes;
    std::size_t runtime_count;
};

const ResourceProfile& Profile(ResourceClass resource_class) {
    static constexpr ResourceProfile kWake = {
        .manifest_kind = "resource_bundle",
        .content_type = "application/vnd.veetee.esp-sr-model-pack",
        .partition_prefix = "resource_",
        .nvs_namespace = "veetee_resource",
        .default_version = "factory-bringup",
        .task_name = "veetee_resources",
        .partition_subtype_base = 0x40,
        .resource_abi = 1,
        .ui_abi = 0,
        .runtimes = kWakeSupportedRuntimes,
        .runtime_count = std::size(kWakeSupportedRuntimes),
    };
    static constexpr ResourceProfile kUi = {
        .manifest_kind = "ui_pack",
        .content_type = "application/vnd.veetee.ui-pack",
        .partition_prefix = "ui_",
        .nvs_namespace = "veetee_ui",
        .default_version = "factory-signal",
        .task_name = "veetee_ui_pack",
        .partition_subtype_base = 0x42,
        .resource_abi = 2,
        .ui_abi = 1,
        .runtimes = kUiSupportedRuntimes,
        .runtime_count = std::size(kUiSupportedRuntimes),
    };
    return resource_class == ResourceClass::kUiPack ? kUi : kWake;
}

int HexNibble(char character) {
    if (character >= '0' && character <= '9') return character - '0';
    if (character >= 'a' && character <= 'f') return character - 'a' + 10;
    if (character >= 'A' && character <= 'F') return character - 'A' + 10;
    return -1;
}

bool DecodePublicKey(const char* encoded, std::array<std::uint8_t, 32>* key) {
    if (encoded == nullptr || key == nullptr || std::strlen(encoded) != 64) {
        return false;
    }
    for (std::size_t index = 0; index < key->size(); ++index) {
        const int high = HexNibble(encoded[index * 2]);
        const int low = HexNibble(encoded[index * 2 + 1]);
        if (high < 0 || low < 0) return false;
        (*key)[index] = static_cast<std::uint8_t>((high << 4U) | low);
    }
    return true;
}

void HashToHex(const std::array<std::uint8_t, 32>& hash, char output[65]) {
    constexpr char kHex[] = "0123456789abcdef";
    for (std::size_t index = 0; index < hash.size(); ++index) {
        output[index * 2] = kHex[hash[index] >> 4U];
        output[index * 2 + 1] = kHex[hash[index] & 0x0fU];
    }
    output[64] = '\0';
}

bool ParseContentRange(const char* value, std::uint64_t* first,
                       std::uint64_t* last, std::uint64_t* total) {
    if (value == nullptr || first == nullptr || last == nullptr ||
        total == nullptr) {
        return false;
    }
    unsigned long long parsed_first = 0;
    unsigned long long parsed_last = 0;
    unsigned long long parsed_total = 0;
    int consumed = 0;
    if (std::sscanf(value, "bytes %llu-%llu/%llu%n", &parsed_first,
                    &parsed_last, &parsed_total, &consumed) != 3 ||
        consumed <= 0 || static_cast<std::size_t>(consumed) != std::strlen(value)) {
        return false;
    }
    *first = parsed_first;
    *last = parsed_last;
    *total = parsed_total;
    return *first <= *last && *last < *total;
}

}  // namespace

esp_err_t ResourceReconciler::Initialize(settings::DeviceSettings* settings,
                                         EventSink sink, void* context,
                                         ResourceClass resource_class) {
    if (settings == nullptr || sink == nullptr ||
        CONFIG_VEETEE_RESOURCE_SIGNING_KEY_ID[0] == '\0' ||
        !DecodePublicKey(CONFIG_VEETEE_RESOURCE_SIGNING_PUBLIC_KEY_HEX,
                         &trusted_key_.public_key)) {
        return ESP_ERR_INVALID_ARG;
    }
    if (psa_crypto_init() != PSA_SUCCESS) return ESP_FAIL;
    settings_ = settings;
    resource_class_ = resource_class;
    sink_ = sink;
    sink_context_ = context;
    trusted_key_.key_id = CONFIG_VEETEE_RESOURCE_SIGNING_KEY_ID;
    std::uint8_t mac[6] = {};
    const esp_err_t mac_error = esp_read_mac(mac, ESP_MAC_WIFI_STA);
    if (mac_error != ESP_OK) return mac_error;
    std::snprintf(hardware_id_, sizeof(hardware_id_),
                  "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2],
                  mac[3], mac[4], mac[5]);
    const ResourceProfile& profile = Profile(resource_class_);
    resource_partitions_[0] = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA,
        static_cast<esp_partition_subtype_t>(profile.partition_subtype_base),
        PartitionLabel(0));
    resource_partitions_[1] = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA,
        static_cast<esp_partition_subtype_t>(profile.partition_subtype_base + 1U),
        PartitionLabel(1));
    if (resource_partitions_[0] == nullptr || resource_partitions_[1] == nullptr) {
        return ESP_ERR_NOT_FOUND;
    }
    resource_slot_bytes_ = std::min<std::uint64_t>(resource_partitions_[0]->size,
                                                   resource_partitions_[1]->size);
    if (resource_slot_bytes_ == 0) return ESP_ERR_NOT_FOUND;

    state_mutex_ = xSemaphoreCreateMutex();
    if (state_mutex_ == nullptr) return ESP_ERR_NO_MEM;
    esp_err_t error = resource_state_.Initialize(
        CONFIG_VEETEE_MIN_RESOURCE_SECURITY_EPOCH, profile.nvs_namespace,
        profile.default_version);
    if (error != ESP_OK) {
        vSemaphoreDelete(state_mutex_);
        state_mutex_ = nullptr;
        return error;
    }
    trusted_key_.minimum_security_epoch = std::max<std::uint32_t>(
        CONFIG_VEETEE_MIN_RESOURCE_SECURITY_EPOCH,
        resource_state_.record().security_epoch_floor);

    queue_ = xQueueCreate(1, sizeof(Target));
    if (queue_ == nullptr) {
        vSemaphoreDelete(state_mutex_);
        state_mutex_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreate(&ResourceReconciler::TaskEntry, profile.task_name, 12288,
                    this, 4, &task_) != pdPASS) {
        vQueueDelete(queue_);
        queue_ = nullptr;
        vSemaphoreDelete(state_mutex_);
        state_mutex_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(kTag,
             "Verifier ready key_id=%s min_epoch=%u slot=%llu bytes firmware_compat=%s",
             trusted_key_.key_id,
             static_cast<unsigned>(trusted_key_.minimum_security_epoch),
             static_cast<unsigned long long>(resource_slot_bytes_),
             CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION);
    return ESP_OK;
}

bool ResourceReconciler::Schedule(const char* desired_version,
                                  const char* manifest_url) {
    Target target{};
    if (queue_ == nullptr || desired_version == nullptr || manifest_url == nullptr ||
        desired_version[0] == '\0' ||
        std::strlen(desired_version) >= sizeof(target.desired_version) ||
        std::strlen(manifest_url) >= sizeof(target.manifest_url) ||
        !network::IsHttpEndpointUrl(manifest_url)) {
        return false;
    }
    target.generation = generation_.fetch_add(1) + 1;
    std::snprintf(target.desired_version, sizeof(target.desired_version), "%s",
                  desired_version);
    std::snprintf(target.manifest_url, sizeof(target.manifest_url), "%s",
                  manifest_url);
    return xQueueOverwrite(queue_, &target) == pdTRUE;
}

void ResourceReconciler::Cancel() {
    generation_.fetch_add(1);
    if (queue_ != nullptr) xQueueReset(queue_);
}

const char* ResourceReconciler::ActivePartitionLabel() const {
    return PartitionLabel(RecordSnapshot().active_slot);
}

const char* ResourceReconciler::PartitionLabel(std::uint8_t slot) const {
    if (slot > 1) return nullptr;
    return resource_class_ == ResourceClass::kUiPack
               ? (slot == 0 ? "ui_0" : "ui_1")
               : (slot == 0 ? "resource_0" : "resource_1");
}

const char* ResourceReconciler::PreviousPartitionLabel() const {
    const settings::ResourceRecord record = RecordSnapshot();
    return record.previous_slot == record.active_slot
               ? nullptr
               : PartitionLabel(record.previous_slot);
}

const char* ResourceReconciler::StagedPartitionLabel() const {
    const settings::ResourceRecord record = RecordSnapshot();
    return record.phase == settings::ResourceRecordPhase::kStaged
               ? PartitionLabel(record.target_slot)
               : nullptr;
}

settings::ResourceRecordPhase ResourceReconciler::phase() const {
    return RecordSnapshot().phase;
}

settings::ResourceRecord ResourceReconciler::Snapshot() const {
    return RecordSnapshot();
}

esp_err_t ResourceReconciler::ActivateStaged() {
    if (state_mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(state_mutex_, portMAX_DELAY);
    settings::ResourceRecord record = resource_state_.record();
    const bool valid = settings::ActivateStagedResource(&record);
    const esp_err_t error = valid ? resource_state_.Save(record)
                                  : ESP_ERR_INVALID_STATE;
    xSemaphoreGive(state_mutex_);
    return error;
}

esp_err_t ResourceReconciler::ConfirmActive() {
    if (state_mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(state_mutex_, portMAX_DELAY);
    settings::ResourceRecord record = resource_state_.record();
    const bool valid = settings::ConfirmActiveResource(&record);
    const esp_err_t error = valid ? resource_state_.Save(record)
                                  : ESP_ERR_INVALID_STATE;
    if (error == ESP_OK) {
        trusted_key_.minimum_security_epoch = std::max<std::uint32_t>(
            CONFIG_VEETEE_MIN_RESOURCE_SECURITY_EPOCH,
            record.security_epoch_floor);
    }
    xSemaphoreGive(state_mutex_);
    return error;
}

esp_err_t ResourceReconciler::Rollback() {
    if (state_mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(state_mutex_, portMAX_DELAY);
    settings::ResourceRecord record = resource_state_.record();
    const bool valid = settings::RollbackResource(&record);
    const esp_err_t error = valid ? resource_state_.Save(record)
                                  : ESP_ERR_INVALID_STATE;
    xSemaphoreGive(state_mutex_);
    return error;
}

void ResourceReconciler::TaskEntry(void* context) {
    static_cast<ResourceReconciler*>(context)->TaskLoop();
}

void ResourceReconciler::TaskLoop() {
    Target target{};
    while (xQueueReceive(queue_, &target, portMAX_DELAY) == pdTRUE) {
        if (IsCurrent(target.generation)) Reconcile(target);
    }
}

void ResourceReconciler::Reconcile(const Target& target) {
    char* document = nullptr;
    std::size_t document_size = 0;
    const esp_err_t fetch_error =
        FetchManifest(target, &document, &document_size);
    if (fetch_error != ESP_OK) {
        if (IsCurrent(target.generation)) {
            EmitWithRetry(ResourceReconcileEvent::kTransportFailed, target,
                          nullptr, esp_err_to_name(fetch_error));
        }
        return;
    }
    if (!IsCurrent(target.generation)) {
        heap_caps_free(document);
        return;
    }

    const ResourceProfile& profile = Profile(resource_class_);
    const DeviceResourceCapability capability = {
        .manifest_kind = profile.manifest_kind,
        .content_type = profile.content_type,
        .board = board::kBoardName,
        .chip = "esp32s3",
        .firmware_version = CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION,
        .resource_abi = profile.resource_abi,
        .ui_abi = profile.ui_abi,
        .flash_bytes = kBoardFlashBytes,
        .psram_bytes = esp_psram_is_initialized() ? esp_psram_get_size() : 0,
        .resource_slot_bytes = resource_slot_bytes_,
        .supported_runtimes = profile.runtimes,
        .supported_runtime_count = profile.runtime_count,
    };
    VerifiedResourceManifest manifest{};
    TrustedReleaseKey active_key = trusted_key_;
    active_key.minimum_security_epoch = std::max<std::uint32_t>(
        active_key.minimum_security_epoch,
        RecordSnapshot().security_epoch_floor);
    ResourceManifestError error = VerifyResourceManifest(
        std::string_view(document, document_size), capability, &active_key, 1,
        &manifest);
    heap_caps_free(document);
    if (error != ResourceManifestError::kOk) {
        EmitWithRetry(ResourceReconcileEvent::kManifestRejected, target, nullptr,
                      ResourceManifestErrorName(error));
        return;
    }
    if (std::strcmp(manifest.version, target.desired_version) != 0) {
        EmitWithRetry(ResourceReconcileEvent::kManifestRejected, target,
                      manifest.version, "desired_version_mismatch");
        return;
    }
    std::uint8_t target_slot = 0;
    std::uint32_t resume_bytes = 0;
    bool already_staged = false;
    bool already_active = false;
    const esp_err_t prepare_error = PrepareDownload(
        manifest, &target_slot, &resume_bytes, &already_staged,
        &already_active);
    if (prepare_error != ESP_OK) {
        EmitWithRetry(ResourceReconcileEvent::kPayloadRejected, target,
                      manifest.version, esp_err_to_name(prepare_error));
        return;
    }
    if (already_active) {
        EmitWithRetry(ResourceReconcileEvent::kAlreadyActive, target,
                      manifest.version, "ok");
        return;
    }
    if (already_staged) {
        EmitWithRetry(ResourceReconcileEvent::kPayloadStaged, target,
                      manifest.version, "ok");
        return;
    }

    Emit(ResourceReconcileEvent::kDownloading, target, manifest.version, "ok");

    const esp_err_t download_error =
        DownloadPayload(target, manifest, target_slot, resume_bytes);
    if (download_error != ESP_OK) {
        if (download_error == ESP_ERR_INVALID_CRC) {
            Rollback();
            EmitWithRetry(ResourceReconcileEvent::kPayloadRejected, target,
                          manifest.version, "payload_sha256_mismatch");
        } else if (IsCurrent(target.generation)) {
            EmitWithRetry(ResourceReconcileEvent::kTransportFailed, target,
                          manifest.version, esp_err_to_name(download_error));
        }
        return;
    }
    Emit(ResourceReconcileEvent::kVerifying, target, manifest.version, "ok");
    const esp_err_t stage_error = StageDownload(target);
    if (stage_error != ESP_OK) {
        EmitWithRetry(ResourceReconcileEvent::kPayloadRejected, target,
                      manifest.version, esp_err_to_name(stage_error));
        return;
    }
    EmitWithRetry(ResourceReconcileEvent::kPayloadStaged, target,
                  manifest.version, "ok");
}

esp_err_t ResourceReconciler::PrepareDownload(
    const VerifiedResourceManifest& manifest, std::uint8_t* target_slot,
    std::uint32_t* resume_bytes, bool* already_staged,
    bool* already_active) {
    if (target_slot == nullptr || resume_bytes == nullptr ||
        already_staged == nullptr || already_active == nullptr ||
        manifest.payload_bytes == 0 || manifest.payload_bytes > UINT32_MAX ||
        state_mutex_ == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    *already_staged = false;
    *already_active = false;
    xSemaphoreTake(state_mutex_, portMAX_DELAY);
    settings::ResourceRecord record = resource_state_.record();
    esp_err_t error = ESP_OK;

    if (record.phase == settings::ResourceRecordPhase::kPendingHealth) {
        error = ESP_ERR_INVALID_STATE;
    } else if (record.phase == settings::ResourceRecordPhase::kStable &&
               std::strcmp(record.active_version, manifest.version) == 0) {
        *already_active = true;
        *target_slot = record.active_slot;
    } else if (record.phase == settings::ResourceRecordPhase::kStaged &&
               std::strcmp(record.desired_version, manifest.version) == 0 &&
               std::strcmp(record.bundle_id, manifest.bundle_id) == 0 &&
               std::strcmp(record.payload_sha256, manifest.payload_sha256) == 0 &&
               record.expected_bytes == manifest.payload_bytes &&
               record.desired_security_epoch == manifest.security_epoch) {
        *already_staged = true;
        *target_slot = record.target_slot;
        *resume_bytes = record.downloaded_bytes;
    } else {
        if (record.phase != settings::ResourceRecordPhase::kStable &&
            record.phase != settings::ResourceRecordPhase::kDownloading &&
            !settings::RollbackResource(&record)) {
            error = ESP_ERR_INVALID_STATE;
        }
        const settings::ResourceRecord previous = record;
        if (error == ESP_OK &&
            !settings::BeginResourceDownload(
                &record, manifest.version, manifest.bundle_id,
                manifest.payload_sha256,
                static_cast<std::uint32_t>(manifest.payload_bytes),
                manifest.security_epoch)) {
            error = ESP_ERR_INVALID_ARG;
        }
        if (error == ESP_OK &&
            std::memcmp(&record, &previous, sizeof(record)) != 0) {
            error = resource_state_.Save(record);
        }
        if (error == ESP_OK) {
            *target_slot = record.target_slot;
            *resume_bytes = record.downloaded_bytes;
        }
    }
    xSemaphoreGive(state_mutex_);
    return error;
}

esp_err_t ResourceReconciler::ErasePartition(const Target& target,
                                             std::uint8_t slot,
                                             std::uint32_t offset) {
    if (slot > 1 || resource_partitions_[slot] == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    const esp_partition_t* partition = resource_partitions_[slot];
    if (offset > partition->size || offset % kEraseChunkBytes != 0) {
        return ESP_ERR_INVALID_ARG;
    }
    while (offset < partition->size) {
        if (!IsCurrent(target.generation)) return ESP_ERR_INVALID_STATE;
        const std::uint32_t bytes = std::min<std::uint32_t>(
            kEraseChunkBytes, partition->size - offset);
        const esp_err_t error = esp_partition_erase_range(partition, offset, bytes);
        if (error != ESP_OK) return error;
        offset += bytes;
    }
    return ESP_OK;
}

esp_err_t ResourceReconciler::DownloadPayload(
    const Target& target, const VerifiedResourceManifest& manifest,
    std::uint8_t target_slot, std::uint32_t resume_bytes) {
    if (!IsCurrent(target.generation) || target_slot > 1 ||
        resource_partitions_[target_slot] == nullptr || settings_ == nullptr ||
        manifest.payload_bytes == 0 || manifest.payload_bytes > UINT32_MAX ||
        manifest.payload_bytes > resource_partitions_[target_slot]->size ||
        resume_bytes > manifest.payload_bytes) {
        return ESP_ERR_INVALID_ARG;
    }
    const auto expected_bytes = static_cast<std::uint32_t>(manifest.payload_bytes);
    if (resume_bytes != expected_bytes &&
        resume_bytes % kProgressCheckpointBytes != 0) {
        const esp_err_t reset_error = ResetDownloadProgress(target);
        if (reset_error != ESP_OK) return reset_error;
        resume_bytes = 0;
    }

    auto* buffer = static_cast<std::uint8_t*>(heap_caps_malloc(
        kDownloadBufferBytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    if (buffer == nullptr) {
        buffer = static_cast<std::uint8_t*>(
            heap_caps_malloc(kDownloadBufferBytes, MALLOC_CAP_8BIT));
    }
    if (buffer == nullptr) return ESP_ERR_NO_MEM;

    psa_hash_operation_t hash_operation = PSA_HASH_OPERATION_INIT;
    esp_err_t error =
        psa_hash_setup(&hash_operation, PSA_ALG_SHA_256) == PSA_SUCCESS
            ? ESP_OK
            : ESP_FAIL;
    const esp_partition_t* partition = resource_partitions_[target_slot];
    for (std::uint32_t offset = 0; error == ESP_OK && offset < resume_bytes;) {
        if (!IsCurrent(target.generation)) {
            error = ESP_ERR_INVALID_STATE;
            break;
        }
        const std::uint32_t bytes = std::min<std::uint32_t>(
            kDownloadBufferBytes, resume_bytes - offset);
        error = esp_partition_read(partition, offset, buffer, bytes);
        if (error == ESP_OK &&
            psa_hash_update(&hash_operation, buffer, bytes) != PSA_SUCCESS) {
            error = ESP_FAIL;
        }
        offset += bytes;
    }
    if (error == ESP_OK && resume_bytes < expected_bytes) {
        error = ErasePartition(target, target_slot, resume_bytes);
    }

    for (int attempt = 0; error == ESP_OK && resume_bytes < expected_bytes;
         ++attempt) {
        esp_http_client_config_t config = {};
        config.url = manifest.payload_url;
        config.event_handler = &ResourceReconciler::PayloadHttpEventHandler;
        config.user_data = this;
        config.timeout_ms = 6000;
        config.buffer_size = static_cast<int>(kDownloadBufferBytes);
        config.keep_alive_enable = true;
        config.crt_bundle_attach = esp_crt_bundle_attach;
        config.disable_auto_redirect = true;
        config.max_redirection_count = 0;
        esp_http_client_handle_t client = esp_http_client_init(&config);
        if (client == nullptr) {
            error = ESP_ERR_NO_MEM;
            break;
        }

        char authorization[160] = {};
        char range[48] = {};
        content_range_[0] = '\0';
        content_range_overflow_ = false;
        request_generation_.store(target.generation);
        error = esp_http_client_set_method(client, HTTP_METHOD_GET);
        if (error == ESP_OK) {
            const int length = std::snprintf(authorization, sizeof(authorization),
                                             "Bearer %s", settings_->device_token);
            if (length <= 7 || length >= static_cast<int>(sizeof(authorization))) {
                error = ESP_ERR_INVALID_SIZE;
            }
        }
        if (error == ESP_OK) {
            error = esp_http_client_set_header(client, "Authorization",
                                               authorization);
        }
        if (error == ESP_OK) {
            error = esp_http_client_set_header(client, "Device-Id", hardware_id_);
        }
        if (error == ESP_OK) {
            error = esp_http_client_set_header(client, "Accept",
                                               "application/octet-stream");
        }
        if (error == ESP_OK) {
            error = esp_http_client_set_header(client, "Accept-Encoding",
                                               "identity");
        }
        if (error == ESP_OK && resume_bytes > 0) {
            const int length = std::snprintf(range, sizeof(range), "bytes=%" PRIu32 "-",
                                             resume_bytes);
            if (length <= 6 || length >= static_cast<int>(sizeof(range))) {
                error = ESP_ERR_INVALID_SIZE;
            } else {
                error = esp_http_client_set_header(client, "Range", range);
            }
        }
        if (error == ESP_OK) error = esp_http_client_open(client, 0);
        const std::int64_t content_length =
            error == ESP_OK ? esp_http_client_fetch_headers(client) : -1;
        const int status =
            error == ESP_OK ? esp_http_client_get_status_code(client) : 0;

        if (error == ESP_OK && resume_bytes > 0 && status == HttpStatus_Ok &&
            attempt == 0) {
            esp_http_client_close(client);
            esp_http_client_cleanup(client);
            request_generation_.store(0);
            std::fill(std::begin(authorization), std::end(authorization), '\0');
            error = ResetDownloadProgress(target);
            if (error == ESP_OK) error = ErasePartition(target, target_slot, 0);
            if (error == ESP_OK) {
                psa_hash_abort(&hash_operation);
                if (psa_hash_setup(&hash_operation, PSA_ALG_SHA_256) !=
                    PSA_SUCCESS) {
                    error = ESP_FAIL;
                }
            }
            resume_bytes = 0;
            continue;
        }

        const std::uint32_t remaining = expected_bytes - resume_bytes;
        if (error == ESP_OK &&
            ((resume_bytes == 0 && status != HttpStatus_Ok) ||
             (resume_bytes > 0 && status != HttpStatus_PartialContent))) {
            error = ESP_ERR_INVALID_RESPONSE;
        }
        if (error == ESP_OK &&
            (content_length < 0 ||
             static_cast<std::uint64_t>(content_length) != remaining)) {
            error = ESP_ERR_INVALID_SIZE;
        }
        if (error == ESP_OK && resume_bytes > 0) {
            std::uint64_t first = 0;
            std::uint64_t last = 0;
            std::uint64_t total = 0;
            if (content_range_overflow_ ||
                !ParseContentRange(content_range_, &first, &last, &total) ||
                first != resume_bytes || last + 1 != expected_bytes ||
                total != expected_bytes) {
                error = ESP_ERR_INVALID_RESPONSE;
            }
        }

        std::uint32_t downloaded = resume_bytes;
        std::uint32_t next_checkpoint =
            ((downloaded / kProgressCheckpointBytes) + 1U) *
            kProgressCheckpointBytes;
        while (error == ESP_OK && downloaded < expected_bytes) {
            if (!IsCurrent(target.generation)) {
                error = ESP_ERR_INVALID_STATE;
                break;
            }
            const int request_bytes = static_cast<int>(std::min<std::uint32_t>(
                kDownloadBufferBytes, expected_bytes - downloaded));
            const int bytes = esp_http_client_read(
                client, reinterpret_cast<char*>(buffer), request_bytes);
            if (bytes < 0) {
                error = ESP_FAIL;
            } else if (bytes == 0) {
                error = ESP_ERR_INVALID_SIZE;
            } else {
                error = esp_partition_write(partition, downloaded, buffer,
                                            static_cast<std::size_t>(bytes));
                if (error == ESP_OK &&
                    psa_hash_update(
                        &hash_operation, buffer,
                        static_cast<std::size_t>(bytes)) != PSA_SUCCESS) {
                    error = ESP_FAIL;
                }
                downloaded += static_cast<std::uint32_t>(bytes);
                if (error == ESP_OK && downloaded >= next_checkpoint &&
                    downloaded < expected_bytes) {
                    error = SaveDownloadProgress(target, next_checkpoint);
                    next_checkpoint += kProgressCheckpointBytes;
                }
            }
        }
        if (error == ESP_OK &&
            !esp_http_client_is_complete_data_received(client)) {
            error = ESP_ERR_INVALID_RESPONSE;
        }
        esp_http_client_close(client);
        esp_http_client_cleanup(client);
        request_generation_.store(0);
        std::fill(std::begin(authorization), std::end(authorization), '\0');
        resume_bytes = downloaded;
    }

    std::array<std::uint8_t, 32> hash{};
    if (error == ESP_OK && resume_bytes != expected_bytes) {
        error = ESP_ERR_INVALID_SIZE;
    }
    std::size_t hash_size = 0;
    if (error == ESP_OK &&
        (psa_hash_finish(&hash_operation, hash.data(), hash.size(), &hash_size) !=
             PSA_SUCCESS ||
         hash_size != hash.size())) {
        error = ESP_FAIL;
    }
    char encoded_hash[65] = {};
    if (error == ESP_OK) {
        HashToHex(hash, encoded_hash);
        if (std::strcmp(encoded_hash, manifest.payload_sha256) != 0) {
            error = ESP_ERR_INVALID_CRC;
        }
    }
    if (error == ESP_OK) error = SaveDownloadProgress(target, expected_bytes);
    std::fill(std::begin(encoded_hash), std::end(encoded_hash), '\0');
    psa_hash_abort(&hash_operation);
    heap_caps_free(buffer);
    request_generation_.store(0);
    return error;
}

esp_err_t ResourceReconciler::SaveDownloadProgress(
    const Target& target, std::uint32_t downloaded_bytes) {
    if (!IsCurrent(target.generation) || state_mutex_ == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(state_mutex_, portMAX_DELAY);
    settings::ResourceRecord record = resource_state_.record();
    const bool valid =
        record.phase == settings::ResourceRecordPhase::kDownloading &&
        std::strcmp(record.desired_version, target.desired_version) == 0 &&
        settings::UpdateResourceDownloadProgress(&record, downloaded_bytes);
    const esp_err_t error = valid ? resource_state_.Save(record)
                                  : ESP_ERR_INVALID_STATE;
    xSemaphoreGive(state_mutex_);
    if (error == ESP_OK) {
        Emit(ResourceReconcileEvent::kDownloading, target,
             target.desired_version, "ok");
    }
    return error;
}

esp_err_t ResourceReconciler::ResetDownloadProgress(const Target& target) {
    if (!IsCurrent(target.generation) || state_mutex_ == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(state_mutex_, portMAX_DELAY);
    settings::ResourceRecord record = resource_state_.record();
    const bool valid =
        record.phase == settings::ResourceRecordPhase::kDownloading &&
        std::strcmp(record.desired_version, target.desired_version) == 0 &&
        settings::ResetResourceDownloadProgress(&record);
    const esp_err_t error = valid ? resource_state_.Save(record)
                                  : ESP_ERR_INVALID_STATE;
    xSemaphoreGive(state_mutex_);
    return error;
}

esp_err_t ResourceReconciler::StageDownload(const Target& target) {
    if (!IsCurrent(target.generation) || state_mutex_ == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    xSemaphoreTake(state_mutex_, portMAX_DELAY);
    settings::ResourceRecord record = resource_state_.record();
    const bool valid =
        record.phase == settings::ResourceRecordPhase::kDownloading &&
        std::strcmp(record.desired_version, target.desired_version) == 0 &&
        settings::StageResourceDownload(&record);
    const esp_err_t error = valid ? resource_state_.Save(record)
                                  : ESP_ERR_INVALID_STATE;
    xSemaphoreGive(state_mutex_);
    return error;
}

settings::ResourceRecord ResourceReconciler::RecordSnapshot() const {
    if (state_mutex_ == nullptr) {
        return settings::MakeDefaultResourceRecord(
            CONFIG_VEETEE_MIN_RESOURCE_SECURITY_EPOCH,
            Profile(resource_class_).default_version);
    }
    xSemaphoreTake(state_mutex_, portMAX_DELAY);
    const settings::ResourceRecord record = resource_state_.record();
    xSemaphoreGive(state_mutex_);
    return record;
}

esp_err_t ResourceReconciler::FetchManifest(const Target& target, char** document,
                                            std::size_t* document_size) {
    if (document == nullptr || document_size == nullptr || settings_ == nullptr ||
        !IsCurrent(target.generation)) {
        return ESP_ERR_INVALID_ARG;
    }
    response_ = static_cast<char*>(heap_caps_malloc(
        kMaximumManifestBytes + 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (response_ == nullptr) {
        response_ = static_cast<char*>(heap_caps_malloc(
            kMaximumManifestBytes + 1, MALLOC_CAP_8BIT));
    }
    if (response_ == nullptr) return ESP_ERR_NO_MEM;
    response_size_ = 0;
    response_overflow_ = false;

    esp_http_client_config_t config = {};
    config.url = target.manifest_url;
    config.event_handler = &ResourceReconciler::HttpEventHandler;
    config.user_data = this;
    config.timeout_ms = 6000;
    config.buffer_size = 2048;
    config.keep_alive_enable = true;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    config.disable_auto_redirect = true;
    config.max_redirection_count = 0;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == nullptr) {
        heap_caps_free(response_);
        response_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    request_generation_.store(target.generation);

    char authorization[160] = {};
    esp_err_t error = esp_http_client_set_method(client, HTTP_METHOD_GET);
    if (error == ESP_OK) {
        const int length = std::snprintf(authorization, sizeof(authorization),
                                         "Bearer %s", settings_->device_token);
        if (length <= 7 || length >= static_cast<int>(sizeof(authorization))) {
            error = ESP_ERR_INVALID_SIZE;
        }
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Authorization", authorization);
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Device-Id", hardware_id_);
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Accept", "application/json");
    }
    if (error == ESP_OK) error = esp_http_client_perform(client);
    const int status =
        error == ESP_OK ? esp_http_client_get_status_code(client) : 0;
    esp_http_client_cleanup(client);
    request_generation_.store(0);
    std::fill(std::begin(authorization), std::end(authorization), '\0');

    if (error == ESP_OK && !IsCurrent(target.generation)) error = ESP_ERR_INVALID_STATE;
    if (error == ESP_OK && status != 200) error = ESP_ERR_INVALID_RESPONSE;
    if (error == ESP_OK && response_overflow_) error = ESP_ERR_INVALID_SIZE;
    if (error == ESP_OK && response_size_ == 0) error = ESP_ERR_INVALID_RESPONSE;
    if (error != ESP_OK) {
        heap_caps_free(response_);
        response_ = nullptr;
        response_size_ = 0;
        return error;
    }
    response_[response_size_] = '\0';
    *document = response_;
    *document_size = response_size_;
    response_ = nullptr;
    response_size_ = 0;
    return ESP_OK;
}

esp_err_t ResourceReconciler::HttpEventHandler(esp_http_client_event_t* event) {
    if (event == nullptr || event->user_data == nullptr) return ESP_ERR_INVALID_ARG;
    auto* reconciler = static_cast<ResourceReconciler*>(event->user_data);
    if (event->event_id != HTTP_EVENT_ON_DATA || event->data_len <= 0) return ESP_OK;
    const std::uint32_t generation = reconciler->request_generation_.load();
    if (generation == 0 || !reconciler->IsCurrent(generation)) {
        return ESP_ERR_INVALID_STATE;
    }
    const std::size_t length = static_cast<std::size_t>(event->data_len);
    if (reconciler->response_ == nullptr ||
        reconciler->response_size_ + length > kMaximumManifestBytes) {
        reconciler->response_overflow_ = true;
        return ESP_FAIL;
    }
    std::memcpy(reconciler->response_ + reconciler->response_size_, event->data,
                length);
    reconciler->response_size_ += length;
    return ESP_OK;
}

esp_err_t ResourceReconciler::PayloadHttpEventHandler(
    esp_http_client_event_t* event) {
    if (event == nullptr || event->user_data == nullptr) return ESP_ERR_INVALID_ARG;
    auto* reconciler = static_cast<ResourceReconciler*>(event->user_data);
    if (event->event_id != HTTP_EVENT_ON_HEADER || event->header_key == nullptr ||
        event->header_value == nullptr ||
        strcasecmp(event->header_key, "Content-Range") != 0) {
        return ESP_OK;
    }
    const std::size_t length = std::strlen(event->header_value);
    if (length >= sizeof(reconciler->content_range_)) {
        reconciler->content_range_overflow_ = true;
        return ESP_FAIL;
    }
    std::memcpy(reconciler->content_range_, event->header_value, length + 1);
    return ESP_OK;
}

bool ResourceReconciler::Emit(ResourceReconcileEvent event,
                              const Target& target, const char* bundle_version,
                              const char* error_code) const {
    if (!IsCurrent(target.generation) || sink_ == nullptr) return false;
    ResourceReconcileNotification notification{
        .event = event,
        .resource_class = resource_class_,
    };
    std::snprintf(notification.desired_version,
                  sizeof(notification.desired_version), "%s",
                  target.desired_version);
    std::snprintf(notification.bundle_version,
                  sizeof(notification.bundle_version), "%s",
                  bundle_version == nullptr ? "" : bundle_version);
    std::snprintf(notification.error_code, sizeof(notification.error_code), "%s",
                  error_code == nullptr ? "unknown" : error_code);
    const settings::ResourceRecord record = RecordSnapshot();
    std::snprintf(notification.current_version,
                  sizeof(notification.current_version), "%s",
                  record.active_version);
    notification.expected_bytes = record.expected_bytes;
    notification.downloaded_bytes = record.downloaded_bytes;
    notification.security_epoch =
        record.desired_security_epoch != 0 ? record.desired_security_epoch
                                           : record.active_security_epoch;
    notification.active_slot = record.active_slot;
    notification.target_slot = record.target_slot;
    return sink_(notification, sink_context_);
}

bool ResourceReconciler::EmitWithRetry(
    ResourceReconcileEvent event, const Target& target,
    const char* bundle_version, const char* error_code) const {
    while (IsCurrent(target.generation)) {
        if (Emit(event, target, bundle_version, error_code)) return true;
        vTaskDelay(pdMS_TO_TICKS(kNotificationRetryMs));
    }
    return false;
}

bool ResourceReconciler::IsCurrent(std::uint32_t generation) const {
    return generation_.load() == generation;
}

}  // namespace veetee::ota
