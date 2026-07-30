#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>

#include "config/device_config.h"
#include "esp_err.h"
#include "esp_http_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "maintenance/maintenance_executor.h"
#include "settings/device_config_store.h"
#include "settings/settings_store.h"

namespace veetee::config {

enum class DeviceConfigReconcileEvent : std::uint8_t {
    kStaged,
    kAlreadyApplied,
    kFailed,
};

struct DeviceConfigReconcileNotification {
    DeviceConfigReconcileEvent event = DeviceConfigReconcileEvent::kFailed;
    std::uint32_t desired_version = 0;
    std::uint32_t applied_version = 0;
    DeviceConfig config{};
    char etag[65] = {};
    char error_code[33] = {};
};

class DeviceConfigReconciler {
public:
    using EventSink = bool (*)(
        const DeviceConfigReconcileNotification& notification, void* context);

    esp_err_t Initialize(settings::SettingsStore* settings_store,
                         settings::DeviceConfigStore* store,
                         maintenance::MaintenanceExecutor* executor,
                         EventSink sink, void* context);
    bool Schedule(std::uint32_t desired_version, const char* etag,
                  const char* url);
    void Cancel();

private:
    struct Target {
        std::uint32_t generation = 0;
        std::uint32_t version = 0;
        std::uint32_t retry_ms = 0;
        char etag[65] = {};
        char url[257] = {};
    };

    struct PendingNotification {
        std::uint32_t generation = 0;
        DeviceConfigReconcileNotification notification{};
    };

    static void MaintenanceEntry(void* context);
    static esp_err_t HttpEventHandler(esp_http_client_event_t* event);

    void ProcessPending();
    void Run(const Target& target);
    esp_err_t Fetch(const Target& target, int* status_code);
    bool Emit(const DeviceConfigReconcileNotification& notification,
              std::uint32_t generation);
    bool DeliverPendingNotification();
    bool Reschedule(const Target& target, std::uint32_t delay_ms);
    void RequestIfPending();
    [[nodiscard]] bool IsCurrent(std::uint32_t generation) const;

    settings::SettingsStore* settings_store_ = nullptr;
    settings::DeviceConfigStore* store_ = nullptr;
    maintenance::MaintenanceExecutor* executor_ = nullptr;
    EventSink sink_ = nullptr;
    void* sink_context_ = nullptr;
    QueueHandle_t queue_ = nullptr;
    QueueHandle_t notification_queue_ = nullptr;
    std::atomic<std::uint32_t> generation_{0};
    ota::TrustedReleaseKey trusted_key_{};
    char hardware_id_[18] = {};
    char* response_ = nullptr;
    std::size_t response_size_ = 0;
    bool response_overflow_ = false;
    char response_etag_[65] = {};
};

}  // namespace veetee::config
