#include "ota/firmware_updater.h"

#include <algorithm>
#include <array>
#include <cinttypes>
#include <cstdio>
#include <cstring>

#include "board/board_config.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_psram.h"
#include "esp_system.h"
#include "network/endpoint_url.h"
#include "psa/crypto.h"
#include "sdkconfig.h"

namespace veetee::ota {
namespace {
constexpr char kTag[] = "veetee_firmware_ota";
constexpr char kFirmwareReleaseMarker[] =
    "VEETEE_RELEASE_VERSION=" CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION;
constexpr std::size_t kChunkBytes = 8192;
constexpr std::size_t kMaximumResponseBytes = 32768;
constexpr TickType_t kNotificationRetryTicks = pdMS_TO_TICKS(20);
constexpr char kAttemptRecordKey[] = "attempt";

class SemaphoreGuard {
public:
    explicit SemaphoreGuard(SemaphoreHandle_t mutex) : mutex_(mutex) {
        if (mutex_ != nullptr) xSemaphoreTake(mutex_, portMAX_DELAY);
    }
    ~SemaphoreGuard() {
        if (mutex_ != nullptr) xSemaphoreGive(mutex_);
    }

    SemaphoreGuard(const SemaphoreGuard&) = delete;
    SemaphoreGuard& operator=(const SemaphoreGuard&) = delete;

private:
    SemaphoreHandle_t mutex_;
};

bool DecodePublicKey(const char* encoded, std::array<std::uint8_t, 32>* key) {
    if (encoded == nullptr || key == nullptr || std::strlen(encoded) != 64) return false;
    for (std::size_t i = 0; i < key->size(); ++i) {
        auto nibble = [](char c) -> int {
            if (c >= '0' && c <= '9') return c - '0';
            if (c >= 'a' && c <= 'f') return c - 'a' + 10;
            if (c >= 'A' && c <= 'F') return c - 'A' + 10;
            return -1;
        };
        const int high = nibble(encoded[i * 2]);
        const int low = nibble(encoded[i * 2 + 1]);
        if (high < 0 || low < 0) return false;
        (*key)[i] = static_cast<std::uint8_t>((high << 4) | low);
    }
    return true;
}
void HashHex(const std::array<std::uint8_t, 32>& hash, char output[65]) {
    constexpr char hex[] = "0123456789abcdef";
    for (std::size_t i = 0; i < hash.size(); ++i) {
        output[i * 2] = hex[hash[i] >> 4];
        output[i * 2 + 1] = hex[hash[i] & 0x0f];
    }
    output[64] = '\0';
}
std::uint8_t PartitionSlot(const esp_partition_t* partition) {
    if (partition == nullptr ||
        partition->subtype < ESP_PARTITION_SUBTYPE_APP_OTA_MIN ||
        partition->subtype >= ESP_PARTITION_SUBTYPE_APP_OTA_MAX) {
        return 0;
    }
    return static_cast<std::uint8_t>(
        partition->subtype - ESP_PARTITION_SUBTYPE_APP_OTA_MIN);
}
}  // namespace

FirmwareUpdater::~FirmwareUpdater() {
    if (nvs_handle_ != 0) nvs_close(nvs_handle_);
    if (state_mutex_ != nullptr) vSemaphoreDelete(state_mutex_);
    heap_caps_free(response_);
    response_ = nullptr;
}

esp_err_t FirmwareUpdater::Initialize(settings::SettingsStore* settings_store,
                                       EventSink sink, void* context) {
    if (settings_store == nullptr || sink == nullptr ||
        std::strlen(CONFIG_VEETEE_RESOURCE_SIGNING_PUBLIC_KEY_HEX) != 64 ||
        CONFIG_VEETEE_RESOURCE_SIGNING_KEY_ID[0] == '\0') return ESP_ERR_INVALID_ARG;
    settings_store_ = settings_store;
    sink_ = sink;
    sink_context_ = context;
    if (psa_crypto_init() != PSA_SUCCESS) return ESP_FAIL;
    state_mutex_ = xSemaphoreCreateMutex();
    if (state_mutex_ == nullptr) return ESP_ERR_NO_MEM;
    esp_err_t error = nvs_open("veetee_fw_ota", NVS_READWRITE, &nvs_handle_);
    if (error != ESP_OK) return error;
    bool persist_epoch_floor = false;
    error = nvs_get_u32(nvs_handle_, "epoch", &security_epoch_floor_);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        security_epoch_floor_ = CONFIG_VEETEE_MIN_RESOURCE_SECURITY_EPOCH;
        persist_epoch_floor = true;
        error = ESP_OK;
    }
    if (error != ESP_OK) return error;
    if (security_epoch_floor_ < CONFIG_VEETEE_MIN_RESOURCE_SECURITY_EPOCH) {
        security_epoch_floor_ = CONFIG_VEETEE_MIN_RESOURCE_SECURITY_EPOCH;
        persist_epoch_floor = true;
    }
    if (persist_epoch_floor) {
        error = nvs_set_u32(nvs_handle_, "epoch", security_epoch_floor_);
        if (error == ESP_OK) error = nvs_commit(nvs_handle_);
        if (error != ESP_OK) return error;
    }

