#include "ota/resource_reconciler.h"

#include <algorithm>
#include <cstdio>
#include <cstring>
#include <iterator>

#include "board/board_config.h"
#include "esp_crt_bundle.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_partition.h"
#include "esp_psram.h"
#include "network/endpoint_url.h"
#include "sdkconfig.h"

namespace veetee::ota {
namespace {

constexpr char kTag[] = "veetee_resources";
constexpr std::size_t kMaximumManifestBytes = 32768;
constexpr std::uint32_t kNotificationRetryMs = 100;
constexpr std::uint64_t kBoardFlashBytes = 16ULL * 1024ULL * 1024ULL;
constexpr std::uint32_t kResourceAbi = 1;
constexpr SupportedResourceRuntime kSupportedRuntimes[] = {
    {.kind = "model_pack", .runtime = "esp-sr", .runtime_abi = 1},
};

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

std::uint64_t ResourceSlotBytes() {
    const esp_partition_t* first = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, static_cast<esp_partition_subtype_t>(0x40),
        "resource_0");
    const esp_partition_t* second = esp_partition_find_first(
        ESP_PARTITION_TYPE_DATA, static_cast<esp_partition_subtype_t>(0x41),
        "resource_1");
    if (first == nullptr || second == nullptr) return 0;
    return std::min<std::uint64_t>(first->size, second->size);
}

}  // namespace

esp_err_t ResourceReconciler::Initialize(settings::DeviceSettings* settings,
                                         EventSink sink, void* context) {
    if (settings == nullptr || sink == nullptr ||
        CONFIG_VEETEE_RESOURCE_SIGNING_KEY_ID[0] == '\0' ||
        !DecodePublicKey(CONFIG_VEETEE_RESOURCE_SIGNING_PUBLIC_KEY_HEX,
                         &trusted_key_.public_key)) {
        return ESP_ERR_INVALID_ARG;
    }
    settings_ = settings;
    sink_ = sink;
    sink_context_ = context;
    trusted_key_.key_id = CONFIG_VEETEE_RESOURCE_SIGNING_KEY_ID;
    trusted_key_.minimum_security_epoch =
        CONFIG_VEETEE_MIN_RESOURCE_SECURITY_EPOCH;
    resource_slot_bytes_ = ResourceSlotBytes();
    if (resource_slot_bytes_ == 0) return ESP_ERR_NOT_FOUND;

    queue_ = xQueueCreate(1, sizeof(Target));
    if (queue_ == nullptr) return ESP_ERR_NO_MEM;
    if (xTaskCreate(&ResourceReconciler::TaskEntry, "veetee_resources", 12288,
                    this, 4, &task_) != pdPASS) {
        vQueueDelete(queue_);
        queue_ = nullptr;
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

    const DeviceResourceCapability capability = {
        .board = board::kBoardName,
        .chip = "esp32s3",
        .firmware_version = CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION,
        .resource_abi = kResourceAbi,
        .flash_bytes = kBoardFlashBytes,
        .psram_bytes = esp_psram_is_initialized() ? esp_psram_get_size() : 0,
        .resource_slot_bytes = resource_slot_bytes_,
        .supported_runtimes = kSupportedRuntimes,
        .supported_runtime_count = std::size(kSupportedRuntimes),
    };
    VerifiedResourceManifest manifest{};
    ResourceManifestError error = VerifyResourceManifest(
        std::string_view(document, document_size), capability, &trusted_key_, 1,
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
    EmitWithRetry(ResourceReconcileEvent::kManifestVerified, target,
                  manifest.version, "ok");
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

bool ResourceReconciler::Emit(ResourceReconcileEvent event,
                              const Target& target, const char* bundle_version,
                              const char* error_code) const {
    if (!IsCurrent(target.generation) || sink_ == nullptr) return false;
    ResourceReconcileNotification notification{.event = event};
    std::snprintf(notification.desired_version,
                  sizeof(notification.desired_version), "%s",
                  target.desired_version);
    std::snprintf(notification.bundle_version,
                  sizeof(notification.bundle_version), "%s",
                  bundle_version == nullptr ? "" : bundle_version);
    std::snprintf(notification.error_code, sizeof(notification.error_code), "%s",
                  error_code == nullptr ? "unknown" : error_code);
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
