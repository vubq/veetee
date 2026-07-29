#include "settings/resource_state_store.h"

#include <array>
#include <cinttypes>
#include <cstring>

#include "esp_log.h"

namespace veetee::settings {
namespace {

constexpr char kTag[] = "veetee_resource_state";
constexpr char kRecordKey[] = "state";

}  // namespace

ResourceStateStore::~ResourceStateStore() {
    if (handle_ != 0) nvs_close(handle_);
}

esp_err_t ResourceStateStore::Initialize(
    std::uint32_t minimum_security_epoch, const char* nvs_namespace,
    const char* default_version, const char* default_activation_model_id,
    const char* default_interrupt_model_id) {
    if (handle_ != 0 || nvs_namespace == nullptr || nvs_namespace[0] == '\0' ||
        std::strlen(nvs_namespace) > 15) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t error = nvs_open(nvs_namespace, NVS_READWRITE, &handle_);
    if (error != ESP_OK) return error;

    std::size_t stored_size = 0;
    error = nvs_get_blob(handle_, kRecordKey, nullptr, &stored_size);
    if (error == ESP_ERR_NVS_TYPE_MISMATCH) {
        ESP_LOGW(kTag,
                 "Replacing resource state with incompatible NVS type; only key=%s is reset",
                 kRecordKey);
        error = nvs_erase_key(handle_, kRecordKey);
        if (error == ESP_OK) error = nvs_commit(handle_);
        if (error != ESP_OK) return error;
        const char* recovery_version =
            default_activation_model_id == nullptr
                ? default_version
                : "reconcile-required";
        record_ = MakeDefaultResourceRecord(
            minimum_security_epoch, recovery_version);
        return Save(record_);
    }
    if (error != ESP_OK && error != ESP_ERR_NVS_NOT_FOUND) return error;
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        record_ = MakeDefaultResourceRecord(
            minimum_security_epoch, default_version,
            default_activation_model_id, default_interrupt_model_id);
        return Save(record_);
    }

    bool migrated = false;
    if (stored_size == sizeof(record_)) {
        std::size_t read_size = sizeof(record_);
        error = nvs_get_blob(handle_, kRecordKey, &record_, &read_size);
        if (error != ESP_OK) return error;
    } else if (stored_size == 268) {
        std::array<std::uint8_t, 268> legacy{};
        std::size_t read_size = legacy.size();
        error = nvs_get_blob(handle_, kRecordKey, legacy.data(), &read_size);
        if (error != ESP_OK) return error;
        migrated = MigrateResourceRecordV1(
            legacy.data(), read_size, default_version,
            default_activation_model_id, default_interrupt_model_id,
            &record_);
    }
    if (!IsValidResourceRecord(record_)) {
        ESP_LOGW(kTag,
                 "Replacing incompatible resource state stored_bytes=%u expected_bytes=%u",
                 static_cast<unsigned>(stored_size),
                 static_cast<unsigned>(sizeof(record_)));
        const char* recovery_version =
            default_activation_model_id == nullptr
                ? default_version
                : "reconcile-required";
        record_ = MakeDefaultResourceRecord(
            minimum_security_epoch, recovery_version);
        return Save(record_);
    }
    if (record_.security_epoch_floor < minimum_security_epoch) {
        ESP_LOGE(kTag,
                 "Resource epoch floor=%" PRIu32 " is below required epoch=%" PRIu32,
                 record_.security_epoch_floor, minimum_security_epoch);
        return ESP_ERR_INVALID_VERSION;
    }
    if (migrated) {
        ESP_LOGW(kTag,
                 "Migrated resource state V1->V2 active_slot=%u active_version=%s detector_inventory=%s",
                 static_cast<unsigned>(record_.active_slot),
                 record_.active_version,
                 HasResourceDetectorInventory(record_.active_detectors)
                     ? "known"
                     : "unknown");
        error = Save(record_);
        if (error != ESP_OK) return error;
    }
    ESP_LOGI(kTag,
             "Resource state phase=%u active_slot=%u active_version=%s bytes=%" PRIu32 "/%" PRIu32,
             static_cast<unsigned>(record_.phase),
             static_cast<unsigned>(record_.active_slot), record_.active_version,
             record_.downloaded_bytes, record_.expected_bytes);
    return ESP_OK;
}

esp_err_t ResourceStateStore::Save(const ResourceRecord& record) {
    if (handle_ == 0 || !IsValidResourceRecord(record)) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t error = nvs_set_blob(handle_, kRecordKey, &record, sizeof(record));
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) record_ = record;
    return error;
}

}  // namespace veetee::settings
