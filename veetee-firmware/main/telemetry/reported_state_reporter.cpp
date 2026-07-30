#include "telemetry/reported_state_reporter.h"

#include <algorithm>
#include <array>
#include <cinttypes>
#include <cstdio>
#include <cstring>

#include "cJSON.h"
#include "board/board_config.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_partition.h"
#include "esp_random.h"
#include "network/endpoint_url.h"
#include "sdkconfig.h"

namespace veetee::telemetry {
namespace {

constexpr char kTag[] = "veetee_reporter";
constexpr std::uint32_t kInitialRetryMs = 500;
constexpr std::uint32_t kMaximumRetryMs = 30000;
constexpr std::size_t kMaximumBodyBytes = 2048;

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

bool AddBoolean(cJSON* object, const char* name, bool value) {
    return cJSON_AddBoolToObject(object, name, value) != nullptr;
}

bool ParseDecimalU32(const char* value, std::uint32_t* output) {
    if (value == nullptr || output == nullptr || value[0] == '\0') return false;
    std::uint32_t parsed = 0;
    for (const char* cursor = value; *cursor != '\0'; ++cursor) {
        if (*cursor < '0' || *cursor > '9') return false;
        const std::uint32_t digit = static_cast<std::uint32_t>(*cursor - '0');
        if (parsed > (UINT32_MAX - digit) / 10U) return false;
        parsed = parsed * 10U + digit;
    }
    *output = parsed;
    return true;
}

std::uint32_t PartitionBytes(const char* first_label, const char* second_label) {
    const esp_partition_t* first = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, first_label);
    const esp_partition_t* second = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, ESP_PARTITION_SUBTYPE_ANY, second_label);
    if (first == nullptr || second == nullptr) return 0;
    return std::min(first->size, second->size);
}

}  // namespace

