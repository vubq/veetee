#include "settings/settings_store.h"

#include <cinttypes>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "esp_log.h"
#include "esp_random.h"
#include "nvs_flash.h"
#include "sdkconfig.h"
#include "settings/activation_record.h"
#include "settings/wifi_profile_record.h"

namespace veetee::settings {
namespace {

constexpr char kTag[] = "veetee_settings";
constexpr char kNamespace[] = "veetee";
constexpr std::uint32_t kSchemaVersion = 3;
constexpr char kActivationRecordKey[] = "activation";
constexpr char kWifiProfileRecordKey[] = "wifi_profiles";
constexpr char kLegacyWifiSsidKey[] = "wifi_ssid";
constexpr char kLegacyWifiPasswordKey[] = "wifi_pass";

void CopyString(char* destination, std::size_t capacity, const char* source) {
    if (capacity == 0) {
        return;
    }
    std::snprintf(destination, capacity, "%s", source == nullptr ? "" : source);
}

void ClearActivation(DeviceSettings* settings) {
    settings->activation_code[0] = '\0';
    settings->activation_challenge[0] = '\0';
    settings->device_id[0] = '\0';
    settings->device_token[0] = '\0';
    settings->websocket_url[0] = '\0';
    settings->config_version = 0;
}

void ApplyActivationRecord(const ActivationRecord& record,
                           DeviceSettings* settings) {
    ClearActivation(settings);
    if (record.state == ActivationRecordState::kPending) {
        CopyString(settings->activation_code, sizeof(settings->activation_code),
                   record.activation_code);
        CopyString(settings->activation_challenge,
                   sizeof(settings->activation_challenge),
                   record.activation_challenge);
    } else if (record.state == ActivationRecordState::kActive) {
        CopyString(settings->device_id, sizeof(settings->device_id),
                   record.device_id);
        CopyString(settings->device_token, sizeof(settings->device_token),
                   record.device_token);
        CopyString(settings->websocket_url, sizeof(settings->websocket_url),
                   record.websocket_url);
        settings->config_version = record.config_version;
    }
}

}  // namespace

bool DeviceSettings::HasProvisioning() const {
    return ssid[0] != '\0' && bootstrap_url[0] != '\0';
}

bool DeviceSettings::HasPendingActivation() const {
    return std::strlen(activation_code) == 6 && activation_challenge[0] != '\0';
}

bool DeviceSettings::HasDeviceIdentity() const {
    return device_id[0] != '\0' && device_token[0] != '\0' &&
           websocket_url[0] != '\0';
}

SettingsStore::~SettingsStore() {
    if (handle_ != 0) {
        nvs_close(handle_);
    }
}

esp_err_t SettingsStore::Initialize(DeviceSettings* settings) {
    if (settings == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t error = nvs_flash_init();
    if (error == ESP_ERR_NVS_NO_FREE_PAGES || error == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(kTag, "NVS requires recovery erase: %s", esp_err_to_name(error));
        error = nvs_flash_erase();
        if (error == ESP_OK) {
            error = nvs_flash_init();
        }
    }
    if (error != ESP_OK) {
        return error;
    }

    error = nvs_open(kNamespace, NVS_READWRITE, &handle_);
    if (error != ESP_OK) {
        return error;
    }

    std::uint32_t schema_version = 0;
    error = nvs_get_u32(handle_, "schema", &schema_version);
    if (error == ESP_ERR_NVS_NOT_FOUND) error = ESP_OK;
    if (error != ESP_OK) {
        return error;
    }
    if (schema_version > kSchemaVersion) {
        ESP_LOGE(kTag, "Unsupported NVS schema version: %" PRIu32, schema_version);
        return ESP_ERR_INVALID_VERSION;
    }
    if ((error = LoadWifiProfiles(settings)) != ESP_OK ||
        (error = LoadString("bootstrap", settings->bootstrap_url,
                            sizeof(settings->bootstrap_url),
                            CONFIG_VEETEE_DEFAULT_BOOTSTRAP_URL)) != ESP_OK ||
        (error = LoadString("locale", settings->locale, sizeof(settings->locale),
                            "vi-VN")) != ESP_OK ||
        (error = LoadString("time_zone", settings->time_zone,
                            sizeof(settings->time_zone), "Asia/Bangkok")) != ESP_OK ||
        (error = LoadString("wake_profile", settings->wake_profile,
                            sizeof(settings->wake_profile))) != ESP_OK ||
        (error = LoadString("client_id", settings->client_id,
                            sizeof(settings->client_id))) != ESP_OK ||
        (error = LoadActivation(settings)) != ESP_OK ||
        (error = EnsureClientId(settings)) != ESP_OK) {
        return error;
    }

    // V3 replaces the single legacy credential pair with one CRC-protected,
    // bounded MRU profile record. Activation V2 and all other keys stay intact.
    if ((error = nvs_set_u32(handle_, "schema", kSchemaVersion)) != ESP_OK) {
        return error;
    }

    error = nvs_commit(handle_);
    if (error == ESP_OK) {
        ESP_LOGI(kTag,
                 "NVS schema=%" PRIu32
                 " provisioned=%s paired=%s activation_pending=%s client_id=%s",
                 kSchemaVersion, settings->HasProvisioning() ? "yes" : "no",
                 settings->HasDeviceIdentity() ? "yes" : "no",
                 settings->HasPendingActivation() ? "yes" : "no",
                 settings->client_id);
    }
    return error;
}

esp_err_t SettingsStore::SaveProvisioning(DeviceSettings* settings) {
    if (handle_ == 0 || settings == nullptr || settings->ssid[0] == '\0' ||
        settings->bootstrap_url[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }

    WifiProfileRecord updated = wifi_profiles_;
    if (!UpsertWifiProfile(&updated, settings->ssid, settings->password)) {
        return ESP_ERR_INVALID_ARG;
    }
    const WifiProfile* selected = FindWifiProfile(updated, settings->ssid);
    if (selected == nullptr) return ESP_ERR_INVALID_STATE;

    esp_err_t error = nvs_set_blob(handle_, kWifiProfileRecordKey, &updated,
                                   sizeof(updated));
    if (error == ESP_OK) {
        error = nvs_set_str(handle_, "bootstrap", settings->bootstrap_url);
    }
    if (error == ESP_OK) error = nvs_set_str(handle_, "locale", settings->locale);
    if (error == ESP_OK) {
        error = nvs_set_str(handle_, "time_zone", settings->time_zone);
    }
    if (error == ESP_OK) {
        error = nvs_set_str(handle_, "wake_profile", settings->wake_profile);
    }
    if (error == ESP_OK) error = nvs_set_u32(handle_, "schema", kSchemaVersion);
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) {
        wifi_profiles_ = updated;
        CopyString(settings->password, sizeof(settings->password),
                   selected->password);
        ESP_LOGI(kTag, "Provisioning saved for SSID '%s' and bootstrap host (password redacted)",
                 settings->ssid);
    }
    return error;
}

esp_err_t SettingsStore::ClearWifiCredentials(DeviceSettings* settings) {
    if (handle_ == 0 || settings == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    esp_err_t error = nvs_erase_key(handle_, kWifiProfileRecordKey);
    if (error == ESP_ERR_NVS_NOT_FOUND) error = ESP_OK;
    esp_err_t next = nvs_erase_key(handle_, kLegacyWifiSsidKey);
    if (next != ESP_OK && next != ESP_ERR_NVS_NOT_FOUND) error = next;
    next = nvs_erase_key(handle_, kLegacyWifiPasswordKey);
    if (next != ESP_OK && next != ESP_ERR_NVS_NOT_FOUND) error = next;
    next = nvs_erase_key(handle_, "bootstrap");
    if (next != ESP_OK && next != ESP_ERR_NVS_NOT_FOUND) error = next;
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) {
        wifi_profiles_ = WifiProfileRecord{};
        SealWifiProfileRecord(&wifi_profiles_);
        settings->ssid[0] = '\0';
        settings->password[0] = '\0';
        settings->bootstrap_url[0] = '\0';
    }
    return error;
}

WifiProfileRecord SettingsStore::WifiProfiles() const {
    return wifi_profiles_;
}

esp_err_t SettingsStore::MarkWifiProfileSuccessful(const char* ssid,
                                                   DeviceSettings* settings) {
    if (handle_ == 0 || ssid == nullptr || settings == nullptr) {
        return ESP_ERR_INVALID_ARG;
    }
    WifiProfileRecord updated = wifi_profiles_;
    if (!veetee::settings::MarkWifiProfileSuccessful(&updated, ssid)) {
        return ESP_ERR_NOT_FOUND;
    }
    esp_err_t error = nvs_set_blob(handle_, kWifiProfileRecordKey, &updated,
                                   sizeof(updated));
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) {
        wifi_profiles_ = updated;
        ApplyActiveWifi(settings);
        ESP_LOGI(kTag, "SSID '%s' promoted to last-successful profile", ssid);
    }
    return error;
}

esp_err_t SettingsStore::SavePendingActivation(const char* code, const char* challenge,
                                               DeviceSettings* settings) {
    if (handle_ == 0 || settings == nullptr || settings->HasDeviceIdentity() ||
        code == nullptr || challenge == nullptr ||
        std::strlen(code) != 6 || std::strlen(challenge) < 16 ||
        std::strlen(challenge) >= sizeof(settings->activation_challenge)) {
        return ESP_ERR_INVALID_ARG;
    }
    for (const char* digit = code; *digit != '\0'; ++digit) {
        if (*digit < '0' || *digit > '9') return ESP_ERR_INVALID_ARG;
    }

    ActivationRecord record{};
    record.state = ActivationRecordState::kPending;
    CopyString(record.activation_code, sizeof(record.activation_code), code);
    CopyString(record.activation_challenge, sizeof(record.activation_challenge),
               challenge);
    SealActivationRecord(&record);

    esp_err_t error = nvs_set_blob(handle_, kActivationRecordKey, &record,
                                   sizeof(record));
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) {
        ApplyActivationRecord(record, settings);
        ESP_LOGI(kTag, "Pending activation persisted (challenge redacted)");
    }
    return error;
}

