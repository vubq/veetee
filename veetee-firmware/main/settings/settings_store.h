#pragma once

#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "nvs.h"
#include "settings/wifi_profile_record.h"

namespace veetee::settings {

struct DeviceSettings {
    char ssid[33] = {};
    char password[65] = {};
    char bootstrap_url[257] = {};
    char locale[16] = "vi-VN";
    char wake_profile[65] = {};
    char client_id[37] = {};
    char activation_code[7] = {};
    char activation_challenge[129] = {};
    char device_id[37] = {};
    char device_token[129] = {};
    char websocket_url[257] = {};
    std::uint32_t config_version = 0;

    [[nodiscard]] bool HasProvisioning() const;
    [[nodiscard]] bool HasPendingActivation() const;
    [[nodiscard]] bool HasDeviceIdentity() const;
};

class SettingsStore {
public:
    ~SettingsStore();

    esp_err_t Initialize(DeviceSettings* settings);
    esp_err_t SaveProvisioning(DeviceSettings* settings);
    esp_err_t ClearWifiCredentials(DeviceSettings* settings);
    [[nodiscard]] WifiProfileRecord WifiProfiles() const;
    esp_err_t MarkWifiProfileSuccessful(const char* ssid,
                                        DeviceSettings* settings);
    esp_err_t SavePendingActivation(const char* code, const char* challenge,
                                    DeviceSettings* settings);
    esp_err_t SaveDeviceActivation(const char* device_id, const char* token,
                                   const char* websocket_url,
                                   std::uint32_t config_version,
                                   DeviceSettings* settings);
    esp_err_t SaveBoundBootstrap(const char* websocket_url,
                                 std::uint32_t config_version,
                                 DeviceSettings* settings);
    esp_err_t ClearPendingActivation(DeviceSettings* settings);
    esp_err_t ClearDeviceIdentity(DeviceSettings* settings);

private:
    esp_err_t LoadString(const char* key, char* destination, std::size_t capacity,
                         const char* fallback = "");
    esp_err_t LoadActivation(DeviceSettings* settings);
    esp_err_t EnsureClientId(DeviceSettings* settings);
    esp_err_t LoadWifiProfiles(DeviceSettings* settings);
    void ApplyActiveWifi(DeviceSettings* settings) const;

    nvs_handle_t handle_ = 0;
    WifiProfileRecord wifi_profiles_{};
};

}  // namespace veetee::settings
