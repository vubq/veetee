#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "esp_http_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "ota/resource_manifest.h"
#include "settings/settings_store.h"

namespace veetee::ota {

enum class ResourceReconcileEvent : std::uint8_t {
    kManifestVerified,
    kManifestRejected,
    kTransportFailed,
};

struct ResourceReconcileNotification {
    ResourceReconcileEvent event;
    char desired_version[33] = {};
    char bundle_version[33] = {};
    char error_code[33] = {};
};

class ResourceReconciler {
public:
    using EventSink = bool (*)(const ResourceReconcileNotification& notification,
                               void* context);

    esp_err_t Initialize(settings::DeviceSettings* settings, EventSink sink,
                         void* context);
    bool Schedule(const char* desired_version, const char* manifest_url);
    void Cancel();

private:
    struct Target {
        std::uint32_t generation = 0;
        char desired_version[33] = {};
        char manifest_url[257] = {};
    };

    static void TaskEntry(void* context);
    static esp_err_t HttpEventHandler(esp_http_client_event_t* event);

    void TaskLoop();
    void Reconcile(const Target& target);
    esp_err_t FetchManifest(const Target& target, char** document,
                            std::size_t* document_size);
    bool Emit(ResourceReconcileEvent event, const Target& target,
              const char* bundle_version, const char* error_code) const;
    bool EmitWithRetry(ResourceReconcileEvent event, const Target& target,
                       const char* bundle_version, const char* error_code) const;
    [[nodiscard]] bool IsCurrent(std::uint32_t generation) const;

    settings::DeviceSettings* settings_ = nullptr;
    EventSink sink_ = nullptr;
    void* sink_context_ = nullptr;
    QueueHandle_t queue_ = nullptr;
    TaskHandle_t task_ = nullptr;
    std::atomic<std::uint32_t> generation_{0};
    std::atomic<std::uint32_t> request_generation_{0};
    TrustedReleaseKey trusted_key_{};
    std::uint64_t resource_slot_bytes_ = 0;
    char* response_ = nullptr;
    std::size_t response_size_ = 0;
    bool response_overflow_ = false;
};

}  // namespace veetee::ota
