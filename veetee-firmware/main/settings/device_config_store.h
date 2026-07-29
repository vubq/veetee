#pragma once

#include <cstdint>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"
#include "settings/device_config_record.h"

namespace veetee::settings {

class DeviceConfigStore {
public:
    ~DeviceConfigStore();

    esp_err_t Initialize(std::uint32_t minimum_security_epoch);
    esp_err_t SaveApplied(const config::DeviceConfig& config,
                          const char* etag);
    esp_err_t PersistWakeAudioPrivacyRevocation();
    esp_err_t Reset(std::uint32_t minimum_security_epoch);

    [[nodiscard]] DeviceConfigRecord Snapshot() const;
    [[nodiscard]] bool WakeAudioPrivacyRevoked() const;
    bool LoadApplied(config::DeviceConfig* config) const;

private:
    nvs_handle_t handle_ = 0;
    SemaphoreHandle_t mutex_ = nullptr;
    DeviceConfigRecord record_{};
};

}  // namespace veetee::settings
