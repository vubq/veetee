#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "maintenance/maintenance_executor.h"
#include "settings/reported_state_record.h"
#include "settings/reported_state_store.h"
#include "settings/settings_store.h"
#include "telemetry/reported_state_outbox.h"

namespace veetee::telemetry {

class ReportedStateReporter {
public:
    esp_err_t Initialize(settings::SettingsStore* settings_store,
                         maintenance::MaintenanceExecutor* executor);
    bool Schedule(const settings::ReportedResourceState& state);
    bool PersistForReplay(const settings::ReportedResourceState& state,
                          bool supersede_pending = false);

    [[nodiscard]] const char* boot_id() const { return boot_id_.data(); }

private:
    static void MaintenanceEntry(void* context);

    void ProcessPending();
    esp_err_t PersistVersion(const settings::ReportedResourceState& state,
                             bool terminal, std::uint32_t* version);
    esp_err_t ClearDeliveredTerminal(std::uint32_t version);
    esp_err_t Send(const settings::ReportedResourceState& state,
                   std::uint32_t version);
    bool BuildBody(const settings::ReportedResourceState& state,
                   std::uint32_t version,
                   const settings::DeviceSettings& settings_snapshot,
                   char* output,
                   std::size_t output_size) const;

    settings::SettingsStore* settings_store_ = nullptr;
    maintenance::MaintenanceExecutor* executor_ = nullptr;
    SemaphoreHandle_t outbox_mutex_ = nullptr;
    SemaphoreHandle_t state_mutex_ = nullptr;
    ReportedStateOutbox outbox_{};
    settings::ReportedStateStore state_store_{};
    settings::ReportedResourceState current_{};
    settings::ReportedResourceState deferred_terminal_{};
    std::uint32_t current_version_ = 0;
    std::uint32_t retry_ms_ = 500;
    bool have_current_ = false;
    bool have_deferred_terminal_ = false;
    bool current_terminal_ = false;
    std::array<char, 37> boot_id_{};
    std::array<char, 18> hardware_id_{};
};

}  // namespace veetee::telemetry
