#include "maintenance/maintenance_executor.h"

#include <algorithm>
#include <cstdint>
#include <type_traits>

#include "esp_log.h"

namespace veetee::maintenance {
namespace {

constexpr char kTag[] = "veetee_maint";
constexpr std::uint32_t kMaintenanceStackBytes = 12 * 1024;
constexpr UBaseType_t kMaintenanceTaskPriority = 3;

bool TickReached(TickType_t now, TickType_t due) {
    using SignedTick = std::make_signed_t<TickType_t>;
    return static_cast<SignedTick>(now - due) >= 0;
}

TickType_t TicksUntil(TickType_t now, TickType_t due) {
    return TickReached(now, due) ? 0 : due - now;
}

}  // namespace

esp_err_t MaintenanceExecutor::Initialize() {
    if (mutex_ != nullptr || idle_barrier_ != nullptr || task_ != nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    mutex_ = xSemaphoreCreateMutex();
    if (mutex_ == nullptr) return ESP_ERR_NO_MEM;
    idle_barrier_ = xSemaphoreCreateBinary();
    if (idle_barrier_ == nullptr) {
        vSemaphoreDelete(mutex_);
        mutex_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    preempt_done_ = xSemaphoreCreateBinary();
    if (preempt_done_ == nullptr) {
        vSemaphoreDelete(idle_barrier_);
        vSemaphoreDelete(mutex_);
        idle_barrier_ = nullptr;
        mutex_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    xSemaphoreGive(idle_barrier_);
    if (xTaskCreate(&MaintenanceExecutor::TaskEntry, "veetee_maint",
                    kMaintenanceStackBytes, this, kMaintenanceTaskPriority,
                    &task_) != pdPASS) {
        vSemaphoreDelete(preempt_done_);
        vSemaphoreDelete(idle_barrier_);
        vSemaphoreDelete(mutex_);
        preempt_done_ = nullptr;
        idle_barrier_ = nullptr;
        mutex_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(kTag, "Shared maintenance executor ready stack=%u bytes",
             static_cast<unsigned>(kMaintenanceStackBytes));
    return ESP_OK;
}

bool MaintenanceExecutor::Register(MaintenanceJobKind kind, Handler handler,
                                   void* context) {
    if (mutex_ == nullptr || !IsValidMaintenanceJob(kind) || handler == nullptr) {
        return false;
    }
    const std::size_t index = MaintenanceJobIndex(kind);
    xSemaphoreTake(mutex_, portMAX_DELAY);
    Slot& slot = slots_[index];
    const bool accepted = !slot.registered;
    if (accepted) {
        slot.handler = handler;
        slot.context = context;
        slot.registered = true;
    }
    xSemaphoreGive(mutex_);
    return accepted;
}

bool MaintenanceExecutor::Request(MaintenanceJobKind kind,
                                  std::uint32_t delay_ms) {
    if (mutex_ == nullptr || task_ == nullptr || !IsValidMaintenanceJob(kind)) {
        return false;
    }
    const TickType_t now = xTaskGetTickCount();
    TickType_t delay_ticks = pdMS_TO_TICKS(delay_ms);
    if (delay_ms > 0 && delay_ticks == 0) delay_ticks = 1;
    const TickType_t due = now + delay_ticks;
    const std::size_t index = MaintenanceJobIndex(kind);
    xSemaphoreTake(mutex_, portMAX_DELAY);
    Slot& slot = slots_[index];
    const bool accepted = slot.registered;
    if (accepted) {
        // An immediate/newer request must wake a delayed retry. If a job is
        // already due sooner, retain that earlier deadline.
        if (!slot.pending || TickReached(slot.due_tick, due)) {
            slot.due_tick = due;
        }
        slot.pending = true;
    }
    xSemaphoreGive(mutex_);
    if (accepted) xTaskNotifyGive(task_);
    return accepted;
}

void MaintenanceExecutor::Cancel(MaintenanceJobKind kind) {
    if (mutex_ == nullptr || !IsValidMaintenanceJob(kind)) return;
    xSemaphoreTake(mutex_, portMAX_DELAY);
    slots_[MaintenanceJobIndex(kind)].pending = false;
    xSemaphoreGive(mutex_);
    if (task_ != nullptr) xTaskNotifyGive(task_);
}

void MaintenanceExecutor::SetRealtimeActive(bool active) {
    ApplyGate(true, active);
    if (task_ != nullptr) xTaskNotifyGive(task_);
}

void MaintenanceExecutor::SetFirmwareExclusive(bool exclusive) {
    ApplyGate(false, exclusive);
    if (task_ != nullptr) xTaskNotifyGive(task_);
}

bool MaintenanceExecutor::TrackHttpClient(MaintenanceJobKind kind,
                                          esp_http_client_handle_t client) {
    if (mutex_ == nullptr || client == nullptr || !IsValidMaintenanceJob(kind)) {
        return false;
    }
    xSemaphoreTake(mutex_, portMAX_DELAY);
    const auto running = static_cast<MaintenanceJobKind>(running_kind_.load());
    const bool accepted = running == kind && active_http_client_ == nullptr &&
                          !preempting_http_ &&
                          CanRunMaintenanceJob(
                              kind, realtime_active_.load(),
                              firmware_exclusive_.load());
    if (accepted) active_http_client_ = client;
    xSemaphoreGive(mutex_);
    return accepted;
}

void MaintenanceExecutor::UntrackHttpClient(
    MaintenanceJobKind kind, esp_http_client_handle_t client) {
    if (mutex_ == nullptr || client == nullptr || !IsValidMaintenanceJob(kind)) {
        return;
    }
    while (true) {
        xSemaphoreTake(mutex_, portMAX_DELAY);
        const bool owns_client =
            running_kind_.load() == static_cast<std::uint8_t>(kind) &&
            active_http_client_ == client;
        if (!owns_client) {
            xSemaphoreGive(mutex_);
            return;
        }
        if (!preempting_http_) {
            active_http_client_ = nullptr;
            xSemaphoreGive(mutex_);
            return;
        }
        // ApplyGate owns the close operation. Do not let this handler call
        // cleanup() until the cross-task esp_http_client_close() has returned.
        xSemaphoreGive(mutex_);
        xSemaphoreTake(preempt_done_, portMAX_DELAY);
    }
}

bool MaintenanceExecutor::WaitForQuiescence(std::uint32_t timeout_ms) {
    if (idle_barrier_ == nullptr || !realtime_active_.load()) return false;
    TickType_t timeout_ticks = pdMS_TO_TICKS(timeout_ms);
    if (timeout_ms > 0 && timeout_ticks == 0) timeout_ticks = 1;
    if (xSemaphoreTake(idle_barrier_, timeout_ticks) != pdTRUE) return false;
    const bool idle = !running();
    xSemaphoreGive(idle_barrier_);
    return idle;
}

void MaintenanceExecutor::ApplyGate(bool realtime_gate, bool value) {
    if (mutex_ == nullptr) {
        if (realtime_gate) {
            realtime_active_.store(value);
        } else {
            firmware_exclusive_.store(value);
        }
        return;
    }
    esp_http_client_handle_t client_to_close = nullptr;
    MaintenanceJobKind kind_to_close = MaintenanceJobKind::kCount;
    xSemaphoreTake(mutex_, portMAX_DELAY);
    const bool changed = realtime_gate
                             ? realtime_active_.load() != value
                             : firmware_exclusive_.load() != value;
    if (realtime_gate) {
        realtime_active_.store(value);
    } else {
        firmware_exclusive_.store(value);
    }
    if (changed) ++dispatch_epoch_;

    const bool realtime_active = realtime_active_.load();
    const bool firmware_exclusive = firmware_exclusive_.load();

    const auto running = static_cast<MaintenanceJobKind>(running_kind_.load());
    if (active_http_client_ != nullptr &&
        !CanRunMaintenanceJob(running, realtime_active, firmware_exclusive) &&
        CanPreemptMaintenanceHttp(running, firmware_exclusive) &&
        !preempting_http_) {
        // Take ownership of the close handshake while the pointer is still
        // protected. The actual ESP-IDF call must happen outside this mutex:
        // esp_http_client_close() dispatches callbacks and is not documented
        // as safe while another task is entering UntrackHttpClient().
        xSemaphoreTake(preempt_done_, 0);  // discard a prior completion token
        preempting_http_ = true;
        client_to_close = active_http_client_;
        kind_to_close = running;
    }
    xSemaphoreGive(mutex_);

    if (client_to_close == nullptr) return;

    const esp_err_t error = esp_http_client_close(client_to_close);
    if (error != ESP_OK) {
        ESP_LOGW(kTag, "Unable to preempt maintenance HTTP kind=%u: %s",
                 static_cast<unsigned>(kind_to_close), esp_err_to_name(error));
    }

    xSemaphoreTake(mutex_, portMAX_DELAY);
    if (active_http_client_ == client_to_close) {
        active_http_client_ = nullptr;
    }
    preempting_http_ = false;
    xSemaphoreGive(mutex_);
    xSemaphoreGive(preempt_done_);
}

void MaintenanceExecutor::TaskEntry(void* context) {
    static_cast<MaintenanceExecutor*>(context)->TaskLoop();
}

void MaintenanceExecutor::TaskLoop() {
    while (true) {
        MaintenanceJobKind kind = MaintenanceJobKind::kCount;
        Handler handler = nullptr;
        void* context = nullptr;
        TickType_t wait_ticks = portMAX_DELAY;
        std::uint32_t dispatch_epoch = 0;
        const bool selected = SelectReadyJob(
            xTaskGetTickCount(), &kind, &handler, &context, &wait_ticks,
            &dispatch_epoch);
        if (!selected) {
            ulTaskNotifyTake(pdTRUE, wait_ticks);
            continue;
        }

        if (!DispatchStillAllowed(kind, dispatch_epoch)) {
            FinishDispatch(kind, true);
            continue;
        }
        handler(context);
        FinishDispatch(kind, false);
    }
}

bool MaintenanceExecutor::SelectReadyJob(TickType_t now,
                                         MaintenanceJobKind* kind,
                                         Handler* handler, void** context,
                                         TickType_t* wait_ticks,
                                         std::uint32_t* dispatch_epoch) {
    if (kind == nullptr || handler == nullptr || context == nullptr ||
        wait_ticks == nullptr || dispatch_epoch == nullptr || mutex_ == nullptr ||
        idle_barrier_ == nullptr) {
        return false;
    }
    *wait_ticks = portMAX_DELAY;

    xSemaphoreTake(mutex_, portMAX_DELAY);
    const bool realtime = realtime_active_.load();
    const bool exclusive = firmware_exclusive_.load();
    for (const MaintenanceJobKind candidate : kMaintenancePriority) {
        Slot& slot = slots_[MaintenanceJobIndex(candidate)];
        if (!slot.registered || !slot.pending ||
            !CanRunMaintenanceJob(candidate, realtime, exclusive)) {
            continue;
        }
        if (TickReached(now, slot.due_tick)) {
            if (xSemaphoreTake(idle_barrier_, 0) != pdTRUE) {
                *wait_ticks = 1;
                break;
            }
            slot.pending = false;
            *kind = candidate;
            *handler = slot.handler;
            *context = slot.context;
            *dispatch_epoch = dispatch_epoch_;
            running_kind_.store(static_cast<std::uint8_t>(candidate));
            xSemaphoreGive(mutex_);
            return true;
        }
        *wait_ticks = std::min(*wait_ticks, TicksUntil(now, slot.due_tick));
    }
    xSemaphoreGive(mutex_);
    return false;
}

bool MaintenanceExecutor::DispatchStillAllowed(
    MaintenanceJobKind kind, std::uint32_t dispatch_epoch) {
    if (mutex_ == nullptr) return false;
    xSemaphoreTake(mutex_, portMAX_DELAY);
    const bool allowed = IsMaintenanceDispatchCurrent(
        kind, dispatch_epoch, dispatch_epoch_, realtime_active_.load(),
        firmware_exclusive_.load());
    xSemaphoreGive(mutex_);
    return allowed;
}

void MaintenanceExecutor::FinishDispatch(MaintenanceJobKind kind,
                                         bool requeue) {
    if (mutex_ == nullptr || idle_barrier_ == nullptr) return;
    xSemaphoreTake(mutex_, portMAX_DELAY);
    if (requeue && IsValidMaintenanceJob(kind)) {
        Slot& slot = slots_[MaintenanceJobIndex(kind)];
        slot.pending = true;
        slot.due_tick = xTaskGetTickCount();
    }
    if (active_http_client_ != nullptr) {
        ESP_LOGE(kTag, "Maintenance HTTP owner did not unregister kind=%u",
                 static_cast<unsigned>(kind));
        active_http_client_ = nullptr;
    }
    running_kind_.store(
        static_cast<std::uint8_t>(MaintenanceJobKind::kCount));
    xSemaphoreGive(mutex_);
    xSemaphoreGive(idle_barrier_);
}

}  // namespace veetee::maintenance
