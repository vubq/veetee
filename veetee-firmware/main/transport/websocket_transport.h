#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "esp_event.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "settings/settings_store.h"
#include "transport/protocol_v1.h"

namespace veetee::transport {

enum class WebSocketTransportEvent : std::uint8_t {
    kReady,
    kLost,
};

struct WebSocketTransportNotification {
    WebSocketTransportEvent event;
};

class WebSocketTransport {
public:
    using EventSink = bool (*)(const WebSocketTransportNotification& notification,
                               void* context);

    esp_err_t Initialize(settings::DeviceSettings* settings, EventSink sink,
                         void* context);
    esp_err_t Open(WakeSource source);
    esp_err_t Abort(const char* reason, const char* source);
    esp_err_t StopListening(const char* reason);
    void Close();

private:
    enum class CommandType : std::uint8_t {
        kOpen,
        kClose,
        kSocketConnected,
        kSocketLost,
        kServerEvent,
        kProtocolError,
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
    };

    static void TaskEntry(void* context);
    static void WebSocketEventHandler(void* handler_arg, esp_event_base_t event_base,
                                      std::int32_t event_id, void* event_data);

    void TaskLoop();
    void HandleCommand(const Command& command);
    void StartClient(std::uint32_t generation, WakeSource source);
    void HandleSocketConnected(std::uint32_t generation);
    void HandleServerEvent(std::uint32_t generation, const ServerEvent& event);
    void HandleLoss(std::uint32_t generation, const char* reason);
    void HandleData(const esp_websocket_event_data_t& data,
                    std::uint32_t generation);
    void Teardown(bool clean, int close_code = 1000, const char* reason = nullptr);
    bool SendText(const char* text, std::size_t length);
    bool QueueCommand(const Command& command, TickType_t timeout);
    bool NotifyWithRetry(WebSocketTransportEvent event,
                         std::uint32_t generation) const;
    [[nodiscard]] bool IsCurrent(std::uint32_t generation) const;
    [[nodiscard]] TickType_t ReceiveTimeout() const;

    settings::DeviceSettings* settings_ = nullptr;
    EventSink sink_ = nullptr;
    void* sink_context_ = nullptr;
    QueueHandle_t command_queue_ = nullptr;
    TaskHandle_t task_ = nullptr;
    esp_websocket_client_handle_t client_ = nullptr;
    std::atomic<std::uint32_t> requested_generation_{0};
    std::atomic<std::uint32_t> client_generation_{0};
    TextFrameAssembler text_assembler_;
    WakeSource wake_source_ = WakeSource::kButton;
    bool awaiting_hello_ = false;
    bool ready_ = false;
    TickType_t hello_deadline_ = 0;
    char hardware_id_[18] = {};
    char session_id_[kMaximumSessionIdBytes + 1] = {};
    std::array<char, 257> uri_{};
    std::array<char, 512> headers_{};
    std::array<char, 384> control_buffer_{};
};

}  // namespace veetee::transport
