#include "ota/firmware_updater.h"

#include <algorithm>
#include <array>
#include <cinttypes>
#include <cstdio>
#include <cstring>

#include "board/board_config.h"
#include "esp_crt_bundle.h"
#include "esp_http_client.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_ota_ops.h"
#include "esp_partition.h"
#include "esp_psram.h"
#include "esp_system.h"
#include "network/endpoint_url.h"
#include "psa/crypto.h"
#include "sdkconfig.h"

namespace veetee::ota {
namespace {
constexpr char kTag[] = "veetee_firmware_ota";
constexpr char kFirmwareReleaseMarker[] =
    "VEETEE_RELEASE_VERSION=" CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION;
constexpr std::size_t kChunkBytes = 8192;
constexpr std::size_t kMaximumResponseBytes = 32768;

bool DecodePublicKey(const char* encoded, std::array<std::uint8_t, 32>* key) {
    if (encoded == nullptr || key == nullptr || std::strlen(encoded) != 64) return false;
    for (std::size_t i = 0; i < key->size(); ++i) {
        auto nibble = [](char c) -> int {
            if (c >= '0' && c <= '9') return c - '0';
            if (c >= 'a' && c <= 'f') return c - 'a' + 10;
            if (c >= 'A' && c <= 'F') return c - 'A' + 10;
            return -1;
        };
        const int high = nibble(encoded[i * 2]);
        const int low = nibble(encoded[i * 2 + 1]);
        if (high < 0 || low < 0) return false;
        (*key)[i] = static_cast<std::uint8_t>((high << 4) | low);
    }
    return true;
}
void HashHex(const std::array<std::uint8_t, 32>& hash, char output[65]) {
    constexpr char hex[] = "0123456789abcdef";
    for (std::size_t i = 0; i < hash.size(); ++i) {
        output[i * 2] = hex[hash[i] >> 4];
        output[i * 2 + 1] = hex[hash[i] & 0x0f];
    }
    output[64] = '\0';
}
std::uint8_t PartitionSlot(const esp_partition_t* partition) {
    if (partition == nullptr ||
        partition->subtype < ESP_PARTITION_SUBTYPE_APP_OTA_MIN ||
        partition->subtype >= ESP_PARTITION_SUBTYPE_APP_OTA_MAX) {
        return 0;
    }
    return static_cast<std::uint8_t>(
        partition->subtype - ESP_PARTITION_SUBTYPE_APP_OTA_MIN);
}
}  // namespace

FirmwareUpdater::~FirmwareUpdater() {
    if (nvs_handle_ != 0) nvs_close(nvs_handle_);
}

esp_err_t FirmwareUpdater::Initialize(settings::DeviceSettings* settings,
                                       EventSink sink, void* context) {
    if (settings == nullptr || sink == nullptr ||
        std::strlen(CONFIG_VEETEE_RESOURCE_SIGNING_PUBLIC_KEY_HEX) != 64 ||
        CONFIG_VEETEE_RESOURCE_SIGNING_KEY_ID[0] == '\0') return ESP_ERR_INVALID_ARG;
    settings_ = settings;
    sink_ = sink;
    sink_context_ = context;
    if (psa_crypto_init() != PSA_SUCCESS) return ESP_FAIL;
    esp_err_t error = nvs_open("veetee_fw_ota", NVS_READWRITE, &nvs_handle_);
    if (error != ESP_OK) return error;
    error = nvs_get_u32(nvs_handle_, "epoch", &security_epoch_floor_);
    if (error == ESP_ERR_NVS_NOT_FOUND) {
        security_epoch_floor_ = CONFIG_VEETEE_MIN_RESOURCE_SECURITY_EPOCH;
        error = ESP_OK;
    }
    if (error != ESP_OK) return error;
    security_epoch_floor_ = std::max<std::uint32_t>(
        security_epoch_floor_, CONFIG_VEETEE_MIN_RESOURCE_SECURITY_EPOCH);
    ESP_LOGI(kTag, "%s", kFirmwareReleaseMarker);
    std::uint8_t mac[6] = {};
    error = esp_read_mac(mac, ESP_MAC_WIFI_STA);
    if (error != ESP_OK) return error;
    std::snprintf(hardware_id_, sizeof(hardware_id_), "%02x:%02x:%02x:%02x:%02x:%02x",
                  mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    queue_ = xQueueCreate(1, sizeof(Target));
    if (queue_ == nullptr) return ESP_ERR_NO_MEM;
    if (xTaskCreate(&FirmwareUpdater::TaskEntry, "veetee_fw_ota", 12288, this, 4,
                    &task_) != pdPASS) {
        vQueueDelete(queue_);
        queue_ = nullptr;
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

bool FirmwareUpdater::Schedule(const char* desired_version, const char* manifest_url) {
    Target target{};
    if (queue_ == nullptr || desired_version == nullptr || manifest_url == nullptr ||
        desired_version[0] == '\0' || std::strlen(desired_version) >= sizeof(target.desired_version) ||
        std::strlen(manifest_url) >= sizeof(target.manifest_url) ||
        !network::IsHttpEndpointUrl(manifest_url)) return false;
    // Bootstrap can keep the desired pointer after a successful rollout. The
    // running image must not download and reboot into the same version again.
    if (std::strcmp(desired_version, CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION) == 0) {
        return true;
    }
    target.generation = generation_.fetch_add(1) + 1;
    std::snprintf(target.desired_version, sizeof(target.desired_version), "%s", desired_version);
    std::snprintf(target.manifest_url, sizeof(target.manifest_url), "%s", manifest_url);
    return xQueueOverwrite(queue_, &target) == pdTRUE;
}

void FirmwareUpdater::Cancel() {
    generation_.fetch_add(1);
    if (queue_ != nullptr) xQueueReset(queue_);
}

esp_err_t FirmwareUpdater::ConfirmPendingBoot() {
    const esp_partition_t* running = esp_ota_get_running_partition();
    if (running == nullptr) return ESP_ERR_NOT_FOUND;
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
    const esp_err_t error = esp_ota_get_state_partition(running, &state);
    if (error != ESP_OK) return error;
    if (state != ESP_OTA_IMG_PENDING_VERIFY) return ESP_ERR_INVALID_STATE;
    ESP_LOGI(kTag, "Firmware boot health window passed; marking image valid");
    return esp_ota_mark_app_valid_cancel_rollback();
}

bool FirmwareUpdater::PendingBootVerification() const {
    const esp_partition_t* running = esp_ota_get_running_partition();
    if (running == nullptr) return false;
    esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
    return esp_ota_get_state_partition(running, &state) == ESP_OK &&
           state == ESP_OTA_IMG_PENDING_VERIFY;
}

std::uint8_t FirmwareUpdater::ActiveSlot() const {
    return PartitionSlot(esp_ota_get_running_partition());
}

esp_err_t FirmwareUpdater::RollbackPendingBoot() {
    if (!PendingBootVerification()) return ESP_ERR_INVALID_STATE;
    ESP_LOGE(kTag, "Firmware boot health failed; requesting bootloader rollback");
    return esp_ota_mark_app_invalid_rollback_and_reboot();
}

void FirmwareUpdater::TaskEntry(void* context) {
    static_cast<FirmwareUpdater*>(context)->TaskLoop();
}
void FirmwareUpdater::TaskLoop() {
    Target target{};
    while (xQueueReceive(queue_, &target, portMAX_DELAY) == pdTRUE) {
        if (IsCurrent(target.generation)) Reconcile(target);
    }
}
bool FirmwareUpdater::IsCurrent(std::uint32_t generation) const {
    return generation == generation_.load();
}

bool FirmwareUpdater::Emit(FirmwareOtaEvent event, const Target& target,
                            const VerifiedFirmwareManifest* manifest,
                            const char* error,
                            std::uint32_t downloaded_bytes) const {
    if (sink_ == nullptr) return false;
    FirmwareOtaNotification notification{};
    notification.event = event;
    std::snprintf(notification.desired_version, sizeof(notification.desired_version), "%s",
                  target.desired_version);
    if (manifest != nullptr) {
        std::snprintf(notification.current_version, sizeof(notification.current_version), "%s",
                      CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION);
        notification.expected_bytes = static_cast<std::uint32_t>(manifest->payload_bytes);
        notification.security_epoch = manifest->security_epoch;
    }
    notification.downloaded_bytes = downloaded_bytes;
    notification.active_slot = ActiveSlot();
    notification.target_slot = target_slot_;
    if (error != nullptr) std::snprintf(notification.error_code, sizeof(notification.error_code), "%s", error);
    return sink_(notification, sink_context_);
}

esp_err_t FirmwareUpdater::FetchManifest(const Target& target) {
    response_size_ = 0;
    response_overflow_ = false;
    response_[0] = '\0';
    esp_http_client_config_t config = {};
    config.url = target.manifest_url;
    config.event_handler = &FirmwareUpdater::HttpEventHandler;
    config.user_data = this;
    config.timeout_ms = 8000;
    config.buffer_size = 1024;
    config.buffer_size_tx = 1024;
    config.keep_alive_enable = true;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == nullptr) return ESP_ERR_NO_MEM;
    esp_err_t error = esp_http_client_set_header(client, "Device-Id", hardware_id_);
    if (error == ESP_OK) {
        char authorization[160] = {};
        const int length = std::snprintf(authorization, sizeof(authorization), "Bearer %s",
                                         settings_->device_token);
        if (length <= 7 || length >= static_cast<int>(sizeof(authorization))) error = ESP_ERR_INVALID_SIZE;
        if (error == ESP_OK) error = esp_http_client_set_header(client, "Authorization", authorization);
    }
    if (error == ESP_OK) error = esp_http_client_perform(client);
    const int status = error == ESP_OK ? esp_http_client_get_status_code(client) : 0;
    esp_http_client_cleanup(client);
    if (error != ESP_OK) return error;
    if (status != 200 || response_overflow_) return ESP_ERR_INVALID_RESPONSE;
    return response_size_ > 0 ? ESP_OK : ESP_ERR_INVALID_RESPONSE;
}

esp_err_t FirmwareUpdater::Download(const Target& target,
                                     const VerifiedFirmwareManifest& manifest) {
    const esp_partition_t* update = esp_ota_get_next_update_partition(nullptr);
    if (update == nullptr || manifest.payload_bytes > update->size) return ESP_ERR_INVALID_SIZE;
    esp_ota_handle_t handle = 0;
    esp_err_t error = esp_ota_begin(update, manifest.payload_bytes, &handle);
    if (error != ESP_OK) return error;
    esp_http_client_config_t config = {};
    config.url = manifest.payload_url;
    config.timeout_ms = 10000;
    config.buffer_size = 2048;
    config.buffer_size_tx = 1024;
    config.keep_alive_enable = true;
    config.crt_bundle_attach = esp_crt_bundle_attach;
    esp_http_client_handle_t client = esp_http_client_init(&config);
    if (client == nullptr) {
        esp_ota_abort(handle);
        return ESP_ERR_NO_MEM;
    }
    char authorization[160] = {};
    std::snprintf(authorization, sizeof(authorization), "Bearer %s", settings_->device_token);
    error = esp_http_client_set_header(client, "Device-Id", hardware_id_);
    if (error == ESP_OK) error = esp_http_client_set_header(client, "Authorization", authorization);
    if (error == ESP_OK) error = esp_http_client_open(client, 0);
    const int status = error == ESP_OK ? esp_http_client_fetch_headers(client) : 0;
    if (error == ESP_OK && status < 0) error = ESP_FAIL;
    if (error == ESP_OK && esp_http_client_get_status_code(client) != 200) error = ESP_ERR_INVALID_RESPONSE;
    if (error != ESP_OK) {
        esp_http_client_cleanup(client);
        esp_ota_abort(handle);
        return error;
    }
    psa_hash_operation_t digest = PSA_HASH_OPERATION_INIT;
    if (psa_hash_setup(&digest, PSA_ALG_SHA_256) != PSA_SUCCESS) {
        esp_http_client_cleanup(client);
        esp_ota_abort(handle);
        return ESP_FAIL;
    }
    std::array<std::uint8_t, kChunkBytes> buffer{};
    std::uint64_t downloaded = 0;
    std::uint64_t nextProgress = 256U * 1024U;
    Emit(FirmwareOtaEvent::kDownloading, target, &manifest, nullptr);
    while (downloaded < manifest.payload_bytes && IsCurrent(target.generation)) {
        const int read = esp_http_client_read(client, reinterpret_cast<char*>(buffer.data()), buffer.size());
        if (read < 0) { error = ESP_FAIL; break; }
        if (read == 0) { error = ESP_ERR_INVALID_SIZE; break; }
        error = esp_ota_write(handle, buffer.data(), static_cast<std::size_t>(read));
        if (error != ESP_OK) break;
        if (psa_hash_update(&digest, buffer.data(), static_cast<std::size_t>(read)) !=
            PSA_SUCCESS) {
            error = ESP_FAIL;
            break;
        }
        downloaded += static_cast<std::uint64_t>(read);
        if (downloaded >= nextProgress || downloaded == manifest.payload_bytes) {
            Emit(FirmwareOtaEvent::kDownloading, target, &manifest, nullptr,
                 static_cast<std::uint32_t>(downloaded));
            nextProgress = downloaded + 256U * 1024U;
        }
    }
    std::array<std::uint8_t, 32> actual{};
    std::size_t hashLength = 0;
    if (psa_hash_finish(&digest, actual.data(), actual.size(), &hashLength) !=
            PSA_SUCCESS ||
        hashLength != actual.size()) {
        psa_hash_abort(&digest);
        error = ESP_FAIL;
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    if (!IsCurrent(target.generation)) error = ESP_ERR_INVALID_STATE;
    if (error == ESP_OK && downloaded != manifest.payload_bytes) error = ESP_ERR_INVALID_SIZE;
    char actualHex[65] = {};
    HashHex(actual, actualHex);
    if (error == ESP_OK && std::strcmp(actualHex, manifest.payload_sha256) != 0) error = ESP_ERR_INVALID_CRC;
    if (error == ESP_OK) {
        Emit(FirmwareOtaEvent::kVerifying, target, &manifest, nullptr);
        error = esp_ota_end(handle);
    } else {
        esp_ota_abort(handle);
    }
    if (error != ESP_OK) return error;
    error = PersistSecurityEpoch(manifest.security_epoch);
    if (error == ESP_OK) error = esp_ota_set_boot_partition(update);
    if (error == ESP_OK) {
        Emit(FirmwareOtaEvent::kStaged, target, &manifest, nullptr);
        Emit(FirmwareOtaEvent::kRebooting, target, &manifest, nullptr);
    }
    return error;
}

esp_err_t FirmwareUpdater::PersistSecurityEpoch(std::uint32_t epoch) {
    if (nvs_handle_ == 0 || epoch < security_epoch_floor_) return ESP_ERR_INVALID_STATE;
    esp_err_t error = nvs_set_u32(nvs_handle_, "epoch", epoch);
    if (error == ESP_OK) error = nvs_commit(nvs_handle_);
    if (error == ESP_OK) security_epoch_floor_ = epoch;
    return error;
}

void FirmwareUpdater::Reconcile(const Target& target) {
    if (settings_ == nullptr || !settings_->HasDeviceIdentity()) return;
    current_target_ = target;
    const esp_partition_t* update = esp_ota_get_next_update_partition(nullptr);
    target_slot_ = PartitionSlot(update);
    Emit(FirmwareOtaEvent::kChecking, target, nullptr, nullptr);
    if (FetchManifest(target) != ESP_OK) {
        Emit(FirmwareOtaEvent::kFailed, target, nullptr, "manifest_fetch_failed");
        return;
    }
    std::array<std::uint8_t, 32> publicKey{};
    if (!DecodePublicKey(CONFIG_VEETEE_RESOURCE_SIGNING_PUBLIC_KEY_HEX, &publicKey)) {
        Emit(FirmwareOtaEvent::kFailed, target, nullptr, "trust_root_invalid");
        return;
    }
    const TrustedReleaseKey key = {
        .key_id = CONFIG_VEETEE_RESOURCE_SIGNING_KEY_ID,
        .minimum_security_epoch = security_epoch_floor_,
        .public_key = publicKey,
    };
    DeviceFirmwareCapability capability = {
        .board = board::kBoardName,
        .chip = "esp32s3",
        .flash_bytes = 16ULL * 1024ULL * 1024ULL,
        .psram_bytes = esp_psram_is_initialized() ? esp_psram_get_size() : 0,
        .slot_bytes = update == nullptr ? 0 : update->size,
    };
    VerifiedFirmwareManifest manifest{};
    const FirmwareManifestError verify = VerifyFirmwareManifest(
        std::string_view(response_.data(), response_size_), capability, &key, 1, &manifest);
    if (verify != FirmwareManifestError::kOk) {
        Emit(FirmwareOtaEvent::kFailed, target, nullptr, FirmwareManifestErrorName(verify));
        return;
    }
    if (std::strcmp(manifest.version, target.desired_version) != 0) {
        Emit(FirmwareOtaEvent::kFailed, target, &manifest, "desired_version_mismatch");
        return;
    }
    const esp_err_t error = Download(target, manifest);
    if (error != ESP_OK) {
        Emit(FirmwareOtaEvent::kFailed, target, &manifest, esp_err_to_name(error));
        return;
    }
    // Reboot only after the complete signed image is committed to the inactive slot.
    if (IsCurrent(target.generation)) esp_restart();
}

esp_err_t FirmwareUpdater::HttpEventHandler(esp_http_client_event_t* event) {
    if (event == nullptr || event->user_data == nullptr) return ESP_FAIL;
    auto* self = static_cast<FirmwareUpdater*>(event->user_data);
    if (event->event_id == HTTP_EVENT_ON_DATA && event->data != nullptr &&
        event->data_len > 0) {
        if (self->response_size_ + static_cast<std::size_t>(event->data_len) >=
            kMaximumResponseBytes) {
            self->response_overflow_ = true;
            return ESP_ERR_NO_MEM;
        }
        std::memcpy(self->response_.data() + self->response_size_, event->data,
                    static_cast<std::size_t>(event->data_len));
        self->response_size_ += static_cast<std::size_t>(event->data_len);
        self->response_[self->response_size_] = '\0';
    }
    return ESP_OK;
}
}  // namespace veetee::ota
