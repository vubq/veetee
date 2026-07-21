#include "settings/activation_record.h"

#include <algorithm>
#include <cstddef>
#include <cstring>
#include <type_traits>

namespace veetee::settings {
namespace {

static_assert(std::is_trivially_copyable_v<ActivationRecord>);
static_assert(sizeof(ActivationRecord) == 576,
              "Activation record layout is a versioned NVS contract");

std::uint32_t Crc32(const void* data, std::size_t length) {
    std::uint32_t crc = 0xFFFFFFFFU;
    const auto* bytes = static_cast<const std::uint8_t*>(data);
    for (std::size_t index = 0; index < length; ++index) {
        crc ^= bytes[index];
        for (int bit = 0; bit < 8; ++bit) {
            const std::uint32_t mask = 0U - (crc & 1U);
            crc = (crc >> 1U) ^ (0xEDB88320U & mask);
        }
    }
    return ~crc;
}

template <std::size_t Size>
bool IsTerminated(const char (&value)[Size]) {
    return std::memchr(value, '\0', Size) != nullptr;
}

bool IsSixDigitCode(const char* value) {
    return std::strlen(value) == 6 &&
           std::all_of(value, value + 6, [](char digit) {
               return digit >= '0' && digit <= '9';
           });
}

bool HasBoundedStrings(const ActivationRecord& record) {
    return IsTerminated(record.activation_code) &&
           IsTerminated(record.activation_challenge) &&
           IsTerminated(record.device_id) && IsTerminated(record.device_token) &&
           IsTerminated(record.websocket_url);
}

bool HasValidStatePayload(const ActivationRecord& record) {
    switch (record.state) {
        case ActivationRecordState::kNone:
            return true;
        case ActivationRecordState::kPending:
            return IsSixDigitCode(record.activation_code) &&
                   std::strlen(record.activation_challenge) >= 16;
        case ActivationRecordState::kActive:
            return record.device_id[0] != '\0' && record.device_token[0] != '\0' &&
                   record.websocket_url[0] != '\0';
    }
    return false;
}

}  // namespace

void SealActivationRecord(ActivationRecord* record) {
    if (record == nullptr) return;
    record->crc32 = Crc32(record, offsetof(ActivationRecord, crc32));
}

bool IsValidActivationRecord(const ActivationRecord& record) {
    return record.version == kActivationRecordVersion &&
           HasBoundedStrings(record) && HasValidStatePayload(record) &&
           record.crc32 == Crc32(&record, offsetof(ActivationRecord, crc32));
}

}  // namespace veetee::settings
