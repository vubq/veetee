#include "config/device_config_reconciler.h"

#include <algorithm>
#include <array>
#include <cctype>
#include <cinttypes>
#include <cstdio>
#include <cstring>
#include <strings.h>

#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "network/endpoint_url.h"
#include "sdkconfig.h"

namespace veetee::config {
namespace {

constexpr char kTag[] = "veetee_config";
constexpr std::size_t kMaximumResponseBytes = 8192;
constexpr std::uint32_t kInitialRetryMs = 1000;
constexpr std::uint32_t kMaximumRetryMs = 30000;
constexpr std::uint32_t kNotificationRetryMs = 100;

bool DecodePublicKey(const char* hex,
                     std::array<std::uint8_t, 32>* output) {
    if (hex == nullptr || output == nullptr || std::strlen(hex) != 64) {
        return false;
    }
    auto nibble = [](char character) -> int {
        if (character >= '0' && character <= '9') return character - '0';
        if (character >= 'a' && character <= 'f') return character - 'a' + 10;
        if (character >= 'A' && character <= 'F') return character - 'A' + 10;
        return -1;
    };
    for (std::size_t index = 0; index < output->size(); ++index) {
        const int high = nibble(hex[index * 2]);
        const int low = nibble(hex[index * 2 + 1]);
        if (high < 0 || low < 0) return false;
        (*output)[index] = static_cast<std::uint8_t>((high << 4) | low);
    }
    return true;
}

bool HeaderEquals(const char* value, const char* expected) {
    return value != nullptr && expected != nullptr &&
           strcasecmp(value, expected) == 0;
}

bool CopyEtag(const char* value, char* output, std::size_t capacity) {
    if (value == nullptr || output == nullptr || capacity == 0) return false;
    const std::size_t length = std::strlen(value);
    const bool quoted = length >= 2 && value[0] == '"' &&
                        value[length - 1] == '"';
    const char* start = quoted ? value + 1 : value;
    const std::size_t token_length = quoted ? length - 2 : length;
    if (token_length != 48 || token_length >= capacity ||
        std::strncmp(start, "cfg1-", 5) != 0) {
        return false;
    }
    for (std::size_t index = 0; index < token_length; ++index) {
        const unsigned char character =
            static_cast<unsigned char>(start[index]);
        if (!((character >= 'a' && character <= 'z') ||
              (character >= 'A' && character <= 'Z') ||
              (character >= '0' && character <= '9') || character == '-' ||
              character == '_')) {
            return false;
        }
    }
    std::memcpy(output, start, token_length);
    output[token_length] = '\0';
    return true;
}

void CopyError(char* destination, std::size_t capacity, const char* source) {
    if (destination == nullptr || capacity == 0) return;
    std::snprintf(destination, capacity, "%s",
                  source == nullptr ? "unknown" : source);
}

}  // namespace

esp_err_t DeviceConfigReconciler::Initialize(
    settings::SettingsStore* settings_store,
    settings::DeviceConfigStore* store,
    maintenance::MaintenanceExecutor* executor,
    EventSink sink, void* context) {
    if (settings_store == nullptr || store == nullptr || executor == nullptr ||
        sink == nullptr ||
        settings_store_ != nullptr ||
        CONFIG_VEETEE_RESOURCE_SIGNING_KEY_ID[0] == '\0' ||
        !DecodePublicKey(CONFIG_VEETEE_RESOURCE_SIGNING_PUBLIC_KEY_HEX,
                         &trusted_key_.public_key)) {
        return ESP_ERR_INVALID_ARG;
    }
    settings_store_ = settings_store;
    store_ = store;
    executor_ = executor;
    sink_ = sink;
    sink_context_ = context;
    trusted_key_.key_id = CONFIG_VEETEE_RESOURCE_SIGNING_KEY_ID;
    const settings::DeviceConfigRecord applied = store_->Snapshot();
    trusted_key_.minimum_security_epoch = std::max<std::uint32_t>(
        CONFIG_VEETEE_MIN_CONFIG_SECURITY_EPOCH,
        applied.security_epoch_floor);

    std::uint8_t mac[6] = {};
    esp_err_t error = esp_read_mac(mac, ESP_MAC_WIFI_STA);
    if (error != ESP_OK) return error;
    std::snprintf(hardware_id_, sizeof(hardware_id_),
                  "%02x:%02x:%02x:%02x:%02x:%02x", mac[0], mac[1], mac[2],
                  mac[3], mac[4], mac[5]);

    response_ = static_cast<char*>(heap_caps_calloc(
        kMaximumResponseBytes + 1, 1, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (response_ == nullptr) return ESP_ERR_NO_MEM;
    constexpr UBaseType_t queue_caps = MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT;
    queue_ = xQueueCreateWithCaps(1, sizeof(Target), queue_caps);
    notification_queue_ =
        xQueueCreateWithCaps(1, sizeof(PendingNotification), queue_caps);
    if (queue_ == nullptr || notification_queue_ == nullptr ||
        !executor_->Register(
            maintenance::MaintenanceJobKind::kDeviceConfig,
            &DeviceConfigReconciler::MaintenanceEntry, this)) {
        if (queue_ != nullptr) vQueueDeleteWithCaps(queue_);
        if (notification_queue_ != nullptr) {
            vQueueDeleteWithCaps(notification_queue_);
        }
        queue_ = nullptr;
        notification_queue_ = nullptr;
        heap_caps_free(response_);
        response_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(kTag, "Config verifier ready key_id=%s min_epoch=%" PRIu32,
             trusted_key_.key_id, trusted_key_.minimum_security_epoch);
    return ESP_OK;
}

bool DeviceConfigReconciler::Schedule(std::uint32_t desired_version,
                                      const char* etag, const char* url) {
    const settings::DeviceSettings settings_snapshot =
        settings_store_ == nullptr ? settings::DeviceSettings{}
                                   : settings_store_->Snapshot();
    Target target{};
    char path[96] = {};
    char canonical_url[257] = {};
    const int path_length =
        settings_store_ == nullptr
            ? -1
            : std::snprintf(path, sizeof(path),
                            "/veetee/config/v1/devices/%s",
                            settings_snapshot.device_id);
    if (queue_ == nullptr || notification_queue_ == nullptr ||
        executor_ == nullptr || desired_version == 0 ||
        desired_version > kMaximumDeviceConfigVersion || etag == nullptr ||
        url == nullptr || std::strlen(etag) >= sizeof(target.etag) ||
        std::strlen(url) >= sizeof(target.url) ||
        !CopyEtag(etag, target.etag, sizeof(target.etag)) ||
        path_length <= 0 || path_length >= static_cast<int>(sizeof(path)) ||
        !network::BuildHttpOriginEndpoint(settings_snapshot.bootstrap_url, path,
                                          canonical_url,
                                          sizeof(canonical_url)) ||
        std::strcmp(url, canonical_url) != 0) {
        return false;
    }
    target.generation = generation_.fetch_add(1) + 1;
    target.version = desired_version;
    target.retry_ms = kInitialRetryMs;
    std::snprintf(target.url, sizeof(target.url), "%s", url);
    xQueueReset(notification_queue_);
    if (xQueueOverwrite(queue_, &target) != pdTRUE) return false;
    return executor_->Request(maintenance::MaintenanceJobKind::kDeviceConfig);
}

void DeviceConfigReconciler::Cancel() {
    generation_.fetch_add(1);
    if (queue_ != nullptr) xQueueReset(queue_);
    if (notification_queue_ != nullptr) xQueueReset(notification_queue_);
    if (executor_ != nullptr) {
        executor_->Cancel(maintenance::MaintenanceJobKind::kDeviceConfig);
    }
}

void DeviceConfigReconciler::MaintenanceEntry(void* context) {
    static_cast<DeviceConfigReconciler*>(context)->ProcessPending();
}

void DeviceConfigReconciler::ProcessPending() {
    if (DeliverPendingNotification()) return;
    Target target{};
    if (queue_ != nullptr && xQueueReceive(queue_, &target, 0) == pdTRUE &&
        IsCurrent(target.generation)) {
        Run(target);
    }
}

void DeviceConfigReconciler::Run(const Target& target) {
    DeviceConfigReconcileNotification notification{};
    notification.desired_version = target.version;
    settings::DeviceConfigRecord applied = store_->Snapshot();
    notification.applied_version = applied.applied_version;
    std::snprintf(notification.etag, sizeof(notification.etag), "%s",
                  target.etag);

    if (target.version == applied.applied_version &&
        std::strcmp(target.etag, applied.etag) == 0) {
        notification.event = DeviceConfigReconcileEvent::kAlreadyApplied;
        store_->LoadApplied(&notification.config);
        Emit(notification, target.generation);
        return;
    }
    if (target.version < applied.applied_version) {
        notification.event = DeviceConfigReconcileEvent::kFailed;
        CopyError(notification.error_code, sizeof(notification.error_code),
                  "version_downgrade");
        Emit(notification, target.generation);
        return;
    }

    int status = 0;
    esp_err_t error = Fetch(target, &status);
    if (!IsCurrent(target.generation)) return;
    if (error == ESP_OK && status == 304) {
        // A 304 is only useful when the exact signed snapshot is already
        // persisted. Otherwise accepting it would create desired/applied drift.
        applied = store_->Snapshot();
        notification.applied_version = applied.applied_version;
        error = target.version == applied.applied_version &&
                        std::strcmp(target.etag, applied.etag) == 0
                    ? ESP_OK
                    : ESP_ERR_INVALID_STATE;
        if (error == ESP_OK) {
            notification.event = DeviceConfigReconcileEvent::kAlreadyApplied;
            store_->LoadApplied(&notification.config);
            Emit(notification, target.generation);
            return;
        }
        notification.event = DeviceConfigReconcileEvent::kFailed;
        CopyError(notification.error_code, sizeof(notification.error_code),
                  "not_modified_without_applied");
        Emit(notification, target.generation);
        return;
    }
    if (error == ESP_OK && status == 200) {
        applied = store_->Snapshot();
        notification.applied_version = applied.applied_version;
        trusted_key_.minimum_security_epoch = std::max<std::uint32_t>(
            CONFIG_VEETEE_MIN_CONFIG_SECURITY_EPOCH,
            applied.security_epoch_floor);
        const settings::DeviceSettings settings_snapshot =
            settings_store_->Snapshot();
        const DeviceConfigError verify = VerifyDeviceConfig(
            std::string_view(response_, response_size_),
            settings_snapshot.device_id, target.version, &trusted_key_, 1,
            &notification.config);
        if (verify == DeviceConfigError::kOk &&
            std::strcmp(response_etag_, target.etag) == 0) {
            notification.event = DeviceConfigReconcileEvent::kStaged;
            Emit(notification, target.generation);
            return;
        }
        CopyError(notification.error_code, sizeof(notification.error_code),
                  verify == DeviceConfigError::kOk
                      ? "etag_mismatch"
                      : DeviceConfigErrorName(verify));
        notification.event = DeviceConfigReconcileEvent::kFailed;
        Emit(notification, target.generation);
        return;
    }
    if (error == ESP_OK) error = ESP_ERR_INVALID_RESPONSE;
    const std::uint32_t retry_ms =
        target.retry_ms == 0 ? kInitialRetryMs : target.retry_ms;
    ESP_LOGW(kTag,
             "Config pull failed desired=%" PRIu32
             " status=%d error=%s retry_ms=%" PRIu32,
             target.version, status, esp_err_to_name(error), retry_ms);
    Reschedule(target, retry_ms);
}

esp_err_t DeviceConfigReconciler::Fetch(const Target& target,
                                        int* status_code) {
    if (status_code == nullptr || settings_store_ == nullptr ||
        response_ == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    *status_code = 0;
    response_size_ = 0;
    response_overflow_ = false;
    response_[0] = '\0';
    response_etag_[0] = '\0';
    const settings::DeviceSettings snapshot = settings_store_->Snapshot();
    if (!snapshot.HasDeviceIdentity()) return ESP_ERR_INVALID_STATE;

    esp_http_client_config_t config = {};
    config.url = target.url;
    config.event_handler = &DeviceConfigReconciler::HttpEventHandler;
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

    char authorization[160] = {};
    char if_none_match[68] = {};
    esp_err_t error = esp_http_client_set_method(client, HTTP_METHOD_GET);
    if (error == ESP_OK) {
        const int length = std::snprintf(authorization, sizeof(authorization),
                                         "Bearer %s", snapshot.device_token);
        error = length > 7 && length < static_cast<int>(sizeof(authorization))
                    ? ESP_OK
                    : ESP_ERR_INVALID_SIZE;
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
                                           "application/json");
    }
    if (error == ESP_OK) {
        error = esp_http_client_set_header(client, "Accept-Encoding",
                                           "identity");
    }
    const settings::DeviceConfigRecord applied = store_->Snapshot();
    const char* applied_etag = applied.etag;
    if (error == ESP_OK && applied_etag[0] != '\0') {
        const int length = std::snprintf(if_none_match, sizeof(if_none_match),
                                         "\"%s\"", applied_etag);
        error = length > 2 && length < static_cast<int>(sizeof(if_none_match))
                    ? esp_http_client_set_header(client, "If-None-Match",
                                                 if_none_match)
                    : ESP_ERR_INVALID_SIZE;
    }
    const bool tracked =
        error == ESP_OK && executor_ != nullptr &&
        executor_->TrackHttpClient(
            maintenance::MaintenanceJobKind::kDeviceConfig, client);
    if (error == ESP_OK && !tracked) error = ESP_ERR_INVALID_STATE;
    if (error == ESP_OK) {
        error = esp_http_client_perform(client);
    }
    if (tracked) {
        executor_->UntrackHttpClient(
            maintenance::MaintenanceJobKind::kDeviceConfig, client);
        *status_code = esp_http_client_get_status_code(client);
    }
    esp_http_client_cleanup(client);
    std::fill(std::begin(authorization), std::end(authorization), '\0');
    if (error != ESP_OK) return error;
    if (response_overflow_) return ESP_ERR_INVALID_SIZE;
    response_[response_size_] = '\0';
    if (*status_code == 200 && response_etag_[0] == '\0') {
        return ESP_ERR_INVALID_RESPONSE;
    }
    return ESP_OK;
}

esp_err_t DeviceConfigReconciler::HttpEventHandler(
    esp_http_client_event_t* event) {
    if (event == nullptr || event->user_data == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    auto* reconciler =
        static_cast<DeviceConfigReconciler*>(event->user_data);
    if (reconciler->executor_ != nullptr &&
        reconciler->executor_->realtime_active()) {
        return ESP_ERR_INVALID_STATE;
    }
    if (event->event_id == HTTP_EVENT_ON_HEADER &&
        HeaderEquals(event->header_key, "ETag")) {
        if (!CopyEtag(event->header_value, reconciler->response_etag_,
                      sizeof(reconciler->response_etag_))) {
            reconciler->response_overflow_ = true;
            return ESP_FAIL;
        }
        return ESP_OK;
    }
    if (event->event_id != HTTP_EVENT_ON_DATA || event->data_len <= 0) {
        return ESP_OK;
    }
    const std::size_t length = static_cast<std::size_t>(event->data_len);
    if (reconciler->response_size_ + length > kMaximumResponseBytes) {
        reconciler->response_overflow_ = true;
        return ESP_FAIL;
    }
    std::memcpy(reconciler->response_ + reconciler->response_size_, event->data,
                length);
    reconciler->response_size_ += length;
    return ESP_OK;
}

bool DeviceConfigReconciler::Emit(
    const DeviceConfigReconcileNotification& notification,
    std::uint32_t generation) {
    if (!IsCurrent(generation) || sink_ == nullptr) return false;
    if (sink_(notification, sink_context_)) return true;

    const PendingNotification pending{
        .generation = generation,
        .notification = notification,
    };
    if (!IsCurrent(generation) || notification_queue_ == nullptr ||
        xQueueOverwrite(notification_queue_, &pending) != pdTRUE) {
        return false;
    }
    return executor_ != nullptr &&
           executor_->Request(maintenance::MaintenanceJobKind::kDeviceConfig,
                              kNotificationRetryMs);
}

bool DeviceConfigReconciler::DeliverPendingNotification() {
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
            executor_->Request(
                maintenance::MaintenanceJobKind::kDeviceConfig,
                kNotificationRetryMs);
        }
        return true;
    }
    return false;
}

bool DeviceConfigReconciler::Reschedule(const Target& target,
                                        std::uint32_t delay_ms) {
    if (!IsCurrent(target.generation) || queue_ == nullptr ||
        executor_ == nullptr) {
        return false;
    }
    Target retry = target;
    const std::uint32_t current_retry =
        retry.retry_ms == 0 ? kInitialRetryMs : retry.retry_ms;
    retry.retry_ms =
        std::min(kMaximumRetryMs, current_retry * 2U);

    // A newer Schedule() owns a non-empty mailbox. Never overwrite it with an
    // older delayed retry after this HTTP attempt returns.
    if (xQueueSend(queue_, &retry, 0) != pdTRUE) return false;
    if (!IsCurrent(retry.generation)) return false;
    return executor_->Request(
        maintenance::MaintenanceJobKind::kDeviceConfig, delay_ms);
}

void DeviceConfigReconciler::RequestIfPending() {
    if (executor_ == nullptr) return;
    const bool pending_notification =
        notification_queue_ != nullptr &&
        uxQueueMessagesWaiting(notification_queue_) != 0;
    const bool pending_target =
        queue_ != nullptr && uxQueueMessagesWaiting(queue_) != 0;
    if (pending_notification || pending_target) {
        executor_->Request(maintenance::MaintenanceJobKind::kDeviceConfig);
    }
}

bool DeviceConfigReconciler::IsCurrent(std::uint32_t generation) const {
    return generation_.load() == generation;
}

}  // namespace veetee::config
