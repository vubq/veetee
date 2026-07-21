#include "settings/resource_state_store.h"

#include <cinttypes>

#include "esp_log.h"

namespace veetee::settings {
namespace {

constexpr char kTag[] = "veetee_resource_state";
constexpr char kNamespace[] = "veetee_resource";
constexpr char kRecordKey[] = "state";

}  // namespace

ResourceStateStore::~ResourceStateStore() {
    if (handle_ != 0) nvs_close(handle_);
}

esp_err_t ResourceStateStore::Initialize(
    std::uint32_t minimum_security_epoch) {
    if (handle_ != 0) return ESP_ERR_INVALID_STATE;
    esp_err_t error = nvs_open(kNamespace, NVS_READWRITE, &handle_);
    if (error != ESP_OK) return error;

    std::size_t size = sizeof(record_);
    error = nvs_get_blob(handle_, kRecordKey, &record_, &size);
    if (error != ESP_OK && error != ESP_ERR_NVS_NOT_FOUND) return error;
    if (error == ESP_ERR_NVS_NOT_FOUND || size != sizeof(record_) ||
        !IsValidResourceRecord(record_)) {
        if (error == ESP_OK) ESP_LOGW(kTag, "Resetting invalid resource state record");
        record_ = MakeDefaultResourceRecord(minimum_security_epoch);
        return Save(record_);
    }
    if (record_.security_epoch_floor < minimum_security_epoch) {
        ESP_LOGE(kTag,
                 "Resource epoch floor=%" PRIu32 " is below required epoch=%" PRIu32,
                 record_.security_epoch_floor, minimum_security_epoch);
        return ESP_ERR_INVALID_VERSION;
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