esp_err_t SettingsStore::SaveDeviceActivation(const char* device_id, const char* token,
                                              const char* websocket_url,
                                              std::uint32_t config_version,
                                              DeviceSettings* settings) {
    if (handle_ == 0 || settings == nullptr || device_id == nullptr || token == nullptr ||
        websocket_url == nullptr || device_id[0] == '\0' || token[0] == '\0' ||
        websocket_url[0] == '\0' ||
        std::strlen(device_id) >= sizeof(settings->device_id) ||
        std::strlen(token) >= sizeof(settings->device_token) ||
        std::strlen(websocket_url) >= sizeof(settings->websocket_url)) {
        return ESP_ERR_INVALID_ARG;
    }

    ActivationRecord record{};
    record.state = ActivationRecordState::kActive;
    CopyString(record.device_id, sizeof(record.device_id), device_id);
    CopyString(record.device_token, sizeof(record.device_token), token);
    CopyString(record.websocket_url, sizeof(record.websocket_url), websocket_url);
    record.config_version = config_version;
    SealActivationRecord(&record);

    esp_err_t error = nvs_set_blob(handle_, kActivationRecordKey, &record,
                                   sizeof(record));
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) {
        ApplyActivationRecord(record, settings);
        ESP_LOGI(kTag, "Device activation persisted (token redacted)");
    }
    return error;
}

