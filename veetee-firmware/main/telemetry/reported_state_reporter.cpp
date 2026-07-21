#include "telemetry/reported_state_reporter.h"

#include <algorithm>
#include <array>
#include <cinttypes>
#include <cstdio>
#include <cstring>

#include "cJSON.h"
#include "esp_app_desc.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "network/endpoint_url.h"

namespace veetee::telemetry {
namespace {

constexpr char kTag[] = "veetee_reporter";
constexpr std::uint32_t kInitialRetryMs = 500;
constexpr std::uint32_t kMaximumRetryMs = 30000;
constexpr std::size_t kMaximumBodyBytes = 1024;

bool IsSafeDeviceId(const char* value) {
    if (value == nullptr || value[0] == '\0' || std::strlen(value) > 64) {
        return false;
    }
    return std::all_of(value, value + std::strlen(value), [](char character) {
        return (character >= 'a' && character <= 'z') ||
               (character >= 'A' && character <= 'Z') ||
               (character >= '0' && character <= '9') || character == '-' ||
               character == '_';
    });
}

void GenerateBootId(std::array<char, 37>* output) {
    std::uint8_t bytes[16] = {};
    esp_fill_random(bytes, sizeof(bytes));
    bytes[6] = static_cast<std::uint8_t>((bytes[6] & 0x0FU) | 0x40U);
    bytes[8] = static_cast<std::uint8_t>((bytes[8] & 0x3FU) | 0x80U);
    std::snprintf(
        output->data(), output->size(),
        "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
        bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5], bytes[6],
        bytes[7], bytes[8], bytes[9], bytes[10], bytes[11], bytes[12],
        bytes[13], bytes[14], bytes[15]);
}

bool AddString(cJSON* object, const char* name, const char* value) {
    return cJSON_AddStringToObject(object, name, value) != nullptr;
}

bool AddNumber(cJSON* object, const char* name, std::uint32_t value) {
    return cJSON_AddNumberToObject(object, name, value) != nullptr;
}

}  // namespace

esp_err_t ReportedStateReporter::Initialize(
    settings::DeviceSettings* settings) {
    if (settings == nullptr || settings_ != nullptr) return ESP_ERR_INVALID_ARG;
    settings_ = settings;
    GenerateBootId(&boot_id_);

    std::uint8_t mac[6] = {};
    esp_err_t error = esp_read_mac(mac, ESP_MAC_WIFI_STA);
    if (error != ESP_OK) return error;
    std::snprintf(hardware_id_.data(), hardware_id_.size(),
                  "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2],
                  mac[3], mac[4], mac[5]);

    error = state_store_.Initialize();
    if (error != ESP_OK) return error;
    outbox_mutex_ = xSemaphoreCreateMutex();
    if (outbox_mutex_ == nullptr) return ESP_ERR_NO_MEM;
    if (xTaskCreate(&ReportedStateReporter::TaskEntry, "veetee_report", 7168,
                    this, 3, &task_) != pdPASS) {
        vSemaphoreDelete(outbox_mutex_);
        outbox_mutex_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(kTag, "Reporter ready boot_id=%s", boot_id_.data());
    return ESP_OK;
}

bool ReportedStateReporter::Schedule(
    const settings::ReportedResourceState& state) {
    if (task_ == nullptr || !settings::IsValidReportedResourceState(state)) {
        return false;
    }
    xSemaphoreTake(outbox_mutex_, portMAX_DELAY);
    const bool queued = outbox_.Push(state);
    xSemaphoreGive(outbox_mutex_);
    if (!queued) {
        ESP_LOGE(kTag, "Reported-state terminal queue is full phase=%s",
                 settings::ReportedResourcePhaseName(state.phase));
        return false;
    }
    xTaskNotifyGive(task_);
    return true;
}

void ReportedStateReporter::TaskEntry(void* context) {
    static_cast<ReportedStateReporter*>(context)->TaskLoop();
}

void ReportedStateReporter::TaskLoop() {
    settings::ReportedResourceState current{};
    std::uint32_t current_version = 0;
    bool have_current = false;
    bool terminal = false;
    std::uint32_t retry_ms = kInitialRetryMs;

    while (true) {
        const auto& persisted = state_store_.record();
        if (!have_current && persisted.has_pending != 0) {
            current = persisted.pending;
            current_version = persisted.pending_version;
            have_current = true;
            terminal = true;
        }
        if (!have_current) {
            xSemaphoreTake(outbox_mutex_, portMAX_DELAY);
            have_current = outbox_.Pop(&current, &terminal);
            xSemaphoreGive(outbox_mutex_);
            if (have_current) current_version = 0;
        }
        if (!have_current) {
            ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
            continue;
        }

        esp_err_t error = ESP_OK;
        if (current_version == 0) {
            error = PersistVersion(current, terminal, &current_version);
        }
        if (error == ESP_OK) error = Send(current, current_version);
        if (error == ESP_OK && terminal) {
            error = ClearDeliveredTerminal(current_version);
        }
        if (error == ESP_OK) {
            ESP_LOGI(kTag, "Reported resource phase=%s version=%" PRIu32,
                     settings::ReportedResourcePhaseName(current.phase),
                     current_version);
            have_current = false;
            current_version = 0;
            retry_ms = kInitialRetryMs;
            continue;
        }

        ESP_LOGW(kTag,
                 "Reported-state delivery failed phase=%s version=%" PRIu32
                 " error=%s retry_ms=%" PRIu32,
                 settings::ReportedResourcePhaseName(current.phase),
                 current_version, esp_err_to_name(error), retry_ms);
        ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(retry_ms));
        retry_ms = std::min(kMaximumRetryMs, retry_ms * 2U);
        xSemaphoreTake(outbox_mutex_, portMAX_DELAY);
        const bool has_replacement = outbox_.HasTerminal() || outbox_.HasLatest();
        xSemaphoreGive(outbox_mutex_);
        if (!terminal && has_replacement) {
            have_current = false;
            current_version = 0;
            retry_ms = kInitialRetryMs;
        }
    }
}