    std::size_t attempt_size = sizeof(attempt_record_);
    error = nvs_get_blob(nvs_handle_, kAttemptRecordKey, &attempt_record_,
                         &attempt_size);
    if (error == ESP_ERR_NVS_NOT_FOUND ||
        (error == ESP_OK &&
         (attempt_size != sizeof(attempt_record_) ||
          !settings::IsValidFirmwareOtaAttemptRecord(attempt_record_)))) {
        if (error == ESP_OK) {
            ESP_LOGW(kTag, "Resetting invalid firmware OTA attempt record");
        }
        attempt_record_ = settings::MakeDefaultFirmwareOtaAttemptRecord();
        error = nvs_set_blob(nvs_handle_, kAttemptRecordKey, &attempt_record_,
                             sizeof(attempt_record_));
        if (error == ESP_OK) error = nvs_commit(nvs_handle_);
    }
    if (error != ESP_OK) return error;
    ESP_LOGI(kTag, "%s", kFirmwareReleaseMarker);
    std::uint8_t mac[6] = {};
    error = esp_read_mac(mac, ESP_MAC_WIFI_STA);
    if (error != ESP_OK) return error;
    std::snprintf(hardware_id_, sizeof(hardware_id_), "%02x:%02x:%02x:%02x:%02x:%02x",
                  mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    response_ = static_cast<char*>(heap_caps_calloc(
        kMaximumResponseBytes + 1, sizeof(char), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (response_ == nullptr) return ESP_ERR_NO_MEM;
    queue_ = xQueueCreate(1, sizeof(Target));
    if (queue_ == nullptr) {
        heap_caps_free(response_);
        response_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    if (xTaskCreate(&FirmwareUpdater::TaskEntry, "veetee_fw_ota", 12288, this, 4,
                    &task_) != pdPASS) {
        vQueueDelete(queue_);
        queue_ = nullptr;
        heap_caps_free(response_);
        response_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

FirmwareScheduleResult FirmwareUpdater::Schedule(const char* desired_version,
                                                 const char* manifest_url) {
    Target target{};
    const settings::DeviceSettings settings_snapshot =
        settings_store_ == nullptr ? settings::DeviceSettings{}
                                   : settings_store_->Snapshot();
    if (queue_ == nullptr || state_mutex_ == nullptr || desired_version == nullptr || manifest_url == nullptr ||
        desired_version[0] == '\0' || std::strlen(desired_version) >= sizeof(target.desired_version) ||
        std::strlen(manifest_url) >= sizeof(target.manifest_url) ||
        !settings_snapshot.HasDeviceIdentity() ||
        !network::IsCanonicalArtifactManifestUrl(
            settings_snapshot.bootstrap_url, manifest_url)) {
        return FirmwareScheduleResult::kRejected;
    }
    // Bootstrap can keep the desired pointer after a successful rollout. The
    // running image must not download and reboot into the same version again.
    if (std::strcmp(desired_version, CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION) == 0) {
        return FirmwareScheduleResult::kAlreadyCurrent;
    }
    {
        SemaphoreGuard guard(state_mutex_);
        if (attempt_record_.has_attempt != 0 ||
            staged_image_.partition != nullptr) {
            return FirmwareScheduleResult::kRejected;
        }
    }
    target.generation = generation_.fetch_add(1) + 1;
    std::snprintf(target.desired_version, sizeof(target.desired_version), "%s", desired_version);
    std::snprintf(target.manifest_url, sizeof(target.manifest_url), "%s", manifest_url);
    return xQueueOverwrite(queue_, &target) == pdTRUE
               ? FirmwareScheduleResult::kScheduled
               : FirmwareScheduleResult::kRejected;
}

void FirmwareUpdater::Cancel() {
    if (queue_ != nullptr) xQueueReset(queue_);
    if (state_mutex_ == nullptr) {
        generation_.fetch_add(1);
        return;
    }
    SemaphoreGuard guard(state_mutex_);
    generation_.fetch_add(1);
    // A persisted attempt may belong to the previous boot and be awaiting
    // health confirmation or terminal-report replay.  Cancel only owns the
    // in-memory staged image created by this updater instance; ordinary boot
    // transitions must never rewrite an older attempt as cancelled.
    const bool owns_staged_operation = staged_image_.partition != nullptr;
    const bool restore_required = staged_image_.boot_committed;
    bool restore_succeeded = true;
    if (restore_required) {
        const esp_err_t restore = RestoreRunningBootLocked();
        if (restore != ESP_OK) {
            restore_succeeded = false;
            ESP_LOGE(kTag, "Unable to restore running boot partition on cancel: %s",
                     esp_err_to_name(restore));
        }
    }
    if (!FirmwareCancelCanContinueAfterRestore(restore_required,
                                                restore_succeeded)) {
        // The boot pointer may still select the staged image.  Retain both the
        // in-memory owner and nonterminal journal so a later Cancel can retry
        // the restore instead of losing the only recovery handle.
        return;
    }
    const bool terminal_transition_required =
        owns_staged_operation && attempt_record_.has_attempt != 0 &&
        !settings::IsTerminalFirmwareOtaAttemptPhase(attempt_record_.phase);
    bool terminal_transition_persisted = !terminal_transition_required;
    if (terminal_transition_required) {
        const esp_err_t error =
            AdvanceAttemptLocked(settings::FirmwareOtaAttemptPhase::kFailed,
                                 "cancelled");
        if (error != ESP_OK) {
            ESP_LOGE(kTag, "Unable to persist cancelled OTA attempt: %s",
                     esp_err_to_name(error));
        } else {
            terminal_transition_persisted = true;
        }
    }
    if (!FirmwareCancelCanReleaseStagedOwnership(
            terminal_transition_required, terminal_transition_persisted)) {
        return;
    }
    staged_image_ = StagedImage{};
}

esp_err_t FirmwareUpdater::ConfirmPendingBoot() {
    const esp_partition_t* running = esp_ota_get_running_partition();
    if (running == nullptr) return ESP_ERR_NOT_FOUND;
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
    const esp_err_t error = esp_ota_get_state_partition(running, &state);
    if (error != ESP_OK) return error;
    if (state != ESP_OTA_IMG_PENDING_VERIFY) return ESP_ERR_INVALID_STATE;
    ESP_LOGI(kTag, "Firmware boot health window passed; marking image valid");
    return esp_ota_mark_app_valid_cancel_rollback();
}

bool FirmwareUpdater::PendingBootVerification() const {
    const esp_partition_t* running = esp_ota_get_running_partition();
    if (running == nullptr) return false;
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
    return esp_ota_get_state_partition(running, &state) == ESP_OK &&
           state == ESP_OTA_IMG_PENDING_VERIFY;
}

std::uint8_t FirmwareUpdater::ActiveSlot() const {
    return PartitionSlot(esp_ota_get_running_partition());
}

bool FirmwareUpdater::HasAttempt() const {
    if (state_mutex_ == nullptr) return false;
    SemaphoreGuard guard(state_mutex_);
    return attempt_record_.has_attempt != 0;
}

bool FirmwareUpdater::HasStagedOwnership() const {
    if (state_mutex_ == nullptr) return false;
    SemaphoreGuard guard(state_mutex_);
    return staged_image_.partition != nullptr;
}

FirmwareOtaRecoveryDecision FirmwareUpdater::RecoveryStatus(
    FirmwareOtaNotification* notification) const {
    if (notification == nullptr || state_mutex_ == nullptr) {
        return FirmwareOtaRecoveryDecision::kNone;
    }
    settings::FirmwareOtaAttemptRecord attempt{};
    {
        SemaphoreGuard guard(state_mutex_);
        attempt = attempt_record_;
    }
    *notification = FirmwareOtaNotification{};
    if (attempt.has_attempt == 0) {
        return FirmwareOtaRecoveryDecision::kNone;
    }
    std::snprintf(notification->current_version,
                  sizeof(notification->current_version), "%s",
                  CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION);
    std::snprintf(notification->desired_version,
                  sizeof(notification->desired_version), "%s",
                  attempt.to_version);
    notification->expected_bytes = attempt.expected_bytes;
    notification->downloaded_bytes = attempt.expected_bytes;
    notification->security_epoch = attempt.security_epoch;
    notification->target_slot = attempt.to_slot;
    const esp_partition_t* running = esp_ota_get_running_partition();
    if (running == nullptr) {
        notification->event = FirmwareOtaEvent::kFailed;
        const bool terminal =
            settings::IsTerminalFirmwareOtaAttemptPhase(attempt.phase);
        std::snprintf(notification->error_code,
                      sizeof(notification->error_code), "%s",
                      terminal ? "terminal_runtime_mismatch"
                               : "running_partition_unavailable");
        return terminal ? FirmwareOtaRecoveryDecision::kInconsistent
                        : FirmwareOtaRecoveryDecision::kFailed;
    }
    esp_ota_img_states_t ota_state = ESP_OTA_IMG_UNDEFINED;
    const esp_err_t state_error = esp_ota_get_state_partition(running, &ota_state);
    FirmwareRunningImageState image_state = FirmwareRunningImageState::kUnknown;
    if (state_error == ESP_OK &&
        (ota_state == ESP_OTA_IMG_NEW ||
         ota_state == ESP_OTA_IMG_PENDING_VERIFY)) {
        image_state = FirmwareRunningImageState::kPendingVerify;
    } else if (state_error == ESP_OK && ota_state == ESP_OTA_IMG_VALID) {
        image_state = FirmwareRunningImageState::kValid;
    }
    const FirmwareOtaRecoveryDecision decision = DecideFirmwareOtaRecovery({
        .attempt = attempt,
        .running_version = CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION,
        .running_slot = PartitionSlot(running),
        .image_state = image_state,
    });
    notification->event =
        decision == FirmwareOtaRecoveryDecision::kPendingHealth
            ? FirmwareOtaEvent::kStaged
            : decision == FirmwareOtaRecoveryDecision::kActive
                  ? FirmwareOtaEvent::kActive
                  : decision == FirmwareOtaRecoveryDecision::kRolledBack
                        ? FirmwareOtaEvent::kRolledBack
                        : FirmwareOtaEvent::kFailed;
    notification->active_slot = PartitionSlot(running);
    if (decision == FirmwareOtaRecoveryDecision::kRolledBack ||
        decision == FirmwareOtaRecoveryDecision::kFailed ||
        decision == FirmwareOtaRecoveryDecision::kInconsistent) {
        const char* error =
            decision == FirmwareOtaRecoveryDecision::kInconsistent
                ? "terminal_runtime_mismatch"
                : attempt.error_code[0] != '\0'
                      ? attempt.error_code
                      : decision == FirmwareOtaRecoveryDecision::kRolledBack
                            ? "bootloader_rollback"
                            : "attempt_state_mismatch";
        std::snprintf(notification->error_code,
                      sizeof(notification->error_code), "%s", error);
    }
    return decision;
}

esp_err_t FirmwareUpdater::CommitStaged() {
    if (state_mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    SemaphoreGuard guard(state_mutex_);
    if (staged_image_.partition == nullptr ||
        staged_image_.attempt_id == 0 ||
        staged_image_.generation != generation_.load() ||
        attempt_record_.has_attempt == 0 ||
        attempt_record_.attempt_id != staged_image_.attempt_id ||
        attempt_record_.phase != settings::FirmwareOtaAttemptPhase::kStaged) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t error = esp_ota_set_boot_partition(staged_image_.partition);
    if (error != ESP_OK) return error;
    staged_image_.boot_committed = true;
    error = AdvanceAttemptLocked(settings::FirmwareOtaAttemptPhase::kRebooting);
    if (error != ESP_OK) {
        const esp_err_t restore = RestoreRunningBootLocked();
        staged_image_.boot_committed = false;
        return restore == ESP_OK ? error : restore;
    }
    return ESP_OK;
}

esp_err_t FirmwareUpdater::MarkAttemptPendingHealth() {
    if (state_mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    SemaphoreGuard guard(state_mutex_);
    return AdvanceAttemptLocked(
        settings::FirmwareOtaAttemptPhase::kPendingHealth);
}

esp_err_t FirmwareUpdater::MarkAttemptActive() {
    if (state_mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    SemaphoreGuard guard(state_mutex_);
    return AdvanceAttemptLocked(settings::FirmwareOtaAttemptPhase::kActive);
}

esp_err_t FirmwareUpdater::MarkAttemptRollbackRequested(
    const char* error_code) {
    if (state_mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    SemaphoreGuard guard(state_mutex_);
    return AdvanceAttemptLocked(
        settings::FirmwareOtaAttemptPhase::kRollbackRequested, error_code);
}

esp_err_t FirmwareUpdater::RestoreAttemptPendingHealth() {
    if (state_mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    SemaphoreGuard guard(state_mutex_);
    return AdvanceAttemptLocked(
        settings::FirmwareOtaAttemptPhase::kPendingHealth);
}

esp_err_t FirmwareUpdater::MarkRecoveredOutcome(
    FirmwareOtaRecoveryDecision decision, const char* error_code) {
    if (state_mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    SemaphoreGuard guard(state_mutex_);
    const char* remembered_error = attempt_record_.error_code[0] == '\0'
                                       ? nullptr
                                       : attempt_record_.error_code;
    switch (decision) {
        case FirmwareOtaRecoveryDecision::kActive:
            return AdvanceAttemptLocked(
                settings::FirmwareOtaAttemptPhase::kActive);
        case FirmwareOtaRecoveryDecision::kRolledBack:
            return AdvanceAttemptLocked(
                settings::FirmwareOtaAttemptPhase::kRolledBack,
                error_code != nullptr
                    ? error_code
                    : remembered_error != nullptr ? remembered_error
                                                   : "bootloader_rollback");
        case FirmwareOtaRecoveryDecision::kFailed:
            return AdvanceAttemptLocked(
                settings::FirmwareOtaAttemptPhase::kFailed,
                error_code != nullptr
                    ? error_code
                    : remembered_error != nullptr ? remembered_error
                                                   : "attempt_state_mismatch");
        case FirmwareOtaRecoveryDecision::kNone:
        case FirmwareOtaRecoveryDecision::kPendingHealth:
        case FirmwareOtaRecoveryDecision::kInconsistent:
            return ESP_ERR_INVALID_ARG;
    }
    return ESP_ERR_INVALID_ARG;
}

esp_err_t FirmwareUpdater::ClearCompletedAttempt() {
    if (state_mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    SemaphoreGuard guard(state_mutex_);
    settings::FirmwareOtaAttemptRecord updated = attempt_record_;
    if (!settings::ClearFirmwareOtaAttempt(&updated)) {
        return ESP_ERR_INVALID_STATE;
    }
    const esp_err_t error = SaveAttemptLocked(updated);
    if (error == ESP_OK) {
        attempt_record_ = updated;
        staged_image_ = StagedImage{};
    }
    return error;
}

esp_err_t FirmwareUpdater::RollbackPendingBoot() {
    if (!PendingBootVerification()) return ESP_ERR_INVALID_STATE;
    ESP_LOGE(kTag, "Firmware boot health failed; requesting bootloader rollback");
    return esp_ota_mark_app_invalid_rollback_and_reboot();
}

esp_err_t FirmwareUpdater::CancelStagedBoot() {
    if (state_mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    SemaphoreGuard guard(state_mutex_);
    esp_err_t error = RestoreRunningBootLocked();
    if (error != ESP_OK) return error;
    if (error == ESP_OK && attempt_record_.has_attempt != 0 &&
        !settings::IsTerminalFirmwareOtaAttemptPhase(attempt_record_.phase)) {
        error = AdvanceAttemptLocked(
            settings::FirmwareOtaAttemptPhase::kFailed,
            "reboot_cancelled");
    }
    if (error != ESP_OK) return error;
    staged_image_ = StagedImage{};
    return ESP_OK;
}

void FirmwareUpdater::TaskEntry(void* context) {
    static_cast<FirmwareUpdater*>(context)->TaskLoop();
}
void FirmwareUpdater::TaskLoop() {
    Target target{};
    while (xQueueReceive(queue_, &target, portMAX_DELAY) == pdTRUE) {
        if (IsCurrent(target.generation)) Reconcile(target);
    }
}
bool FirmwareUpdater::IsCurrent(std::uint32_t generation) const {
    return generation == generation_.load();
}

bool FirmwareUpdater::Emit(FirmwareOtaEvent event, const Target& target,
                            const VerifiedFirmwareManifest* manifest,
                            const char* error,
                            std::uint32_t downloaded_bytes) const {
    if (sink_ == nullptr || !IsCurrent(target.generation)) return false;
    FirmwareOtaNotification notification{};
    notification.event = event;
    std::snprintf(notification.desired_version, sizeof(notification.desired_version), "%s",
                  target.desired_version);
    if (manifest != nullptr) {
        std::snprintf(notification.current_version, sizeof(notification.current_version), "%s",
                      CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION);
        notification.expected_bytes = static_cast<std::uint32_t>(manifest->payload_bytes);
        notification.security_epoch = manifest->security_epoch;
    }
    notification.downloaded_bytes = downloaded_bytes;
    notification.active_slot = ActiveSlot();
    notification.target_slot = target_slot_;
    if (error != nullptr) std::snprintf(notification.error_code, sizeof(notification.error_code), "%s", error);
    while (IsCurrent(target.generation)) {
        if (sink_(notification, sink_context_)) return true;
        vTaskDelay(kNotificationRetryTicks);
    }
    return false;
}

esp_err_t FirmwareUpdater::FetchManifest(const Target& target) {
    const settings::DeviceSettings settings_snapshot =
        settings_store_ == nullptr ? settings::DeviceSettings{}
                                   : settings_store_->Snapshot();
    if (!settings_snapshot.HasDeviceIdentity()) return ESP_ERR_INVALID_STATE;
    response_size_ = 0;
    response_overflow_ = false;
    response_[0] = '\0';
    esp_http_client_config_t config = {};
    config.url = target.manifest_url;
    config.event_handler = &FirmwareUpdater::HttpEventHandler;
    config.user_data = this;
    config.timeout_ms = 8000;
    config.buffer_size = 1024;
    config.buffer_size_tx = 1024;
    config.keep_alive_enable = true;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    config.disable_auto_redirect = true;
    config.max_redirection_count = 0;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == nullptr) return ESP_ERR_NO_MEM;
    esp_err_t error = esp_http_client_set_header(client, "Device-Id", hardware_id_);
    if (error == ESP_OK) {
        char authorization[160] = {};
        const int length = std::snprintf(authorization, sizeof(authorization), "Bearer %s",
                                         settings_snapshot.device_token);
        if (length <= 7 || length >= static_cast<int>(sizeof(authorization))) error = ESP_ERR_INVALID_SIZE;
        if (error == ESP_OK) error = esp_http_client_set_header(client, "Authorization", authorization);
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Accept-Encoding", "identity");
    }
    if (error == ESP_OK) error = esp_http_client_perform(client);
    const int status = error == ESP_OK ? esp_http_client_get_status_code(client) : 0;
    esp_http_client_cleanup(client);
    if (error != ESP_OK) return error;
    if (status != 200 || response_overflow_) return ESP_ERR_INVALID_RESPONSE;
    return response_size_ > 0 ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

esp_err_t FirmwareUpdater::Download(const Target& target,
                                     const VerifiedFirmwareManifest& manifest) {
    if (!IsCurrent(target.generation)) return ESP_ERR_INVALID_STATE;
    const settings::DeviceSettings settings_snapshot =
        settings_store_ == nullptr ? settings::DeviceSettings{}
                                   : settings_store_->Snapshot();
    if (!settings_snapshot.HasDeviceIdentity()) return ESP_ERR_INVALID_STATE;
    const esp_partition_t* update = esp_ota_get_next_update_partition(nullptr);
    if (update == nullptr || manifest.payload_bytes > update->size) return ESP_ERR_INVALID_SIZE;
    esp_ota_handle_t handle = 0;
    esp_err_t error = esp_ota_begin(update, manifest.payload_bytes, &handle);
    if (error != ESP_OK) return error;
    esp_http_client_config_t config = {};
    config.url = manifest.payload_url;
    config.timeout_ms = 10000;
    config.buffer_size = 2048;
    config.buffer_size_tx = 1024;
    config.keep_alive_enable = true;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    config.disable_auto_redirect = true;
    config.max_redirection_count = 0;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == nullptr) {
        esp_ota_abort(handle);
        return ESP_ERR_NO_MEM;
    }
    char authorization[160] = {};
    std::snprintf(authorization, sizeof(authorization), "Bearer %s",
                  settings_snapshot.device_token);
    error = esp_http_client_set_header(client, "Device-Id", hardware_id_);
    if (error == ESP_OK) error = esp_http_client_set_header(client, "Authorization", authorization);
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Accept-Encoding", "identity");
    }
    if (error == ESP_OK) error = esp_http_client_open(client, 0);
    const int status = error == ESP_OK ? esp_http_client_fetch_headers(client) : 0;
    if (error == ESP_OK && status < 0) error = ESP_FAIL;
    if (error == ESP_OK && esp_http_client_get_status_code(client) != 200) error = ESP_ERR_INVALID_RESPONSE;
    if (error != ESP_OK) {
        esp_http_client_cleanup(client);
        esp_ota_abort(handle);
        return error;
    }
    psa_hash_operation_t digest = PSA_HASH_OPERATION_INIT;
    if (psa_hash_setup(&digest, PSA_ALG_SHA_256) != PSA_SUCCESS) {
        esp_http_client_cleanup(client);
        esp_ota_abort(handle);
        return ESP_FAIL;
    }
    // The manifest response buffer lives in PSRAM and is no longer needed after
    // verification. Reuse its first chunk instead of consuming 8 KiB of the OTA
    // worker's 12 KiB stack.
    auto* buffer = reinterpret_cast<std::uint8_t*>(response_);
    if (buffer == nullptr) {
        psa_hash_abort(&digest);
        esp_http_client_cleanup(client);
        esp_ota_abort(handle);
        return ESP_ERR_NO_MEM;
    }
    std::uint64_t downloaded = 0;
    std::uint64_t nextProgress = 256U * 1024U;
    Emit(FirmwareOtaEvent::kDownloading, target, &manifest, nullptr);
    while (downloaded < manifest.payload_bytes && IsCurrent(target.generation)) {
        const int read = esp_http_client_read(
            client, reinterpret_cast<char*>(buffer), kChunkBytes);
        if (read < 0) { error = ESP_FAIL; break; }
        if (read == 0) { error = ESP_ERR_INVALID_SIZE; break; }
        error = esp_ota_write(handle, buffer, static_cast<std::size_t>(read));
        if (error != ESP_OK) break;
        if (psa_hash_update(&digest, buffer, static_cast<std::size_t>(read)) !=
            PSA_SUCCESS) {
            error = ESP_FAIL;
            break;
        }
        downloaded += static_cast<std::uint64_t>(read);
        if (downloaded >= nextProgress || downloaded == manifest.payload_bytes) {
            Emit(FirmwareOtaEvent::kDownloading, target, &manifest, nullptr,
                 static_cast<std::uint32_t>(downloaded));
            nextProgress = downloaded + 256U * 1024U;
        }
    }
    std::array<std::uint8_t, 32> actual{};
    std::size_t hashLength = 0;
    if (psa_hash_finish(&digest, actual.data(), actual.size(), &hashLength) !=
            PSA_SUCCESS ||
        hashLength != actual.size()) {
        psa_hash_abort(&digest);
        error = ESP_FAIL;
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    if (!IsCurrent(target.generation)) error = ESP_ERR_INVALID_STATE;
    if (error == ESP_OK && downloaded != manifest.payload_bytes) error = ESP_ERR_INVALID_SIZE;
    char actualHex[65] = {};
    HashHex(actual, actualHex);
    if (error == ESP_OK && std::strcmp(actualHex, manifest.payload_sha256) != 0) error = ESP_ERR_INVALID_CRC;
    if (error == ESP_OK && IsCurrent(target.generation) &&
        Emit(FirmwareOtaEvent::kVerifying, target, &manifest, nullptr) &&
        IsCurrent(target.generation)) {
        error = esp_ota_end(handle);
    } else {
        esp_ota_abort(handle);
    }
    if (error != ESP_OK || !IsCurrent(target.generation)) {
        return error == ESP_OK ? ESP_ERR_INVALID_STATE : error;
    }
    {
        SemaphoreGuard guard(state_mutex_);
        if (!IsCurrent(target.generation)) return ESP_ERR_INVALID_STATE;
        error = PersistSecurityEpoch(manifest.security_epoch);
        if (error == ESP_OK) {
            error = BeginStagedAttemptLocked(target, manifest, update);
        }
    }
    if (error == ESP_OK && IsCurrent(target.generation)) {
        Emit(FirmwareOtaEvent::kStaged, target, &manifest, nullptr,
             static_cast<std::uint32_t>(downloaded));
    }
    return error;
}

esp_err_t FirmwareUpdater::PersistSecurityEpoch(std::uint32_t epoch) {
    if (nvs_handle_ == 0 || epoch < security_epoch_floor_) return ESP_ERR_INVALID_STATE;
    esp_err_t error = nvs_set_u32(nvs_handle_, "epoch", epoch);
    if (error == ESP_OK) error = nvs_commit(nvs_handle_);
    if (error == ESP_OK) security_epoch_floor_ = epoch;
    return error;
}

esp_err_t FirmwareUpdater::SaveAttemptLocked(
    const settings::FirmwareOtaAttemptRecord& record) {
    if (nvs_handle_ == 0 ||
        !settings::IsValidFirmwareOtaAttemptRecord(record)) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t error = nvs_set_blob(nvs_handle_, kAttemptRecordKey, &record,
                                   sizeof(record));
    if (error == ESP_OK) error = nvs_commit(nvs_handle_);
    return error;
}

esp_err_t FirmwareUpdater::AdvanceAttemptLocked(
    settings::FirmwareOtaAttemptPhase phase, const char* error_code) {
    settings::FirmwareOtaAttemptRecord updated = attempt_record_;
    if (!settings::AdvanceFirmwareOtaAttempt(&updated, phase, error_code)) {
        return ESP_ERR_INVALID_STATE;
    }
    const esp_err_t error = SaveAttemptLocked(updated);
    if (error == ESP_OK) attempt_record_ = updated;
    return error;
}

esp_err_t FirmwareUpdater::BeginStagedAttemptLocked(
    const Target& target, const VerifiedFirmwareManifest& manifest,
    const esp_partition_t* update) {
    const esp_partition_t* running = esp_ota_get_running_partition();
    if (update == nullptr || running == nullptr ||
        !IsCurrent(target.generation) ||
        manifest.payload_bytes > UINT32_MAX) {
        return ESP_ERR_INVALID_STATE;
    }
    settings::FirmwareOtaAttemptRecord updated = attempt_record_;
    if (!settings::BeginFirmwareOtaAttempt(
            &updated, CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION,
            target.desired_version, PartitionSlot(running),
            PartitionSlot(update), manifest.security_epoch,
            static_cast<std::uint32_t>(manifest.payload_bytes))) {
        return ESP_ERR_INVALID_STATE;
    }
    const esp_err_t error = SaveAttemptLocked(updated);
    if (error != ESP_OK) return error;
    attempt_record_ = updated;
    staged_image_ = StagedImage{
        .partition = update,
        .generation = target.generation,
        .attempt_id = updated.attempt_id,
        .boot_committed = false,
    };
    return ESP_OK;
}

esp_err_t FirmwareUpdater::RestoreRunningBootLocked() {
    const esp_partition_t* running = esp_ota_get_running_partition();
    if (running == nullptr) return ESP_ERR_NOT_FOUND;
    const esp_err_t error = esp_ota_set_boot_partition(running);
    if (error == ESP_OK) staged_image_.boot_committed = false;
    return error;
}

void FirmwareUpdater::Reconcile(const Target& target) {
    if (settings_store_ == nullptr ||
        !settings_store_->Snapshot().HasDeviceIdentity()) {
        return;
    }
    current_target_ = target;
    const esp_partition_t* update = esp_ota_get_next_update_partition(nullptr);
    target_slot_ = PartitionSlot(update);
    Emit(FirmwareOtaEvent::kChecking, target, nullptr, nullptr);
    if (FetchManifest(target) != ESP_OK) {
        Emit(FirmwareOtaEvent::kFailed, target, nullptr, "manifest_fetch_failed");
        return;
    }
    if (!IsCurrent(target.generation)) return;
    std::array<std::uint8_t, 32> publicKey{};
    if (!DecodePublicKey(CONFIG_VEETEE_RESOURCE_SIGNING_PUBLIC_KEY_HEX, &publicKey)) {
        Emit(FirmwareOtaEvent::kFailed, target, nullptr, "trust_root_invalid");
        return;
    }
    const TrustedReleaseKey key = {
        .key_id = CONFIG_VEETEE_RESOURCE_SIGNING_KEY_ID,
        .minimum_security_epoch = security_epoch_floor_,
        .public_key = publicKey,
    };
    DeviceFirmwareCapability capability = {
        .board = board::kBoardName,
        .chip = "esp32s3",
        .flash_bytes = 16ULL * 1024ULL * 1024ULL,
        .psram_bytes = esp_psram_is_initialized() ? esp_psram_get_size() : 0,
        .slot_bytes = update == nullptr ? 0 : update->size,
    };
    VerifiedFirmwareManifest manifest{};
    const FirmwareManifestError verify = VerifyFirmwareManifest(
        std::string_view(response_, response_size_), capability, &key, 1, &manifest);
    if (verify != FirmwareManifestError::kOk) {
        Emit(FirmwareOtaEvent::kFailed, target, nullptr, FirmwareManifestErrorName(verify));
        return;
    }
    if (!IsCurrent(target.generation)) return;
    const settings::DeviceSettings settings_snapshot =
        settings_store_->Snapshot();
    if (!network::IsCanonicalArtifactContentUrl(
            settings_snapshot.bootstrap_url, manifest.payload_url)) {
        Emit(FirmwareOtaEvent::kFailed, target, &manifest,
             "payload_origin_rejected");
        return;
    }
    if (std::strcmp(manifest.version, target.desired_version) != 0) {
        Emit(FirmwareOtaEvent::kFailed, target, &manifest, "desired_version_mismatch");
        return;
    }
    const esp_err_t error = Download(target, manifest);
    if (error != ESP_OK) {
        Emit(FirmwareOtaEvent::kFailed, target, &manifest, esp_err_to_name(error));
        return;
    }
    // The application task owns the reboot after it has persisted terminal
    // reporting state and rendered the upgrading boundary.
}

esp_err_t FirmwareUpdater::HttpEventHandler(esp_http_client_event_t* event) {
    if (event == nullptr || event->user_data == nullptr) return ESP_FAIL;
    auto* self = static_cast<FirmwareUpdater*>(event->user_data);
    if (event->event_id == HTTP_EVENT_ON_DATA && event->data != nullptr &&
        event->data_len > 0) {
        if (self->response_size_ + static_cast<std::size_t>(event->data_len) >=
            kMaximumResponseBytes) {
            self->response_overflow_ = true;
            return ESP_ERR_NO_MEM;
        }
        std::memcpy(self->response_ + self->response_size_, event->data,
                    static_cast<std::size_t>(event->data_len));
        self->response_size_ += static_cast<std::size_t>(event->data_len);
        self->response_[self->response_size_] = '\0';
    }
    return ESP_OK;
}
}  // namespace veetee::ota