esp_err_t SettingsStore::SaveBoundBootstrap(const char* websocket_url,
                                            std::uint32_t config_version,
                                            DeviceSettings* settings) {
    if (handle_ == 0 || settings == nullptr || !settings->HasDeviceIdentity() ||
        websocket_url == nullptr || websocket_url[0] == '\0' ||
        std::strlen(websocket_url) >= sizeof(settings->websocket_url)) {
        return ESP_ERR_INVALID_ARG;
    }
    ActivationRecord record{};
    record.state = ActivationRecordState::kActive;
    CopyString(record.device_id, sizeof(record.device_id), settings->device_id);
    CopyString(record.device_token, sizeof(record.device_token),
               settings->device_token);
    CopyString(record.websocket_url, sizeof(record.websocket_url), websocket_url);
    record.config_version = config_version;
    SealActivationRecord(&record);

    esp_err_t error = nvs_set_blob(handle_, kActivationRecordKey, &record,
                                   sizeof(record));
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) {
        ApplyActivationRecord(record, settings);
    }
    return error;
}

esp_err_t SettingsStore::ClearPendingActivation(DeviceSettings* settings) {
    if (handle_ == 0 || settings == nullptr) return ESP_ERR_INVALID_ARG;
    if (settings->HasDeviceIdentity()) return ESP_OK;
    esp_err_t error = nvs_erase_key(handle_, kActivationRecordKey);
    if (error == ESP_ERR_NVS_NOT_FOUND) error = ESP_OK;
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) {
        ClearActivation(settings);
    }
    return error;
}

