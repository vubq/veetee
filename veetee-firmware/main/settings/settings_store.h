#pragma once

#include <cstddef>

#include "esp_err.h"
#include "nvs.h"

namespace veetee::settings {

struct DeviceSettings {
    char ssid[33] = {};
    char password[65] = {};
    char bootstrap_url[257] = {};
    char locale[16] = "vi-VN";
    char wake_profile[65] = {};
    char client_id[37] = {};

    [[nodiscard]] bool HasProvisioning() const;
};

class SettingsStore {
public:
    ~SettingsStore();

    esp_err_t Initialize(DeviceSettings* settings);
    esp_err_t SaveProvisioning(const DeviceSettings& settings);
    esp_err_t ClearWifiCredentials(DeviceSettings* settings);

private:
    esp_err_t LoadString(const char* key, char* destination, std::size_t capacity,
                         const char* fallback = "");
    esp_err_t EnsureClientId(DeviceSettings* settings);

    nvs_handle_t handle_ = 0;
};

}  // namespace veetee::settings
