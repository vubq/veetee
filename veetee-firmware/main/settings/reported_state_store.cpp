#include "settings/reported_state_store.h"

#include <cinttypes>

#include "esp_log.h"

namespace veetee::settings {
namespace {

constexpr char kTag[] = "veetee_report_state";
constexpr char kNamespace[] = "veetee_report";
constexpr char kRecordKey[] = "state";

}  // namespace

ReportedStateStore::~ReportedStateStore() {
    if (handle_ != 0) nvs_close(handle_);
}

esp_err_t ReportedStateStore::Initialize() {
    if (handle_ != 0) return ESP_ERR_INVALID_STATE;
    esp_err_t error = nvs_open(kNamespace, NVS_READWRITE, &handle_);
    if (error != ESP_OK) return error;

    std::size_t size = sizeof(record_);
    error = nvs_get_blob(handle_, kRecordKey, &record_, &size);
    if (error != ESP_OK && error != ESP_ERR_NVS_NOT_FOUND) return error;
    if (error == ESP_ERR_NVS_NOT_FOUND || size != sizeof(record_) ||
        !IsValidReportedStateRecord(record_)) {
        if (error == ESP_OK) ESP_LOGW(kTag, "Resetting invalid reported-state record");
        record_ = MakeDefaultReportedStateRecord();
        return Save(record_);
    }
    ESP_LOGI(kTag, "Reported-state sequence=%" PRIu32 " pending=%s",
             record_.last_issued_version, record_.has_pending ? "yes" : "no");
    return ESP_OK;
}

esp_err_t ReportedStateStore::Save(const ReportedStateRecord& record) {
    if (handle_ == 0 || !IsValidReportedStateRecord(record)) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t error = nvs_set_blob(handle_, kRecordKey, &record, sizeof(record));
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) record_ = record;
    return error;
}

}  // namespace veetee::settings
