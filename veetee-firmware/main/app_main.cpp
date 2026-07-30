#include <algorithm>
#include <atomic>
#include <cinttypes>
#include <cctype>
#include <cstring>
#include <cstdio>
#include <cstdlib>

#include "app/state_machine.h"
#include "app/application_queue_policy.h"
#include "board/board_config.h"
#include "board/veetee_board.h"
#include "config/device_config_health_policy.h"
#include "config/device_config_reconciler.h"
#include "config/device_config_resource_policy.h"
#include "diagnostics/runtime_stats_sampler.h"
#include "esp_app_desc.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_psram.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "input/button.h"
#include "maintenance/maintenance_executor.h"
#include "mcp/device_mcp.h"
#include "network/wifi_manager.h"
#include "ota/bootstrap_client.h"
#include "ota/firmware_boot_health_policy.h"
#include "ota/firmware_updater.h"
#include "ota/resource_reconciler.h"
#include "settings/settings_store.h"
#include "settings/device_config_store.h"
#include "telemetry/reported_state_reporter.h"
#include "transport/websocket_transport.h"
#include "sdkconfig.h"

namespace {

constexpr char kTag[] = "veetee_app";
constexpr std::uint64_t kResourceApplyDelayUs = 250000;
constexpr std::uint64_t kResourceHealthWindowUs = 5000000;
constexpr std::int64_t kFirmwarePostWifiHealthDeadlineUs = 30000000;
constexpr std::uint64_t kFirmwareHealthPollUs = 500000;
constexpr std::uint8_t kFirmwareHealthTimerStartFailureLimit = 3;
constexpr std::uint64_t kDeviceConfigPollIntervalUs = 60000000;
constexpr std::uint32_t kProvisioningRetryDelayMs = 3000;

enum class AppMessageKind : std::uint8_t {
    kStateEvent,
    kMcpEnvelope,
    kDeviceConfigReconcile,
    kDeviceConfigInvalidated,
    kDeviceConfigPoll,
    kDeviceConfigHealthCheck,
    kResourceReconcile,
    kResourceApply,
    kResourceHealthCheck,
    kFirmwareReconcile,
    kFirmwareHealthCheck,
    kProvisioningCleanup,
    kRuntimeStats,
};

struct AppMessage {
    AppMessageKind kind = AppMessageKind::kStateEvent;
    veetee::app::Event event = veetee::app::Event::kBootNeedsProvisioning;
    char activation_code[7] = {};
    char* control_payload = nullptr;
    std::size_t control_length = 0;
    std::uint32_t conversation_generation = 0;
    std::uint32_t config_version = 0;
    char firmware_target_version[33] = {};
    char firmware_manifest_url[257] = {};
    veetee::ota::ResourceClass resource_class =
        veetee::ota::ResourceClass::kWakeModel;
    veetee::ota::ResourceReconcileNotification resource_notification{};
    veetee::ota::FirmwareOtaNotification firmware_notification{};
    veetee::config::DeviceConfigReconcileNotification config_notification{};
};

struct FirmwareTerminalOutcome {
    bool pending = false;
    bool staged_cancel_pending = false;
    bool attempt_marked = false;
    bool report_persisted = false;
    bool attempt_cleared = false;
    veetee::ota::FirmwareOtaRecoveryDecision decision =
        veetee::ota::FirmwareOtaRecoveryDecision::kNone;
    veetee::ota::FirmwareOtaNotification notification{};
    char error_code[33] = {};
};

QueueHandle_t g_event_queue = nullptr;
QueueHandle_t g_wake_event_queue = nullptr;
QueueHandle_t g_critical_event_queue = nullptr;
std::atomic<std::uint32_t> g_conversation_generation{0};
veetee::app::StateMachine g_state_machine;
veetee::board::VeeteeBoard g_board;
veetee::settings::SettingsStore g_settings_store;
veetee::settings::DeviceSettings g_settings;
veetee::settings::DeviceConfigStore g_device_config_store;
veetee::maintenance::MaintenanceExecutor g_maintenance;
veetee::diagnostics::RuntimeStatsSampler g_runtime_stats;
veetee::config::DeviceConfigReconciler g_device_config_reconciler;
veetee::config::DeviceConfig g_applied_device_config;
bool g_wake_audio_privacy_revoked = true;
bool g_applied_wake_restore_pending = false;
veetee::config::DeviceConfig g_pending_device_config;
char g_pending_device_config_etag[65] = {};
veetee::config::DeviceConfig g_health_checking_device_config;
char g_health_checking_device_config_etag[65] = {};
veetee::config::DeviceConfig g_resource_applying_device_config;
char g_resource_applying_device_config_etag[65] = {};
veetee::network::WifiManager g_wifi;
veetee::ota::BootstrapClient g_bootstrap;
veetee::ota::ResourceReconciler g_resources;
veetee::ota::ResourceReconciler g_ui_resources;
veetee::ota::FirmwareUpdater g_firmware;
veetee::telemetry::ReportedStateReporter g_reporter;
veetee::transport::WebSocketTransport g_transport;
veetee::mcp::DeviceMcp g_mcp;
esp_timer_handle_t g_resource_apply_timer = nullptr;
esp_timer_handle_t g_resource_health_timer = nullptr;
esp_timer_handle_t g_ui_apply_timer = nullptr;
esp_timer_handle_t g_ui_health_timer = nullptr;
esp_timer_handle_t g_firmware_health_timer = nullptr;
esp_timer_handle_t g_device_config_poll_timer = nullptr;
esp_timer_handle_t g_device_config_health_timer = nullptr;
#if CONFIG_VEETEE_BENCHMARK_RUNTIME_STATS
esp_timer_handle_t g_runtime_stats_timer = nullptr;
#endif
std::atomic<bool> g_firmware_health_check_due{false};
std::atomic<bool> g_resource_health_check_due{false};
std::atomic<bool> g_ui_health_check_due{false};
std::atomic<bool> g_device_config_health_check_due{false};
std::int64_t g_firmware_boot_started_us = 0;
std::int64_t g_firmware_health_overall_deadline_us = 0;
std::int64_t g_firmware_health_post_wifi_deadline_us = 0;
std::int64_t g_firmware_health_fallback_poll_us = 0;
std::uint8_t g_firmware_health_timer_start_failures = 0;
bool g_firmware_health_monitoring = false;
bool g_firmware_pending_health_attempt_marked = false;
bool g_firmware_pending_health_report_persisted = false;
veetee::ota::FirmwareOtaNotification g_firmware_health_notification{};
FirmwareTerminalOutcome g_firmware_terminal_outcome{};
bool g_authenticated_bootstrap_complete = false;
bool g_resource_apply_pending = false;
bool g_ui_apply_pending = false;
bool g_device_config_apply_pending = false;
bool g_device_config_health_pending = false;
bool g_device_config_loaded_with_resource = false;
bool g_device_config_invalidation_pending = false;
std::uint32_t g_invalidated_device_config_version = 0;

void ScheduleResourceApply();

bool IsRealtimeConversationState(veetee::app::State state) {
    using State = veetee::app::State;
    switch (state) {
        case State::kConnecting:
        case State::kListening:
        case State::kEvaluating:
        case State::kThinking:
        case State::kSpeaking:
        case State::kAborting:
        case State::kClosing:
            return true;
        default:
            return false;
    }
}

bool PostMessage(const AppMessage& message) {
    const veetee::app::ApplicationQueueLane lane =
        message.kind == AppMessageKind::kStateEvent
            ? veetee::app::ApplicationQueueForEvent(message.event)
            : veetee::app::ApplicationQueueLane::kRegular;
    if (lane == veetee::app::ApplicationQueueLane::kWake ||
        lane == veetee::app::ApplicationQueueLane::kCriticalControl) {
        QueueHandle_t queue =
            lane == veetee::app::ApplicationQueueLane::kCriticalControl
                ? g_critical_event_queue
                : g_wake_event_queue;
        if (queue == nullptr) return false;
        if (xQueueSend(queue, &message.event, 0) == pdTRUE) {
            return true;
        }
        // The queue is deliberately bounded. Preserve the newest physical
        // control without allowing repeated activation wakes to evict a
        // button/interrupt event, which has its own higher-priority lane.
        veetee::app::Event displaced{};
        if (xQueueReceive(queue, &displaced, 0) == pdTRUE &&
            xQueueSend(queue, &message.event, 0) == pdTRUE) {
            return true;
        }
        ESP_LOGW(kTag, "Dropping urgent conversation control: queue unavailable");
        return false;
    }
    if (g_event_queue == nullptr ||
        xQueueSend(g_event_queue, &message, 0) != pdTRUE) {
        if (message.kind == AppMessageKind::kMcpEnvelope) {
            ESP_LOGW(kTag, "Dropping MCP request: application queue full");
        } else if (message.kind == AppMessageKind::kDeviceConfigReconcile ||
                   message.kind == AppMessageKind::kDeviceConfigInvalidated ||
                   message.kind == AppMessageKind::kDeviceConfigPoll ||
                   message.kind == AppMessageKind::kDeviceConfigHealthCheck) {
            ESP_LOGW(kTag,
                     "Dropping device-config reconcile result: application queue full");
        } else if (message.kind == AppMessageKind::kResourceReconcile ||
                   message.kind == AppMessageKind::kResourceApply ||
                   message.kind == AppMessageKind::kResourceHealthCheck) {
            ESP_LOGW(kTag, "Dropping resource reconcile result: application queue full");
        } else {
            ESP_LOGW(kTag, "Dropping event %s: application queue full",
                     veetee::app::ToString(message.event));
        }
        return false;
    }
    return true;
}

void OnResourceApplyTimer(void*) {
    if (!PostMessage(AppMessage{
            .kind = AppMessageKind::kResourceApply,
            .resource_class = veetee::ota::ResourceClass::kWakeModel}) &&
        g_resource_apply_timer != nullptr) {
        esp_timer_start_once(g_resource_apply_timer, kResourceApplyDelayUs);
    }
}

void OnResourceHealthTimer(void*) {
    g_resource_health_check_due.store(true, std::memory_order_release);
    if (!PostMessage(AppMessage{
            .kind = AppMessageKind::kResourceHealthCheck,
            .resource_class = veetee::ota::ResourceClass::kWakeModel}) &&
        g_resource_health_timer != nullptr) {
        const esp_err_t error = esp_timer_start_once(
            g_resource_health_timer, kResourceApplyDelayUs);
        if (error != ESP_OK) {
            ESP_LOGE(kTag, "Unable to retry wake-resource health delivery: %s",
                     esp_err_to_name(error));
        }
    }
}

void OnUiApplyTimer(void*) {
    if (!PostMessage(AppMessage{
            .kind = AppMessageKind::kResourceApply,
            .resource_class = veetee::ota::ResourceClass::kUiPack}) &&
        g_ui_apply_timer != nullptr) {
        esp_timer_start_once(g_ui_apply_timer, kResourceApplyDelayUs);
    }
}

void OnUiHealthTimer(void*) {
    g_ui_health_check_due.store(true, std::memory_order_release);
    if (!PostMessage(AppMessage{
            .kind = AppMessageKind::kResourceHealthCheck,
            .resource_class = veetee::ota::ResourceClass::kUiPack}) &&
        g_ui_health_timer != nullptr) {
        const esp_err_t error = esp_timer_start_once(
            g_ui_health_timer, kResourceApplyDelayUs);
        if (error != ESP_OK) {
            ESP_LOGE(kTag, "Unable to retry UI health delivery: %s",
                     esp_err_to_name(error));
        }
    }
}

void OnFirmwareHealthTimer(void*) {
    // The application loop observes this flag before blocking on its regular
    // queue.  A full queue therefore cannot strand a pending-verify image.
    g_firmware_health_check_due.store(true);
}

void OnDeviceConfigPollTimer(void*) {
    if (!PostMessage(AppMessage{.kind = AppMessageKind::kDeviceConfigPoll}) &&
        g_device_config_poll_timer != nullptr) {
        esp_timer_start_once(g_device_config_poll_timer,
                             kResourceApplyDelayUs);
    }
}

void OnDeviceConfigHealthTimer(void*) {
    g_device_config_health_check_due.store(true, std::memory_order_release);
    if (!PostMessage(
            AppMessage{.kind = AppMessageKind::kDeviceConfigHealthCheck}) &&
        g_device_config_health_timer != nullptr) {
        const esp_err_t error = esp_timer_start_once(
            g_device_config_health_timer, kResourceApplyDelayUs);
        if (error != ESP_OK) {
            ESP_LOGE(kTag, "Unable to retry config health delivery: %s",
                     esp_err_to_name(error));
        }
    }
}

#if CONFIG_VEETEE_BENCHMARK_RUNTIME_STATS
void OnRuntimeStatsTimer(void*) {
    PostMessage(AppMessage{.kind = AppMessageKind::kRuntimeStats});
}
#endif

bool SamePartition(const char* left, const char* right) {
    return left != nullptr && right != nullptr && std::strcmp(left, right) == 0;
}

veetee::config::DeviceConfigResourceLinkError DeviceConfigResourceLink(
    const veetee::config::DeviceConfig& config, const char* resource_version,
    const veetee::settings::ResourceDetectorInventory& inventory) {
    return veetee::config::ValidateDeviceConfigResourceLink(
        config, resource_version, inventory);
}

bool DeviceConfigMatchesResource(
    const veetee::config::DeviceConfig& config, const char* resource_version,
    const veetee::settings::ResourceDetectorInventory& inventory) {
    return DeviceConfigResourceLink(config, resource_version, inventory) ==
           veetee::config::DeviceConfigResourceLinkError::kOk;
}

bool DeviceConfigMatchesActiveResource(
    const veetee::config::DeviceConfig& config) {
    const veetee::settings::ResourceRecord resource = g_resources.Snapshot();
    return DeviceConfigMatchesResource(
        config, resource.active_version, resource.active_detectors);
}

veetee::config::DeviceConfig ButtonOnlyRuntimeConfig(
    const veetee::config::DeviceConfig& persisted) {
    veetee::config::DeviceConfig runtime = persisted;
    runtime.has_wake_profile = false;
    runtime.wake_profile_id = {};
    runtime.wake_profile_version = 0;
    runtime.required_resource_version = {};
    runtime.activation = {};
    runtime.interrupt = {};
    runtime.send_wake_audio = false;
    return runtime;
}

esp_err_t ObserveVerifiedWakeAudioConfig(
    const veetee::config::DeviceConfig& config) {
    g_wake_audio_privacy_revoked =
        veetee::config::NextWakeAudioPrivacyRevoked(
            g_wake_audio_privacy_revoked, config.send_wake_audio, false);
    if (config.send_wake_audio) return ESP_OK;

    const bool runtime_revoked = g_board.RevokeWakeAudioConsent();
    if (!runtime_revoked) {
        ESP_LOGE(kTag, "Unable to revoke wake-audio consent");
    }
    const esp_err_t persist_error =
        g_device_config_store.PersistWakeAudioPrivacyRevocation();
    if (persist_error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to persist wake-audio privacy revocation: %s",
                 esp_err_to_name(persist_error));
        return persist_error;
    }
    return runtime_revoked ? ESP_OK : ESP_FAIL;
}

void ConfirmAppliedWakeAudioConfig(
    const veetee::config::DeviceConfig& config) {
    g_wake_audio_privacy_revoked =
        g_device_config_store.WakeAudioPrivacyRevoked() ||
        veetee::config::NextWakeAudioPrivacyRevoked(
            g_wake_audio_privacy_revoked, config.send_wake_audio, true);
}

bool CommittedWakeAudioRuntimeAllowed(
    const veetee::config::DeviceConfig& config) {
    return veetee::config::WakeAudioCommittedRuntimeAllowed(
        config.send_wake_audio, g_wake_audio_privacy_revoked,
        g_device_config_store.WakeAudioPrivacyRevoked());
}

bool CommittedWakeAudioRuntimeMatches(
    const veetee::config::DeviceConfig& config) {
    return CommittedWakeAudioRuntimeAllowed(config) &&
           g_board.WakeAudioConsentMatches(config.version,
                                           config.send_wake_audio);
}

bool FinalizeCommittedWakeAudioRuntime(
    const veetee::config::DeviceConfig& config) {
    if (!CommittedWakeAudioRuntimeAllowed(config)) return false;
    if (!config.send_wake_audio) {
        return CommittedWakeAudioRuntimeMatches(config);
    }
    const bool runtime_enabled =
        g_board.EnableWakeAudioConsentAfterCommit(config.version) &&
        CommittedWakeAudioRuntimeMatches(config);
    if (!veetee::config::WakeAudioCommitRequiresRevocation(
            config.send_wake_audio, runtime_enabled)) {
        return true;
    }

    ESP_LOGE(kTag,
             "Unable to enable committed wake-audio consent version=%" PRIu32
             "; restoring durable privacy revocation",
             config.version);
    g_wake_audio_privacy_revoked = true;
    if (!g_board.RevokeWakeAudioConsent()) {
        ESP_LOGE(kTag,
                 "Unable to revoke wake-audio consent after enable failure");
    }
    const esp_err_t persist_error =
        g_device_config_store.PersistWakeAudioPrivacyRevocation();
    if (persist_error != ESP_OK) {
        ESP_LOGE(kTag,
                 "Unable to restore durable wake-audio revocation after enable failure: %s",
                 esp_err_to_name(persist_error));
    }
    return false;
}

