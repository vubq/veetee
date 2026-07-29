#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "esp_http_client.h"
#include "esp_partition.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "ota/firmware_manifest.h"
#include "ota/firmware_ota_recovery_policy.h"
#include "settings/firmware_ota_attempt_record.h"
#include "settings/settings_store.h"
#include "nvs.h"

namespace veetee::ota {

enum class FirmwareOtaEvent : std::uint8_t {
    kChecking,
    kDownloading,
    kVerifying,
    kStaged,
    kRebooting,
    kActive,
    kFailed,
    kRolledBack,
};

enum class FirmwareScheduleResult : std::uint8_t {
    kScheduled,
    kAlreadyCurrent,
    kRejected,
};

struct FirmwareOtaNotification {
    FirmwareOtaEvent event = FirmwareOtaEvent::kChecking;
    char desired_version[33] = {};
    char current_version[33] = {};
    char error_code[33] = {};
    std::uint32_t expected_bytes = 0;
    std::uint32_t downloaded_bytes = 0;
    std::uint32_t security_epoch = 0;
    std::uint8_t active_slot = 0;
    std::uint8_t target_slot = 0;
};

class FirmwareUpdater {
public:
    using EventSink = bool (*)(const FirmwareOtaNotification& notification,
                               void* context);
    ~FirmwareUpdater();

    esp_err_t Initialize(settings::SettingsStore* settings_store, EventSink sink,
                         void* context);
    FirmwareScheduleResult Schedule(const char* desired_version,
                                    const char* manifest_url);
    void Cancel();
    [[nodiscard]] bool PendingBootVerification() const;
    [[nodiscard]] std::uint8_t ActiveSlot() const;
    [[nodiscard]] bool HasAttempt() const;
    [[nodiscard]] bool HasStagedOwnership() const;
    FirmwareOtaRecoveryDecision RecoveryStatus(
        FirmwareOtaNotification* notification) const;
    esp_err_t CommitStaged();
    esp_err_t MarkAttemptPendingHealth();
    esp_err_t MarkAttemptActive();
    esp_err_t MarkAttemptRollbackRequested(const char* error_code);
    esp_err_t RestoreAttemptPendingHealth();
    esp_err_t MarkRecoveredOutcome(FirmwareOtaRecoveryDecision decision,
                                   const char* error_code = nullptr);
    esp_err_t ClearCompletedAttempt();
    esp_err_t ConfirmPendingBoot();
    esp_err_t RollbackPendingBoot();
    esp_err_t CancelStagedBoot();

private:
    struct Target {
        std::uint32_t generation = 0;
        char desired_version[33] = {};
        char manifest_url[257] = {};
    };

    struct StagedImage {
        const esp_partition_t* partition = nullptr;
        std::uint32_t generation = 0;
        std::uint32_t attempt_id = 0;
        bool boot_committed = false;
    };

    static void TaskEntry(void* context);
    static esp_err_t HttpEventHandler(esp_http_client_event_t* event);
    void TaskLoop();
    void Reconcile(const Target& target);
    esp_err_t FetchManifest(const Target& target);
    esp_err_t Download(const Target& target, const VerifiedFirmwareManifest& manifest);
    esp_err_t PersistSecurityEpoch(std::uint32_t epoch);
    esp_err_t SaveAttemptLocked(
        const settings::FirmwareOtaAttemptRecord& record);
    esp_err_t AdvanceAttemptLocked(settings::FirmwareOtaAttemptPhase phase,
                                   const char* error_code = nullptr);
    esp_err_t BeginStagedAttemptLocked(
        const Target& target, const VerifiedFirmwareManifest& manifest,
        const esp_partition_t* update);
    esp_err_t RestoreRunningBootLocked();
    bool IsCurrent(std::uint32_t generation) const;
    bool Emit(FirmwareOtaEvent event, const Target& target,
              const VerifiedFirmwareManifest* manifest, const char* error,
              std::uint32_t downloaded_bytes = 0) const;

    settings::SettingsStore* settings_store_ = nullptr;
    EventSink sink_ = nullptr;
    void* sink_context_ = nullptr;
    QueueHandle_t queue_ = nullptr;
    TaskHandle_t task_ = nullptr;
    mutable SemaphoreHandle_t state_mutex_ = nullptr;
    std::atomic<std::uint32_t> generation_{0};
    char hardware_id_[18] = {};
    char* response_ = nullptr;
    std::size_t response_size_ = 0;
    bool response_overflow_ = false;
    Target current_target_{};
    nvs_handle_t nvs_handle_ = 0;
    std::uint32_t security_epoch_floor_ = 0;
    std::uint8_t target_slot_ = 0;
    settings::FirmwareOtaAttemptRecord attempt_record_{};
    StagedImage staged_image_{};
};

}  // namespace veetee::ota
