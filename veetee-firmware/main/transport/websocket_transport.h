#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

#include "audio/wake_audio_pre_roll.h"
#include "esp_err.h"
#include "esp_event.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "settings/settings_store.h"
#include "transport/protocol_v1.h"
#include "transport/websocket_task_policy.h"

namespace veetee::transport {

enum class WebSocketTransportEvent : std::uint8_t {
    kReady,
    kReconnecting,
    kLost,
    kListenStarted,
    kSttFinal,
    kLlmStarted,
    kTurnFailed,
    kTtsStarted,
    kTtsStopped,
    kAssistantSleep,
    kConfigChanged,
};

enum class WebSocketCloseMode : std::uint8_t {
    kGraceful,
    kAbortive,
};

struct WebSocketTransportNotification {
    WebSocketTransportEvent event;
    std::uint32_t config_version = 0;
};

class WebSocketTransport {
public:
    using EventSink = bool (*)(const WebSocketTransportNotification& notification,
                               void* context);
    using AudioSink = bool (*)(const std::uint8_t* packet, std::size_t length,
                               void* context);
    using McpSink = bool (*)(const char* envelope, std::size_t length,
                             void* context);

    esp_err_t Initialize(settings::SettingsStore* settings_store,
                         EventSink event_sink,
                         AudioSink audio_sink, McpSink mcp_sink,
                         audio::WakeAudioSource* wake_audio_source,
                         void* context);
    esp_err_t Open(WakeSource source);
    esp_err_t Abort(const char* reason, const char* source);
    esp_err_t StopListening(const char* reason);
    bool SendAudio(const std::uint8_t* packet, std::size_t length);
    bool SendMcpPayload(const char* payload, std::size_t length);
    void Close(WebSocketCloseMode mode = WebSocketCloseMode::kGraceful);
    [[nodiscard]] bool control_task_running() const {
        return task_ != nullptr;
    }
    [[nodiscard]] std::uint32_t control_stack_free_bytes() const {
        return task_ == nullptr
                   ? 0
                   : static_cast<std::uint32_t>(
                         uxTaskGetStackHighWaterMark(task_));
    }
    [[nodiscard]] std::uint64_t outbound_audio_queue_drops() const {
        return outbound_audio_queue_drops_.load();
    }
    [[nodiscard]] std::uint32_t outbound_audio_queue_high_water() const {
        return outbound_audio_queue_high_water_.load();
    }
    [[nodiscard]] std::uint64_t reconnect_attempt_count() const {
        return reconnect_attempt_count_.load();
    }
    [[nodiscard]] std::uint64_t reconnect_exhausted_count() const {
        return reconnect_exhausted_count_.load();
    }

private:
    enum class CommandType : std::uint8_t {
        kOpen,
        kClose,
        kSocketConnected,
        kSocketLost,
        kServerEvent,
        kOpusPacket,
        kProtocolError,
        kMcpEnvelope,
        kMcpPayload,
        kAbort,
        kStopListening,
    };

    struct Command {
        CommandType type;
        std::uint32_t generation;
        WakeSource wake_source = WakeSource::kButton;
        ServerEvent server_event{};
        char reason[65] = {};
        char source[33] = {};
        WebSocketCloseMode close_mode = WebSocketCloseMode::kGraceful;
        std::uint16_t packet_length = 0;
        std::array<std::uint8_t, kMaximumOpusPacketBytes> packet{};
        char* control_payload = nullptr;
        std::uint16_t control_length = 0;
    };

    struct OutboundAudioFrame {
        std::uint32_t generation;
        std::uint16_t length;
        std::array<std::uint8_t, kMaximumOpusPacketBytes> packet{};
    };

    static void TaskEntry(void* context);
    static void WebSocketEventHandler(void* handler_arg, esp_event_base_t event_base,
                                      std::int32_t event_id, void* event_data);