veetee::config::DeviceConfig PrivacySafeRuntimeConfig(
    const veetee::config::DeviceConfig& config) {
    veetee::config::DeviceConfig runtime = config;
    runtime.send_wake_audio = veetee::config::EffectiveSendWakeAudio(
        runtime.send_wake_audio, g_wake_audio_privacy_revoked);
    return runtime;
}

struct BootWakeRuntimePlan {
    veetee::config::DeviceConfig config{};
    const char* active_partition = nullptr;
    const char* fallback_partition = nullptr;
    bool resource_version_mismatch = false;
    veetee::config::DeviceConfigResourceLinkError link_error =
        veetee::config::DeviceConfigResourceLinkError::kOk;
};

void CopyErrorCode(char* destination, std::size_t capacity,
                   const char* source) {
    if (destination == nullptr || capacity == 0) return;
    std::size_t output = 0;
    for (const char* cursor = source == nullptr ? "unknown" : source;
         *cursor != '\0' && output + 1 < capacity; ++cursor) {
        const unsigned char character = static_cast<unsigned char>(*cursor);
        if (std::isalnum(character) != 0) {
            destination[output++] = static_cast<char>(std::tolower(character));
        } else if (*cursor == '.' || *cursor == '_' || *cursor == '-') {
            destination[output++] = *cursor;
        } else {
            destination[output++] = '_';
        }
    }
    destination[output] = '\0';
}

bool ScheduleResourceReport(
    veetee::settings::ReportedResourcePhase phase,
    const veetee::settings::ResourceRecord& current,
    const char* desired_override = nullptr, const char* error_code = nullptr,
    const veetee::settings::ResourceRecord* operation = nullptr,
    veetee::settings::ReportedArtifactKind artifact_kind =
        veetee::settings::ReportedArtifactKind::kWakeResource) {
    const auto& source = operation == nullptr ? current : *operation;
    veetee::settings::ReportedResourceState report{};
    report.phase = phase;
    report.artifact_kind = artifact_kind;
    report.active_slot = current.active_slot;
    report.target_slot = phase == veetee::settings::ReportedResourcePhase::kActive
                             ? current.active_slot
                             : source.target_slot;
    report.expected_bytes = source.expected_bytes;
    report.downloaded_bytes = source.downloaded_bytes;
    report.security_epoch = source.desired_security_epoch != 0
                                ? source.desired_security_epoch
                                : current.active_security_epoch;
    std::snprintf(report.current_version, sizeof(report.current_version), "%s",
                  current.active_version);
    const char* desired = desired_override != nullptr && desired_override[0] != '\0'
                              ? desired_override
                              : source.desired_version[0] != '\0'
                                    ? source.desired_version
                                    : current.active_version;
    std::snprintf(report.desired_version, sizeof(report.desired_version), "%s",
                  desired);
    if (phase == veetee::settings::ReportedResourcePhase::kFailed ||
        phase == veetee::settings::ReportedResourcePhase::kRolledBack) {
        CopyErrorCode(report.error_code, sizeof(report.error_code), error_code);
    }
    const bool queued = g_reporter.Schedule(report);
    if (!queued) {
        ESP_LOGW(kTag, "Unable to queue resource report phase=%s",
                 veetee::settings::ReportedResourcePhaseName(phase));
    }
    return queued;
}

bool ScheduleResourceNotificationReport(
    veetee::settings::ReportedResourcePhase phase,
    const veetee::ota::ResourceReconcileNotification& notification,
    const char* error_code = nullptr) {
    const bool is_ui = notification.resource_class ==
                       veetee::ota::ResourceClass::kUiPack;
    veetee::ota::ResourceReconciler& reconciler =
        is_ui ? g_ui_resources : g_resources;
    veetee::settings::ResourceRecord current = reconciler.Snapshot();
    veetee::settings::ResourceRecord operation = current;
    operation.active_slot = notification.active_slot;
    operation.target_slot = notification.target_slot;
    operation.expected_bytes = notification.expected_bytes;
    operation.downloaded_bytes = notification.downloaded_bytes;
    operation.desired_security_epoch = notification.security_epoch;
    if (notification.current_version[0] != '\0') {
        std::snprintf(current.active_version, sizeof(current.active_version), "%s",
                      notification.current_version);
    }
    return ScheduleResourceReport(phase, current,
                                  notification.desired_version, error_code,
                                  &operation,
                                  is_ui
                                      ? veetee::settings::ReportedArtifactKind::kUiPack
                                      : veetee::settings::ReportedArtifactKind::kWakeResource);
}

veetee::settings::ReportedResourceState MakeFirmwareReport(
    veetee::settings::ReportedResourcePhase phase,
    const veetee::ota::FirmwareOtaNotification& notification,
    const char* error_code = nullptr) {
    veetee::settings::ReportedResourceState report{};
    report.phase = phase;
    report.artifact_kind = veetee::settings::ReportedArtifactKind::kFirmware;
    report.active_slot = notification.active_slot;
    report.target_slot = notification.target_slot;
    report.expected_bytes = notification.expected_bytes;
    report.downloaded_bytes = notification.downloaded_bytes;
    report.security_epoch = notification.security_epoch;
    std::snprintf(report.current_version, sizeof(report.current_version), "%s",
                  notification.current_version[0] != '\0'
                      ? notification.current_version
                      : CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION);
    std::snprintf(report.desired_version, sizeof(report.desired_version), "%s",
                  notification.desired_version[0] != '\0'
                      ? notification.desired_version
                      : CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION);
    if (phase == veetee::settings::ReportedResourcePhase::kFailed ||
        phase == veetee::settings::ReportedResourcePhase::kRolledBack) {
        CopyErrorCode(report.error_code, sizeof(report.error_code),
                      error_code == nullptr ? notification.error_code : error_code);
    }
    return report;
}

bool ScheduleFirmwareReport(
    veetee::settings::ReportedResourcePhase phase,
    const veetee::ota::FirmwareOtaNotification& notification,
    const char* error_code = nullptr) {
    return g_reporter.Schedule(
        MakeFirmwareReport(phase, notification, error_code));
}

bool ScheduleDeviceConfigReport(
    veetee::settings::ReportedResourcePhase phase,
    std::uint32_t desired_version, std::uint32_t applied_version,
    const char* error_code = nullptr) {
    veetee::settings::ReportedResourceState report{};
    report.phase = phase;
    report.artifact_kind =
        veetee::settings::ReportedArtifactKind::kDeviceConfig;
    const int applied_length = std::snprintf(
        report.current_version, sizeof(report.current_version), "%" PRIu32,
        applied_version);
    const int desired_length = std::snprintf(
        report.desired_version, sizeof(report.desired_version), "%" PRIu32,
        desired_version);
    if (applied_length <= 0 ||
        applied_length >= static_cast<int>(sizeof(report.current_version)) ||
        desired_length <= 0 ||
        desired_length >= static_cast<int>(sizeof(report.desired_version))) {
        return false;
    }
    if (phase == veetee::settings::ReportedResourcePhase::kFailed ||
        phase == veetee::settings::ReportedResourcePhase::kRolledBack) {
        CopyErrorCode(report.error_code, sizeof(report.error_code), error_code);
    }
    const bool queued = g_reporter.Schedule(report);
    if (!queued) {
        ESP_LOGW(kTag,
                 "Unable to queue device-config report desired=%" PRIu32
                 " applied=%" PRIu32,
                 desired_version, applied_version);
    }
    return queued;
}

BootWakeRuntimePlan PrepareBootWakeRuntime() {
    using Decision =
        veetee::config::DeviceConfigBootResourceDecision;
    veetee::settings::ResourceRecord resource = g_resources.Snapshot();
    const bool active_matches = DeviceConfigMatchesResource(
        g_applied_device_config, resource.active_version,
        resource.active_detectors);
    const bool previous_matches = DeviceConfigMatchesResource(
        g_applied_device_config, resource.previous_version,
        resource.previous_detectors);
    const bool transaction_pending =
        resource.phase != veetee::settings::ResourceRecordPhase::kStable;
    const Decision decision = veetee::config::DecideDeviceConfigBootResource(
        g_applied_device_config.has_wake_profile, active_matches,
        previous_matches, transaction_pending,
        resource.active_slot != resource.previous_slot);

    if (decision == Decision::kRollbackToMatchingPrevious ||
        decision == Decision::kRollbackTransactionButtonOnly) {
        const veetee::settings::ResourceRecord attempted = resource;
        const esp_err_t error = g_resources.Rollback();
        if (error == ESP_OK) {
            resource = g_resources.Snapshot();
            const char* desired = attempted.desired_version[0] != '\0'
                                      ? attempted.desired_version
                                      : attempted.active_version;
            ScheduleResourceReport(
                veetee::settings::ReportedResourcePhase::kRolledBack,
                resource, desired, "config_resource_mismatch", &attempted);
            ESP_LOGW(kTag,
                     "Terminated boot resource transaction for config/resource mismatch active=%s",
                     resource.active_version);
        } else {
            ESP_LOGE(kTag,
                     "Unable to terminate mismatched boot resource transaction: %s; continuing button-only",
                     esp_err_to_name(error));
        }
    }

    BootWakeRuntimePlan plan{};
    resource = g_resources.Snapshot();
    plan.link_error = DeviceConfigResourceLink(
        g_applied_device_config, resource.active_version,
        resource.active_detectors);
    const bool selected_matches =
        plan.link_error ==
        veetee::config::DeviceConfigResourceLinkError::kOk;
    plan.resource_version_mismatch =
        g_applied_device_config.has_wake_profile && !selected_matches;
    plan.config = PrivacySafeRuntimeConfig(
        plan.resource_version_mismatch
            ? ButtonOnlyRuntimeConfig(g_applied_device_config)
            : g_applied_device_config);
    if (plan.resource_version_mismatch) {
        ESP_LOGE(kTag,
                 "Applied config cannot link wake resource required=%s active=%s error=%s; booting button-only until authenticated reconcile",
                 g_applied_device_config.required_resource_version.data(),
                 resource.active_version,
                 veetee::config::DeviceConfigResourceLinkErrorName(
                     plan.link_error));
        return plan;
    }

    const bool runtime_uses_resource =
        plan.config.version == 0 || plan.config.has_wake_profile;
    if (!runtime_uses_resource) return plan;
    if (plan.config.version == 0 &&
        (std::strcmp(resource.active_version, "factory-bringup") != 0 ||
         !veetee::settings::ResourceDetectorInventoryMatches(
             resource.active_detectors, "wn9s_hiesp", nullptr))) {
        ESP_LOGW(kTag,
                 "Factory config cannot prove factory detector inventory; booting button-only");
        return plan;
    }
    plan.active_partition = g_resources.ActivePartitionLabel();
    const bool previous_runtime_matches =
        plan.config.version == 0
            ? std::strcmp(resource.previous_version, "factory-bringup") == 0 &&
                  veetee::settings::ResourceDetectorInventoryMatches(
                      resource.previous_detectors, "wn9s_hiesp", nullptr)
            : DeviceConfigMatchesResource(
                  plan.config, resource.previous_version,
                  resource.previous_detectors);
    if (previous_runtime_matches) {
        plan.fallback_partition = g_resources.PreviousPartitionLabel();
    }
    return plan;
}

void ClearPendingDeviceConfig() {
    g_pending_device_config = veetee::config::DeviceConfig{};
    std::fill(std::begin(g_pending_device_config_etag),
              std::end(g_pending_device_config_etag), '\0');
    g_device_config_apply_pending = false;
}

void ClearDeviceConfigHealthTransaction() {
    if (g_device_config_health_timer != nullptr) {
        esp_timer_stop(g_device_config_health_timer);
    }
    g_device_config_health_check_due.store(false, std::memory_order_release);
    g_health_checking_device_config = veetee::config::DeviceConfig{};
    std::fill(std::begin(g_health_checking_device_config_etag),
              std::end(g_health_checking_device_config_etag), '\0');
    g_device_config_health_pending = false;
}

void ClearResourceApplyingDeviceConfig() {
    g_resource_applying_device_config = veetee::config::DeviceConfig{};
    std::fill(std::begin(g_resource_applying_device_config_etag),
              std::end(g_resource_applying_device_config_etag), '\0');
    g_device_config_loaded_with_resource = false;
}

void FailPendingDeviceConfig(const char* error_code) {
    if (!g_device_config_apply_pending) return;
    ScheduleDeviceConfigReport(
        veetee::settings::ReportedResourcePhase::kFailed,
        g_pending_device_config.version,
        g_device_config_store.Snapshot().applied_version, error_code);
    ClearPendingDeviceConfig();
}

void FailResourceApplyingDeviceConfig(const char* error_code) {
    if (!g_device_config_loaded_with_resource) return;
    const std::uint32_t failed_version =
        g_resource_applying_device_config.version;
    char failed_etag[65] = {};
    std::snprintf(failed_etag, sizeof(failed_etag), "%s",
                  g_resource_applying_device_config_etag);
    ScheduleDeviceConfigReport(
        veetee::settings::ReportedResourcePhase::kFailed, failed_version,
        g_device_config_store.Snapshot().applied_version, error_code);
    ClearResourceApplyingDeviceConfig();
    if (g_device_config_apply_pending &&
        g_pending_device_config.version == failed_version &&
        std::strcmp(g_pending_device_config_etag, failed_etag) == 0) {
        ClearPendingDeviceConfig();
    }
}

void RestoreAppliedWakeRuntime(const char* partition_label) {
    const auto resource = g_resources.Snapshot();
    const bool resource_matches = DeviceConfigMatchesResource(
        g_applied_device_config, resource.active_version,
        resource.active_detectors);
    const veetee::config::DeviceConfig selected =
        resource_matches ? g_applied_device_config
                         : ButtonOnlyRuntimeConfig(g_applied_device_config);
    const veetee::config::DeviceConfig runtime =
        PrivacySafeRuntimeConfig(selected);
    const esp_err_t restore = g_board.ReloadWakeRuntime(partition_label, runtime);
    if (restore != ESP_OK) {
        ESP_LOGE(kTag,
                 "Unable to restore applied wake config on %s: %s; button wake remains available",
                 partition_label == nullptr ? "none" : partition_label,
                 esp_err_to_name(restore));
    }
}

void TryRestoreAppliedWakeRuntimeAfterReconcile() {
    if (!g_applied_wake_restore_pending ||
        !g_applied_device_config.has_wake_profile ||
        g_state_machine.state() != veetee::app::State::kIdle) {
        return;
    }
    if (!CommittedWakeAudioRuntimeAllowed(g_applied_device_config)) {
        g_applied_wake_restore_pending = false;
        ScheduleDeviceConfigReport(
            veetee::settings::ReportedResourcePhase::kFailed,
            g_applied_device_config.version,
            g_device_config_store.Snapshot().applied_version,
            "wake_audio_enable_failed");
        return;
    }
    const auto resource = g_resources.Snapshot();
    const auto link = DeviceConfigResourceLink(
        g_applied_device_config, resource.active_version,
        resource.active_detectors);
    if (link != veetee::config::DeviceConfigResourceLinkError::kOk) {
        return;
    }
    const char* active_partition = g_resources.ActivePartitionLabel();
    const bool detector_ready =
        g_board.wake_task_expected() && g_board.WakeResourceHealthy() &&
        SamePartition(g_board.loaded_wake_partition(), active_partition);
    if (detector_ready &&
        g_board.WakeRuntimeConfigVersionMatches(
            g_applied_device_config.version) &&
        (CommittedWakeAudioRuntimeMatches(g_applied_device_config) ||
         FinalizeCommittedWakeAudioRuntime(g_applied_device_config))) {
        g_applied_wake_restore_pending = false;
        return;
    }
    if (!CommittedWakeAudioRuntimeAllowed(g_applied_device_config)) {
        g_applied_wake_restore_pending = false;
        ScheduleDeviceConfigReport(
            veetee::settings::ReportedResourcePhase::kFailed,
            g_applied_device_config.version,
            g_device_config_store.Snapshot().applied_version,
            "wake_audio_enable_failed");
        return;
    }
    const esp_err_t error = g_board.ReloadWakeRuntime(
        active_partition, PrivacySafeRuntimeConfig(g_applied_device_config));
    const bool detector_healthy =
        error == ESP_OK && g_board.wake_task_expected() &&
        g_board.WakeResourceHealthy() &&
        SamePartition(g_board.loaded_wake_partition(), active_partition);
    const bool healthy =
        detector_healthy &&
        FinalizeCommittedWakeAudioRuntime(g_applied_device_config) &&
        CommittedWakeAudioRuntimeMatches(g_applied_device_config);
    if (healthy) {
        g_applied_wake_restore_pending = false;
        ESP_LOGI(kTag,
                 "Restored applied wake config=%" PRIu32
                 " after signed detector inventory reconcile",
                 g_applied_device_config.version);
        ScheduleDeviceConfigReport(
            veetee::settings::ReportedResourcePhase::kActive,
            g_applied_device_config.version,
            g_device_config_store.Snapshot().applied_version);
    } else if (!CommittedWakeAudioRuntimeAllowed(
                   g_applied_device_config)) {
        g_applied_wake_restore_pending = false;
        ScheduleDeviceConfigReport(
            veetee::settings::ReportedResourcePhase::kFailed,
            g_applied_device_config.version,
            g_device_config_store.Snapshot().applied_version,
            "wake_audio_enable_failed");
    } else {
        ESP_LOGE(kTag,
                 "Unable to restore applied wake runtime after inventory reconcile: %s",
                 error == ESP_OK ? "runtime_unhealthy"
                                 : esp_err_to_name(error));
    }
}

