#pragma once

#include <cstdint>

namespace veetee::settings {

constexpr std::uint32_t kActivationRecordVersion = 1;

enum class ActivationRecordState : std::uint8_t {
    kNone = 0,
    kPending = 1,
    kActive = 2,
};

struct ActivationRecord {
    std::uint32_t version = kActivationRecordVersion;
    ActivationRecordState state = ActivationRecordState::kNone;
    std::uint8_t reserved[3] = {};
    char activation_code[7] = {};
    char activation_challenge[129] = {};
    char device_id[37] = {};
    char device_token[129] = {};
    char websocket_url[257] = {};
    std::uint32_t config_version = 0;
    std::uint32_t crc32 = 0;
};

void SealActivationRecord(ActivationRecord* record);
bool IsValidActivationRecord(const ActivationRecord& record);

}  // namespace veetee::settings