    void TaskLoop();
    void HandleCommand(const Command& command);
    void StartClient(std::uint32_t generation, WakeSource source,
                     bool reset_reconnect_policy);
    void HandleSocketConnected(std::uint32_t generation);
    void HandleServerEvent(std::uint32_t generation, const ServerEvent& event);
    void HandleMcpEnvelope(std::uint32_t generation, const ServerEvent& event,
                           const char* envelope, std::size_t length);
    void HandleLoss(std::uint32_t generation, const char* reason,
                    bool protocol_failure = false);
    void HandleData(const esp_websocket_event_data_t& data,
                    std::uint32_t generation);
    bool SendOpeningSequence(std::uint32_t generation);
    bool AcquireIoTaskReserve();
    void ReleaseIoTaskReserve();
    void Teardown(bool clean, int close_code = 1000, const char* reason = nullptr);
    bool SendText(const char* text, std::size_t length);
    bool SendMcpPayloadNow(const char* payload, std::size_t length);
    bool SendBinary(const std::uint8_t* data, std::size_t length);
    bool QueueCommand(const Command& command, TickType_t timeout);
    bool QueueUrgentCommand(const Command& command, TickType_t timeout);
    bool QueueCriticalCommand(const Command& command, TickType_t timeout);
    bool QueuePriorityCommand(const Command& command, TickType_t timeout);
    static WebSocketCommandPriority CommandPriority(const Command& command);
    static void ReleaseCommandPayload(const Command& command);
    bool NotifyWithRetry(WebSocketTransportEvent event,
                         std::uint32_t generation) const;
    bool NotifyOnce(const WebSocketTransportNotification& notification) const;
    [[nodiscard]] bool IsCurrent(std::uint32_t generation) const;
    [[nodiscard]] TickType_t ReceiveTimeout() const;

    settings::SettingsStore* settings_store_ = nullptr;
    EventSink event_sink_ = nullptr;
    AudioSink audio_sink_ = nullptr;
    McpSink mcp_sink_ = nullptr;
    audio::WakeAudioSource* wake_audio_source_ = nullptr;
    void* sink_context_ = nullptr;
    QueueHandle_t command_queue_ = nullptr;
    QueueHandle_t urgent_command_queue_ = nullptr;
    QueueHandle_t outbound_audio_queue_ = nullptr;
    TaskHandle_t task_ = nullptr;
    void* io_task_reserve_ = nullptr;
    std::size_t io_task_reserve_bytes_ = 0;
    esp_websocket_client_handle_t client_ = nullptr;
    std::atomic<esp_websocket_client_handle_t> callback_client_{nullptr};
    std::atomic<std::uint32_t> requested_generation_{0};
    std::atomic<std::uint32_t> client_generation_{0};
    std::atomic<bool> ready_for_audio_{false};
    std::atomic<bool> abortive_close_requested_{false};
    std::atomic<std::uint64_t> outbound_audio_queue_drops_{0};
    std::atomic<std::uint32_t> outbound_audio_queue_high_water_{0};
    std::atomic<bool> reconnect_enabled_{false};
    std::atomic<std::uint64_t> reconnect_attempt_count_{0};
    std::atomic<std::uint64_t> reconnect_exhausted_count_{0};
    TextFrameAssembler text_assembler_;
    BinaryFrameAssembler binary_assembler_;
    WakeOpeningSnapshot wake_opening_{};
    bool awaiting_hello_ = false;
    bool ready_ = false;
    bool playback_open_ = false;
    bool reconnect_pending_ = false;
    std::uint8_t reconnect_attempt_ = 0;
    std::uint32_t reconnect_generation_ = 0;
    TickType_t reconnect_deadline_ = 0;
    TickType_t hello_deadline_ = 0;
    char hardware_id_[18] = {};
    char session_id_[kMaximumSessionIdBytes + 1] = {};
    std::array<char, 257> uri_{};
    std::array<char, 512> headers_{};
    std::array<char, 384> control_buffer_{};
    std::array<std::uint8_t, kMaximumOpusPacketBytes> wake_audio_buffer_{};
};

}  // namespace veetee::transport