bool PendingDeviceConfigIsApplicable() {
    if (!g_device_config_apply_pending) return false;
    const veetee::settings::DeviceSettings settings_snapshot =
        g_settings_store.Snapshot();
    const veetee::settings::DeviceConfigRecord applied =
        g_device_config_store.Snapshot();
    if (g_pending_device_config.version != settings_snapshot.config_version ||
        g_pending_device_config.version < applied.applied_version) {
        FailPendingDeviceConfig("stale_result");
        return false;
    }
    if (g_pending_device_config.version == applied.applied_version) {
        const bool exact =
            std::strcmp(g_pending_device_config_etag, applied.etag) == 0;
        ScheduleDeviceConfigReport(
            exact ? veetee::settings::ReportedResourcePhase::kActive
                  : veetee::settings::ReportedResourcePhase::kFailed,
            g_pending_device_config.version, applied.applied_version,
            exact ? nullptr : "immutable_version");
        ClearPendingDeviceConfig();
        return false;
    }
    return true;
}

void TryApplyPendingDeviceConfig() {
    if (!g_device_config_apply_pending ||
        g_device_config_health_pending ||
        g_device_config_loaded_with_resource ||
        g_state_machine.state() != veetee::app::State::kIdle) {
        return;
    }
    if (!PendingDeviceConfigIsApplicable()) return;
    const auto resource = g_resources.Snapshot();
    const auto resource_link = DeviceConfigResourceLink(
        g_pending_device_config, resource.active_version,
        resource.active_detectors);
    const auto apply_decision =
        veetee::config::DecideDeviceConfigResourceApply(
            resource_link,
            veetee::settings::HasResourceDetectorInventory(
                resource.active_detectors));
    if (apply_decision ==
        veetee::config::DeviceConfigResourceApplyDecision::kWaitForResource) {
        // The matching signed resource may still be downloading/staged. The
        // resource callback will retry this apply at the same idle boundary.
        // A V1 resource record deliberately has unknown inventory until its
        // authenticated signed manifest is reconciled.
        return;
    }
    if (apply_decision ==
        veetee::config::DeviceConfigResourceApplyDecision::kReject) {
        const char* error_code =
            veetee::config::DeviceConfigResourceLinkErrorName(resource_link);
        ESP_LOGE(kTag,
                 "Rejecting config desired=%" PRIu32
                 " because signed detector inventory does not link: %s",
                 g_pending_device_config.version, error_code);
        FailPendingDeviceConfig(error_code);
        return;
    }

    ScheduleDeviceConfigReport(
        veetee::settings::ReportedResourcePhase::kApplying,
        g_pending_device_config.version,
        g_device_config_store.Snapshot().applied_version);
    const char* active_partition = g_resources.ActivePartitionLabel();
    const veetee::config::DeviceConfig candidate_runtime =
        PrivacySafeRuntimeConfig(g_pending_device_config);
    esp_err_t error =
        g_board.ApplyDeviceConfig(candidate_runtime, active_partition);
    if (error != ESP_OK || !g_board.WakeResourceHealthy()) {
        ESP_LOGE(kTag, "Device config apply failed desired=%" PRIu32 ": %s",
                 g_pending_device_config.version,
                 error == ESP_OK ? "runtime_unhealthy"
                                 : esp_err_to_name(error));
        RestoreAppliedWakeRuntime(active_partition);
        FailPendingDeviceConfig(error == ESP_ERR_NOT_FOUND
                                    ? "model_not_found"
                                    : "apply_failed");
        return;
    }

    g_health_checking_device_config = g_pending_device_config;
    std::snprintf(g_health_checking_device_config_etag,
                  sizeof(g_health_checking_device_config_etag), "%s",
                  g_pending_device_config_etag);
    g_device_config_health_pending = true;
    g_device_config_health_check_due.store(false, std::memory_order_release);
    esp_timer_stop(g_device_config_health_timer);
    error = esp_timer_start_once(g_device_config_health_timer,
                                 kResourceHealthWindowUs);
    if (error != ESP_OK) {
        ESP_LOGE(kTag,
                 "Unable to schedule device-config health check desired=%" PRIu32
                 ": %s",
                 g_health_checking_device_config.version,
                 esp_err_to_name(error));
        RestoreAppliedWakeRuntime(active_partition);
        ClearDeviceConfigHealthTransaction();
        FailPendingDeviceConfig("health_timer_failed");
        return;
    }
    ESP_LOGI(kTag,
             "Device config pending health version=%" PRIu32 " window_ms=%u",
             g_health_checking_device_config.version,
             static_cast<unsigned>(kResourceHealthWindowUs / 1000U));
}

void CheckDeviceConfigHealth() {
    if (!g_device_config_health_pending) {
        g_device_config_health_check_due.store(false,
                                               std::memory_order_release);
        return;
    }
    const veetee::config::DeviceConfig checked =
        g_health_checking_device_config;
    char checked_etag[65] = {};
    std::snprintf(checked_etag, sizeof(checked_etag), "%s",
                  g_health_checking_device_config_etag);
    const char* active_partition = g_resources.ActivePartitionLabel();
    const bool runtime_healthy = g_board.WakeResourceHealthy();
    const auto resource = g_resources.Snapshot();
    const auto resource_link = DeviceConfigResourceLink(
        checked, resource.active_version, resource.active_detectors);
    const bool resource_version_matches =
        resource_link ==
        veetee::config::DeviceConfigResourceLinkError::kOk;
    const std::uint32_t current_config_version =
        g_settings_store.Snapshot().config_version;
    const auto decision = veetee::config::DecideDeviceConfigHealth({
        .transaction_pending = g_device_config_health_pending,
        .health_window_due =
            g_device_config_health_check_due.load(std::memory_order_acquire),
        .safe_boundary =
            g_state_machine.state() == veetee::app::State::kIdle &&
            !g_state_machine.assistant_gate_open(),
        .target_current = checked.version == current_config_version,
        .runtime_healthy = runtime_healthy,
        .partition_required = checked.has_wake_profile,
        .partition_matches = SamePartition(
            g_board.loaded_wake_partition(), active_partition),
        .resource_version_matches = resource_version_matches,
    });
    if (decision ==
        veetee::config::DeviceConfigHealthDecision::kWait) {
        return;
    }
    const bool health_confirmed =
        decision == veetee::config::DeviceConfigHealthDecision::kConfirm;
    esp_err_t error = health_confirmed
                          ? g_device_config_store.SaveApplied(
                                checked,
                                g_health_checking_device_config_etag)
                          : ESP_ERR_INVALID_STATE;
    if (error == ESP_OK) {
        ConfirmAppliedWakeAudioConfig(checked);
        g_applied_device_config = checked;
        const bool runtime_committed =
            FinalizeCommittedWakeAudioRuntime(checked);
        if (runtime_committed) {
            ESP_LOGI(
                kTag,
                "Device config health confirmed active=%" PRIu32 " wake=%s",
                checked.version,
                checked.has_wake_profile ? "enabled" : "disabled");
            ScheduleDeviceConfigReport(
                veetee::settings::ReportedResourcePhase::kActive,
                checked.version, checked.version);
        } else {
            ScheduleDeviceConfigReport(
                veetee::settings::ReportedResourcePhase::kFailed,
                checked.version, checked.version,
                "wake_audio_enable_failed");
        }
        ClearDeviceConfigHealthTransaction();
        if (g_device_config_apply_pending &&
            g_pending_device_config.version == checked.version &&
            std::strcmp(g_pending_device_config_etag,
                        checked_etag) == 0) {
            ClearPendingDeviceConfig();
        }
    } else {
        ESP_LOGE(kTag,
                 "Device config health failed desired=%" PRIu32 ": %s",
                 checked.version, esp_err_to_name(error));
        RestoreAppliedWakeRuntime(active_partition);
        ScheduleDeviceConfigReport(
            veetee::settings::ReportedResourcePhase::kFailed,
            checked.version,
            g_device_config_store.Snapshot().applied_version,
            health_confirmed
                ? "config_persist_failed"
                : resource_link !=
                          veetee::config::DeviceConfigResourceLinkError::kOk
                      ? veetee::config::DeviceConfigResourceLinkErrorName(
                            resource_link)
                      : checked.version == current_config_version
                            ? "health_check_failed"
                            : "superseded");
        ClearDeviceConfigHealthTransaction();
        if (g_device_config_apply_pending &&
            g_pending_device_config.version == checked.version &&
            std::strcmp(g_pending_device_config_etag,
                        checked_etag) == 0) {
            ClearPendingDeviceConfig();
        }
    }

    if (g_resource_apply_pending) ScheduleResourceApply();
    TryApplyPendingDeviceConfig();
}

void CancelDeviceConfigHealth() {
    if (!g_device_config_health_pending) return;
    RestoreAppliedWakeRuntime(g_resources.ActivePartitionLabel());
    ClearDeviceConfigHealthTransaction();
}

void ReconcileInvalidatedDeviceConfigAtBoundary() {
    if (!g_device_config_invalidation_pending ||
        g_state_machine.state() != veetee::app::State::kIdle ||
        g_state_machine.assistant_gate_open()) {
        return;
    }
    ESP_LOGI(kTag,
             "Reconciling config invalidation version=%" PRIu32
             " through authenticated bootstrap",
             g_invalidated_device_config_version);
    g_device_config_invalidation_pending = false;
    g_invalidated_device_config_version = 0;
    g_bootstrap.Start();
}

void ScheduleResourceApply() {
    if (!g_resource_apply_pending || g_resource_apply_timer == nullptr) return;
    esp_timer_stop(g_resource_apply_timer);
    const esp_err_t error =
        esp_timer_start_once(g_resource_apply_timer, kResourceApplyDelayUs);
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to schedule resource apply: %s",
                 esp_err_to_name(error));
    }
}

void ScheduleUiApply() {
    if (!g_ui_apply_pending || g_ui_apply_timer == nullptr) return;
    esp_timer_stop(g_ui_apply_timer);
    const esp_err_t error =
        esp_timer_start_once(g_ui_apply_timer, kResourceApplyDelayUs);
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to schedule UI apply: %s",
                 esp_err_to_name(error));
    }
}

void RollbackWakeResource(const char* fallback_partition,
                          const char* reason) {
    const veetee::settings::ResourceRecord attempted = g_resources.Snapshot();
    if (g_resource_health_timer != nullptr) {
        esp_timer_stop(g_resource_health_timer);
    }
    g_resource_health_check_due.store(false, std::memory_order_release);
    if (fallback_partition != nullptr) {
        const bool previous_partition = SamePartition(
            fallback_partition, g_resources.PreviousPartitionLabel());
        const char* fallback_version = previous_partition
                                           ? attempted.previous_version
                                           : attempted.active_version;
        const auto& fallback_detectors =
            previous_partition ? attempted.previous_detectors
                               : attempted.active_detectors;
        const veetee::config::DeviceConfig selected =
            DeviceConfigMatchesResource(g_applied_device_config,
                                        fallback_version,
                                        fallback_detectors)
                ? g_applied_device_config
                : ButtonOnlyRuntimeConfig(g_applied_device_config);
        const veetee::config::DeviceConfig runtime =
            PrivacySafeRuntimeConfig(selected);
        const esp_err_t reload_error = g_board.ReloadWakeRuntime(
            fallback_partition, runtime);
        if (reload_error != ESP_OK) {
            ESP_LOGE(kTag,
                     "Wake resource fallback %s failed: %s; button wake remains available",
                     fallback_partition, esp_err_to_name(reload_error));
        }
    }
    const esp_err_t rollback_error = g_resources.Rollback();
    if (rollback_error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to rollback resource state reason=%s: %s",
                 reason, esp_err_to_name(rollback_error));
    } else {
        ESP_LOGW(kTag, "Resource rolled back reason=%s active=%s", reason,
                 g_resources.ActivePartitionLabel());
        const char* desired = attempted.desired_version[0] != '\0'
                                  ? attempted.desired_version
                                  : attempted.active_version;
        ScheduleResourceReport(
            veetee::settings::ReportedResourcePhase::kRolledBack,
            g_resources.Snapshot(), desired, reason, &attempted);
    }
    if (g_device_config_loaded_with_resource) {
        FailResourceApplyingDeviceConfig(reason);
    }
}

void CancelDeviceConfigTransactions() {
    CancelDeviceConfigHealth();
    if (g_device_config_loaded_with_resource &&
        g_resources.phase() ==
            veetee::settings::ResourceRecordPhase::kPendingHealth) {
        RollbackWakeResource(g_resources.PreviousPartitionLabel(),
                             "apply_interrupted");
    } else {
        ClearResourceApplyingDeviceConfig();
    }
    ClearPendingDeviceConfig();
}

void ApplyStagedWakeResource() {
    if (!g_resource_apply_pending || g_device_config_health_pending ||
        g_state_machine.state() != veetee::app::State::kIdle) {
        return;
    }
    const char* staged_partition = g_resources.StagedPartitionLabel();
    const char* active_partition = g_resources.ActivePartitionLabel();
    if (staged_partition == nullptr) {
        g_resource_apply_pending = false;
        return;
    }

    const veetee::settings::ResourceRecord staged = g_resources.Snapshot();
    const std::uint32_t desired_config_version =
        g_settings_store.Snapshot().config_version;
    if (g_device_config_apply_pending) {
        PendingDeviceConfigIsApplicable();
    }
    if (desired_config_version >
            g_device_config_store.Snapshot().applied_version &&
        !g_device_config_apply_pending) {
        ESP_LOGI(kTag,
                 "Deferring resource apply until signed config version=%" PRIu32
                 " is staged",
                 desired_config_version);
        return;
    }
    const bool pending_version_matches =
        g_device_config_apply_pending &&
        (!g_pending_device_config.has_wake_profile ||
         std::strcmp(
             g_pending_device_config.required_resource_version.data(),
             staged.desired_version) == 0);
    if (g_device_config_apply_pending && !pending_version_matches) {
        ESP_LOGI(kTag,
                 "Deferring resource=%s for matching config resource=%s",
                 staged.desired_version,
                 g_pending_device_config.required_resource_version.data());
        return;
    }
    const auto pending_link =
        g_device_config_apply_pending
            ? DeviceConfigResourceLink(
                  g_pending_device_config, staged.desired_version,
                  staged.desired_detectors)
            : veetee::config::DeviceConfigResourceLinkError::kOk;
    if (g_device_config_apply_pending &&
        pending_link !=
            veetee::config::DeviceConfigResourceLinkError::kOk) {
        const char* error_code =
            veetee::config::DeviceConfigResourceLinkErrorName(pending_link);
        ESP_LOGE(kTag,
                 "Rejecting staged resource=%s for config=%" PRIu32
                 " detector linkage=%s",
                 staged.desired_version, g_pending_device_config.version,
                 error_code);
        RollbackWakeResource(active_partition, error_code);
        g_resource_apply_pending = false;
        FailPendingDeviceConfig(error_code);
        return;
    }
    const bool use_pending_config = g_device_config_apply_pending;
    const bool use_applied_config =
        !use_pending_config && g_applied_device_config.has_wake_profile;
    const auto applied_link = DeviceConfigResourceLink(
        g_applied_device_config, staged.desired_version,
        staged.desired_detectors);
    if (use_applied_config &&
        applied_link !=
            veetee::config::DeviceConfigResourceLinkError::kOk) {
        const char* error_code =
            veetee::config::DeviceConfigResourceLinkErrorName(applied_link);
        ESP_LOGE(kTag,
                 "Rejecting resource=%s because applied config requires=%s linkage=%s",
                 staged.desired_version,
                 g_applied_device_config.required_resource_version.data(),
                 error_code);
        RollbackWakeResource(active_partition, error_code);
        g_resource_apply_pending = false;
        ScheduleDeviceConfigReport(
            veetee::settings::ReportedResourcePhase::kFailed,
            g_applied_device_config.version,
            g_device_config_store.Snapshot().applied_version,
            error_code);
        return;
    }

    ESP_LOGI(kTag, "Applying staged wake resource partition=%s", staged_partition);
    ScheduleResourceReport(
        veetee::settings::ReportedResourcePhase::kApplying,
        g_resources.Snapshot());
    g_device_config_loaded_with_resource = use_pending_config;
    if (use_pending_config) {
        g_resource_applying_device_config = g_pending_device_config;
        std::snprintf(g_resource_applying_device_config_etag,
                      sizeof(g_resource_applying_device_config_etag), "%s",
                      g_pending_device_config_etag);
        ScheduleDeviceConfigReport(
            veetee::settings::ReportedResourcePhase::kApplying,
            g_resource_applying_device_config.version,
            g_device_config_store.Snapshot().applied_version);
    }
    const veetee::config::DeviceConfig applied_runtime =
        PrivacySafeRuntimeConfig(g_applied_device_config);
    esp_err_t error =
        use_pending_config
            ? g_board.ReloadWakeRuntime(staged_partition,
                                        PrivacySafeRuntimeConfig(
                                            g_resource_applying_device_config))
            : g_board.ReloadWakeRuntime(staged_partition, applied_runtime);
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Staged wake resource load failed: %s",
                 esp_err_to_name(error));
        RollbackWakeResource(active_partition, "staged_load_failed");
        g_resource_apply_pending = false;
        return;
    }
    error = g_resources.ActivateStaged();
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to activate staged resource journal: %s",
                 esp_err_to_name(error));
        RollbackWakeResource(active_partition, "activation_journal_failed");
        g_resource_apply_pending = false;
        return;
    }
    g_resource_apply_pending = false;
    g_resource_health_check_due.store(false, std::memory_order_release);
    esp_timer_stop(g_resource_health_timer);
    error = esp_timer_start_once(g_resource_health_timer,
                                 kResourceHealthWindowUs);
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to schedule resource health check: %s",
                 esp_err_to_name(error));
        RollbackWakeResource(active_partition, "health_timer_failed");
    }
}