esp_err_t SettingsStore::ClearDeviceIdentity(DeviceSettings* settings) {
    if (handle_ == 0 || settings == nullptr) return ESP_ERR_INVALID_ARG;
    esp_err_t error = nvs_erase_key(handle_, kActivationRecordKey);
    if (error == ESP_ERR_NVS_NOT_FOUND) error = ESP_OK;
    if (error == ESP_OK) error = nvs_commit(handle_);
    if (error == ESP_OK) {
        ClearActivation(settings);
        ESP_LOGW(kTag, "Device identity cleared by physical recovery action");
    }
    return error;
}

esp_err_t SettingsStore::LoadString(const char* key, char* destination,
                                    std::size_t capacity, const char* fallback) {
    std::size_t length = capacity;
    const esp_err_t error = nvs_get_str(handle_, key, destination, &length);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        CopyString(destination, capacity, fallback);
        return ESP_OK;
    }
    if (error == ESP_ERR_NVS_INVALID_LENGTH) {
        ESP_LOGE(kTag, "NVS value '%s' exceeds firmware bound", key);
    }
    return error;
}

esp_err_t SettingsStore::LoadWifiProfiles(DeviceSettings* settings) {
    WifiProfileRecord loaded{};
    std::size_t length = sizeof(loaded);
    esp_err_t error = nvs_get_blob(handle_, kWifiProfileRecordKey, &loaded,
                                   &length);
    bool loaded_valid = false;
    if (error == ESP_OK) {
        loaded_valid = length == sizeof(loaded) && IsValidWifiProfileRecord(loaded);
        if (!loaded_valid) {
            ESP_LOGE(kTag,
                     "Wi-Fi profile record failed version/CRC validation; clearing credentials");
            error = nvs_erase_key(handle_, kWifiProfileRecordKey);
            if (error != ESP_OK && error != ESP_ERR_NVS_NOT_FOUND) return error;
        }
    } else if (error != ESP_ERR_NVS_NOT_FOUND) {
        return error;
    }

    if (loaded_valid) {
        wifi_profiles_ = loaded;
    } else {
        wifi_profiles_ = WifiProfileRecord{};
        char legacy_ssid[sizeof(settings->ssid)] = {};
        char legacy_password[sizeof(settings->password)] = {};
        std::size_t ssid_length = sizeof(legacy_ssid);
        error = nvs_get_str(handle_, kLegacyWifiSsidKey, legacy_ssid,
                            &ssid_length);
        if (error == ESP_ERR_NVS_NOT_FOUND) {
            error = ESP_OK;
        } else if (error != ESP_OK) {
            return error;
        }
        if (legacy_ssid[0] != '\0') {
            std::size_t password_length = sizeof(legacy_password);
            const esp_err_t password_error = nvs_get_str(
                handle_, kLegacyWifiPasswordKey, legacy_password,
                &password_length);
            if (password_error != ESP_OK &&
                password_error != ESP_ERR_NVS_NOT_FOUND) {
                return password_error;
            }
            if (!UpsertWifiProfile(&wifi_profiles_, legacy_ssid,
                                   legacy_password)) {
                return ESP_ERR_INVALID_SIZE;
            }
            error = nvs_set_blob(handle_, kWifiProfileRecordKey,
                                 &wifi_profiles_, sizeof(wifi_profiles_));
            if (error != ESP_OK) return error;
            ESP_LOGI(kTag, "Migrated legacy Wi-Fi credential into V3 profile store");
        } else {
            SealWifiProfileRecord(&wifi_profiles_);
        }
    }

    // Removal and the new blob are committed together by Initialize().
    error = nvs_erase_key(handle_, kLegacyWifiSsidKey);
    if (error != ESP_OK && error != ESP_ERR_NVS_NOT_FOUND) return error;
    error = nvs_erase_key(handle_, kLegacyWifiPasswordKey);
    if (error != ESP_OK && error != ESP_ERR_NVS_NOT_FOUND) return error;
    ApplyActiveWifi(settings);
    return ESP_OK;
}

