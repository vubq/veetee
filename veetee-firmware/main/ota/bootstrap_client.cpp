#include "ota/bootstrap_client.h"

#include <algorithm>
#include <cctype>
#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>

#include "board/board_config.h"
#include "cJSON.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "network/endpoint_url.h"
#include "ota/firmware_bootstrap_policy.h"
#include "sdkconfig.h"

namespace veetee::ota {
namespace {

constexpr char kTag[] = "veetee_bootstrap";
constexpr std::size_t kMaximumResponseBytes = 8192;
constexpr UBaseType_t kNotificationQueueDepth = 6;
constexpr std::uint32_t kInitialRetryMs = 1000;
constexpr std::uint32_t kMaximumRetryMs = 30000;
constexpr std::uint32_t kActivationPollMs = 2000;
constexpr std::uint32_t kActivationTicketRefreshMs = 30000;
constexpr std::uint32_t kNotificationRetryMs = 100;

bool CopyJsonString(const cJSON* object, const char* key, char* destination,
                    std::size_t capacity, bool required = true) {
    const cJSON* item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (!cJSON_IsString(item) || item->valuestring == nullptr) {
        if (!required) {
            destination[0] = '\0';
            return true;
        }
        return false;
    }
    const std::size_t length = std::strlen(item->valuestring);
    if (length == 0 || length >= capacity) return false;
    std::memcpy(destination, item->valuestring, length + 1);
    return true;
}

bool CopyJsonU32(const cJSON* object, const char* key, std::uint32_t* destination,
                 std::uint32_t fallback = 0) {
    const cJSON* item = cJSON_GetObjectItemCaseSensitive(object, key);
    if (item == nullptr) {
        *destination = fallback;
        return true;
    }
    if (!cJSON_IsNumber(item) || !std::isfinite(item->valuedouble) ||
        item->valuedouble < 0 || item->valuedouble > UINT32_MAX ||
        std::floor(item->valuedouble) != item->valuedouble) {
        return false;
    }
    *destination = static_cast<std::uint32_t>(item->valuedouble);
    return true;
}

bool IsSixDigitCode(const char* value) {
    if (value == nullptr || std::strlen(value) != 6) return false;
    return std::all_of(value, value + 6, [](char digit) {
        return digit >= '0' && digit <= '9';
    });
}

bool IsOpaqueIdentifier(const char* value, std::size_t minimum_length) {
    if (value == nullptr) return false;
    const std::size_t length = std::strlen(value);
    if (length < minimum_length) return false;
    return std::all_of(value, value + length, [](unsigned char character) {
        return std::isalnum(character) != 0 || character == '-' ||
               character == '_' || character == '.';
    });
}

std::string BuildBootstrapReport() {
    cJSON* root = cJSON_CreateObject();
    if (root == nullptr) return {};
    cJSON* application = cJSON_AddObjectToObject(root, "application");
    cJSON* board = cJSON_AddObjectToObject(root, "board");
    if (application == nullptr || board == nullptr ||
        cJSON_AddStringToObject(application, "version",
                               CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION) == nullptr ||
        cJSON_AddStringToObject(board, "type", board::kBoardName) == nullptr) {
        cJSON_Delete(root);
        return {};
    }
    char* encoded = cJSON_PrintUnformatted(root);
    std::string result = encoded == nullptr ? "" : encoded;
    cJSON_free(encoded);
    cJSON_Delete(root);
    return result;
}

std::string ActivationUrl(const char* bootstrap_url) {
    std::string url = bootstrap_url == nullptr ? "" : bootstrap_url;
    if (url.empty()) return url;
    if (url.back() != '/') url.push_back('/');
    url += "activate";
    return url;
}

}  // namespace

esp_err_t BootstrapClient::Initialize(settings::SettingsStore* store,
                                      settings::DeviceSettings* settings,
                                      maintenance::MaintenanceExecutor* executor,
                                      EventSink sink, void* context) {
    if (store == nullptr || settings == nullptr || executor == nullptr ||
        sink == nullptr || store_ != nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    store_ = store;
    settings_ = settings;
    executor_ = executor;
    sink_ = sink;
    sink_context_ = context;

    std::uint8_t mac[6] = {};
    const esp_err_t error = esp_read_mac(mac, ESP_MAC_WIFI_STA);
    if (error != ESP_OK) return error;
    std::snprintf(hardware_id_, sizeof(hardware_id_),
                  "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2],
                  mac[3], mac[4], mac[5]);

    response_ = static_cast<char*>(heap_caps_calloc(
        kMaximumResponseBytes + 1, 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (response_ == nullptr) return ESP_ERR_NO_MEM;
    constexpr UBaseType_t queue_caps = MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
    attempt_queue_ = xQueueCreateWithCaps(1, sizeof(Attempt), queue_caps);
    notification_queue_ = xQueueCreateWithCaps(
        kNotificationQueueDepth, sizeof(PendingNotification), queue_caps);
    if (attempt_queue_ == nullptr || notification_queue_ == nullptr ||
        !executor_->Register(maintenance::MaintenanceJobKind::kBootstrap,
                             &BootstrapClient::MaintenanceEntry, this)) {
        if (attempt_queue_ != nullptr) {
            vQueueDeleteWithCaps(attempt_queue_);
        }
        if (notification_queue_ != nullptr) {
            vQueueDeleteWithCaps(notification_queue_);
        }
        attempt_queue_ = nullptr;
        notification_queue_ = nullptr;
        heap_caps_free(response_);
        response_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

void BootstrapClient::Start() {
    if (executor_ == nullptr || attempt_queue_ == nullptr ||
        notification_queue_ == nullptr) {
        return;
    }
    active_.store(true);
    const std::uint32_t generation = generation_.fetch_add(1) + 1;
    const settings::DeviceSettings snapshot = store_->Snapshot();
    const bool pending_activation = snapshot.HasPendingActivation();
    const Attempt attempt{
        .generation = generation,
        .retry_ms = kInitialRetryMs,
        .activation_elapsed_ms = 0,
        .refresh_activation_ticket = pending_activation,
        .announce_pending_activation = pending_activation,
    };
    xQueueReset(notification_queue_);
    if (xQueueOverwrite(attempt_queue_, &attempt) != pdTRUE ||
        !executor_->Request(maintenance::MaintenanceJobKind::kBootstrap)) {
        ESP_LOGE(kTag, "Unable to schedule bootstrap maintenance attempt");
    }
}

void BootstrapClient::Cancel() {
    active_.store(false);
    generation_.fetch_add(1);
    if (attempt_queue_ != nullptr) xQueueReset(attempt_queue_);
    if (notification_queue_ != nullptr) xQueueReset(notification_queue_);
    if (executor_ != nullptr) {
        executor_->Cancel(maintenance::MaintenanceJobKind::kBootstrap);
    }
}

void BootstrapClient::MaintenanceEntry(void* context) {
    static_cast<BootstrapClient*>(context)->ProcessPending();
}

void BootstrapClient::ProcessPending() {
    if (DeliverPendingNotification()) return;
    Attempt attempt{};
    if (attempt_queue_ != nullptr &&
        xQueueReceive(attempt_queue_, &attempt, 0) == pdTRUE &&
        IsCurrent(attempt.generation)) {
        ProcessAttempt(attempt);
    }
}

void BootstrapClient::ProcessAttempt(Attempt attempt) {
    if (!IsCurrent(attempt.generation)) return;
    settings::DeviceSettings snapshot = store_->Snapshot();
    if (attempt.announce_pending_activation &&
        snapshot.HasPendingActivation()) {
        if (QueueNotification(BootstrapEvent::kActivationCodeAvailable,
                              snapshot.activation_code, nullptr,
                              attempt.generation)) {
            attempt.announce_pending_activation = false;
            Reschedule(attempt, 0);
        } else {
            Reschedule(attempt, kNotificationRetryMs);
        }
        return;
    }

    esp_err_t error = ESP_FAIL;
    if (snapshot.HasDeviceIdentity()) {
        BootstrapPayload payload{};
        error = RequestBootstrap(snapshot, true, &payload, attempt.generation);
        if (error == ESP_OK && IsCurrent(attempt.generation)) {
            if (FirmwareBootstrapRequiresUpdate(
                    payload.has_firmware, payload.firmware_version,
                    CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION,
                    firmware_updates_deferred_.load())) {
                if (!QueueNotification(BootstrapEvent::kFirmwareDesired,
                                       nullptr, &payload,
                                       attempt.generation)) {
                    Reschedule(attempt, kNotificationRetryMs);
                }
                return;
            }
            const std::uint32_t config_version =
                payload.has_config ? payload.config_version
                                   : snapshot.config_version;
            error = store_->SaveBoundBootstrap(payload.websocket_url,
                                                config_version, settings_);
            if (error == ESP_OK) {
                if (!QueueBootstrapComplete(payload, attempt.generation)) {
                    Reschedule(attempt, kNotificationRetryMs);
                }
                return;
            }
        }
        if (error == ESP_ERR_INVALID_STATE && IsCurrent(attempt.generation)) {
            ESP_LOGE(
                kTag,
                "Manager rejected the stored device identity; physical recovery required");
            if (!QueueNotification(BootstrapEvent::kDeviceIdentityRejected,
                                   nullptr, nullptr, attempt.generation)) {
                Reschedule(attempt, kNotificationRetryMs);
            }
            return;
        }
        Retry(attempt, error);
        return;
    }

    if (snapshot.HasPendingActivation()) {
        if (attempt.refresh_activation_ticket) {
            BootstrapPayload ticket{};
            error = RequestBootstrap(snapshot, false, &ticket,
                                     attempt.generation);
            if (error == ESP_OK && !ticket.has_activation) {
                error = ESP_ERR_INVALID_RESPONSE;
            }
            if (error == ESP_OK) {
                error = store_->SavePendingActivation(
                    ticket.activation_code, ticket.activation_challenge,
                    settings_);
                if (error == ESP_OK) {
                    snapshot = store_->Snapshot();
                    attempt.refresh_activation_ticket = false;
                    attempt.activation_elapsed_ms = 0;
                    attempt.retry_ms = kInitialRetryMs;
                    if (QueueNotification(
                            BootstrapEvent::kActivationCodeAvailable,
                            snapshot.activation_code, nullptr,
                            attempt.generation)) {
                        Reschedule(attempt, 0);
                    } else {
                        attempt.announce_pending_activation = true;
                        Reschedule(attempt, kNotificationRetryMs);
                    }
                    return;
                }
            } else if (error == ESP_ERR_INVALID_STATE) {
                // HTTP 409 means Manager already consumed the code; keep polling
                // activate without blocking the shared maintenance worker.
                attempt.refresh_activation_ticket = false;
                attempt.activation_elapsed_ms = 0;
                attempt.retry_ms = kInitialRetryMs;
                Reschedule(attempt, 0);
                return;
            }
            Retry(attempt, error);
            return;
        }

        ActivationPayload payload{};
        error = RequestActivation(snapshot, &payload, attempt.generation);
        if (error == ESP_OK && IsCurrent(attempt.generation)) {
            error = store_->SaveDeviceActivation(
                payload.device_id, payload.device_token,
                payload.websocket_url, payload.config_version, settings_);
            if (error == ESP_OK) {
                // The activation response has no immutable snapshot ETag or
                // canonical URL. Re-enter authenticated bootstrap before
                // declaring activation complete.
                attempt.retry_ms = kInitialRetryMs;
                attempt.activation_elapsed_ms = 0;
                attempt.refresh_activation_ticket = false;
                Reschedule(attempt, 0);
                return;
            }
        } else if (error == ESP_ERR_TIMEOUT) {
            attempt.retry_ms = kInitialRetryMs;
            attempt.activation_elapsed_ms += kActivationPollMs;
            if (attempt.activation_elapsed_ms >=
                kActivationTicketRefreshMs) {
                attempt.refresh_activation_ticket = true;
            }
            Reschedule(attempt, kActivationPollMs);
            return;
        }
        Retry(attempt, error);
        return;
    }

    BootstrapPayload payload{};
    error = RequestBootstrap(snapshot, false, &payload, attempt.generation);
    if (error == ESP_OK && IsCurrent(attempt.generation) &&
        payload.has_activation) {
        error = store_->SavePendingActivation(
            payload.activation_code, payload.activation_challenge, settings_);
        if (error == ESP_OK) {
            snapshot = store_->Snapshot();
            attempt.refresh_activation_ticket = false;
            attempt.activation_elapsed_ms = 0;
            attempt.retry_ms = kInitialRetryMs;
            if (QueueNotification(BootstrapEvent::kActivationCodeAvailable,
                                  snapshot.activation_code, nullptr,
                                  attempt.generation)) {
                Reschedule(attempt, 0);
            } else {
                attempt.announce_pending_activation = true;
                Reschedule(attempt, kNotificationRetryMs);
            }
            return;
        }
    }
    if (error == ESP_OK) error = ESP_ERR_INVALID_RESPONSE;
    Retry(attempt, error);
}

esp_err_t BootstrapClient::RequestBootstrap(
    const settings::DeviceSettings& snapshot, bool authenticated,
    BootstrapPayload* payload, std::uint32_t generation) {
    const std::string report = BuildBootstrapReport();
    if (report.empty()) return ESP_ERR_NO_MEM;
    int status_code = 0;
    const esp_err_t error = PerformPost(
        snapshot, snapshot.bootstrap_url, report.c_str(),
        authenticated ? snapshot.device_token : nullptr, &status_code);
    if (!IsCurrent(generation)) return error;
    if (!authenticated && status_code == 409) return ESP_ERR_INVALID_STATE;
    if (authenticated &&
        (status_code == 401 || status_code == 403 || status_code == 404)) {
        return ESP_ERR_INVALID_STATE;
    }
    if (error != ESP_OK) return error;
    if (status_code != 200) {
        ESP_LOGW(kTag, "Bootstrap HTTP status=%d", status_code);
        return ESP_ERR_INVALID_RESPONSE;
    }
    return ParseBootstrap(payload);
}

esp_err_t BootstrapClient::RequestActivation(
    const settings::DeviceSettings& snapshot, ActivationPayload* payload,
    std::uint32_t generation) {
    if (!IsOpaqueIdentifier(snapshot.activation_challenge, 16)) {
        return ESP_ERR_INVALID_ARG;
    }
    char request_body[192] = {};
    const int length = std::snprintf(request_body, sizeof(request_body),
                                     "{\"challenge\":\"%s\"}",
                                     snapshot.activation_challenge);
    if (length <= 0 || length >= static_cast<int>(sizeof(request_body))) {
        return ESP_ERR_INVALID_SIZE;
    }
    const std::string url = ActivationUrl(snapshot.bootstrap_url);
    int status_code = 0;
    const esp_err_t error = PerformPost(snapshot, url.c_str(), request_body,
                                        nullptr, &status_code);
    if (error != ESP_OK || !IsCurrent(generation)) return error;
    if (status_code == 202) return ESP_ERR_TIMEOUT;
    if (status_code != 200) {
        ESP_LOGW(kTag, "Activation HTTP status=%d", status_code);
        return ESP_ERR_INVALID_RESPONSE;
    }
    return ParseActivation(payload);
}

esp_err_t BootstrapClient::PerformPost(
    const settings::DeviceSettings& snapshot, const char* url, const char* body,
    const char* bearer_token, int* status_code) {
    if (url == nullptr || body == nullptr || status_code == nullptr ||
        response_ == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    *status_code = 0;
    if (!network::IsHttpEndpointUrl(url)) return ESP_ERR_INVALID_ARG;
    response_size_ = 0;
    response_overflow_ = false;
    response_[0] = '\0';

    esp_http_client_config_t config = {};
    config.url = url;
    config.event_handler = &BootstrapClient::HttpEventHandler;
    config.user_data = this;
    config.timeout_ms = 6000;
    config.buffer_size = 1024;
    config.buffer_size_tx = 1024;
    config.keep_alive_enable = true;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    config.disable_auto_redirect = true;
    config.max_redirection_count = 0;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == nullptr) return ESP_ERR_NO_MEM;

    esp_err_t error = esp_http_client_set_method(client, HTTP_METHOD_POST);
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Content-Type",
                                           "application/json");
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Device-Id", hardware_id_);
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Client-Id", snapshot.client_id);
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Device-Model",
                                           board::kBoardName);
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Firmware-Version",
                                           CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION);
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Accept-Language",
                                           snapshot.locale);
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Activation-Version", "1");
    }
    char authorization[160] = {};
    if (error == ESP_OK && bearer_token != nullptr && bearer_token[0] != '\0') {
        const int length = std::snprintf(authorization, sizeof(authorization),
                                         "Bearer %s", bearer_token);
        if (length <= 0 || length >= static_cast<int>(sizeof(authorization))) {
            error = ESP_ERR_INVALID_SIZE;
        } else {
            error = esp_http_client_set_header(client, "Authorization", authorization);
        }
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_post_field(client, body, std::strlen(body));
    }
    const bool tracked =
        error == ESP_OK && executor_ != nullptr &&
        executor_->TrackHttpClient(maintenance::MaintenanceJobKind::kBootstrap,
                                   client);
    if (error == ESP_OK && !tracked) error = ESP_ERR_INVALID_STATE;
    if (error == ESP_OK) {
        error = esp_http_client_perform(client);
    }
    if (tracked) {
        executor_->UntrackHttpClient(
            maintenance::MaintenanceJobKind::kBootstrap, client);
        // A 401 challenge can make esp_http_client return ESP_ERR_NOT_SUPPORTED.
        // Preserve the received status so identity rejection remains recoverable.
        *status_code = esp_http_client_get_status_code(client);
    }
    esp_http_client_cleanup(client);

    std::fill(std::begin(authorization), std::end(authorization), '\0');
    if (error != ESP_OK) return error;
    if (response_overflow_) return ESP_ERR_INVALID_SIZE;
    response_[response_size_] = '\0';
    return ESP_OK;
}

esp_err_t BootstrapClient::HttpEventHandler(esp_http_client_event_t* event) {
    if (event == nullptr || event->user_data == nullptr) return ESP_ERR_INVALID_ARG;
    auto* client = static_cast<BootstrapClient*>(event->user_data);
    if (client->executor_ != nullptr && client->executor_->realtime_active()) {
        return ESP_ERR_INVALID_STATE;
    }
    if (event->event_id != HTTP_EVENT_ON_DATA || event->data_len <= 0) return ESP_OK;
    const std::size_t length = static_cast<std::size_t>(event->data_len);
    if (client->response_ == nullptr ||
        client->response_size_ + length > kMaximumResponseBytes) {
        client->response_overflow_ = true;
        return ESP_FAIL;
    }
    std::memcpy(client->response_ + client->response_size_, event->data,
                length);
    client->response_size_ += length;
    return ESP_OK;
}

esp_err_t BootstrapClient::ParseBootstrap(BootstrapPayload* payload) const {
    if (payload == nullptr) return ESP_ERR_INVALID_ARG;
    cJSON* root = cJSON_ParseWithLength(response_, response_size_);
    if (root == nullptr) return ESP_ERR_INVALID_RESPONSE;

    bool valid = true;
    const cJSON* websocket = cJSON_GetObjectItemCaseSensitive(root, "websocket");
    valid = cJSON_IsObject(websocket) &&
            CopyJsonString(websocket, "url", payload->websocket_url,
                           sizeof(payload->websocket_url)) &&
            network::IsWebSocketEndpointUrl(payload->websocket_url);

    const cJSON* activation = cJSON_GetObjectItemCaseSensitive(root, "activation");
    if (valid && cJSON_IsObject(activation)) {
        payload->has_activation = true;
        valid = CopyJsonString(activation, "code", payload->activation_code,
                               sizeof(payload->activation_code)) &&
                IsSixDigitCode(payload->activation_code) &&
                CopyJsonString(activation, "challenge",
                               payload->activation_challenge,
                               sizeof(payload->activation_challenge)) &&
                IsOpaqueIdentifier(payload->activation_challenge, 16);
    }
    const cJSON* config = cJSON_GetObjectItemCaseSensitive(root, "config");
    if (valid && cJSON_IsObject(config)) {
        payload->has_config = true;
        valid = CopyJsonU32(config, "version", &payload->config_version) &&
                CopyJsonString(config, "etag", payload->config_etag,
                               sizeof(payload->config_etag)) &&
                CopyJsonString(config, "url", payload->config_url,
                               sizeof(payload->config_url)) &&
                network::IsHttpEndpointUrl(payload->config_url);
    }
    const cJSON* resources = cJSON_GetObjectItemCaseSensitive(root, "resources");
    if (valid && cJSON_IsObject(resources)) {
        payload->has_resources = true;
        valid = CopyJsonString(resources, "version", payload->resource_version,
                               sizeof(payload->resource_version)) &&
                CopyJsonString(resources, "manifest_url",
                               payload->resource_manifest_url,
                               sizeof(payload->resource_manifest_url)) &&
                network::IsHttpEndpointUrl(payload->resource_manifest_url);
    }
    const cJSON* ui = cJSON_GetObjectItemCaseSensitive(root, "ui");
    if (valid && cJSON_IsObject(ui)) {
        payload->has_ui = true;
        valid = CopyJsonString(ui, "version", payload->ui_version,
                               sizeof(payload->ui_version)) &&
                CopyJsonString(ui, "manifest_url", payload->ui_manifest_url,
                               sizeof(payload->ui_manifest_url)) &&
                network::IsHttpEndpointUrl(payload->ui_manifest_url);
    }
    const cJSON* firmware = cJSON_GetObjectItemCaseSensitive(root, "firmware");
    if (valid && cJSON_IsObject(firmware)) {
        const cJSON* manifest = cJSON_GetObjectItemCaseSensitive(firmware, "manifest_url");
        if (cJSON_IsString(manifest)) {
            payload->has_firmware = true;
            valid = CopyJsonString(firmware, "version", payload->firmware_version,
                                   sizeof(payload->firmware_version)) &&
                    CopyJsonString(firmware, "manifest_url",
                                   payload->firmware_manifest_url,
                                   sizeof(payload->firmware_manifest_url)) &&
                    network::IsHttpEndpointUrl(payload->firmware_manifest_url);
        }
    }
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

esp_err_t BootstrapClient::ParseActivation(ActivationPayload* payload) const {
    if (payload == nullptr) return ESP_ERR_INVALID_ARG;
    cJSON* root = cJSON_ParseWithLength(response_, response_size_);
    if (root == nullptr) return ESP_ERR_INVALID_RESPONSE;
    const bool valid =
        CopyJsonString(root, "device_id", payload->device_id,
                       sizeof(payload->device_id)) &&
        IsOpaqueIdentifier(payload->device_id, 4) &&
        CopyJsonString(root, "token", payload->device_token,
                       sizeof(payload->device_token)) &&
        IsOpaqueIdentifier(payload->device_token, 32) &&
        CopyJsonString(root, "websocket_url", payload->websocket_url,
                       sizeof(payload->websocket_url)) &&
        network::IsWebSocketEndpointUrl(payload->websocket_url) &&
        CopyJsonU32(root, "config_version", &payload->config_version);
    cJSON_Delete(root);
    return valid ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

bool BootstrapClient::QueueNotification(BootstrapEvent event,
                                        const char* activation_code,
                                        const BootstrapPayload* payload,
                                        std::uint32_t generation) {
    if (!IsCurrent(generation) || notification_queue_ == nullptr ||
        executor_ == nullptr) {
        return false;
    }
    PendingNotification pending{
        .generation = generation,
        .notification = BootstrapNotification{.event = event},
    };
    BootstrapNotification& notification = pending.notification;
    if (activation_code != nullptr) {
        std::snprintf(notification.activation_code,
                      sizeof(notification.activation_code), "%s", activation_code);
    }
    if (event == BootstrapEvent::kResourceDesired && payload != nullptr &&
        payload->has_resources) {
        std::snprintf(notification.resource_version,
                      sizeof(notification.resource_version), "%s",
                      payload->resource_version);
        std::snprintf(notification.resource_manifest_url,
                      sizeof(notification.resource_manifest_url), "%s",
                      payload->resource_manifest_url);
    }
    if (event == BootstrapEvent::kConfigDesired && payload != nullptr &&
        payload->has_config) {
        notification.config_version = payload->config_version;
        std::snprintf(notification.config_etag,
                      sizeof(notification.config_etag), "%s",
                      payload->config_etag);
        std::snprintf(notification.config_url,
                      sizeof(notification.config_url), "%s",
                      payload->config_url);
    }
    if (event == BootstrapEvent::kUiPackDesired && payload != nullptr &&
        payload->has_ui) {
        std::snprintf(notification.ui_version, sizeof(notification.ui_version),
                      "%s", payload->ui_version);
        std::snprintf(notification.ui_manifest_url,
                      sizeof(notification.ui_manifest_url), "%s",
                      payload->ui_manifest_url);
    }
    if (event == BootstrapEvent::kFirmwareDesired && payload != nullptr &&
        payload->has_firmware) {
        std::snprintf(notification.firmware_version,
                      sizeof(notification.firmware_version), "%s",
                      payload->firmware_version);
        std::snprintf(notification.firmware_manifest_url,
                      sizeof(notification.firmware_manifest_url), "%s",
                      payload->firmware_manifest_url);
    }
    if (!IsCurrent(generation) ||
        xQueueSend(notification_queue_, &pending, 0) != pdTRUE) {
        return false;
    }
    return executor_->Request(maintenance::MaintenanceJobKind::kBootstrap);
}

bool BootstrapClient::QueueBootstrapComplete(const BootstrapPayload& payload,
                                             std::uint32_t generation) {
    if (!IsCurrent(generation) || notification_queue_ == nullptr) return false;
    const UBaseType_t required =
        1U + static_cast<UBaseType_t>(payload.has_config) +
        static_cast<UBaseType_t>(payload.has_resources) +
        static_cast<UBaseType_t>(payload.has_ui);
    if (uxQueueSpacesAvailable(notification_queue_) < required) return false;

    if (payload.has_config &&
        !QueueNotification(BootstrapEvent::kConfigDesired, nullptr, &payload,
                           generation)) {
        return false;
    }
    if (payload.has_resources &&
        !QueueNotification(BootstrapEvent::kResourceDesired, nullptr, &payload,
                           generation)) {
        return false;
    }
    if (payload.has_ui &&
        !QueueNotification(BootstrapEvent::kUiPackDesired, nullptr, &payload,
                           generation)) {
        return false;
    }
    return QueueNotification(BootstrapEvent::kActivationComplete, nullptr,
                             nullptr, generation);
}

bool BootstrapClient::DeliverPendingNotification() {
    if (notification_queue_ == nullptr) return false;
    PendingNotification pending{};
    while (xQueueReceive(notification_queue_, &pending, 0) == pdTRUE) {
        if (!IsCurrent(pending.generation)) continue;
        if (sink_ != nullptr &&
            sink_(pending.notification, sink_context_)) {
            RequestIfPending();
            return true;
        }
        if (IsCurrent(pending.generation) &&
            xQueueSendToFront(notification_queue_, &pending, 0) == pdTRUE &&
            executor_ != nullptr) {
            executor_->Request(maintenance::MaintenanceJobKind::kBootstrap,
                               kNotificationRetryMs);
        }
        return true;
    }
    return false;
}

bool BootstrapClient::Reschedule(const Attempt& attempt,
                                 std::uint32_t delay_ms) {
    if (!IsCurrent(attempt.generation) || attempt_queue_ == nullptr ||
        executor_ == nullptr) {
        return false;
    }
    // A concurrent Start() owns a non-empty mailbox. Never let this older
    // delayed attempt overwrite the new generation.
    if (xQueueSend(attempt_queue_, &attempt, 0) != pdTRUE) return false;
    if (!IsCurrent(attempt.generation)) return false;
    return executor_->Request(maintenance::MaintenanceJobKind::kBootstrap,
                              delay_ms);
}

void BootstrapClient::Retry(Attempt attempt, esp_err_t error) {
    if (!IsCurrent(attempt.generation)) return;
    const std::uint32_t retry_ms =
        attempt.retry_ms == 0 ? kInitialRetryMs : attempt.retry_ms;
    ESP_LOGW(kTag,
             "Bootstrap/activation attempt failed: %s; retry in %" PRIu32
             " ms",
             esp_err_to_name(error), retry_ms);
    attempt.retry_ms =
        std::min(kMaximumRetryMs, retry_ms * 2U);
    Reschedule(attempt, retry_ms);
}

void BootstrapClient::RequestIfPending() {
    if (executor_ == nullptr) return;
    const bool pending_notification =
        notification_queue_ != nullptr &&
        uxQueueMessagesWaiting(notification_queue_) != 0;
    const bool pending_attempt =
        attempt_queue_ != nullptr && uxQueueMessagesWaiting(attempt_queue_) != 0;
    if (pending_notification || pending_attempt) {
        executor_->Request(maintenance::MaintenanceJobKind::kBootstrap);
    }
}

bool BootstrapClient::IsCurrent(std::uint32_t generation) const {
    return active_.load() && generation_.load() == generation;
}

}  // namespace veetee::ota