void CheckActiveWakeResourceHealth() {
    if (!g_resource_health_check_due.load(std::memory_order_acquire)) {
        return;
    }
    if (g_resources.phase() !=
        veetee::settings::ResourceRecordPhase::kPendingHealth) {
        g_resource_health_check_due.store(false, std::memory_order_release);
        return;
    }
    if (
        g_state_machine.state() != veetee::app::State::kIdle ||
        g_state_machine.assistant_gate_open()) {
        return;
    }
    const std::uint32_t desired_config_version =
        g_settings_store.Snapshot().config_version;
    if (g_device_config_loaded_with_resource &&
        g_resource_applying_device_config.version !=
            desired_config_version) {
        RollbackWakeResource(g_resources.PreviousPartitionLabel(),
                             "config_superseded");
        if (g_resource_apply_pending) ScheduleResourceApply();
        TryApplyPendingDeviceConfig();
        return;
    }
    const char* active_partition = g_resources.ActivePartitionLabel();
    const bool paired_config = g_device_config_loaded_with_resource;
    const veetee::config::DeviceConfig health_config =
        g_device_config_loaded_with_resource
            ? g_resource_applying_device_config
            : g_applied_device_config;
    const auto activated_resource = g_resources.Snapshot();
    const auto health_link = DeviceConfigResourceLink(
        health_config, activated_resource.active_version,
        activated_resource.active_detectors);
    if (health_link !=
        veetee::config::DeviceConfigResourceLinkError::kOk) {
        const char* error_code =
            veetee::config::DeviceConfigResourceLinkErrorName(health_link);
        RollbackWakeResource(g_resources.PreviousPartitionLabel(),
                             error_code);
        if (!paired_config) {
            ScheduleDeviceConfigReport(
                veetee::settings::ReportedResourcePhase::kFailed,
                health_config.version,
                g_device_config_store.Snapshot().applied_version,
                error_code);
        }
        if (g_resource_apply_pending) ScheduleResourceApply();
        TryApplyPendingDeviceConfig();
        return;
    }
    if (g_board.WakeResourceHealthy() &&
        SamePartition(g_board.loaded_wake_partition(), active_partition)) {
        const veetee::settings::ResourceRecord activated = g_resources.Snapshot();
        const esp_err_t error = g_resources.ConfirmActive();
        if (error == ESP_OK) {
            g_resource_health_check_due.store(false,
                                              std::memory_order_release);
            if (g_device_config_loaded_with_resource) {
                const std::uint32_t applied_version =
                    g_resource_applying_device_config.version;
                char applied_etag[65] = {};
                std::snprintf(applied_etag, sizeof(applied_etag), "%s",
                              g_resource_applying_device_config_etag);
                const esp_err_t config_error =
                    g_device_config_store.SaveApplied(
                        g_resource_applying_device_config,
                        g_resource_applying_device_config_etag);
                if (config_error != ESP_OK) {
                    ESP_LOGE(kTag,
                             "Unable to persist config after resource health: %s",
                             esp_err_to_name(config_error));
                    RollbackWakeResource(
                        g_resources.PreviousPartitionLabel(),
                        "config_persist_failed");
                    if (g_resource_apply_pending) ScheduleResourceApply();
                    TryApplyPendingDeviceConfig();
                    return;
                }
                ConfirmAppliedWakeAudioConfig(
                    g_resource_applying_device_config);
                g_applied_device_config =
                    g_resource_applying_device_config;
                const bool runtime_committed =
                    FinalizeCommittedWakeAudioRuntime(
                        g_resource_applying_device_config);
                ScheduleDeviceConfigReport(
                    runtime_committed
                        ? veetee::settings::ReportedResourcePhase::kActive
                        : veetee::settings::ReportedResourcePhase::kFailed,
                    applied_version, applied_version,
                    runtime_committed ? nullptr
                                      : "wake_audio_enable_failed");
                ClearResourceApplyingDeviceConfig();
                if (g_device_config_apply_pending &&
                    g_pending_device_config.version == applied_version &&
                    std::strcmp(g_pending_device_config_etag,
                                applied_etag) == 0) {
                    ClearPendingDeviceConfig();
                }
            }
            ESP_LOGI(kTag, "Resource health confirmed active=%s",
                     active_partition);
            ScheduleResourceReport(
                veetee::settings::ReportedResourcePhase::kActive,
                g_resources.Snapshot(), activated.active_version, nullptr,
                &activated);
            TryApplyPendingDeviceConfig();
            return;
        }
        ESP_LOGE(kTag, "Unable to confirm resource health: %s",
                 esp_err_to_name(error));
    }
    RollbackWakeResource(g_resources.PreviousPartitionLabel(),
                         "health_check_failed");
    if (g_resource_apply_pending) ScheduleResourceApply();
    TryApplyPendingDeviceConfig();
}

bool IsFactorySignalVersion(const char* version) {
    return version != nullptr && std::strcmp(version, "factory-signal") == 0;
}

void RollbackUiPack(const char* fallback_partition, const char* reason) {
    const veetee::settings::ResourceRecord attempted = g_ui_resources.Snapshot();
    if (g_ui_health_timer != nullptr) {
        esp_timer_stop(g_ui_health_timer);
    }
    g_ui_health_check_due.store(false, std::memory_order_release);
    const bool previous_partition =
        SamePartition(fallback_partition, g_ui_resources.PreviousPartitionLabel());
    const char* fallback_version = previous_partition
                                       ? attempted.previous_version
                                       : attempted.active_version;
    if (fallback_partition == nullptr || IsFactorySignalVersion(fallback_version)) {
        g_board.UseBuiltInSignal();
    } else {
        const esp_err_t reload_error = g_board.ReloadUiPack(fallback_partition);
        if (reload_error != ESP_OK) {
            ESP_LOGE(kTag,
                     "UI fallback %s failed: %s; using built-in Mobile (signal)",
                     fallback_partition, esp_err_to_name(reload_error));
            g_board.UseBuiltInSignal();
        }
    }
    const esp_err_t rollback_error = g_ui_resources.Rollback();
    if (rollback_error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to rollback UI state reason=%s: %s", reason,
                 esp_err_to_name(rollback_error));
        return;
    }
    const char* desired = attempted.desired_version[0] != '\0'
                              ? attempted.desired_version
                              : attempted.active_version;
    ScheduleResourceReport(
        veetee::settings::ReportedResourcePhase::kRolledBack,
        g_ui_resources.Snapshot(), desired, reason, &attempted,
        veetee::settings::ReportedArtifactKind::kUiPack);
}

void ApplyStagedUiPack() {
    if (!g_ui_apply_pending ||
        g_state_machine.state() != veetee::app::State::kIdle) {
        return;
    }
    const char* staged_partition = g_ui_resources.StagedPartitionLabel();
    const char* active_partition = g_ui_resources.ActivePartitionLabel();
    if (staged_partition == nullptr) {
        g_ui_apply_pending = false;
        return;
    }
    ESP_LOGI(kTag, "Applying staged UI Pack partition=%s", staged_partition);
    ScheduleResourceReport(
        veetee::settings::ReportedResourcePhase::kApplying,
        g_ui_resources.Snapshot(), nullptr, nullptr, nullptr,
        veetee::settings::ReportedArtifactKind::kUiPack);
    esp_err_t error = g_board.ReloadUiPack(staged_partition);
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Staged UI Pack load failed: %s",
                 esp_err_to_name(error));
        RollbackUiPack(active_partition, "staged_load_failed");
        g_ui_apply_pending = false;
        return;
    }
    error = g_ui_resources.ActivateStaged();
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to activate UI journal: %s",
                 esp_err_to_name(error));
        RollbackUiPack(active_partition, "activation_journal_failed");
        g_ui_apply_pending = false;
        return;
    }
    g_ui_apply_pending = false;
    g_ui_health_check_due.store(false, std::memory_order_release);
    esp_timer_stop(g_ui_health_timer);
    error = esp_timer_start_once(g_ui_health_timer, kResourceHealthWindowUs);
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to schedule UI health check: %s",
                 esp_err_to_name(error));
        RollbackUiPack(active_partition, "health_timer_failed");
    }
}

void CheckActiveUiPackHealth() {
    if (!veetee::app::ShouldServiceDeferredHealth(
            g_ui_health_check_due.load(std::memory_order_acquire),
            g_state_machine.state(), g_state_machine.assistant_gate_open())) {
        return;
    }
    if (g_ui_resources.phase() !=
        veetee::settings::ResourceRecordPhase::kPendingHealth) {
        g_ui_health_check_due.store(false, std::memory_order_release);
        return;
    }
    const char* active_partition = g_ui_resources.ActivePartitionLabel();
    if (g_board.UiPackHealthy() &&
        SamePartition(g_board.loaded_ui_partition(), active_partition)) {
        const veetee::settings::ResourceRecord activated =
            g_ui_resources.Snapshot();
        const esp_err_t error = g_ui_resources.ConfirmActive();
        if (error == ESP_OK) {
            g_ui_health_check_due.store(false, std::memory_order_release);
            ESP_LOGI(kTag, "UI Pack health confirmed active=%s",
                     active_partition);
            ScheduleResourceReport(
                veetee::settings::ReportedResourcePhase::kActive,
                g_ui_resources.Snapshot(), activated.active_version, nullptr,
                &activated, veetee::settings::ReportedArtifactKind::kUiPack);
            return;
        }
        ESP_LOGE(kTag, "Unable to confirm UI Pack health: %s",
                 esp_err_to_name(error));
    }
    RollbackUiPack(g_ui_resources.PreviousPartitionLabel(),
                   "health_check_failed");
}

bool PostEvent(veetee::app::Event event) {
    return PostMessage(
        AppMessage{.kind = AppMessageKind::kStateEvent, .event = event});
}

std::int64_t FirmwareOverallHealthWindowUs() {
    return (static_cast<std::int64_t>(
                CONFIG_VEETEE_WIFI_CONNECT_TIMEOUT_SECONDS) +
            30) *
           1000000LL;
}

void RequestFirmwareHealthCheck() {
    g_firmware_health_check_due.store(true);
}

bool ArmFirmwareHealthPoll() {
    if (g_firmware_health_timer == nullptr) {
        g_firmware_health_fallback_poll_us =
            esp_timer_get_time() +
            static_cast<std::int64_t>(kFirmwareHealthPollUs);
        ++g_firmware_health_timer_start_failures;
        return false;
    }
    const esp_err_t stop_error = esp_timer_stop(g_firmware_health_timer);
    if (stop_error != ESP_OK && stop_error != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(kTag, "Unable to stop firmware health timer: %s",
                 esp_err_to_name(stop_error));
    }
    const esp_err_t error =
        esp_timer_start_once(g_firmware_health_timer, kFirmwareHealthPollUs);
    if (error == ESP_OK) {
        g_firmware_health_timer_start_failures = 0;
        g_firmware_health_fallback_poll_us = 0;
        return true;
    }
    if (g_firmware_health_timer_start_failures < UINT8_MAX) {
        ++g_firmware_health_timer_start_failures;
    }
    g_firmware_health_fallback_poll_us =
        esp_timer_get_time() +
        static_cast<std::int64_t>(kFirmwareHealthPollUs);
    ESP_LOGE(kTag,
             "Unable to arm firmware health timer: %s failures=%u; "
             "application poll remains active",
             esp_err_to_name(error),
             static_cast<unsigned>(g_firmware_health_timer_start_failures));
    return false;
}

void QueueFirmwareTerminalOutcome(
    veetee::ota::FirmwareOtaRecoveryDecision decision,
    const veetee::ota::FirmwareOtaNotification& notification,
    const char* error_code = nullptr,
    bool staged_cancel_pending = false) {
    if (g_firmware_terminal_outcome.pending) {
        ESP_LOGE(kTag, "Firmware terminal outcome already pending decision=%s",
                 veetee::ota::FirmwareOtaRecoveryDecisionName(
                     g_firmware_terminal_outcome.decision));
        return;
    }
    g_firmware_terminal_outcome = FirmwareTerminalOutcome{};
    g_firmware_terminal_outcome.pending = true;
    g_firmware_terminal_outcome.staged_cancel_pending =
        staged_cancel_pending;
    g_firmware_terminal_outcome.decision = decision;
    g_firmware_terminal_outcome.notification = notification;
    if (error_code != nullptr) {
        CopyErrorCode(g_firmware_terminal_outcome.error_code,
                      sizeof(g_firmware_terminal_outcome.error_code),
                      error_code);
    } else if (decision ==
               veetee::ota::FirmwareOtaRecoveryDecision::kInconsistent) {
        CopyErrorCode(g_firmware_terminal_outcome.error_code,
                      sizeof(g_firmware_terminal_outcome.error_code),
                      "terminal_runtime_mismatch");
    }
    // Do not accept a second executable target until the current attempt and
    // its terminal report are both durable.
    g_bootstrap.SetFirmwareUpdatesDeferred(true);
    RequestFirmwareHealthCheck();
}

bool ProcessFirmwareTerminalOutcome() {
    auto& outcome = g_firmware_terminal_outcome;
    if (!outcome.pending) return true;

    if (outcome.staged_cancel_pending) {
        const esp_err_t error = g_firmware.CancelStagedBoot();
        if (error != ESP_OK) {
            ESP_LOGE(kTag, "Unable to retry staged boot cancellation: %s",
                     esp_err_to_name(error));
            return false;
        }
        outcome.staged_cancel_pending = false;
    }

    bool terminal_replay_complete = false;
    while (!terminal_replay_complete) {
        veetee::ota::FirmwareTerminalReplayProgress progress{};
        progress.journal_transition_required =
            outcome.decision !=
            veetee::ota::FirmwareOtaRecoveryDecision::kInconsistent;
        progress.attempt_marked = outcome.attempt_marked;
        progress.report_persisted = outcome.report_persisted;
        progress.attempt_cleared = outcome.attempt_cleared;
        switch (veetee::ota::NextFirmwareTerminalReplayStep(progress)) {
            case veetee::ota::FirmwareTerminalReplayStep::kMarkAttempt: {
                const esp_err_t error = g_firmware.MarkRecoveredOutcome(
                    outcome.decision,
                    outcome.error_code[0] == '\0' ? nullptr
                                                   : outcome.error_code);
                if (error != ESP_OK) {
                    ESP_LOGE(
                        kTag,
                        "Unable to persist firmware terminal attempt decision=%s: %s",
                        veetee::ota::FirmwareOtaRecoveryDecisionName(
                            outcome.decision),
                        esp_err_to_name(error));
                    return false;
                }
                outcome.attempt_marked = true;
                break;
            }
            case veetee::ota::FirmwareTerminalReplayStep::kPersistReport: {
                using Phase = veetee::settings::ReportedResourcePhase;
                const Phase phase =
                    outcome.decision ==
                            veetee::ota::FirmwareOtaRecoveryDecision::kActive
                        ? Phase::kActive
                        : outcome.decision ==
                                  veetee::ota::FirmwareOtaRecoveryDecision::kRolledBack
                              ? Phase::kRolledBack
                              : Phase::kFailed;
                if (!g_reporter.PersistForReplay(
                        MakeFirmwareReport(
                            phase, outcome.notification,
                            outcome.error_code[0] == '\0'
                                ? nullptr
                                : outcome.error_code),
                        false)) {
                    // A rebooting or pending-health report may still be
                    // replaying. Preserve ordering and retry without
                    // superseding it.
                    return false;
                }
                outcome.report_persisted = true;
                break;
            }
            case veetee::ota::FirmwareTerminalReplayStep::kClearAttempt: {
                const esp_err_t error = g_firmware.ClearCompletedAttempt();
                if (error != ESP_OK) {
                    ESP_LOGE(kTag,
                             "Unable to clear completed firmware attempt: %s",
                             esp_err_to_name(error));
                    return false;
                }
                outcome.attempt_cleared = true;
                break;
            }
            case veetee::ota::FirmwareTerminalReplayStep::kComplete:
                terminal_replay_complete = true;
                break;
        }
    }

    ESP_LOGI(kTag, "Firmware terminal outcome durable decision=%s",
             veetee::ota::FirmwareOtaRecoveryDecisionName(outcome.decision));
    const auto completed_decision = outcome.decision;
    outcome = FirmwareTerminalOutcome{};
    const bool pending_boot = g_firmware.PendingBootVerification();
    g_bootstrap.SetFirmwareUpdatesDeferred(pending_boot);
    if (pending_boot &&
        completed_decision ==
            veetee::ota::FirmwareOtaRecoveryDecision::kInconsistent) {
        ESP_LOGE(kTag,
                 "Inconsistent terminal journal on pending image; requesting rollback");
        const esp_err_t rollback_error = g_firmware.RollbackPendingBoot();
        if (rollback_error != ESP_OK) {
            ESP_LOGE(kTag, "Unable to rollback inconsistent pending image: %s",
                     esp_err_to_name(rollback_error));
            RequestFirmwareHealthCheck();
        }
        return rollback_error == ESP_OK;
    }
    if (g_state_machine.state() == veetee::app::State::kIdle &&
        g_settings_store.Snapshot().HasDeviceIdentity() && !pending_boot) {
        // The bootstrap pass that proved health intentionally ignored firmware
        // targets.  Pull once more after clearing the attempt to observe a
        // newer rollout target without rebooting into the same image.
        g_bootstrap.Start();
    }
    return true;
}

