#pragma once

#include <cstdint>

namespace veetee::settings {

constexpr std::uint32_t kFirmwareOtaAttemptRecordVersion = 1;
constexpr std::uint32_t kMaximumFirmwareOtaAttemptId = 2147483647U;

enum class FirmwareOtaAttemptPhase : std::uint8_t {
    kNone = 0,
    kStaged = 1,
    kRebooting = 2,
    kPendingHealth = 3,
    kRollbackRequested = 4,
    kActive = 5,
    kRolledBack = 6,
    kFailed = 7,
};

struct FirmwareOtaAttemptRecord {
    std::uint32_t record_version = kFirmwareOtaAttemptRecordVersion;
    std::uint32_t last_attempt_id = 0;
    std::uint32_t attempt_id = 0;
    std::uint32_t security_epoch = 0;
    std::uint32_t expected_bytes = 0;
    std::uint8_t has_attempt = 0;
    FirmwareOtaAttemptPhase phase = FirmwareOtaAttemptPhase::kNone;
    std::uint8_t from_slot = 0;
    std::uint8_t to_slot = 0;
    char from_version[33] = {};
    char to_version[33] = {};
    char error_code[33] = {};
    std::uint8_t reserved = 0;
    std::uint32_t crc32 = 0;
};

FirmwareOtaAttemptRecord MakeDefaultFirmwareOtaAttemptRecord();
void SealFirmwareOtaAttemptRecord(FirmwareOtaAttemptRecord* record);
bool IsValidFirmwareOtaAttemptRecord(const FirmwareOtaAttemptRecord& record);
bool IsTerminalFirmwareOtaAttemptPhase(FirmwareOtaAttemptPhase phase);

bool BeginFirmwareOtaAttempt(FirmwareOtaAttemptRecord* record,
                             const char* from_version,
                             const char* to_version,
                             std::uint8_t from_slot,
                             std::uint8_t to_slot,
                             std::uint32_t security_epoch,
                             std::uint32_t expected_bytes);
bool AdvanceFirmwareOtaAttempt(FirmwareOtaAttemptRecord* record,
                               FirmwareOtaAttemptPhase phase,
                               const char* error_code = nullptr);
bool ClearFirmwareOtaAttempt(FirmwareOtaAttemptRecord* record);

}  // namespace veetee::settings
