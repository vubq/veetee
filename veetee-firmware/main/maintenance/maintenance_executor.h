#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "esp_http_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "maintenance/maintenance_policy.h"

namespace veetee::maintenance {

class MaintenanceExecutor {
public:
    using Handler = void (*)(void* context);

    esp_err_t Initialize();
    bool Register(MaintenanceJobKind kind, Handler handler, void* context);
    bool Request(MaintenanceJobKind kind, std::uint32_t delay_ms = 0);
    void Cancel(MaintenanceJobKind kind);

    void SetRealtimeActive(bool active);
    void SetFirmwareExclusive(bool exclusive);
    bool TrackHttpClient(MaintenanceJobKind kind,
                         esp_http_client_handle_t client);
    void UntrackHttpClient(MaintenanceJobKind kind,
                           esp_http_client_handle_t client);
    [[nodiscard]] bool WaitForQuiescence(std::uint32_t timeout_ms);

    [[nodiscard]] bool realtime_active() const {
        return realtime_active_.load();
    }
    [[nodiscard]] bool firmware_exclusive() const {
        return firmware_exclusive_.load();
    }
    [[nodiscard]] bool running() const {
        return running_kind_.load() !=
               static_cast<std::uint8_t>(MaintenanceJobKind::kCount);
    }
    [[nodiscard]] std::uint32_t stack_free_bytes() const {
        return task_ == nullptr
                   ? 0
                   : static_cast<std::uint32_t>(
                         uxTaskGetStackHighWaterMark(task_));
    }

private:
    struct Slot {
        Handler handler = nullptr;
        void* context = nullptr;
        bool registered = false;
        bool pending = false;
        TickType_t due_tick = 0;
    };

    static void TaskEntry(void* context);
    void TaskLoop();
    bool SelectReadyJob(TickType_t now, MaintenanceJobKind* kind,
                        Handler* handler, void** context,
                        TickType_t* wait_ticks,
                        std::uint32_t* dispatch_epoch);
    bool DispatchStillAllowed(MaintenanceJobKind kind,
                              std::uint32_t dispatch_epoch);
    void FinishDispatch(MaintenanceJobKind kind, bool requeue);
    void ApplyGate(bool realtime_gate, bool value);

    std::array<Slot, kMaintenanceJobCount> slots_{};
    SemaphoreHandle_t mutex_ = nullptr;
    SemaphoreHandle_t idle_barrier_ = nullptr;
    TaskHandle_t task_ = nullptr;
    std::atomic<bool> realtime_active_{false};
    std::atomic<bool> firmware_exclusive_{false};
    std::uint32_t dispatch_epoch_ = 1;
    esp_http_client_handle_t active_http_client_ = nullptr;
    // Cross-task HTTP preemption needs an ownership handshake. The
    // application task may close the socket while the maintenance task is in
    // esp_http_client_perform(), but the handler must not destroy the client
    // until that close has returned.
    SemaphoreHandle_t preempt_done_ = nullptr;
    bool preempting_http_ = false;
    std::atomic<std::uint8_t> running_kind_{
        static_cast<std::uint8_t>(MaintenanceJobKind::kCount)};
};

}  // namespace veetee::maintenance