void RecoverFirmwareTerminalAttemptIfAny() {
    if (g_firmware_terminal_outcome.pending) return;
    veetee::ota::FirmwareOtaNotification notification{};
    const auto decision = g_firmware.RecoveryStatus(&notification);
    if (decision == veetee::ota::FirmwareOtaRecoveryDecision::kActive ||
        decision == veetee::ota::FirmwareOtaRecoveryDecision::kRolledBack ||
        decision == veetee::ota::FirmwareOtaRecoveryDecision::kFailed ||
        decision == veetee::ota::FirmwareOtaRecoveryDecision::kInconsistent) {
        QueueFirmwareTerminalOutcome(
            decision, notification,
            notification.error_code[0] == '\0' ? nullptr
                                                 : notification.error_code);
    }
}

void CancelFirmwareAndRecover() {
    g_firmware.Cancel();
    if (g_firmware.HasStagedOwnership()) {
        // Cancel retained ownership because restoring otadata or persisting the
        // failed journal did not complete.  Move the retry into the durable
        // application poll instead of inferring RolledBack from the currently
        // running image while the next boot may still select the staged slot.
        veetee::ota::FirmwareOtaNotification notification{};
        g_firmware.RecoveryStatus(&notification);
        QueueFirmwareTerminalOutcome(
            veetee::ota::FirmwareOtaRecoveryDecision::kFailed, notification,
            "cancelled", true);
        return;
    }
    RecoverFirmwareTerminalAttemptIfAny();
}

void BeginFirmwareHealthMonitoring(
    const veetee::ota::FirmwareOtaNotification& notification) {
    g_firmware_health_notification = notification;
    g_firmware_health_monitoring = true;
    g_firmware_pending_health_attempt_marked = false;
    g_firmware_pending_health_report_persisted = false;
    g_firmware_health_overall_deadline_us =
        g_firmware_boot_started_us + FirmwareOverallHealthWindowUs();
    g_firmware_health_post_wifi_deadline_us = 0;
    g_bootstrap.SetFirmwareUpdatesDeferred(true);

    const esp_err_t mark_error = g_firmware.MarkAttemptPendingHealth();
    if (mark_error == ESP_OK) {
        g_firmware_pending_health_attempt_marked = true;
        g_firmware_pending_health_report_persisted =
            g_reporter.PersistForReplay(
                MakeFirmwareReport(
                    veetee::settings::ReportedResourcePhase::kPendingHealth,
                    g_firmware_health_notification),
                false);
    } else {
        ESP_LOGE(kTag, "Unable to persist pending-health attempt: %s",
                 esp_err_to_name(mark_error));
    }
    RequestFirmwareHealthCheck();
}

void RequestFirmwareRollback(const char* error_code) {
    const esp_err_t mark_error =
        g_firmware.MarkAttemptRollbackRequested(error_code);
    if (mark_error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to persist rollback-requested attempt: %s",
                 esp_err_to_name(mark_error));
    }
    const esp_err_t rollback_error = g_firmware.RollbackPendingBoot();
    if (rollback_error == ESP_OK) return;

    ESP_LOGE(kTag, "Firmware rollback request returned: %s",
             esp_err_to_name(rollback_error));
    if (mark_error == ESP_OK) {
        const esp_err_t restore_error =
            g_firmware.RestoreAttemptPendingHealth();
        if (restore_error != ESP_OK) {
            ESP_LOGE(kTag, "Unable to restore pending-health attempt: %s",
                     esp_err_to_name(restore_error));
        }
    }
    // Do not report rolled_back here: only the previous image can infer and
    // durably report that outcome after the bootloader actually selected it.
    ArmFirmwareHealthPoll();
}

void OnButtonEvent(veetee::input::ButtonEvent event, void*) {
    switch (event) {
        case veetee::input::ButtonEvent::kShortPress:
            PostEvent(veetee::app::Event::kButtonShortPress);
            break;
        case veetee::input::ButtonEvent::kLongPress:
            PostEvent(veetee::app::Event::kButtonLongPress);
            break;
        case veetee::input::ButtonEvent::kWifiConfigHold:
            PostEvent(veetee::app::Event::kEnterWifiConfig);
            break;
    }
}

bool OnDetectorEvent(veetee::audio::DetectorRole role, const char* profile_id,
                     void*) {
    ESP_LOGI(kTag, "Local detector event role=%s profile=%s",
             veetee::audio::ToString(role), profile_id);
    switch (role) {
        case veetee::audio::DetectorRole::kActivation:
            return PostEvent(veetee::app::Event::kActivationWakeDetected);
        case veetee::audio::DetectorRole::kInterrupt:
            return PostEvent(veetee::app::Event::kInterruptDetected);
        case veetee::audio::DetectorRole::kDisabled:
            return false;
    }
    return false;
}

void OnWifiEvent(veetee::network::WifiManagerEvent event, void*) {
    switch (event) {
        case veetee::network::WifiManagerEvent::kConnected:
            PostEvent(veetee::app::Event::kWifiConnected);
            break;
        case veetee::network::WifiManagerEvent::kConnectionTimeout:
            PostEvent(veetee::app::Event::kWifiConnectionTimeout);
            break;
        case veetee::network::WifiManagerEvent::kDisconnected:
            PostEvent(veetee::app::Event::kWifiDisconnected);
            break;
        case veetee::network::WifiManagerEvent::kProvisioningSaved:
            PostEvent(veetee::app::Event::kProvisioningSaved);
            break;
        case veetee::network::WifiManagerEvent::kProvisioningCleanup:
            if (!PostMessage(AppMessage{
                    .kind = AppMessageKind::kProvisioningCleanup})) {
                g_wifi.RetryProvisioningCleanup();
            }
            break;
    }
}

bool OnBootstrapEvent(const veetee::ota::BootstrapNotification& notification,
                      void*) {
    AppMessage message{};
    switch (notification.event) {
        case veetee::ota::BootstrapEvent::kActivationCodeAvailable:
            message.event = veetee::app::Event::kActivationCodeAvailable;
            std::snprintf(message.activation_code, sizeof(message.activation_code),
                          "%s", notification.activation_code);
            break;
        case veetee::ota::BootstrapEvent::kActivationComplete:
            message.event = veetee::app::Event::kActivationComplete;
            break;
        case veetee::ota::BootstrapEvent::kDeviceIdentityRejected:
            message.event = veetee::app::Event::kDeviceIdentityRejected;
            break;
        case veetee::ota::BootstrapEvent::kConfigDesired:
            ScheduleDeviceConfigReport(
                veetee::settings::ReportedResourcePhase::kChecking,
                notification.config_version,
                g_device_config_store.Snapshot().applied_version);
            if (g_device_config_reconciler.Schedule(
                    notification.config_version, notification.config_etag,
                    notification.config_url)) {
                return true;
            }
            ScheduleDeviceConfigReport(
                veetee::settings::ReportedResourcePhase::kFailed,
                notification.config_version,
                g_device_config_store.Snapshot().applied_version,
                "schedule_rejected");
            return true;
        case veetee::ota::BootstrapEvent::kResourceDesired:
            ScheduleResourceReport(
                veetee::settings::ReportedResourcePhase::kChecking,
                g_resources.Snapshot(), notification.resource_version);
            if (g_resources.Schedule(notification.resource_version,
                                     notification.resource_manifest_url)) {
                return true;
            }
            ScheduleResourceReport(
                veetee::settings::ReportedResourcePhase::kFailed,
                g_resources.Snapshot(), notification.resource_version,
                "schedule_rejected");
            return true;
        case veetee::ota::BootstrapEvent::kUiPackDesired:
            ScheduleResourceReport(
                veetee::settings::ReportedResourcePhase::kChecking,
                g_ui_resources.Snapshot(), notification.ui_version, nullptr,
                nullptr, veetee::settings::ReportedArtifactKind::kUiPack);
            if (g_ui_resources.Schedule(notification.ui_version,
                                        notification.ui_manifest_url)) {
                return true;
            }
            ScheduleResourceReport(
                veetee::settings::ReportedResourcePhase::kFailed,
                g_ui_resources.Snapshot(), notification.ui_version,
                "schedule_rejected", nullptr,
                veetee::settings::ReportedArtifactKind::kUiPack);
            return true;
        case veetee::ota::BootstrapEvent::kFirmwareDesired:
            message.event = veetee::app::Event::kFirmwareUpdateRequested;
            std::snprintf(message.firmware_target_version,
                          sizeof(message.firmware_target_version), "%s",
                          notification.firmware_version);
            std::snprintf(message.firmware_manifest_url,
                          sizeof(message.firmware_manifest_url), "%s",
                          notification.firmware_manifest_url);
            break;
    }
    return PostMessage(message);
}

bool OnResourceReconcileEvent(
    const veetee::ota::ResourceReconcileNotification& notification, void*) {
    AppMessage message{};
    message.kind = AppMessageKind::kResourceReconcile;
    message.resource_notification = notification;
    return PostMessage(message);
}

bool OnDeviceConfigReconcileEvent(
    const veetee::config::DeviceConfigReconcileNotification& notification,
    void*) {
    AppMessage message{};
    message.kind = AppMessageKind::kDeviceConfigReconcile;
    message.config_notification = notification;
    return PostMessage(message);
}

bool OnFirmwareOtaEvent(const veetee::ota::FirmwareOtaNotification& notification,
                        void*) {
    AppMessage message{};
    message.kind = AppMessageKind::kFirmwareReconcile;
    message.firmware_notification = notification;
    return PostMessage(message);
}

bool OnTransportEvent(
    const veetee::transport::WebSocketTransportNotification& notification,
    void*) {
    switch (notification.event) {
        case veetee::transport::WebSocketTransportEvent::kReady:
            return PostEvent(veetee::app::Event::kTransportConnected);
        case veetee::transport::WebSocketTransportEvent::kReconnecting:
            return PostEvent(
                veetee::app::Event::kTransportReconnectScheduled);
        case veetee::transport::WebSocketTransportEvent::kLost:
            return PostEvent(veetee::app::Event::kTransportLost);
        case veetee::transport::WebSocketTransportEvent::kListenStarted:
            return PostEvent(veetee::app::Event::kAdmissionRejected);
        case veetee::transport::WebSocketTransportEvent::kSttFinal:
            return PostEvent(veetee::app::Event::kVadFinal);
        case veetee::transport::WebSocketTransportEvent::kLlmStarted:
            return PostEvent(veetee::app::Event::kAdmissionAccepted);
        case veetee::transport::WebSocketTransportEvent::kTurnFailed:
            return PostEvent(veetee::app::Event::kTurnFailed);
        case veetee::transport::WebSocketTransportEvent::kTtsStarted:
            g_board.BeginPlayback();
            return PostEvent(veetee::app::Event::kTtsStarted);
        case veetee::transport::WebSocketTransportEvent::kTtsStopped:
            g_board.EndPlayback();
            return true;
        case veetee::transport::WebSocketTransportEvent::kAssistantSleep:
            return PostEvent(veetee::app::Event::kAssistantSleepRequested);
        case veetee::transport::WebSocketTransportEvent::kConfigChanged:
            return PostMessage(AppMessage{
                .kind = AppMessageKind::kDeviceConfigInvalidated,
                .config_version = notification.config_version});
    }
    return false;
}

bool OnDownlinkAudio(const std::uint8_t* packet, std::size_t length, void*) {
    return g_board.QueueOpusPlayback(packet, length);
}

bool OnMcpEnvelope(const char* envelope, std::size_t length, void*) {
    if (envelope == nullptr || length == 0 ||
        length > veetee::transport::kMaximumControlFrameBytes) {
        return false;
    }
    char* copy = static_cast<char*>(std::malloc(length + 1));
    if (copy == nullptr) return false;
    std::memcpy(copy, envelope, length);
    copy[length] = '\0';
    const AppMessage message{.kind = AppMessageKind::kMcpEnvelope,
                             .control_payload = copy,
                             .control_length = length,
                             .conversation_generation =
                                 g_conversation_generation.load()};
    if (PostMessage(message)) return true;
    std::free(copy);
    return false;
}

bool ReadDeviceStatus(veetee::mcp::DeviceStatus* status, void*) {
    if (status == nullptr) return false;
    status->state = veetee::app::ToString(g_state_machine.state());
    status->assistant_gate_open = g_state_machine.assistant_gate_open();
    status->firmware_version = esp_app_get_description()->version;
    status->volume_percent = g_board.speaker_volume();
    return true;
}

const char* ResetReasonName(esp_reset_reason_t reason) {
    switch (reason) {
        case ESP_RST_POWERON:
            return "power_on";
        case ESP_RST_EXT:
            return "external";
        case ESP_RST_SW:
            return "software";
        case ESP_RST_PANIC:
            return "panic";
        case ESP_RST_INT_WDT:
            return "interrupt_watchdog";
        case ESP_RST_TASK_WDT:
            return "task_watchdog";
        case ESP_RST_WDT:
            return "watchdog";
        case ESP_RST_DEEPSLEEP:
            return "deep_sleep";
        case ESP_RST_BROWNOUT:
            return "brownout";
        case ESP_RST_SDIO:
            return "sdio";
        case ESP_RST_UNKNOWN:
        default:
            return "unknown";
    }
}

bool ReadDeviceDiagnostics(veetee::mcp::DeviceDiagnostics* diagnostics,
                           void*) {
    if (diagnostics == nullptr) return false;
    *diagnostics = veetee::mcp::DeviceDiagnostics{};
    if (!ReadDeviceStatus(&diagnostics->device, nullptr)) return false;

    diagnostics->uptime_ms =
        static_cast<std::uint64_t>(esp_timer_get_time() / 1000);
    diagnostics->reset_reason = ResetReasonName(esp_reset_reason());
    diagnostics->internal_free_bytes = static_cast<std::uint32_t>(
        heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    diagnostics->internal_min_free_bytes = static_cast<std::uint32_t>(
        heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL));
    diagnostics->psram_free_bytes = static_cast<std::uint32_t>(
        heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    diagnostics->psram_min_free_bytes = static_cast<std::uint32_t>(
        heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM));

    const veetee::network::WifiHealth network = g_wifi.Health();
    diagnostics->network_connected = network.connected;
    diagnostics->network_rssi = network.rssi;
    std::snprintf(diagnostics->network_ipv4.data(),
                  diagnostics->network_ipv4.size(), "%s", network.ipv4);
    diagnostics->network_disconnect_count = network.disconnect_count;
    diagnostics->network_reconnect_attempt_count =
        network.reconnect_attempt_count;
    diagnostics->websocket_reconnect_attempt_count =
        g_transport.reconnect_attempt_count();
    diagnostics->websocket_reconnect_exhausted_count =
        g_transport.reconnect_exhausted_count();
    diagnostics->network_last_disconnect_reason =
        network.last_disconnect_reason;

    diagnostics->audio = g_board.AudioHealth(diagnostics->uptime_ms);
    diagnostics->transport_uplink_queue_drops =
        g_transport.outbound_audio_queue_drops();
    diagnostics->transport_uplink_queue_high_water =
        g_transport.outbound_audio_queue_high_water();
    diagnostics->capture_task = {
        .expected = true,
        .running = diagnostics->audio.capture_task_running,
        .stack_free_bytes = diagnostics->audio.capture_stack_free_bytes,
    };
    diagnostics->playback_task = {
        .expected = true,
        .running = diagnostics->audio.playback_task_running,
        .stack_free_bytes = diagnostics->audio.playback_stack_free_bytes,
    };
    diagnostics->wake_task = {
        .expected = g_board.wake_task_expected(),
        .running = g_board.wake_task_running(),
        .stack_free_bytes = g_board.wake_stack_free_bytes(),
    };
    diagnostics->websocket_control_task = {
        .expected = true,
        .running = g_transport.control_task_running(),
        .stack_free_bytes = g_transport.control_stack_free_bytes(),
    };
    diagnostics->wake_resource_healthy = g_board.WakeResourceHealthy();
    diagnostics->ui_pack_healthy = g_board.UiPackHealthy();
    diagnostics->wake_dropped_frames = g_board.wake_dropped_frames();
    return true;
}

