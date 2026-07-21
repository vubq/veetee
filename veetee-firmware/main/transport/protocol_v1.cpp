#include "transport/protocol_v1.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>

#include "cJSON.h"

namespace veetee::transport {
namespace {

constexpr std::uint8_t kContinuationOpcode = 0x0;
constexpr std::uint8_t kTextOpcode = 0x1;
constexpr std::uint8_t kBinaryOpcode = 0x2;

constexpr char kDeviceHello[] =
    R"({"type":"hello","version":1,"features":{"mcp":true,"aec":false,"glyph_push":false},"transport":"websocket","audio_params":{"format":"opus","sample_rate":16000,"channels":1,"frame_duration":60}})";

bool IsSafeIdentifier(const char* value, std::size_t maximum_length) {
    if (value == nullptr) return false;
    const std::size_t length = std::strlen(value);
    if (length == 0 || length > maximum_length) return false;
    return std::all_of(value, value + length, [](unsigned char character) {
        return (character >= 'a' && character <= 'z') ||
               (character >= 'A' && character <= 'Z') ||
               (character >= '0' && character <= '9') || character == '-' ||
               character == '_' || character == '.' || character == ':';
    });
}

bool JsonStringEquals(const cJSON* object, const char* key, const char* expected) {
    const cJSON* item = cJSON_GetObjectItemCaseSensitive(object, key);
    return cJSON_IsString(item) && item->valuestring != nullptr &&
           std::strcmp(item->valuestring, expected) == 0;
}

bool JsonIntegerEquals(const cJSON* object, const char* key, int expected) {
    const cJSON* item = cJSON_GetObjectItemCaseSensitive(object, key);
    return cJSON_IsNumber(item) && std::isfinite(item->valuedouble) &&
           item->valuedouble == expected;
}

bool CopySessionId(const cJSON* root, char* destination, std::size_t capacity) {
    const cJSON* item = cJSON_GetObjectItemCaseSensitive(root, "session_id");
    if (!cJSON_IsString(item) || item->valuestring == nullptr ||
        !IsSafeIdentifier(item->valuestring, kMaximumSessionIdBytes)) {
        return false;
    }
    const std::size_t length = std::strlen(item->valuestring);
    if (length >= capacity) return false;
    std::memcpy(destination, item->valuestring, length + 1);
    return true;
}

bool StoreFormattedLength(int written, std::size_t capacity, std::size_t* length) {
    if (written <= 0 || static_cast<std::size_t>(written) >= capacity) return false;
    if (length != nullptr) *length = static_cast<std::size_t>(written);
    return true;
}

}  // namespace

AssembleResult TextFrameAssembler::Append(
    std::uint8_t opcode, bool fin, std::size_t payload_length,
    std::size_t payload_offset, const char* data, std::size_t data_length,
    const char** message, std::size_t* message_length) {
    if (message == nullptr || message_length == nullptr ||
        (data == nullptr && data_length != 0) ||
        (opcode != kTextOpcode && opcode != kContinuationOpcode)) {
        Reset();
        return AssembleResult::kError;
    }
    *message = nullptr;
    *message_length = 0;

    const std::size_t normalized_payload_length =
        payload_length == 0 ? data_length : payload_length;
    if (normalized_payload_length < data_length ||
        payload_offset > normalized_payload_length ||
        data_length > normalized_payload_length - payload_offset) {
        Reset();
        return AssembleResult::kError;
    }

    if (payload_offset == 0) {
        if (opcode == kTextOpcode) {
            if (active_) {
                Reset();
                return AssembleResult::kError;
            }
            active_ = true;
            received_ = 0;
            awaiting_continuation_ = false;
        } else if (!active_ || !awaiting_continuation_) {
            Reset();
            return AssembleResult::kError;
        }
        frame_length_ = normalized_payload_length;
        frame_offset_ = 0;
    } else if (!active_ || payload_offset != frame_offset_ ||
               normalized_payload_length != frame_length_) {
        Reset();
        return AssembleResult::kError;
    }

    if (!active_ || received_ > kMaximumControlFrameBytes ||
        data_length > kMaximumControlFrameBytes - received_) {
        Reset();
        return AssembleResult::kError;
    }
    if (data_length != 0) {
        std::memcpy(buffer_ + received_, data, data_length);
    }
    received_ += data_length;
    frame_offset_ += data_length;

    if (frame_offset_ < frame_length_) return AssembleResult::kIncomplete;
    if (frame_offset_ != frame_length_) {
        Reset();
        return AssembleResult::kError;
    }
    if (!fin) {
        awaiting_continuation_ = true;
        frame_length_ = 0;
        frame_offset_ = 0;
        return AssembleResult::kIncomplete;
    }

    buffer_[received_] = '\0';
    *message = buffer_;
    *message_length = received_;
    active_ = false;
    awaiting_continuation_ = false;
    frame_length_ = 0;
    frame_offset_ = 0;
    return AssembleResult::kComplete;
}

void TextFrameAssembler::Reset() {
    received_ = 0;
    frame_length_ = 0;
    frame_offset_ = 0;
    active_ = false;
    awaiting_continuation_ = false;
    buffer_[0] = '\0';
}

AssembleResult BinaryFrameAssembler::Append(
    std::uint8_t opcode, bool fin, std::size_t payload_length,
    std::size_t payload_offset, const char* data, std::size_t data_length,
    const std::uint8_t** packet, std::size_t* packet_length) {
    if (packet == nullptr || packet_length == nullptr ||
        (data == nullptr && data_length != 0) ||
        (opcode != kBinaryOpcode && opcode != kContinuationOpcode)) {
        Reset();
        return AssembleResult::kError;
    }
    *packet = nullptr;
    *packet_length = 0;

    const std::size_t normalized_payload_length =
        payload_length == 0 ? data_length : payload_length;
    if (normalized_payload_length == 0 ||
        normalized_payload_length > kMaximumOpusPacketBytes ||
        normalized_payload_length < data_length ||
        payload_offset > normalized_payload_length ||
        data_length > normalized_payload_length - payload_offset) {
        Reset();
        return AssembleResult::kError;
    }

    if (payload_offset == 0) {
        if (opcode == kBinaryOpcode) {
            if (active_) {
                Reset();
                return AssembleResult::kError;
            }
            active_ = true;
            received_ = 0;
            awaiting_continuation_ = false;
        } else if (!active_ || !awaiting_continuation_) {
            Reset();
            return AssembleResult::kError;
        }
        frame_length_ = normalized_payload_length;
        frame_offset_ = 0;
    } else if (!active_ || payload_offset != frame_offset_ ||
               normalized_payload_length != frame_length_) {
        Reset();
        return AssembleResult::kError;
    }

    if (!active_ || received_ > kMaximumOpusPacketBytes ||
        data_length > kMaximumOpusPacketBytes - received_) {
        Reset();
        return AssembleResult::kError;
    }
    if (data_length != 0) {
        std::memcpy(buffer_ + received_, data, data_length);
    }
    received_ += data_length;
    frame_offset_ += data_length;

    if (frame_offset_ < frame_length_) return AssembleResult::kIncomplete;
    if (frame_offset_ != frame_length_) {
        Reset();
        return AssembleResult::kError;
    }
    if (!fin) {
        awaiting_continuation_ = true;
        frame_length_ = 0;
        frame_offset_ = 0;
        return AssembleResult::kIncomplete;
    }

    *packet = buffer_;
    *packet_length = received_;
    active_ = false;
    awaiting_continuation_ = false;
    frame_length_ = 0;
    frame_offset_ = 0;
    return AssembleResult::kComplete;
}

void BinaryFrameAssembler::Reset() {
    received_ = 0;
    frame_length_ = 0;
    frame_offset_ = 0;
    active_ = false;
    awaiting_continuation_ = false;
}

const char* DeviceHelloJson() {
    return kDeviceHello;
}

bool ParseServerEvent(const char* json, std::size_t length, ServerEvent* event) {
    if (json == nullptr || event == nullptr || length == 0 ||
        length > kMaximumControlFrameBytes) {
        return false;
    }
    cJSON* root = cJSON_ParseWithLength(json, length);
    if (!cJSON_IsObject(root)) {
        cJSON_Delete(root);
        return false;
    }

    ServerEvent parsed{};
    const cJSON* type = cJSON_GetObjectItemCaseSensitive(root, "type");
    bool valid = cJSON_IsString(type) && type->valuestring != nullptr;
    if (valid && std::strcmp(type->valuestring, "hello") == 0) {
        const cJSON* audio = cJSON_GetObjectItemCaseSensitive(root, "audio_params");
        valid = JsonStringEquals(root, "transport", "websocket") &&
                CopySessionId(root, parsed.session_id, sizeof(parsed.session_id)) &&
                cJSON_IsObject(audio) && JsonStringEquals(audio, "format", "opus") &&
                JsonIntegerEquals(audio, "sample_rate", 24000) &&
                JsonIntegerEquals(audio, "channels", 1) &&
                JsonIntegerEquals(audio, "frame_duration", 60);
        parsed.kind = ServerEventKind::kHello;
    } else if (valid) {
        valid = CopySessionId(root, parsed.session_id, sizeof(parsed.session_id));
        parsed.kind = ServerEventKind::kOther;
        if (valid && std::strcmp(type->valuestring, "listen") == 0 &&
            JsonStringEquals(root, "state", "start")) {
            parsed.kind = ServerEventKind::kListenStart;
        } else if (valid && std::strcmp(type->valuestring, "stt") == 0) {
            const cJSON* text = cJSON_GetObjectItemCaseSensitive(root, "text");
            valid = cJSON_IsString(text) && text->valuestring != nullptr;
            parsed.kind = ServerEventKind::kStt;
        } else if (valid && std::strcmp(type->valuestring, "llm") == 0) {
            const cJSON* emotion = cJSON_GetObjectItemCaseSensitive(root, "emotion");
            valid = cJSON_IsString(emotion) && emotion->valuestring != nullptr;
            // Text deltas are high-volume UI metadata; only the thinking edge
            // belongs on the bounded application state queue.
            parsed.kind = valid && std::strcmp(emotion->valuestring, "thinking") == 0
                              ? ServerEventKind::kLlm
                              : ServerEventKind::kOther;
        } else if (valid && std::strcmp(type->valuestring, "tts") == 0) {
            if (JsonStringEquals(root, "state", "start")) {
                parsed.kind = ServerEventKind::kTtsStart;
            } else if (JsonStringEquals(root, "state", "stop")) {
                parsed.kind = ServerEventKind::kTtsStop;
            }
        } else if (valid && std::strcmp(type->valuestring, "system") == 0 &&
                   JsonStringEquals(root, "command", "assistant_sleep")) {
            parsed.kind = ServerEventKind::kAssistantSleep;
        }
    }

    cJSON_Delete(root);
    if (!valid) return false;
    *event = parsed;
    return true;
}

bool BuildListenStart(const char* session_id, WakeSource source, char* destination,
                      std::size_t capacity, std::size_t* length) {
    if (!IsSafeIdentifier(session_id, kMaximumSessionIdBytes) ||
        destination == nullptr || capacity == 0) {
        return false;
    }
    const int written = std::snprintf(
        destination, capacity,
        R"({"session_id":"%s","type":"listen","state":"start","mode":"auto","source":"%s"})",
        session_id, ToString(source));
    return StoreFormattedLength(written, capacity, length);
}

bool BuildAbort(const char* session_id, const char* reason, const char* source,
                char* destination, std::size_t capacity, std::size_t* length) {
    if (!IsSafeIdentifier(session_id, kMaximumSessionIdBytes) ||
        !IsSafeIdentifier(reason, 64) || !IsSafeIdentifier(source, 32) ||
        destination == nullptr || capacity == 0) {
        return false;
    }
    const int written = std::snprintf(
        destination, capacity,
        R"({"session_id":"%s","type":"abort","reason":"%s","source":"%s"})",
        session_id, reason, source);
    return StoreFormattedLength(written, capacity, length);
}

bool BuildListenStop(const char* session_id, const char* reason, char* destination,
                     std::size_t capacity, std::size_t* length) {
    if (!IsSafeIdentifier(session_id, kMaximumSessionIdBytes) ||
        !IsSafeIdentifier(reason, 64) || destination == nullptr || capacity == 0) {
        return false;
    }
    const int written = std::snprintf(
        destination, capacity,
        R"({"session_id":"%s","type":"listen","state":"stop","reason":"%s"})",
        session_id, reason);
    return StoreFormattedLength(written, capacity, length);
}

const char* ToString(WakeSource source) {
    switch (source) {
        case WakeSource::kButton: return "button";
        case WakeSource::kWakeWord: return "wake_word";
    }
    return "button";
}

}  // namespace veetee::transport