void SettingsStore::ApplyActiveWifi(DeviceSettings* settings) const {
    settings->ssid[0] = '\0';
    settings->password[0] = '\0';
    if (wifi_profiles_.count == 0) return;
    CopyString(settings->ssid, sizeof(settings->ssid),
               wifi_profiles_.profiles[0].ssid);
    CopyString(settings->password, sizeof(settings->password),
               wifi_profiles_.profiles[0].password);
}

esp_err_t SettingsStore::LoadActivation(DeviceSettings* settings) {
    ActivationRecord record{};
    std::size_t length = sizeof(record);
    const esp_err_t error = nvs_get_blob(handle_, kActivationRecordKey, &record,
                                         &length);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        ClearActivation(settings);
        return ESP_OK;
    }
    if (error != ESP_OK) return error;
    if (length != sizeof(record) || !IsValidActivationRecord(record)) {
        ESP_LOGE(kTag, "Activation record failed version/CRC validation");
        return ESP_ERR_INVALID_CRC;
    }
    ApplyActivationRecord(record, settings);
    if ((record.state == ActivationRecordState::kPending &&
         !settings->HasPendingActivation()) ||
        (record.state == ActivationRecordState::kActive &&
         !settings->HasDeviceIdentity())) {
        ESP_LOGE(kTag, "Activation record contains invalid bounded values");
        return ESP_ERR_INVALID_SIZE;
    }
    return ESP_OK;
}

esp_err_t SettingsStore::EnsureClientId(DeviceSettings* settings) {
    if (settings->client_id[0] != '\0') {
        return ESP_OK;
    }

    std::uint8_t bytes[16];
    esp_fill_random(bytes, sizeof(bytes));
    bytes[6] = static_cast<std::uint8_t>((bytes[6] & 0x0FU) | 0x40U);
    bytes[8] = static_cast<std::uint8_t>((bytes[8] & 0x3FU) | 0x80U);
    std::snprintf(settings->client_id, sizeof(settings->client_id),
                  "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
                  bytes[0], bytes[1], bytes[2], bytes[3], bytes[4], bytes[5],
                  bytes[6], bytes[7], bytes[8], bytes[9], bytes[10], bytes[11],
                  bytes[12], bytes[13], bytes[14], bytes[15]);
    return nvs_set_str(handle_, "client_id", settings->client_id);
}

}  // namespace veetee::settings