bool StartAudioDiagnostic(std::uint32_t duration_seconds, void*) {
    return g_board.StartAudioDiagnostic(
        duration_seconds,
        static_cast<std::uint64_t>(esp_timer_get_time() / 1000));
}

bool SetSpeakerVolume(int volume_percent, void*) {
    return g_board.SetSpeakerVolume(volume_percent);
}

bool SendMcpResponse(const char* payload, std::size_t length, void*) {
    return g_transport.SendMcpPayload(payload, length);
}

bool OnEncodedAudio(const std::uint8_t* packet, std::size_t length, void*) {
    return g_transport.SendAudio(packet, length);
}

bool OnPlaybackFinished(void*) {
    return PostEvent(veetee::app::Event::kTtsStopped);
}

void LogTransportError(const char* operation, esp_err_t error) {
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "WebSocket %s failed: %s", operation,
                 esp_err_to_name(error));
    }
}

void RunApplication(void*) {
    AppMessage message{};
    while (true) {
        veetee::app::Event control_event{};
        if (g_critical_event_queue != nullptr &&
            xQueueReceive(g_critical_event_queue, &control_event, 0) ==
                pdTRUE) {
            message = AppMessage{.kind = AppMessageKind::kStateEvent,
                                 .event = control_event};
        } else if (g_wake_event_queue != nullptr &&
                   xQueueReceive(g_wake_event_queue, &control_event, 0) ==
                       pdTRUE) {
            message = AppMessage{.kind = AppMessageKind::kStateEvent,
                                 .event = control_event};
        } else if (g_firmware_health_check_due.exchange(false) ||
                   (g_firmware_health_fallback_poll_us > 0 &&
                    esp_timer_get_time() >=
                        g_firmware_health_fallback_poll_us)) {
            g_firmware_health_fallback_poll_us = 0;
            message =
                AppMessage{.kind = AppMessageKind::kFirmwareHealthCheck};
        } else if (g_resource_health_check_due.load(
                       std::memory_order_acquire) &&
                   g_state_machine.state() == veetee::app::State::kIdle &&
                   !g_state_machine.assistant_gate_open()) {
            message = AppMessage{
                .kind = AppMessageKind::kResourceHealthCheck,
                .resource_class = veetee::ota::ResourceClass::kWakeModel};
        } else if (g_device_config_health_check_due.load(
                       std::memory_order_acquire) &&
                   g_state_machine.state() == veetee::app::State::kIdle &&
                   !g_state_machine.assistant_gate_open()) {
            message =
                AppMessage{.kind = AppMessageKind::kDeviceConfigHealthCheck};
        } else if (veetee::app::ShouldServiceDeferredHealth(
                       g_ui_health_check_due.load(std::memory_order_acquire),
                       g_state_machine.state(),
                       g_state_machine.assistant_gate_open())) {
            message = AppMessage{
                .kind = AppMessageKind::kResourceHealthCheck,
                .resource_class = veetee::ota::ResourceClass::kUiPack};
        } else if (xQueueReceive(
                       g_event_queue, &message,
                       pdMS_TO_TICKS(veetee::app::kApplicationQueuePollMs)) !=
                   pdTRUE) {
            continue;
        }
        if (message.kind == AppMessageKind::kRuntimeStats) {
            g_runtime_stats.Sample(
                veetee::app::ToString(g_state_machine.state()));
            continue;
        }
        if (message.kind == AppMessageKind::kMcpEnvelope) {
            if (!veetee::app::ShouldHandleMcpEnvelope(
                    message.conversation_generation,
                    g_conversation_generation.load())) {
                ESP_LOGW(kTag,
                         "Dropping stale MCP request queued before cancellation");
                std::free(message.control_payload);
                continue;
            }
            const bool handled = g_mcp.HandleEnvelope(message.control_payload,
                                                      message.control_length);
            std::free(message.control_payload);
            if (!handled) ESP_LOGW(kTag, "MCP request was rejected");
            continue;
        }
        if (message.kind == AppMessageKind::kDeviceConfigPoll) {
            if (g_settings_store.Snapshot().HasDeviceIdentity()) {
                g_device_config_invalidation_pending = true;
                ReconcileInvalidatedDeviceConfigAtBoundary();
            }
            if (g_device_config_poll_timer != nullptr) {
                const esp_err_t error = esp_timer_start_once(
                    g_device_config_poll_timer,
                    kDeviceConfigPollIntervalUs);
                if (error != ESP_OK) {
                    ESP_LOGW(kTag, "Unable to reschedule config poll: %s",
                             esp_err_to_name(error));
                }
            }
            continue;
        }
        if (message.kind == AppMessageKind::kDeviceConfigHealthCheck) {
            CheckDeviceConfigHealth();
            continue;
        }
        if (message.kind == AppMessageKind::kDeviceConfigInvalidated) {
            if (message.config_version >
                g_device_config_store.Snapshot().applied_version) {
                g_invalidated_device_config_version = std::max(
                    g_invalidated_device_config_version,
                    message.config_version);
                g_device_config_invalidation_pending = true;
                ReconcileInvalidatedDeviceConfigAtBoundary();
            }
            continue;
        }
        if (message.kind == AppMessageKind::kDeviceConfigReconcile) {
            const auto& notification = message.config_notification;
            const std::uint32_t current_config_version =
                g_settings_store.Snapshot().config_version;
            if (notification.desired_version != current_config_version) {
                ESP_LOGW(kTag,
                         "Ignoring stale config result desired=%" PRIu32
                         " current=%" PRIu32,
                         notification.desired_version,
                         current_config_version);
                continue;
            }
            if (notification.event ==
                veetee::config::DeviceConfigReconcileEvent::kStaged) {
                if (notification.config.version != current_config_version ||
                    notification.desired_version !=
                        current_config_version ||
                    notification.config.version <
                        g_device_config_store.Snapshot().applied_version) {
                    ScheduleDeviceConfigReport(
                        veetee::settings::ReportedResourcePhase::kFailed,
                        notification.desired_version,
                        g_device_config_store.Snapshot().applied_version,
                        "stale_result");
                    continue;
                }
                const esp_err_t privacy_error =
                    ObserveVerifiedWakeAudioConfig(notification.config);
                if (privacy_error != ESP_OK) {
                    ScheduleDeviceConfigReport(
                        veetee::settings::ReportedResourcePhase::kFailed,
                        notification.desired_version,
                        g_device_config_store.Snapshot().applied_version,
                        "privacy_latch_persist_failed");
                    continue;
                }
                g_pending_device_config = notification.config;
                std::snprintf(g_pending_device_config_etag,
                              sizeof(g_pending_device_config_etag), "%s",
                              notification.etag);
                g_device_config_apply_pending = true;
                ESP_LOGI(kTag,
                         "Device config staged desired=%" PRIu32
                         " required_resource=%s",
                         notification.desired_version,
                         notification.config.has_wake_profile
                             ? notification.config.required_resource_version.data()
                             : "none");
                TryApplyPendingDeviceConfig();
                if (g_resource_apply_pending) ScheduleResourceApply();
            } else if (notification.event ==
                       veetee::config::DeviceConfigReconcileEvent::kAlreadyApplied) {
                const esp_err_t privacy_error =
                    ObserveVerifiedWakeAudioConfig(notification.config);
                if (privacy_error != ESP_OK) {
                    ScheduleDeviceConfigReport(
                        veetee::settings::ReportedResourcePhase::kFailed,
                        notification.desired_version,
                        notification.applied_version,
                        "privacy_latch_persist_failed");
                    continue;
                }
                const auto active_resource = g_resources.Snapshot();
                const auto resource_link = DeviceConfigResourceLink(
                    notification.config, active_resource.active_version,
                    active_resource.active_detectors);
                const bool resource_version_matches =
                    resource_link ==
                    veetee::config::DeviceConfigResourceLinkError::kOk;
                bool wake_audio_runtime_allowed =
                    CommittedWakeAudioRuntimeAllowed(notification.config);
                bool detector_runtime_healthy =
                    !notification.config.has_wake_profile ||
                    (g_board.wake_task_expected() &&
                     g_board.WakeResourceHealthy() &&
                     SamePartition(g_board.loaded_wake_partition(),
                                   g_resources.ActivePartitionLabel()));
                if (resource_version_matches && wake_audio_runtime_allowed &&
                    detector_runtime_healthy &&
                    g_board.WakeRuntimeConfigVersionMatches(
                        notification.config.version) &&
                    !CommittedWakeAudioRuntimeMatches(notification.config) &&
                    notification.config.send_wake_audio) {
                    FinalizeCommittedWakeAudioRuntime(notification.config);
                    wake_audio_runtime_allowed =
                        CommittedWakeAudioRuntimeAllowed(notification.config);
                }
                bool runtime_healthy =
                    resource_version_matches && wake_audio_runtime_allowed &&
                    detector_runtime_healthy &&
                    CommittedWakeAudioRuntimeMatches(notification.config);
                if (resource_version_matches && wake_audio_runtime_allowed &&
                    !runtime_healthy &&
                    g_state_machine.state() == veetee::app::State::kIdle) {
                    const esp_err_t retry = g_board.ReloadWakeRuntime(
                        g_resources.ActivePartitionLabel(),
                        PrivacySafeRuntimeConfig(notification.config));
                    detector_runtime_healthy =
                        retry == ESP_OK &&
                        (!notification.config.has_wake_profile ||
                         (g_board.wake_task_expected() &&
                          g_board.WakeResourceHealthy() &&
                          SamePartition(
                              g_board.loaded_wake_partition(),
                              g_resources.ActivePartitionLabel())));
                    const bool runtime_finalized =
                        detector_runtime_healthy &&
                        FinalizeCommittedWakeAudioRuntime(
                            notification.config);
                    wake_audio_runtime_allowed =
                        CommittedWakeAudioRuntimeAllowed(notification.config);
                    runtime_healthy =
                        resource_version_matches &&
                        wake_audio_runtime_allowed &&
                        detector_runtime_healthy && runtime_finalized &&
                        CommittedWakeAudioRuntimeMatches(notification.config);
                }
                if (runtime_healthy) {
                    g_applied_wake_restore_pending = false;
                    ConfirmAppliedWakeAudioConfig(notification.config);
                    g_applied_device_config = notification.config;
                } else if (resource_version_matches &&
                           wake_audio_runtime_allowed &&
                           notification.config.has_wake_profile) {
                    g_applied_wake_restore_pending = true;
                } else {
                    g_applied_wake_restore_pending = false;
                }
                ScheduleDeviceConfigReport(
                    runtime_healthy
                        ? veetee::settings::ReportedResourcePhase::kActive
                        : veetee::settings::ReportedResourcePhase::kFailed,
                    notification.desired_version,
                    notification.applied_version,
                    runtime_healthy
                        ? nullptr
                        : !resource_version_matches
                              ? veetee::config::DeviceConfigResourceLinkErrorName(
                                    resource_link)
                              : !wake_audio_runtime_allowed
                                    ? "wake_audio_enable_failed"
                                    : "model_not_loaded");
            } else {
                if (g_device_config_apply_pending &&
                    !g_device_config_loaded_with_resource &&
                    g_pending_device_config.version <=
                        notification.desired_version) {
                    ClearPendingDeviceConfig();
                }
                ScheduleDeviceConfigReport(
                    veetee::settings::ReportedResourcePhase::kFailed,
                    notification.desired_version,
                    notification.applied_version,
                    notification.error_code);
            }
            continue;
        }
        if (message.kind == AppMessageKind::kResourceApply) {
            if (message.resource_class == veetee::ota::ResourceClass::kUiPack) {
                ApplyStagedUiPack();
            } else {
                ApplyStagedWakeResource();
            }
            continue;
        }
        if (message.kind == AppMessageKind::kResourceHealthCheck) {
            if (message.resource_class == veetee::ota::ResourceClass::kUiPack) {
                CheckActiveUiPackHealth();
            } else {
                CheckActiveWakeResourceHealth();
            }
            continue;
        }
        if (message.kind == AppMessageKind::kResourceReconcile) {
            const auto& notification = message.resource_notification;
            if (notification.event ==
                veetee::ota::ResourceReconcileEvent::kDownloading) {
                ScheduleResourceNotificationReport(
                    veetee::settings::ReportedResourcePhase::kDownloading,
                    notification);
            } else if (notification.event ==
                       veetee::ota::ResourceReconcileEvent::kVerifying) {
                ScheduleResourceNotificationReport(
                    veetee::settings::ReportedResourcePhase::kVerifying,
                    notification);
            } else if (notification.event ==
                veetee::ota::ResourceReconcileEvent::kPayloadStaged) {
                ESP_LOGI(kTag,
                         "Resource payload staged desired=%s bundle=%s; apply pending",
                         notification.desired_version,
                         notification.bundle_version);
                const bool is_ui = notification.resource_class ==
                                   veetee::ota::ResourceClass::kUiPack;
                if (is_ui) {
                    g_ui_apply_pending = true;
                } else {
                    g_resource_apply_pending = true;
                }
                ScheduleResourceNotificationReport(
                    veetee::settings::ReportedResourcePhase::kStaged,
                    notification);
                if (is_ui) {
                    ScheduleUiApply();
                } else {
                    ScheduleResourceApply();
                }
            } else if (notification.event ==
                       veetee::ota::ResourceReconcileEvent::kAlreadyActive) {
                ESP_LOGI(kTag, "Resource already active desired=%s",
                         notification.desired_version);
                if (notification.resource_class ==
                    veetee::ota::ResourceClass::kUiPack) {
                    g_ui_apply_pending = false;
                } else {
                    g_resource_apply_pending = false;
                }
                ScheduleResourceNotificationReport(
                    veetee::settings::ReportedResourcePhase::kActive,
                    notification);
                if (notification.resource_class ==
                    veetee::ota::ResourceClass::kWakeModel) {
                    if (!g_device_config_apply_pending &&
                        g_applied_device_config.has_wake_profile &&
                        DeviceConfigMatchesActiveResource(
                            g_applied_device_config)) {
                        if (CommittedWakeAudioRuntimeAllowed(
                                g_applied_device_config)) {
                            g_applied_wake_restore_pending = true;
                            TryRestoreAppliedWakeRuntimeAfterReconcile();
                        } else {
                            g_applied_wake_restore_pending = false;
                            ScheduleDeviceConfigReport(
                                veetee::settings::ReportedResourcePhase::kFailed,
                                g_applied_device_config.version,
                                g_device_config_store.Snapshot().applied_version,
                                "wake_audio_enable_failed");
                        }
                    }
                    TryApplyPendingDeviceConfig();
                }
            } else {
                ESP_LOGW(kTag,
                         "Resource reconcile failed desired=%s error=%s stage=%s",
                         notification.desired_version, notification.error_code,
                         notification.event == veetee::ota::ResourceReconcileEvent::kManifestRejected
                             ? "verify"
                             : notification.event == veetee::ota::ResourceReconcileEvent::kPayloadRejected
                                   ? "payload"
                                   : "transport");
                ScheduleResourceNotificationReport(
                    veetee::settings::ReportedResourcePhase::kFailed,
                    notification, notification.error_code);
                if (notification.resource_class ==
                        veetee::ota::ResourceClass::kWakeModel &&
                    g_device_config_apply_pending &&
                    g_pending_device_config.has_wake_profile &&
                    std::strcmp(
                        g_pending_device_config.required_resource_version.data(),
                        notification.desired_version) == 0) {
                    FailPendingDeviceConfig("required_resource_failed");
                }
            }
            continue;
        }
        if (message.kind == AppMessageKind::kFirmwareReconcile) {
            const auto& notification = message.firmware_notification;
            using Phase = veetee::settings::ReportedResourcePhase;
            switch (notification.event) {
                case veetee::ota::FirmwareOtaEvent::kChecking:
                    ScheduleFirmwareReport(Phase::kChecking, notification);
                    break;
                case veetee::ota::FirmwareOtaEvent::kDownloading:
                    ScheduleFirmwareReport(Phase::kDownloading, notification);
                    break;
                case veetee::ota::FirmwareOtaEvent::kVerifying:
                    ScheduleFirmwareReport(Phase::kVerifying, notification);
                    break;
                case veetee::ota::FirmwareOtaEvent::kStaged: {
                    if (g_state_machine.state() !=
                        veetee::app::State::kUpgrading) {
                        ESP_LOGW(kTag,
                                 "Ignoring staged firmware event outside upgrading");
                        RecoverFirmwareTerminalAttemptIfAny();
                        break;
                    }
                    // The staged journal is already durable locally.  Persist
                    // rebooting as the first external boot boundary; enqueueing
                    // staged here lets the reporter assign it a later sequence
                    // on ESP32-S3 SMP and regress Manager state after reboot.
                    if (!g_reporter.PersistForReplay(
                            MakeFirmwareReport(Phase::kRebooting,
                                               notification),
                            false)) {
                        ESP_LOGE(
                            kTag,
                            "Unable to persist rebooting report; refusing OTA reboot");
                        const esp_err_t cancel_error =
                            g_firmware.CancelStagedBoot();
                        if (cancel_error != ESP_OK) {
                            ESP_LOGE(kTag,
                                     "Unable to cancel staged firmware boot: %s",
                                     esp_err_to_name(cancel_error));
                        }
                        QueueFirmwareTerminalOutcome(
                            veetee::ota::FirmwareOtaRecoveryDecision::kFailed,
                            notification, "reboot_report_persist_failed",
                            cancel_error != ESP_OK);
                        PostEvent(
                            veetee::app::Event::kFirmwareUpdateFailed);
                        break;
                    }
                    const esp_err_t commit_error = g_firmware.CommitStaged();
                    if (commit_error != ESP_OK) {
                        ESP_LOGE(kTag,
                                 "Unable to commit staged boot partition: %s",
                                 esp_err_to_name(commit_error));
                        const esp_err_t cancel_error =
                            g_firmware.CancelStagedBoot();
                        if (cancel_error != ESP_OK) {
                            ESP_LOGE(kTag,
                                     "Unable to restore staged boot partition: %s",
                                     esp_err_to_name(cancel_error));
                        }
                        QueueFirmwareTerminalOutcome(
                            veetee::ota::FirmwareOtaRecoveryDecision::kFailed,
                            notification, "boot_partition_commit_failed",
                            cancel_error != ESP_OK);
                        PostEvent(
                            veetee::app::Event::kFirmwareUpdateFailed);
                        break;
                    }
                    ESP_LOGI(
                        kTag,
                        "Firmware report and boot partition committed; rebooting");
                    esp_restart();
                    break;
                }
                case veetee::ota::FirmwareOtaEvent::kRebooting:
                    ESP_LOGW(kTag,
                             "Ignoring legacy rebooting event; staged handler owns commit");
                    break;
                case veetee::ota::FirmwareOtaEvent::kActive:
                    ScheduleFirmwareReport(Phase::kActive, notification);
                    break;
                case veetee::ota::FirmwareOtaEvent::kRolledBack:
                    ScheduleFirmwareReport(Phase::kRolledBack, notification,
                                           notification.error_code);
                    break;
                case veetee::ota::FirmwareOtaEvent::kFailed:
                    ScheduleFirmwareReport(Phase::kFailed, notification,
                                           notification.error_code);
                    PostEvent(veetee::app::Event::kFirmwareUpdateFailed);
                    break;
            }
            continue;
        }
        if (message.kind == AppMessageKind::kProvisioningCleanup) {
            const esp_err_t error = g_wifi.FinishProvisioningHandoff();
            if (error != ESP_OK && error != ESP_ERR_INVALID_STATE) {
                ESP_LOGW(kTag, "Unable to finish provisioning handoff: %s",
                         esp_err_to_name(error));
            }
            continue;
        }
        if (message.kind == AppMessageKind::kFirmwareHealthCheck) {
            if (g_firmware_terminal_outcome.pending) {
                if (!ProcessFirmwareTerminalOutcome()) {
                    ArmFirmwareHealthPoll();
                }
                continue;
            }
            if (!g_firmware.PendingBootVerification()) {
                g_firmware_health_monitoring = false;
                g_firmware_health_overall_deadline_us = 0;
                g_firmware_health_post_wifi_deadline_us = 0;
                if (!g_firmware.HasAttempt()) {
                    g_bootstrap.SetFirmwareUpdatesDeferred(false);
                }
                continue;
            }
            if (!g_firmware_health_monitoring) {
                veetee::ota::FirmwareOtaNotification notification{};
                std::snprintf(notification.current_version,
                              sizeof(notification.current_version), "%s",
                              CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION);
                std::snprintf(notification.desired_version,
                              sizeof(notification.desired_version), "%s",
                              CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION);
                notification.active_slot = g_firmware.ActiveSlot();
                notification.target_slot = notification.active_slot;
                BeginFirmwareHealthMonitoring(notification);
            }
            if (!g_firmware_pending_health_attempt_marked) {
                const esp_err_t mark_error =
                    g_firmware.MarkAttemptPendingHealth();
                if (mark_error == ESP_OK) {
                    g_firmware_pending_health_attempt_marked = true;
                } else {
                    ESP_LOGE(kTag,
                             "Unable to retry pending-health attempt: %s",
                             esp_err_to_name(mark_error));
                }
            }
            if (g_firmware_pending_health_attempt_marked &&
                !g_firmware_pending_health_report_persisted) {
                g_firmware_pending_health_report_persisted =
                    g_reporter.PersistForReplay(
                        MakeFirmwareReport(
                            veetee::settings::ReportedResourcePhase::kPendingHealth,
                            g_firmware_health_notification),
                        false);
            }

            const std::int64_t now_us = esp_timer_get_time();
            const std::uint64_t now_ms =
                static_cast<std::uint64_t>(now_us / 1000);
            const auto audio_health = g_board.AudioHealth(now_ms);
            const auto decision = veetee::ota::EvaluateFirmwareBootHealth({
                .pending_verify = true,
                .deadline_expired =
                    veetee::ota::FirmwareBootHealthDeadlineExpired(
                        now_us, g_firmware_health_overall_deadline_us,
                        g_firmware_health_post_wifi_deadline_us),
                .identity_valid =
                    g_firmware_pending_health_attempt_marked &&
                    g_firmware_pending_health_report_persisted &&
                    g_settings_store.Snapshot().HasDeviceIdentity(),
                .authenticated_bootstrap_complete =
                    g_authenticated_bootstrap_complete,
                .app_idle =
                    g_state_machine.state() == veetee::app::State::kIdle,
                .capture_task_running = audio_health.capture_task_running,
                .playback_task_running = audio_health.playback_task_running,
                .wake_resource_healthy = g_board.WakeResourceHealthy(),
                .ui_pack_healthy = g_board.UiPackHealthy(),
                .wake_task_required = g_board.wake_profile_expected(),
                .wake_task_running = g_board.wake_task_running(),
            });
            if (decision ==
                veetee::ota::FirmwareBootHealthDecision::kWait) {
                const bool timer_armed = ArmFirmwareHealthPoll();
                if (veetee::ota::FirmwareHealthPollFailureRequiresRollback(
                        timer_armed,
                        g_firmware_health_timer_start_failures,
                        kFirmwareHealthTimerStartFailureLimit)) {
                    RequestFirmwareRollback("health_timer_failed");
                }
                continue;
            }
            if (decision ==
                veetee::ota::FirmwareBootHealthDecision::kConfirm) {
                const esp_err_t confirm_error =
                    g_firmware.ConfirmPendingBoot();
                if (confirm_error != ESP_OK) {
                    ESP_LOGE(kTag,
                             "Unable to confirm pending firmware image: %s",
                             esp_err_to_name(confirm_error));
                    RequestFirmwareRollback("boot_confirm_failed");
                    continue;
                }
                g_firmware_health_monitoring = false;
                g_firmware_health_overall_deadline_us = 0;
                g_firmware_health_post_wifi_deadline_us = 0;
                QueueFirmwareTerminalOutcome(
                    veetee::ota::FirmwareOtaRecoveryDecision::kActive,
                    g_firmware_health_notification);
                if (!ProcessFirmwareTerminalOutcome()) {
                    ArmFirmwareHealthPoll();
                }
                continue;
            }
            RequestFirmwareRollback("boot_health_failed");
            continue;
        }
        const veetee::app::Event event = message.event;
        const veetee::app::TransitionResult result = g_state_machine.Handle(event);
        g_conversation_generation.store(result.cancellation_generation);
        if (!result.accepted) {
            if (event ==
                veetee::app::Event::kTransportReconnectScheduled) {
                g_board.AbortPlayback();
                g_transport.Close(
                    veetee::transport::WebSocketCloseMode::kAbortive);
                PostEvent(veetee::app::Event::kTransportLost);
            }
            ESP_LOGD(kTag, "Ignored event %s in %s", veetee::app::ToString(event),
                     veetee::app::ToString(result.from));
            continue;
        }

        const bool realtime_was_active =
            IsRealtimeConversationState(result.from);
        const bool realtime_is_active =
            IsRealtimeConversationState(result.to);
        bool maintenance_quiesced = true;
        if (realtime_is_active && !realtime_was_active) {
            // Close any active deferrable HTTP socket, then wait only for the
            // bounded handler cleanup. Desired targets and durable reports stay
            // queued and resume at the next idle/session boundary.
            const std::int64_t barrier_started_us = esp_timer_get_time();
            g_maintenance.SetRealtimeActive(true);
            const std::int64_t elapsed_us =
                std::max<std::int64_t>(
                    0, esp_timer_get_time() - barrier_started_us);
            const std::uint32_t elapsed_ms = static_cast<std::uint32_t>(
                (elapsed_us + 999) / 1000);
            const std::uint32_t remaining_ms =
                veetee::maintenance::RemainingRealtimeMaintenanceBarrierMs(
                    elapsed_ms);
            maintenance_quiesced =
                veetee::maintenance::IsRealtimeMaintenanceBarrierWithinBudget(
                    elapsed_ms) &&
                g_maintenance.WaitForQuiescence(remaining_ms);
            if (!maintenance_quiesced) {
                ESP_LOGE(kTag,
                         "Maintenance preemption exceeded %" PRIu32
                         " ms (elapsed=%" PRIu32
                         "); refusing overlapping voice TLS",
                         veetee::maintenance::kRealtimeMaintenanceBarrierMs,
                         elapsed_ms);
            } else {
                ESP_LOGI(kTag,
                         "Maintenance quiesced before voice TLS elapsed=%" PRIu32
                         " ms",
                         elapsed_ms);
            }
        } else if (!realtime_is_active && realtime_was_active) {
            g_maintenance.SetRealtimeActive(false);
        }
        g_maintenance.SetFirmwareExclusive(
            result.to == veetee::app::State::kUpgrading);

        ESP_LOGI(kTag, "State %s -> %s event=%s gate=%s generation=%" PRIu32,
                 veetee::app::ToString(result.from), veetee::app::ToString(result.to),
                 veetee::app::ToString(event),
                 result.assistant_gate_open ? "open" : "closed",
                 result.cancellation_generation);
        g_board.ApplyState(result.to);
        if (event == veetee::app::Event::kActivationComplete) {
            g_authenticated_bootstrap_complete = true;
            if (g_firmware.PendingBootVerification()) {
                RequestFirmwareHealthCheck();
            }
        } else if (event == veetee::app::Event::kWifiConnected) {
            if (g_firmware.PendingBootVerification()) {
                g_firmware_health_post_wifi_deadline_us =
                    esp_timer_get_time() +
                    kFirmwarePostWifiHealthDeadlineUs;
                g_bootstrap.SetFirmwareUpdatesDeferred(true);
                RequestFirmwareHealthCheck();
            }
        } else if (event == veetee::app::Event::kWifiDisconnected) {
            g_authenticated_bootstrap_complete = false;
            g_firmware_health_post_wifi_deadline_us = 0;
            if (g_firmware.PendingBootVerification()) {
                RequestFirmwareHealthCheck();
            }
        }

        if (result.to == veetee::app::State::kUpgrading &&
            event == veetee::app::Event::kFirmwareUpdateRequested) {
            g_transport.Close(
                veetee::transport::WebSocketCloseMode::kAbortive);
            g_board.AbortPlayback();
            g_bootstrap.Cancel();
            g_device_config_reconciler.Cancel();
            g_resources.Cancel();
            g_ui_resources.Cancel();
            CancelDeviceConfigTransactions();
            if (g_resource_apply_timer != nullptr) {
                esp_timer_stop(g_resource_apply_timer);
            }
            if (g_resource_health_timer != nullptr) {
                esp_timer_stop(g_resource_health_timer);
            }
            if (g_ui_apply_timer != nullptr) esp_timer_stop(g_ui_apply_timer);
            if (g_ui_health_timer != nullptr) esp_timer_stop(g_ui_health_timer);
            if (g_resources.phase() ==
                veetee::settings::ResourceRecordPhase::kPendingHealth) {
                RollbackWakeResource(g_resources.PreviousPartitionLabel(),
                                     "firmware_update_boundary");
            }
            if (g_ui_resources.phase() ==
                veetee::settings::ResourceRecordPhase::kPendingHealth) {
                RollbackUiPack(g_ui_resources.PreviousPartitionLabel(),
                               "firmware_update_boundary");
            }

            const auto schedule = g_firmware.Schedule(
                message.firmware_target_version,
                message.firmware_manifest_url);
            if (schedule ==
                veetee::ota::FirmwareScheduleResult::kAlreadyCurrent) {
                PostEvent(veetee::app::Event::kFirmwareAlreadyCurrent);
            } else if (schedule ==
                       veetee::ota::FirmwareScheduleResult::kRejected) {
                PostEvent(veetee::app::Event::kFirmwareUpdateFailed);
            }
            continue;
        }

        if (event == veetee::app::Event::kTransportReconnectScheduled ||
            event == veetee::app::Event::kTransportLost) {
            g_board.AbortPlayback();
        }

        if (event == veetee::app::Event::kTurnFailed &&
            !g_board.PlayRecoverySignal()) {
            ESP_LOGW(kTag, "Unable to queue local recovery signal");
        }

        if (result.to == veetee::app::State::kPairingRecovery) {
            g_transport.Close(
                veetee::transport::WebSocketCloseMode::kAbortive);
            g_bootstrap.Cancel();
            g_device_config_reconciler.Cancel();
            g_resources.Cancel();
            g_ui_resources.Cancel();
            CancelFirmwareAndRecover();
            CancelDeviceConfigTransactions();
        } else if (result.to == veetee::app::State::kWifiConfiguring) {
            g_transport.Close();
            g_bootstrap.Cancel();
            g_device_config_reconciler.Cancel();
            g_resources.Cancel();
            g_ui_resources.Cancel();
            CancelDeviceConfigTransactions();
            if (event == veetee::app::Event::kEnterWifiConfig) {
                if (result.from == veetee::app::State::kPairingRecovery) {
                    const esp_err_t identity_error =
                        g_settings_store.ClearDeviceIdentity(&g_settings);
                    if (identity_error != ESP_OK) {
                        ESP_LOGE(kTag,
                                 "Unable to clear rejected device identity: %s",
                                 esp_err_to_name(identity_error));
                    } else {
                        const esp_err_t config_error =
                            g_device_config_store.Reset(
                                CONFIG_VEETEE_MIN_CONFIG_SECURITY_EPOCH);
                        if (config_error != ESP_OK) {
                            ESP_LOGE(
                                kTag,
                                "Unable to clear config during physical recovery: %s",
                                esp_err_to_name(config_error));
                        } else {
                            g_applied_device_config =
                                veetee::config::DeviceConfig{};
                            g_wake_audio_privacy_revoked = true;
                            if (!g_board.RevokeWakeAudioConsent()) {
                                ESP_LOGE(kTag,
                                         "Unable to revoke wake-audio consent during physical recovery");
                            }
                        }
                    }
                }
            }
            const esp_err_t error = g_wifi.StartProvisioning();
            if (error != ESP_OK) {
                ESP_LOGE(kTag, "Unable to start provisioning: %s; retrying",
                         esp_err_to_name(error));
                vTaskDelay(pdMS_TO_TICKS(kProvisioningRetryDelayMs));
                PostEvent(veetee::app::Event::kRetryWifiProvisioning);
            }
        } else if (result.to == veetee::app::State::kNetworkConnecting) {
            g_transport.Close(
                result.network_lost
                    ? veetee::transport::WebSocketCloseMode::kAbortive
                    : veetee::transport::WebSocketCloseMode::kGraceful);
            g_bootstrap.Cancel();
            g_device_config_reconciler.Cancel();
            g_resources.Cancel();
            g_ui_resources.Cancel();
            CancelFirmwareAndRecover();
            const esp_err_t error = g_wifi.StartStation();
            if (error != ESP_OK) {
                ESP_LOGE(kTag, "Unable to start station: %s; opening setup portal",
                         esp_err_to_name(error));
                PostEvent(veetee::app::Event::kWifiConnectionTimeout);
            }
        } else if (result.to == veetee::app::State::kActivating) {
            g_transport.Close();
            if (event == veetee::app::Event::kActivationCodeAvailable) {
                const esp_err_t error = g_board.ShowActivationCode(
                    message.activation_code);
                if (error != ESP_OK) {
                    ESP_LOGE(kTag, "Unable to render activation code: %s",
                             esp_err_to_name(error));
                }
            } else {
                g_bootstrap.Start();
            }
        } else if (result.to == veetee::app::State::kIdle &&
                   event == veetee::app::Event::kActivationComplete) {
            g_bootstrap.Cancel();
            const esp_err_t error = g_board.ShowStandby();
            if (error != ESP_OK) {
                ESP_LOGE(kTag, "Unable to render standby screen: %s",
                         esp_err_to_name(error));
            }
            ScheduleResourceReport(
                veetee::settings::ReportedResourcePhase::kActive,
                g_resources.Snapshot());
            ScheduleResourceReport(
                veetee::settings::ReportedResourcePhase::kActive,
                g_ui_resources.Snapshot(), nullptr, nullptr, nullptr,
                veetee::settings::ReportedArtifactKind::kUiPack);
        } else if (result.to == veetee::app::State::kIdle &&
                   event == veetee::app::Event::kFirmwareAlreadyCurrent) {
            // Finish a normal authenticated bootstrap pass for config,
            // resource and UI desired state after a same-version race.
            g_bootstrap.Start();
        } else if (result.to == veetee::app::State::kConnecting &&
                   event !=
                       veetee::app::Event::kTransportReconnectScheduled) {
            if (!maintenance_quiesced) {
                PostEvent(veetee::app::Event::kTransportLost);
                continue;
            }
            const veetee::transport::WakeSource source =
                event == veetee::app::Event::kActivationWakeDetected
                    ? veetee::transport::WakeSource::kWakeWord
                    : veetee::transport::WakeSource::kButton;
            const esp_err_t error = g_transport.Open(source);
            if (error != ESP_OK) {
                LogTransportError("open", error);
                PostEvent(veetee::app::Event::kTransportLost);
            }
        } else if (result.to == veetee::app::State::kAborting) {
            g_board.AbortPlayback();
            esp_err_t transport_error = ESP_OK;
            const char* operation = "abort";
            if (!result.assistant_gate_open) {
                operation = "stop listening";
                transport_error =
                    g_transport.StopListening("user_disable");
            } else if (event == veetee::app::Event::kInterruptDetected) {
                operation = "interrupt";
                transport_error = g_transport.Abort(
                    "local_interrupt_detected", "interrupt_profile");
            } else if (event == veetee::app::Event::kActivationWakeDetected) {
                operation = "closing cancellation";
                transport_error = g_transport.Abort(
                    "session_closing_cancelled", "wake_word");
            } else {
                operation = "button interrupt";
                transport_error =
                    g_transport.Abort("button_interrupt", "button");
            }
            if (transport_error == ESP_OK) {
                PostEvent(veetee::app::Event::kAbortComplete);
            } else {
                LogTransportError(operation, transport_error);
                g_transport.Close(
                    veetee::transport::WebSocketCloseMode::kAbortive);
                PostEvent(veetee::app::Event::kTransportLost);
            }
        } else if (result.to == veetee::app::State::kIdle) {
            if (event == veetee::app::Event::kButtonLongPress) {
                LogTransportError("stop listening",
                                  g_transport.StopListening("user_disable"));
            } else if (!result.assistant_gate_open) {
                g_transport.Close();
            }
        }
        if (result.to == veetee::app::State::kIdle) {
            CheckActiveWakeResourceHealth();
            CheckDeviceConfigHealth();
            TryRestoreAppliedWakeRuntimeAfterReconcile();
        }
        if (result.to == veetee::app::State::kIdle &&
            g_device_config_apply_pending) {
            TryApplyPendingDeviceConfig();
        }
        if (result.to == veetee::app::State::kIdle &&
            g_resource_apply_pending) {
            ScheduleResourceApply();
        }
        if (result.to == veetee::app::State::kIdle && g_ui_apply_pending) {
            ScheduleUiApply();
        }
        if (result.to == veetee::app::State::kIdle) {
            ReconcileInvalidatedDeviceConfigAtBoundary();
        }
    }
}