esp_err_t ReportedStateReporter::Initialize(
    settings::SettingsStore* settings_store,
    maintenance::MaintenanceExecutor* executor) {
    if (settings_store == nullptr || executor == nullptr ||
        settings_store_ != nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    settings_store_ = settings_store;
    executor_ = executor;
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
    state_mutex_ = xSemaphoreCreateMutex();
    if (outbox_mutex_ == nullptr || state_mutex_ == nullptr) {
        if (outbox_mutex_ != nullptr) vSemaphoreDelete(outbox_mutex_);
        if (state_mutex_ != nullptr) vSemaphoreDelete(state_mutex_);
        outbox_mutex_ = nullptr;
        state_mutex_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    if (!executor_->Register(maintenance::MaintenanceJobKind::kReporter,
                             &ReportedStateReporter::MaintenanceEntry, this)) {
        vSemaphoreDelete(outbox_mutex_);
        vSemaphoreDelete(state_mutex_);
        outbox_mutex_ = nullptr;
        state_mutex_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    if (state_store_.record().has_pending != 0 &&
        !executor_->Request(maintenance::MaintenanceJobKind::kReporter)) {
        return ESP_FAIL;
    }
    ESP_LOGI(kTag, "Reporter ready boot_id=%s", boot_id_.data());
    return ESP_OK;
}

bool ReportedStateReporter::Schedule(
    const settings::ReportedResourceState& state) {
    if (executor_ == nullptr ||
        !settings::IsValidReportedResourceState(state)) {
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
    return executor_->Request(maintenance::MaintenanceJobKind::kReporter);
}

bool ReportedStateReporter::PersistForReplay(
    const settings::ReportedResourceState& state, bool supersede_pending) {
    if (executor_ == nullptr || state_mutex_ == nullptr ||
        !settings::IsTerminalReportedResourcePhase(state.phase) ||
        !settings::IsValidReportedResourceState(state)) {
        return false;
    }
    xSemaphoreTake(state_mutex_, portMAX_DELAY);
    settings::ReportedStateRecord record = state_store_.record();
    std::uint32_t version = 0;
    const bool staged = record.has_pending == 0
                            ? settings::StagePendingReportedState(
                                  &record, state, &version)
                            : supersede_pending &&
                                  settings::ReplacePendingReportedState(
                                      &record, state, &version);
    const esp_err_t error = staged ? state_store_.Save(record)
                                   : ESP_ERR_INVALID_STATE;
    xSemaphoreGive(state_mutex_);
    if (error != ESP_OK) return false;
    return executor_->Request(maintenance::MaintenanceJobKind::kReporter);
}

void ReportedStateReporter::MaintenanceEntry(void* context) {
    static_cast<ReportedStateReporter*>(context)->ProcessPending();
}

void ReportedStateReporter::ProcessPending() {
    while (executor_ != nullptr) {
        xSemaphoreTake(state_mutex_, portMAX_DELAY);
        const settings::ReportedStateRecord persisted = state_store_.record();
        xSemaphoreGive(state_mutex_);
        if (have_current_ && !current_terminal_ &&
            persisted.has_pending != 0) {
            // A durable boot/terminal boundary always wins over coalescable
            // progress.  The intermediate may already own a lower unused
            // sequence; dropping it is safe, while sending or assigning it
            // after the pending boundary could regress Manager state.
            ESP_LOGI(kTag,
                     "Dropping intermediate phase=%s version=%" PRIu32
                     " for pending terminal version=%" PRIu32,
                     settings::ReportedResourcePhaseName(current_.phase),
                     current_version_, persisted.pending_version);
            have_current_ = false;
            current_version_ = 0;
            retry_ms_ = kInitialRetryMs;
            continue;
        }
        if (!have_current_ && persisted.has_pending != 0) {
            current_ = persisted.pending;
            current_version_ = persisted.pending_version;
            have_current_ = true;
            current_terminal_ = true;
        }
        if (!have_current_ && have_deferred_terminal_) {
            current_ = deferred_terminal_;
            current_version_ = 0;
            have_current_ = true;
            have_deferred_terminal_ = false;
            current_terminal_ = true;
        }
        if (!have_current_) {
            xSemaphoreTake(outbox_mutex_, portMAX_DELAY);
            have_current_ = outbox_.Pop(&current_, &current_terminal_);
            xSemaphoreGive(outbox_mutex_);
            if (have_current_) current_version_ = 0;
        }
        if (!have_current_) return;

        esp_err_t error = ESP_OK;
        if (current_version_ == 0) {
            error = PersistVersion(current_, current_terminal_,
                                   &current_version_);
        }
        if (error == ESP_OK) error = Send(current_, current_version_);
        if (error == ESP_OK && current_terminal_) {
            error = ClearDeliveredTerminal(current_version_);
        }
        if (error == ESP_ERR_INVALID_STATE && current_terminal_) {
            xSemaphoreTake(state_mutex_, portMAX_DELAY);
            const settings::ReportedStateRecord latest = state_store_.record();
            xSemaphoreGive(state_mutex_);
            if (current_version_ == 0 && latest.has_pending != 0) {
                // PersistForReplay won the race after this FIFO terminal was
                // popped.  Keep FIFO ownership in RAM, replay the durable
                // boundary first, then assign this terminal a later version.
                deferred_terminal_ = current_;
                have_deferred_terminal_ = true;
                have_current_ = false;
                retry_ms_ = kInitialRetryMs;
                continue;
            }
            const bool superseded =
                latest.has_pending == 0 ||
                latest.pending_version != current_version_;
            if (superseded) {
                have_current_ = false;
                current_version_ = 0;
                retry_ms_ = kInitialRetryMs;
                continue;
            }
        }
        if (error == ESP_ERR_INVALID_STATE && !current_terminal_) {
            xSemaphoreTake(state_mutex_, portMAX_DELAY);
            const bool pending_terminal =
                state_store_.record().has_pending != 0;
            xSemaphoreGive(state_mutex_);
            if (pending_terminal) {
                have_current_ = false;
                current_version_ = 0;
                retry_ms_ = kInitialRetryMs;
                continue;
            }
        }
        if (error == ESP_OK) {
            ESP_LOGI(kTag, "Reported resource phase=%s version=%" PRIu32,
                     settings::ReportedResourcePhaseName(current_.phase),
                     current_version_);
            have_current_ = false;
            current_version_ = 0;
            retry_ms_ = kInitialRetryMs;
            executor_->Request(maintenance::MaintenanceJobKind::kReporter);
            return;
        }

        ESP_LOGW(kTag,
                 "Reported-state delivery failed phase=%s version=%" PRIu32
                 " error=%s retry_ms=%" PRIu32,
                 settings::ReportedResourcePhaseName(current_.phase),
                 current_version_, esp_err_to_name(error), retry_ms_);
        const std::uint32_t delay_ms = retry_ms_;
        retry_ms_ = std::min(kMaximumRetryMs, retry_ms_ * 2U);
        xSemaphoreTake(outbox_mutex_, portMAX_DELAY);
        const bool has_replacement = outbox_.HasTerminal() || outbox_.HasLatest();
        xSemaphoreGive(outbox_mutex_);
        if (!current_terminal_ && has_replacement) {
            have_current_ = false;
            current_version_ = 0;
            retry_ms_ = kInitialRetryMs;
            executor_->Request(maintenance::MaintenanceJobKind::kReporter);
        } else {
            executor_->Request(maintenance::MaintenanceJobKind::kReporter,
                               delay_ms);
        }
        return;
    }
}

esp_err_t ReportedStateReporter::PersistVersion(
    const settings::ReportedResourceState& state, bool terminal,
    std::uint32_t* version) {
    xSemaphoreTake(state_mutex_, portMAX_DELAY);
    settings::ReportedStateRecord record = state_store_.record();
    const bool valid = terminal
                           ? settings::StagePendingReportedState(&record, state,
                                                                 version)
                           : settings::IssueReportedStateVersion(&record, version);
    const esp_err_t error = valid ? state_store_.Save(record)
                                  : ESP_ERR_INVALID_STATE;
    xSemaphoreGive(state_mutex_);
    return error;
}

esp_err_t ReportedStateReporter::ClearDeliveredTerminal(
    std::uint32_t version) {
    xSemaphoreTake(state_mutex_, portMAX_DELAY);
    settings::ReportedStateRecord record = state_store_.record();
    const esp_err_t error =
        settings::ClearPendingReportedState(&record, version)
            ? state_store_.Save(record)
            : ESP_ERR_INVALID_STATE;
    xSemaphoreGive(state_mutex_);
    return error;
}

esp_err_t ReportedStateReporter::Send(
    const settings::ReportedResourceState& state, std::uint32_t version) {
    if (settings_store_ == nullptr) return ESP_ERR_INVALID_STATE;
    const settings::DeviceSettings snapshot = settings_store_->Snapshot();
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
    if (!BuildBody(state, version, snapshot, body.data(), body.size())) {
        return ESP_ERR_INVALID_SIZE;
    }

    esp_http_client_config_t config = {};
    config.url = url;
    config.timeout_ms = 6000;
    config.buffer_size = 1024;
    config.buffer_size_tx = 2048;
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
    const bool tracked =
        error == ESP_OK && executor_ != nullptr &&
        executor_->TrackHttpClient(maintenance::MaintenanceJobKind::kReporter,
                                   client);
    if (error == ESP_OK && !tracked) error = ESP_ERR_INVALID_STATE;
    if (error == ESP_OK) error = esp_http_client_perform(client);
    if (tracked) {
        executor_->UntrackHttpClient(
            maintenance::MaintenanceJobKind::kReporter, client);
    }
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
    const settings::DeviceSettings& settings_snapshot, char* output,
    std::size_t output_size) const {
    if (!settings::IsValidReportedResourceState(state) || version == 0 ||
        output == nullptr || output_size == 0) {
        return false;
    }
    cJSON* root = cJSON_CreateObject();
    cJSON* reported = root == nullptr ? nullptr : cJSON_AddObjectToObject(root, "state");
    cJSON* firmware = reported == nullptr
                          ? nullptr
                          : cJSON_AddObjectToObject(reported, "firmware");
    cJSON* capabilities = reported == nullptr
                              ? nullptr
                              : cJSON_AddObjectToObject(reported, "capabilities");
    cJSON* display = capabilities == nullptr
                         ? nullptr
                         : cJSON_AddObjectToObject(capabilities, "display");
    cJSON* compositions = display == nullptr
                              ? nullptr
                              : cJSON_AddArrayToObject(display, "compositions");
    cJSON* wake = capabilities == nullptr
                      ? nullptr
                      : cJSON_AddObjectToObject(capabilities, "wake");
    const bool is_config =
        state.artifact_kind == settings::ReportedArtifactKind::kDeviceConfig;
    const char* artifact_name =
        state.artifact_kind == settings::ReportedArtifactKind::kUiPack
            ? "ui"
            : state.artifact_kind == settings::ReportedArtifactKind::kFirmware
                  ? "firmware_ota"
                  : is_config ? "config" : "resource";
    cJSON* resource = reported == nullptr
                          ? nullptr
                          : cJSON_AddObjectToObject(reported, artifact_name);
    const char* phase = settings::ReportedResourcePhaseName(state.phase);
    std::uint32_t applied_config_version = 0;
    std::uint32_t desired_config_version = 0;
    const bool config_versions =
        !is_config ||
        (ParseDecimalU32(state.current_version, &applied_config_version) &&
         ParseDecimalU32(state.desired_version, &desired_config_version));
    char display_target[48] = {};
    const int display_target_length = std::snprintf(
        display_target, sizeof(display_target), "st7789-%dx%d-rgb565",
        CONFIG_VEETEE_LCD_WIDTH, CONFIG_VEETEE_LCD_HEIGHT);
    const bool composition_values =
        compositions != nullptr &&
        cJSON_AddItemToArray(compositions, cJSON_CreateString("signal")) &&
        cJSON_AddItemToArray(compositions, cJSON_CreateString("monolith")) &&
        cJSON_AddItemToArray(compositions, cJSON_CreateString("quiet"));
    const bool valid = root != nullptr && reported != nullptr && firmware != nullptr &&
                       capabilities != nullptr && display != nullptr && wake != nullptr &&
                       resource != nullptr && config_versions &&
                       display_target_length > 0 &&
                       display_target_length < static_cast<int>(sizeof(display_target)) &&
                       AddNumber(root, "version", version) &&
                       AddString(root, "bootId", boot_id_.data()) &&
                       AddNumber(reported, "schemaVersion", 1) &&
                       AddString(reported, "locale", settings_snapshot.locale) &&
                       AddString(reported, "timeZone", settings_snapshot.time_zone) &&
                       AddString(firmware, "version",
                                 CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION) &&
                       AddString(capabilities, "board", board::kBoardName) &&
                       AddString(display, "target", display_target) &&
                       AddString(display, "controller", "st7789") &&
                       AddNumber(display, "width", CONFIG_VEETEE_LCD_WIDTH) &&
                       AddNumber(display, "height", CONFIG_VEETEE_LCD_HEIGHT) &&
                       AddString(display, "colorFormat", "rgb565") &&
                       AddNumber(display, "resourceAbi", 2) &&
                       AddNumber(display, "uiAbi", 1) &&
                       AddNumber(display, "slotBytes",
                                 PartitionBytes("ui_0", "ui_1")) &&
                       AddBoolean(display, "hotReload", true) &&
                       composition_values &&
                       AddString(wake, "runtime", "esp-sr") &&
                       AddNumber(wake, "runtimeAbi", 1) &&
                       AddNumber(wake, "resourceAbi", 1) &&
                       AddNumber(wake, "slotBytes",
                                 PartitionBytes("resource_0", "resource_1")) &&
                       AddNumber(wake, "sampleRateHz", board::kMicSampleRate) &&
                       AddNumber(wake, "channels", 1) &&
                       AddBoolean(wake, "hotReload", true) &&
                       AddString(resource, "phase", phase) &&
                       (is_config
                            ? AddNumber(resource, "appliedVersion",
                                        applied_config_version) &&
                                  AddNumber(resource, "desiredVersion",
                                            desired_config_version)
                            : AddString(resource, "currentVersion",
                                        state.current_version) &&
                                  AddString(resource, "desiredVersion",
                                            state.desired_version) &&
                                  AddNumber(resource, "activeSlot",
                                            state.active_slot) &&
                                  AddNumber(resource, "targetSlot",
                                            state.target_slot) &&
                                  AddNumber(resource, "expectedBytes",
                                            state.expected_bytes) &&
                                  AddNumber(resource, "downloadedBytes",
                                            state.downloaded_bytes) &&
                                  AddNumber(resource, "securityEpoch",
                                            state.security_epoch)) &&
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
