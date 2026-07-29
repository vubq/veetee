#include "settings/device_config_store.h"

#include <cinttypes>

#include "esp_log.h"
#include "settings/device_config_store_policy.h"

namespace veetee::settings {
namespace {

constexpr char kTag[] = "veetee_device_cfg";

DeviceConfigBlobStatus BlobStatus(esp_err_t error) {
    switch (error) {
        case ESP_OK:
            return DeviceConfigBlobStatus::kFound;
        case ESP_ERR_NVS_NOT_FOUND:
            return DeviceConfigBlobStatus::kNotFound;
        case ESP_ERR_NVS_INVALID_LENGTH:
            return DeviceConfigBlobStatus::kLengthMismatch;
        case ESP_ERR_NVS_TYPE_MISMATCH:
            return DeviceConfigBlobStatus::kTypeMismatch;
        default:
            return DeviceConfigBlobStatus::kStorageError;
    }
}

esp_err_t SaveDefaultRecord(nvs_handle_t handle,
                            std::uint32_t minimum_security_epoch,
                            DeviceConfigRecord* record) {
    if (handle == 0 || record == nullptr) return ESP_ERR_INVALID_ARG;
    if (!InitializeDefaultDeviceConfigRecord(record,
                                             minimum_security_epoch)) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t error = nvs_set_blob(handle, kDeviceConfigNvsRecordKey, record,
                                   sizeof(*record));
    if (error == ESP_OK) error = nvs_commit(handle);
    return error;
}

}  // namespace

DeviceConfigStore::~DeviceConfigStore() {
    if (handle_ != 0) nvs_close(handle_);
    if (mutex_ != nullptr) vSemaphoreDelete(mutex_);
}

esp_err_t DeviceConfigStore::Initialize(
    std::uint32_t minimum_security_epoch) {
    if (handle_ != 0 || mutex_ != nullptr || minimum_security_epoch == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    mutex_ = xSemaphoreCreateMutex();
    if (mutex_ == nullptr) return ESP_ERR_NO_MEM;
    esp_err_t error =
        nvs_open(kDeviceConfigNvsNamespace, NVS_READWRITE, &handle_);
    if (error != ESP_OK) return error;

    // Probe before reading. ESP-IDF returns ESP_ERR_NVS_INVALID_LENGTH when an
    // older blob is larger than the caller's buffer, so a fixed-size read can
    // otherwise turn an isolated config schema mismatch into a reboot loop.
    std::size_t stored_bytes = 0;
    error = nvs_get_blob(handle_, kDeviceConfigNvsRecordKey, nullptr,
                         &stored_bytes);
    DeviceConfigBlobStatus status = BlobStatus(error);
    DeviceConfigBlobAction action = DecideDeviceConfigBlobAction(
        status, stored_bytes, sizeof(record_));
    if (action == DeviceConfigBlobAction::kFail) return error;
    if (action == DeviceConfigBlobAction::kResetConfigOnly) {
        if (status != DeviceConfigBlobStatus::kNotFound) {
            ESP_LOGW(kTag,
                     "Replacing incompatible device-config blob stored_bytes=%u expected_bytes=%u status=%u; Wi-Fi and identity remain intact",
                     static_cast<unsigned>(stored_bytes),
                     static_cast<unsigned>(sizeof(record_)),
                     static_cast<unsigned>(status));
        }
        return SaveDefaultRecord(handle_, minimum_security_epoch, &record_);
    }

    std::size_t read_bytes = sizeof(record_);
    error = nvs_get_blob(handle_, kDeviceConfigNvsRecordKey, &record_,
                         &read_bytes);
    status = BlobStatus(error);
    action = DecideDeviceConfigBlobAction(status, read_bytes, sizeof(record_));
    if (action == DeviceConfigBlobAction::kFail) return error;
    if (action == DeviceConfigBlobAction::kResetConfigOnly ||
        !IsValidDeviceConfigRecord(record_)) {
        ESP_LOGW(kTag,
                 "Replacing invalid device-config record stored_bytes=%u expected_bytes=%u status=%u; Wi-Fi and identity remain intact",
                 static_cast<unsigned>(read_bytes),
                 static_cast<unsigned>(sizeof(record_)),
                 static_cast<unsigned>(status));
        return SaveDefaultRecord(handle_, minimum_security_epoch, &record_);
    }
    const DeviceConfigRecordMigration migration =
        ReconcileDeviceConfigSecurityFloor(&record_, minimum_security_epoch);
    if (migration == DeviceConfigRecordMigration::kInvalid) {
        return ESP_ERR_INVALID_STATE;
    }
    if (migration ==
        DeviceConfigRecordMigration::kResetForSecurityEpoch) {
        ESP_LOGW(kTag,
                 "Invalidating applied config below required security epoch=%" PRIu32,
                 minimum_security_epoch);
        error = nvs_set_blob(handle_, kDeviceConfigNvsRecordKey, &record_,
                             sizeof(record_));
        if (error == ESP_OK) error = nvs_commit(handle_);
        return error;
    }
    ESP_LOGI(kTag, "Applied device config=%" PRIu32 " wake_profile=%s",
             record_.applied_version,
             record_.has_wake_profile ? "yes" : "no");
    return ESP_OK;
}

esp_err_t DeviceConfigStore::SaveApplied(
    const config::DeviceConfig& config, const char* etag) {
    if (handle_ == 0 || mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(mutex_, portMAX_DELAY);
    DeviceConfigRecord updated = record_;
    if (!StageAppliedDeviceConfig(&updated, config, etag)) {
        xSemaphoreGive(mutex_);
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t error =
        nvs_set_blob(handle_, kDeviceConfigNvsRecordKey, &updated,
                     sizeof(updated));
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) record_ = updated;
    xSemaphoreGive(mutex_);
    return error;
}

esp_err_t DeviceConfigStore::PersistWakeAudioPrivacyRevocation() {
    if (handle_ == 0 || mutex_ == nullptr) return ESP_ERR_INVALID_STATE;
    xSemaphoreTake(mutex_, portMAX_DELAY);
    if (!IsValidDeviceConfigRecord(record_)) {
        xSemaphoreGive(mutex_);
        return ESP_ERR_INVALID_STATE;
    }
    if (record_.wake_audio_privacy_revoked != 0) {
        xSemaphoreGive(mutex_);
        return ESP_OK;
    }
    DeviceConfigRecord updated = record_;
    if (!MarkWakeAudioPrivacyRevoked(&updated)) {
        xSemaphoreGive(mutex_);
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t error = nvs_set_blob(handle_, kDeviceConfigNvsRecordKey,
                                   &updated, sizeof(updated));
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) record_ = updated;
    xSemaphoreGive(mutex_);
    return error;
}

esp_err_t DeviceConfigStore::Reset(
    std::uint32_t minimum_security_epoch) {
    if (handle_ == 0 || mutex_ == nullptr || minimum_security_epoch == 0) {
        return ESP_ERR_INVALID_ARG;
    }
    xSemaphoreTake(mutex_, portMAX_DELAY);
    const DeviceConfigRecord reset =
        MakeDefaultDeviceConfigRecord(minimum_security_epoch);
    esp_err_t error = nvs_set_blob(handle_, kDeviceConfigNvsRecordKey, &reset,
                                   sizeof(reset));
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) record_ = reset;
    xSemaphoreGive(mutex_);
    return error;
}

DeviceConfigRecord DeviceConfigStore::Snapshot() const {
    if (mutex_ == nullptr) return record_;
    xSemaphoreTake(mutex_, portMAX_DELAY);
    const DeviceConfigRecord snapshot = record_;
    xSemaphoreGive(mutex_);
    return snapshot;
}

bool DeviceConfigStore::WakeAudioPrivacyRevoked() const {
    if (mutex_ == nullptr) {
        return DeviceConfigWakeAudioPrivacyRevoked(record_);
    }
    xSemaphoreTake(mutex_, portMAX_DELAY);
    const bool revoked = DeviceConfigWakeAudioPrivacyRevoked(record_);
    xSemaphoreGive(mutex_);
    return revoked;
}

bool DeviceConfigStore::LoadApplied(config::DeviceConfig* config) const {
    if (config == nullptr || mutex_ == nullptr) return false;
    xSemaphoreTake(mutex_, portMAX_DELAY);
    const bool loaded = LoadAppliedDeviceConfig(record_, config);
    xSemaphoreGive(mutex_);
    return loaded;
}

}  // namespace veetee::settings