void LogPlatformInfo() {
    const esp_app_desc_t* app = esp_app_get_description();
    const std::size_t internal_free = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    const std::size_t psram_size = esp_psram_is_initialized() ? esp_psram_get_size() : 0;
    const std::size_t psram_free = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    ESP_LOGI(kTag, "Veetee firmware %s board=%s reset_reason=%d",
             app->version, veetee::board::kBoardName,
             static_cast<int>(esp_reset_reason()));
    ESP_LOGI(kTag, "Heap internal_free=%u PSRAM size=%u free=%u",
             static_cast<unsigned>(internal_free), static_cast<unsigned>(psram_size),
             static_cast<unsigned>(psram_free));
}

bool ShouldPlayBootChime(esp_reset_reason_t reason) {
    switch (reason) {
        case ESP_RST_POWERON:
        case ESP_RST_EXT:
        case ESP_RST_USB:
        case ESP_RST_JTAG:
            return true;
        default:
            return false;
    }
}

}  // namespace

extern "C" void app_main() {
    g_firmware_boot_started_us = esp_timer_get_time();
    const esp_reset_reason_t reset_reason = esp_reset_reason();
    LogPlatformInfo();

    g_event_queue = xQueueCreate(veetee::app::kApplicationQueueDepth,
                                 sizeof(AppMessage));
    g_wake_event_queue = xQueueCreate(
        veetee::app::kWakeApplicationQueueDepth,
        sizeof(veetee::app::Event));
    g_critical_event_queue = xQueueCreate(
        veetee::app::kCriticalApplicationQueueDepth,
        sizeof(veetee::app::Event));
    if (g_event_queue == nullptr || g_wake_event_queue == nullptr ||
        g_critical_event_queue == nullptr) {
        ESP_LOGE(kTag, "Unable to allocate bounded application queues");
        abort();
    }

    const esp_timer_create_args_t apply_timer_args = {
        .callback = &OnResourceApplyTimer,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "resource_apply",
        .skip_unhandled_events = false,
    };
    const esp_timer_create_args_t health_timer_args = {
        .callback = &OnResourceHealthTimer,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "resource_health",
        .skip_unhandled_events = false,
    };
    const esp_timer_create_args_t ui_apply_timer_args = {
        .callback = &OnUiApplyTimer,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "ui_apply",
        .skip_unhandled_events = false,
    };
    const esp_timer_create_args_t ui_health_timer_args = {
        .callback = &OnUiHealthTimer,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "ui_health",
        .skip_unhandled_events = false,
    };
    const esp_timer_create_args_t firmware_health_timer_args = {
        .callback = &OnFirmwareHealthTimer,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "firmware_health",
        .skip_unhandled_events = false,
    };
    const esp_timer_create_args_t device_config_poll_timer_args = {
        .callback = &OnDeviceConfigPollTimer,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "config_poll",
        .skip_unhandled_events = true,
    };
    const esp_timer_create_args_t device_config_health_timer_args = {
        .callback = &OnDeviceConfigHealthTimer,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "config_health",
        .skip_unhandled_events = false,
    };
#if CONFIG_VEETEE_BENCHMARK_RUNTIME_STATS
    const esp_timer_create_args_t runtime_stats_timer_args = {
        .callback = &OnRuntimeStatsTimer,
        .arg = nullptr,
        .dispatch_method = ESP_TIMER_TASK,
        .name = "runtime_stats",
        .skip_unhandled_events = true,
    };
#endif
    ESP_ERROR_CHECK(
        esp_timer_create(&apply_timer_args, &g_resource_apply_timer));
    ESP_ERROR_CHECK(
        esp_timer_create(&health_timer_args, &g_resource_health_timer));
    ESP_ERROR_CHECK(esp_timer_create(&ui_apply_timer_args, &g_ui_apply_timer));
    ESP_ERROR_CHECK(esp_timer_create(&ui_health_timer_args, &g_ui_health_timer));
    ESP_ERROR_CHECK(esp_timer_create(&firmware_health_timer_args,
                                     &g_firmware_health_timer));
    ESP_ERROR_CHECK(esp_timer_create(&device_config_poll_timer_args,
                                     &g_device_config_poll_timer));
    ESP_ERROR_CHECK(esp_timer_create(&device_config_health_timer_args,
                                     &g_device_config_health_timer));
#if CONFIG_VEETEE_BENCHMARK_RUNTIME_STATS
    ESP_ERROR_CHECK(esp_timer_create(&runtime_stats_timer_args,
                                     &g_runtime_stats_timer));
#endif

    ESP_ERROR_CHECK(g_settings_store.Initialize(&g_settings));
    ESP_ERROR_CHECK(g_device_config_store.Initialize(
        CONFIG_VEETEE_MIN_CONFIG_SECURITY_EPOCH));
    if (!g_device_config_store.LoadApplied(&g_applied_device_config)) {
        ESP_LOGE(kTag, "Unable to load applied device config");
        abort();
    }
    ConfirmAppliedWakeAudioConfig(g_applied_device_config);
    // Reserve the contiguous internal block before any maintenance/application
    // task stacks are created. WebSocket control queues/task remain in PSRAM.
    ESP_ERROR_CHECK(g_transport.Initialize(&g_settings_store, &OnTransportEvent,
                                           &OnDownlinkAudio, &OnMcpEnvelope,
                                           &g_board, nullptr));
    ESP_ERROR_CHECK(g_maintenance.Initialize());
    ESP_ERROR_CHECK(g_runtime_stats.Initialize());
    ESP_ERROR_CHECK(g_wifi.Initialize(&g_settings_store, &g_settings, &OnWifiEvent, nullptr));
    ESP_ERROR_CHECK(g_resources.Initialize(&g_settings_store, &g_maintenance,
                                           &OnResourceReconcileEvent, nullptr));
    ESP_ERROR_CHECK(g_ui_resources.Initialize(
        &g_settings_store, &g_maintenance, &OnResourceReconcileEvent, nullptr,
        veetee::ota::ResourceClass::kUiPack));
    ESP_ERROR_CHECK(g_reporter.Initialize(&g_settings_store, &g_maintenance));
    ESP_ERROR_CHECK(g_device_config_reconciler.Initialize(
        &g_settings_store, &g_device_config_store,
        &g_maintenance, &OnDeviceConfigReconcileEvent, nullptr));
    ESP_ERROR_CHECK(g_firmware.Initialize(&g_settings_store, &g_maintenance,
                                          &OnFirmwareOtaEvent, nullptr));
    g_bootstrap.SetFirmwareUpdatesDeferred(
        g_firmware.PendingBootVerification() || g_firmware.HasAttempt());
    ESP_ERROR_CHECK(g_bootstrap.Initialize(&g_settings_store, &g_settings,
                                           &g_maintenance,
                                           &OnBootstrapEvent, nullptr));
    const BootWakeRuntimePlan boot_wake = PrepareBootWakeRuntime();
    ESP_ERROR_CHECK(g_board.Initialize(
        &OnButtonEvent, &OnDetectorEvent, &OnEncodedAudio,
        &OnPlaybackFinished, boot_wake.active_partition,
        boot_wake.fallback_partition,
        IsFactorySignalVersion(g_ui_resources.Snapshot().active_version)
            ? nullptr
            : g_ui_resources.ActivePartitionLabel(),
        IsFactorySignalVersion(g_ui_resources.Snapshot().previous_version)
            ? nullptr
            : g_ui_resources.PreviousPartitionLabel(),
        &boot_wake.config,
        nullptr));
    ESP_ERROR_CHECK(g_board.StartAudio(ShouldPlayBootChime(reset_reason)));
    if (boot_wake.resource_version_mismatch) {
        ScheduleDeviceConfigReport(
            veetee::settings::ReportedResourcePhase::kFailed,
            g_applied_device_config.version,
            g_applied_device_config.version,
            veetee::config::DeviceConfigResourceLinkErrorName(
                boot_wake.link_error));
    } else if (g_applied_device_config.version > 0 &&
        g_applied_device_config.has_wake_profile &&
        (!g_board.wake_task_expected() ||
         g_board.loaded_wake_partition() == nullptr)) {
        ScheduleDeviceConfigReport(
            veetee::settings::ReportedResourcePhase::kFailed,
            g_applied_device_config.version,
            g_applied_device_config.version, "model_not_loaded");
    }
    veetee::ota::FirmwareOtaNotification recovery_notification{};
    const auto recovery_decision =
        g_firmware.RecoveryStatus(&recovery_notification);
    if (recovery_decision ==
        veetee::ota::FirmwareOtaRecoveryDecision::kPendingHealth) {
        BeginFirmwareHealthMonitoring(recovery_notification);
    } else if (recovery_decision ==
                   veetee::ota::FirmwareOtaRecoveryDecision::kActive ||
               recovery_decision ==
                   veetee::ota::FirmwareOtaRecoveryDecision::kRolledBack ||
               recovery_decision ==
                   veetee::ota::FirmwareOtaRecoveryDecision::kFailed ||
               recovery_decision ==
                   veetee::ota::FirmwareOtaRecoveryDecision::kInconsistent) {
        QueueFirmwareTerminalOutcome(
            recovery_decision, recovery_notification,
            recovery_notification.error_code[0] == '\0'
                ? nullptr
                : recovery_notification.error_code);
    } else if (g_firmware.PendingBootVerification()) {
        // Fail closed for a legacy/corrupt unjournaled pending image.  It still
        // receives the bounded health window, but cannot be mistaken for a
        // completed durable attempt.
        std::snprintf(recovery_notification.current_version,
                      sizeof(recovery_notification.current_version), "%s",
                      CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION);
        std::snprintf(recovery_notification.desired_version,
                      sizeof(recovery_notification.desired_version), "%s",
                      CONFIG_VEETEE_FIRMWARE_COMPAT_VERSION);
        recovery_notification.active_slot = g_firmware.ActiveSlot();
        recovery_notification.target_slot = recovery_notification.active_slot;
        BeginFirmwareHealthMonitoring(recovery_notification);
    }

    const auto resource_phase = g_resources.phase();
    if (resource_phase ==
        veetee::settings::ResourceRecordPhase::kPendingHealth) {
        if (SamePartition(g_board.loaded_wake_partition(),
                          g_resources.ActivePartitionLabel())) {
            g_resource_health_check_due.store(false,
                                              std::memory_order_release);
            const esp_err_t error = esp_timer_start_once(
                g_resource_health_timer, kResourceHealthWindowUs);
            if (error != ESP_OK) {
                ESP_LOGE(kTag,
                         "Unable to arm boot resource health window: %s",
                         esp_err_to_name(error));
                RollbackWakeResource(g_resources.PreviousPartitionLabel(),
                                     "health_timer_failed");
            }
        } else {
            RollbackWakeResource(g_resources.PreviousPartitionLabel(),
                                 "boot_active_load_failed");
        }
    } else if (resource_phase ==
               veetee::settings::ResourceRecordPhase::kStable) {
        const char* loaded_partition = g_board.loaded_wake_partition();
        if (loaded_partition != nullptr &&
            !SamePartition(loaded_partition,
                           g_resources.ActivePartitionLabel())) {
            RollbackWakeResource(loaded_partition, "boot_active_load_failed");
        }
    } else if (resource_phase ==
               veetee::settings::ResourceRecordPhase::kStaged) {
        g_resource_apply_pending = true;
    }
    const auto ui_phase = g_ui_resources.phase();
    if (ui_phase == veetee::settings::ResourceRecordPhase::kPendingHealth) {
        if (SamePartition(g_board.loaded_ui_partition(),
                          g_ui_resources.ActivePartitionLabel())) {
            g_ui_health_check_due.store(false, std::memory_order_release);
            const esp_err_t error = esp_timer_start_once(
                g_ui_health_timer, kResourceHealthWindowUs);
            if (error != ESP_OK) {
                ESP_LOGE(kTag, "Unable to arm boot UI health window: %s",
                         esp_err_to_name(error));
                RollbackUiPack(g_ui_resources.PreviousPartitionLabel(),
                               "health_timer_failed");
            }
        } else {
            RollbackUiPack(g_ui_resources.PreviousPartitionLabel(),
                           "boot_active_load_failed");
        }
    } else if (ui_phase == veetee::settings::ResourceRecordPhase::kStable) {
        const auto ui_record = g_ui_resources.Snapshot();
        if (!IsFactorySignalVersion(ui_record.active_version) &&
            !SamePartition(g_board.loaded_ui_partition(),
                           g_ui_resources.ActivePartitionLabel())) {
            RollbackUiPack(g_ui_resources.PreviousPartitionLabel(),
                           "boot_active_load_failed");
        }
    } else if (ui_phase == veetee::settings::ResourceRecordPhase::kStaged) {
        g_ui_apply_pending = true;
    }
    if (!g_mcp.Initialize(&ReadDeviceStatus, &ReadDeviceDiagnostics,
                          &StartAudioDiagnostic, &SetSpeakerVolume,
                          &SendMcpResponse, nullptr)) {
        ESP_LOGE(kTag, "Unable to initialize device MCP");
        abort();
    }

    if (xTaskCreate(&RunApplication, "veetee_app", 12288, nullptr, 6, nullptr) != pdPASS) {
        ESP_LOGE(kTag, "Unable to create application task");
        abort();
    }
    ESP_ERROR_CHECK(esp_timer_start_once(g_device_config_poll_timer,
                                         kDeviceConfigPollIntervalUs));
#if CONFIG_VEETEE_BENCHMARK_RUNTIME_STATS
    ESP_ERROR_CHECK(
        esp_timer_start_periodic(g_runtime_stats_timer, 5'000'000));
#endif
    PostEvent(g_settings_store.Snapshot().HasProvisioning()
                  ? veetee::app::Event::kBootWithCredentials
                  : veetee::app::Event::kBootNeedsProvisioning);
}