esp_err_t ReportedStateReporter::PersistVersion(
    const settings::ReportedResourceState& state, bool terminal,
    std::uint32_t* version) {
    settings::ReportedStateRecord record = state_store_.record();
    const bool valid = terminal
                           ? settings::StagePendingReportedState(&record, state,
                                                                 version)
                           : settings::IssueReportedStateVersion(&record, version);
    return valid ? state_store_.Save(record) : ESP_ERR_INVALID_STATE;
}

esp_err_t ReportedStateReporter::ClearDeliveredTerminal(
    std::uint32_t version) {
    settings::ReportedStateRecord record = state_store_.record();
    return settings::ClearPendingReportedState(&record, version)
               ? state_store_.Save(record)
               : ESP_ERR_INVALID_STATE;
}

esp_err_t ReportedStateReporter::Send(
    const settings::ReportedResourceState& state, std::uint32_t version) {
    if (settings_ == nullptr) return ESP_ERR_INVALID_STATE;
    const settings::DeviceSettings snapshot = *settings_;
    if (!snapshot.HasDeviceIdentity() ||
        !IsSafeDeviceId(snapshot.device_id)) {
        return ESP_ERR_INVALID_STATE;
    }

    char path[96] = {};
    const int path_length = std::snprintf(
        path, sizeof(path), "/veetee/devices/%s/reported-state",
        snapshot.device_id);
    char url[321] = {};
    if (path_length <= 0 || path_length >= static_cast<int>(sizeof(path)) ||
        !network::BuildHttpOriginEndpoint(snapshot.bootstrap_url, path, url,
                                          sizeof(url))) {
        return ESP_ERR_INVALID_ARG;
    }

    std::array<char, kMaximumBodyBytes> body{};
    if (!BuildBody(state, version, body.data(), body.size())) {
        return ESP_ERR_INVALID_SIZE;
    }

    esp_http_client_config_t config = {};
    config.url = url;
    config.timeout_ms = 6000;
    config.buffer_size = 1024;
    config.buffer_size_tx = 1024;
    config.keep_alive_enable = true;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    config.disable_auto_redirect = true;
    config.max_redirection_count = 0;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == nullptr) return ESP_ERR_NO_MEM;

    char authorization[160] = {};
    esp_err_t error = esp_http_client_set_method(client, HTTP_METHOD_PUT);
    if (error == ESP_OK) {
        const int length = std::snprintf(authorization, sizeof(authorization),
                                         "Bearer %s", snapshot.device_token);
        if (length <= 7 || length >= static_cast<int>(sizeof(authorization))) {
            error = ESP_ERR_INVALID_SIZE;
        }
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Authorization",
                                           authorization);
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Device-Id",
                                           hardware_id_.data());
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Content-Type",
                                           "application/json");
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Accept", "application/json");
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_post_field(client, body.data(),
                                               std::strlen(body.data()));
    }
    if (error == ESP_OK) error = esp_http_client_perform(client);
    const int status =
        error == ESP_OK ? esp_http_client_get_status_code(client) : 0;
    esp_http_client_cleanup(client);
    std::fill(std::begin(authorization), std::end(authorization), '\0');
    if (error == ESP_OK && (status < 200 || status >= 300)) {
        ESP_LOGW(kTag, "Reported-state HTTP status=%d", status);
        error = ESP_ERR_INVALID_RESPONSE;
    }
    return error;
}

bool ReportedStateReporter::BuildBody(
    const settings::ReportedResourceState& state, std::uint32_t version,
    char* output, std::size_t output_size) const {
    if (!settings::IsValidReportedResourceState(state) || version == 0 ||
        output == nullptr || output_size == 0) {
        return false;
    }
    cJSON* root = cJSON_CreateObject();
    cJSON* reported = root == nullptr ? nullptr : cJSON_AddObjectToObject(root, "state");
    cJSON* firmware = reported == nullptr
                          ? nullptr
                          : cJSON_AddObjectToObject(reported, "firmware");
    cJSON* resource = reported == nullptr
                          ? nullptr
                          : cJSON_AddObjectToObject(reported, "resource");
    const char* phase = settings::ReportedResourcePhaseName(state.phase);
    const bool valid = root != nullptr && reported != nullptr && firmware != nullptr &&
                       resource != nullptr && AddNumber(root, "version", version) &&
                       AddString(root, "bootId", boot_id_.data()) &&
                       AddNumber(reported, "schemaVersion", 1) &&
                       AddString(firmware, "version",
                                 esp_app_get_description()->version) &&
                       AddString(resource, "phase", phase) &&
                       AddString(resource, "currentVersion",
                                 state.current_version) &&
                       AddString(resource, "desiredVersion",
                                 state.desired_version) &&
                       AddNumber(resource, "activeSlot", state.active_slot) &&
                       AddNumber(resource, "targetSlot", state.target_slot) &&
                       AddNumber(resource, "expectedBytes", state.expected_bytes) &&
                       AddNumber(resource, "downloadedBytes",
                                 state.downloaded_bytes) &&
                       AddNumber(resource, "securityEpoch",
                                 state.security_epoch) &&
                       (state.error_code[0] == '\0' ||
                        AddString(resource, "errorCode", state.error_code));
    const bool printed = valid &&
                         cJSON_PrintPreallocated(root, output,
                                                static_cast<int>(output_size),
                                                false) != 0;
    cJSON_Delete(root);
    return printed;
}

}  // namespace veetee::telemetry
