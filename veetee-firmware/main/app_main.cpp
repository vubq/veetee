#include <cinttypes>
#include <cctype>
#include <cstring>
#include <cstdio>
#include <cstdlib>

#include "app/state_machine.h"
#include "board/board_config.h"
#include "board/veetee_board.h"
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
#include "mcp/device_mcp.h"
#include "network/wifi_manager.h"
#include "ota/bootstrap_client.h"
#include "ota/resource_reconciler.h"
#include "settings/settings_store.h"
#include "telemetry/reported_state_reporter.h"
#include "transport/websocket_transport.h"

namespace {

constexpr char kTag[] = "veetee_app";
constexpr UBaseType_t kEventQueueDepth = 16;
constexpr std::uint64_t kResourceApplyDelayUs = 250000;
constexpr std::uint64_t kResourceHealthWindowUs = 5000000;

enum class AppMessageKind : std::uint8_t {
    kStateEvent,
    kMcpEnvelope,
    kResourceReconcile,
    kResourceApply,
    kResourceHealthCheck,
};

struct AppMessage {
    AppMessageKind kind = AppMessageKind::kStateEvent;
    veetee::app::Event event = veetee::app::Event::kBootNeedsProvisioning;
    char activation_code[7] = {};
    char* control_payload = nullptr;
    std::size_t control_length = 0;
    veetee::ota::ResourceReconcileNotification resource_notification{};
};

QueueHandle_t g_event_queue = nullptr;
veetee::app::StateMachine g_state_machine;
veetee::board::VeeteeBoard g_board;
veetee::settings::SettingsStore g_settings_store;
veetee::settings::DeviceSettings g_settings;
veetee::network::WifiManager g_wifi;
veetee::ota::BootstrapClient g_bootstrap;
veetee::ota::ResourceReconciler g_resources;
veetee::telemetry::ReportedStateReporter g_reporter;
veetee::transport::WebSocketTransport g_transport;
veetee::mcp::DeviceMcp g_mcp;
esp_timer_handle_t g_resource_apply_timer = nullptr;
esp_timer_handle_t g_resource_health_timer = nullptr;
bool g_resource_apply_pending = false;

bool PostMessage(const AppMessage& message) {
    if (g_event_queue == nullptr ||
        xQueueSend(g_event_queue, &message, 0) != pdTRUE) {
        if (message.kind == AppMessageKind::kMcpEnvelope) {
            ESP_LOGW(kTag, "Dropping MCP request: application queue full");
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
    if (!PostMessage(AppMessage{.kind = AppMessageKind::kResourceApply}) &&
        g_resource_apply_timer != nullptr) {
        esp_timer_start_once(g_resource_apply_timer, kResourceApplyDelayUs);
    }
}

void OnResourceHealthTimer(void*) {
    if (!PostMessage(AppMessage{.kind = AppMessageKind::kResourceHealthCheck}) &&
        g_resource_health_timer != nullptr) {
        esp_timer_start_once(g_resource_health_timer, kResourceApplyDelayUs);
    }
}

bool SamePartition(const char* left, const char* right) {
    return left != nullptr && right != nullptr && std::strcmp(left, right) == 0;
}

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
    const veetee::settings::ResourceRecord* operation = nullptr) {
    const auto& source = operation == nullptr ? current : *operation;
    veetee::settings::ReportedResourceState report{};
    report.phase = phase;
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
    veetee::settings::ResourceRecord current = g_resources.Snapshot();
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
                                  &operation);
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

void RollbackWakeResource(const char* fallback_partition,
                          const char* reason) {
    const veetee::settings::ResourceRecord attempted = g_resources.Snapshot();
    if (fallback_partition != nullptr) {
        const esp_err_t reload_error =
            g_board.ReloadWakeResource(fallback_partition);
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
}

void ApplyStagedWakeResource() {
    if (!g_resource_apply_pending ||
        g_state_machine.state() != veetee::app::State::kIdle) {
        return;
    }
    const char* staged_partition = g_resources.StagedPartitionLabel();
    const char* active_partition = g_resources.ActivePartitionLabel();
    if (staged_partition == nullptr) {
        g_resource_apply_pending = false;
        return;
    }

    ESP_LOGI(kTag, "Applying staged wake resource partition=%s", staged_partition);
    ScheduleResourceReport(
        veetee::settings::ReportedResourcePhase::kApplying,
        g_resources.Snapshot());
    esp_err_t error = g_board.ReloadWakeResource(staged_partition);
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
    esp_timer_stop(g_resource_health_timer);
    error = esp_timer_start_once(g_resource_health_timer,
                                 kResourceHealthWindowUs);
    if (error != ESP_OK) {
        ESP_LOGE(kTag, "Unable to schedule resource health check: %s",
                 esp_err_to_name(error));
        PostMessage(AppMessage{.kind = AppMessageKind::kResourceHealthCheck});
    }
}

void CheckActiveWakeResourceHealth() {
    if (g_resources.phase() !=
        veetee::settings::ResourceRecordPhase::kPendingHealth) {
        return;
    }
    const char* active_partition = g_resources.ActivePartitionLabel();
    if (g_board.WakeResourceHealthy() &&
        SamePartition(g_board.loaded_wake_partition(), active_partition)) {
        const veetee::settings::ResourceRecord activated = g_resources.Snapshot();
        const esp_err_t error = g_resources.ConfirmActive();
        if (error == ESP_OK) {
            ESP_LOGI(kTag, "Resource health confirmed active=%s",
                     active_partition);
            ScheduleResourceReport(
                veetee::settings::ReportedResourcePhase::kActive,
                g_resources.Snapshot(), activated.active_version, nullptr,
                &activated);
            return;
        }
        ESP_LOGE(kTag, "Unable to confirm resource health: %s",
                 esp_err_to_name(error));
    }
    RollbackWakeResource(g_resources.PreviousPartitionLabel(),
                         "health_check_failed");
}

bool PostEvent(veetee::app::Event event) {
    return PostMessage(
        AppMessage{.kind = AppMessageKind::kStateEvent, .event = event});
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

bool OnTransportEvent(
    const veetee::transport::WebSocketTransportNotification& notification,
    void*) {
    switch (notification.event) {
        case veetee::transport::WebSocketTransportEvent::kReady:
            return PostEvent(veetee::app::Event::kTransportConnected);
        case veetee::transport::WebSocketTransportEvent::kLost:
            g_board.AbortPlayback();
            return PostEvent(veetee::app::Event::kTransportLost);
        case veetee::transport::WebSocketTransportEvent::kListenStarted:
            return PostEvent(veetee::app::Event::kAdmissionRejected);
        case veetee::transport::WebSocketTransportEvent::kSttFinal:
            return PostEvent(veetee::app::Event::kVadFinal);
        case veetee::transport::WebSocketTransportEvent::kLlmStarted:
            return PostEvent(veetee::app::Event::kAdmissionAccepted);
        case veetee::transport::WebSocketTransportEvent::kTtsStarted:
            g_board.BeginPlayback();
            return PostEvent(veetee::app::Event::kTtsStarted);
        case veetee::transport::WebSocketTransportEvent::kTtsStopped:
            g_board.EndPlayback();
            return true;
        case veetee::transport::WebSocketTransportEvent::kAssistantSleep:
            return PostEvent(veetee::app::Event::kAssistantSleepRequested);
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
                             .control_length = length};
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
    while (xQueueReceive(g_event_queue, &message, portMAX_DELAY) == pdTRUE) {
        if (message.kind == AppMessageKind::kMcpEnvelope) {
            const bool handled = g_mcp.HandleEnvelope(message.control_payload,
                                                      message.control_length);
            std::free(message.control_payload);
            if (!handled) ESP_LOGW(kTag, "MCP request was rejected");
            continue;
        }
        if (message.kind == AppMessageKind::kResourceApply) {
            ApplyStagedWakeResource();
            continue;
        }
        if (message.kind == AppMessageKind::kResourceHealthCheck) {
            CheckActiveWakeResourceHealth();
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
                g_resource_apply_pending = true;
                ScheduleResourceNotificationReport(
                    veetee::settings::ReportedResourcePhase::kStaged,
                    notification);
                ScheduleResourceApply();
            } else if (notification.event ==
                       veetee::ota::ResourceReconcileEvent::kAlreadyActive) {
                ESP_LOGI(kTag, "Resource already active desired=%s",
                         notification.desired_version);
                g_resource_apply_pending = false;
                ScheduleResourceNotificationReport(
                    veetee::settings::ReportedResourcePhase::kActive,
                    notification);
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
            }
            continue;
        }
        const veetee::app::Event event = message.event;
        const veetee::app::TransitionResult result = g_state_machine.Handle(event);
        if (!result.accepted) {
            ESP_LOGD(kTag, "Ignored event %s in %s", veetee::app::ToString(event),
                     veetee::app::ToString(result.from));
            continue;
        }

        ESP_LOGI(kTag, "State %s -> %s event=%s gate=%s generation=%" PRIu32,
                 veetee::app::ToString(result.from), veetee::app::ToString(result.to),
                 veetee::app::ToString(event),
                 result.assistant_gate_open ? "open" : "closed",
                 result.cancellation_generation);
        g_board.ApplyState(result.to);

        if (result.to == veetee::app::State::kWifiConfiguring) {
            g_transport.Close();
            g_bootstrap.Cancel();
            g_resources.Cancel();
            if (event == veetee::app::Event::kEnterWifiConfig) {
                if (result.from == veetee::app::State::kPairingRecovery) {
                    const esp_err_t identity_error =
                        g_settings_store.ClearDeviceIdentity(&g_settings);
                    if (identity_error != ESP_OK) {
                        ESP_LOGE(kTag,
                                 "Unable to clear rejected device identity: %s",
                                 esp_err_to_name(identity_error));
                    }
                }
                const esp_err_t reset_error = g_wifi.ResetProvisioning();
                if (reset_error != ESP_OK) {
                    ESP_LOGE(kTag, "Unable to clear stored provisioning: %s",
                             esp_err_to_name(reset_error));
                }
            }
            const esp_err_t error = g_wifi.StartProvisioning();
            if (error != ESP_OK) {
                ESP_LOGE(kTag, "Unable to start provisioning: %s; retrying",
                         esp_err_to_name(error));
                vTaskDelay(pdMS_TO_TICKS(1000));
                PostEvent(veetee::app::Event::kRetryWifiProvisioning);
            }
        } else if (result.to == veetee::app::State::kNetworkConnecting) {
            g_transport.Close();
            g_bootstrap.Cancel();
            g_resources.Cancel();
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
        } else if (result.to == veetee::app::State::kConnecting) {
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
            if (!result.assistant_gate_open) {
                LogTransportError("stop listening",
                                  g_transport.StopListening("user_disable"));
            } else if (event == veetee::app::Event::kInterruptDetected) {
                LogTransportError(
                    "interrupt",
                    g_transport.Abort("local_interrupt_detected",
                                      "interrupt_profile"));
            } else if (event == veetee::app::Event::kActivationWakeDetected) {
                LogTransportError(
                    "closing cancellation",
                    g_transport.Abort("session_closing_cancelled", "wake_word"));
            } else {
                LogTransportError(
                    "button interrupt",
                    g_transport.Abort("button_interrupt", "button"));
            }
            PostEvent(veetee::app::Event::kAbortComplete);
        } else if (result.to == veetee::app::State::kIdle) {
            if (event == veetee::app::Event::kButtonLongPress) {
                LogTransportError("stop listening",
                                  g_transport.StopListening("user_disable"));
            } else if (!result.assistant_gate_open) {
                g_transport.Close();
            }
        }
        if (result.to == veetee::app::State::kIdle &&
            g_resource_apply_pending) {
            ScheduleResourceApply();
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
    const esp_reset_reason_t reset_reason = esp_reset_reason();
    LogPlatformInfo();

    g_event_queue = xQueueCreate(kEventQueueDepth, sizeof(AppMessage));
    if (g_event_queue == nullptr) {
        ESP_LOGE(kTag, "Unable to allocate application event queue");
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
    ESP_ERROR_CHECK(
        esp_timer_create(&apply_timer_args, &g_resource_apply_timer));
    ESP_ERROR_CHECK(
        esp_timer_create(&health_timer_args, &g_resource_health_timer));

    ESP_ERROR_CHECK(g_settings_store.Initialize(&g_settings));
    ESP_ERROR_CHECK(g_wifi.Initialize(&g_settings_store, &g_settings, &OnWifiEvent, nullptr));
    ESP_ERROR_CHECK(g_resources.Initialize(&g_settings,
                                           &OnResourceReconcileEvent, nullptr));
    ESP_ERROR_CHECK(g_reporter.Initialize(&g_settings));
    ESP_ERROR_CHECK(g_bootstrap.Initialize(&g_settings_store, &g_settings,
                                           &OnBootstrapEvent, nullptr));
    ESP_ERROR_CHECK(g_transport.Initialize(&g_settings, &OnTransportEvent,
                                           &OnDownlinkAudio, &OnMcpEnvelope,
                                           nullptr));
    ESP_ERROR_CHECK(g_board.Initialize(
        &OnButtonEvent, &OnDetectorEvent, &OnEncodedAudio,
        &OnPlaybackFinished, g_resources.ActivePartitionLabel(),
        g_resources.PreviousPartitionLabel(), nullptr));
    ESP_ERROR_CHECK(g_board.StartAudio(ShouldPlayBootChime(reset_reason)));

    const auto resource_phase = g_resources.phase();
    if (resource_phase ==
        veetee::settings::ResourceRecordPhase::kPendingHealth) {
        if (SamePartition(g_board.loaded_wake_partition(),
                          g_resources.ActivePartitionLabel())) {
            ESP_ERROR_CHECK(esp_timer_start_once(g_resource_health_timer,
                                                 kResourceHealthWindowUs));
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
    if (!g_mcp.Initialize(&ReadDeviceStatus, &SetSpeakerVolume,
                          &SendMcpResponse, nullptr)) {
        ESP_LOGE(kTag, "Unable to initialize device MCP");
        abort();
    }

    if (xTaskCreate(&RunApplication, "veetee_app", 6144, nullptr, 6, nullptr) != pdPASS) {
        ESP_LOGE(kTag, "Unable to create application task");
        abort();
    }
    PostEvent(g_settings.HasProvisioning()
                  ? veetee::app::Event::kBootWithCredentials
                  : veetee::app::Event::kBootNeedsProvisioning);
}
