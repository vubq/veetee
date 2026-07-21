#pragma once

#include <cstddef>
#include <cstdint>

namespace veetee::transport {

constexpr std::size_t kMaximumControlFrameBytes = 8192;
constexpr std::size_t kMaximumSessionIdBytes = 64;
constexpr std::size_t kMaximumOpusPacketBytes = 1500;

enum class WakeSource : std::uint8_t {
    kButton,
    kWakeWord,
};

enum class ServerEventKind : std::uint8_t {
    kHello,
    kListenStart,
    kStt,
    kLlm,
    kTtsStart,
    kTtsStop,
    kAssistantSleep,
    kOther,
};

struct ServerEvent {
    ServerEventKind kind = ServerEventKind::kOther;
    char session_id[kMaximumSessionIdBytes + 1] = {};
};

enum class AssembleResult : std::uint8_t {
    kIncomplete,
    kComplete,
    kError,
};

class TextFrameAssembler {
public:
    AssembleResult Append(std::uint8_t opcode, bool fin, std::size_t payload_length,
                          std::size_t payload_offset, const char* data,
                          std::size_t data_length, const char** message,
                          std::size_t* message_length);
    void Reset();

private:
    char buffer_[kMaximumControlFrameBytes + 1] = {};
    std::size_t received_ = 0;
    std::size_t frame_length_ = 0;
    std::size_t frame_offset_ = 0;
    bool active_ = false;
    bool awaiting_continuation_ = false;
};

class BinaryFrameAssembler {
public:
    AssembleResult Append(std::uint8_t opcode, bool fin,
                          std::size_t payload_length,
                          std::size_t payload_offset,
                          const char* data, std::size_t data_length,
                          const std::uint8_t** packet,
                          std::size_t* packet_length);
    void Reset();
    [[nodiscard]] bool active() const { return active_; }

private:
    std::uint8_t buffer_[kMaximumOpusPacketBytes] = {};
    std::size_t received_ = 0;
    std::size_t frame_length_ = 0;
    std::size_t frame_offset_ = 0;
    bool active_ = false;
    bool awaiting_continuation_ = false;
};

const char* DeviceHelloJson();
bool ParseServerEvent(const char* json, std::size_t length, ServerEvent* event);
bool BuildListenStart(const char* session_id, WakeSource source, char* destination,
                      std::size_t capacity, std::size_t* length);
bool BuildAbort(const char* session_id, const char* reason, const char* source,
                char* destination, std::size_t capacity, std::size_t* length);
bool BuildListenStop(const char* session_id, const char* reason, char* destination,
                     std::size_t capacity, std::size_t* length);
const char* ToString(WakeSource source);

}  // namespace veetee::transport
