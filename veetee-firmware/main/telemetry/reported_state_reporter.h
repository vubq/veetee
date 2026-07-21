#pragma once

#include <array>
#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "settings/reported_state_record.h"
#include "settings/reported_state_store.h"
#include "settings/settings_store.h"
#include "telemetry/reported_state_outbox.h"

namespace veetee::telemetry {

class ReportedStateReporter {
public:
    esp_err_t Initialize(settings::DeviceSettings* settings);
    bool Schedule(const settings::ReportedResourceState& state);

    [[nodiscard]] const char* boot_id() const { return boot_id_.data(); }

private:
    static void TaskEntry(void* context);

    void TaskLoop();
    esp_err_t PersistVersion(const settings::ReportedResourceState& state,
                             bool terminal, std::uint32_t* version);
    esp_err_t ClearDeliveredTerminal(std::uint32_t version);
    esp_err_t Send(const settings::ReportedResourceState& state,
                   std::uint32_t version);
    bool BuildBody(const settings::ReportedResourceState& state,
                   std::uint32_t version, char* output,
                   std::size_t output_size) const;

    settings::DeviceSettings* settings_ = nullptr;
    SemaphoreHandle_t outbox_mutex_ = nullptr;
    TaskHandle_t task_ = nullptr;
    ReportedStateOutbox outbox_{};
    settings::ReportedStateStore state_store_{};
    std::array<char, 37> boot_id_{};
    std::array<char, 18> hardware_id_{};
};

}  // namespace veetee::telemetry
