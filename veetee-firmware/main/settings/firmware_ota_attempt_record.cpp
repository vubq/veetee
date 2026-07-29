#include "settings/firmware_ota_attempt_record.h"

#include <algorithm>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <type_traits>

namespace veetee::settings {
namespace {

static_assert(std::is_trivially_copyable_v<FirmwareOtaAttemptRecord>);
static_assert(sizeof(FirmwareOtaAttemptRecord) == 128,
              "Firmware OTA attempt layout is a versioned NVS contract");

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

bool IsVersionToken(const char* value) {
    return value != nullptr && value[0] != '\0' &&
           std::all_of(value, value + std::strlen(value), [](char character) {
               return (character >= 'a' && character <= 'z') ||
                      (character >= 'A' && character <= 'Z') ||
                      (character >= '0' && character <= '9') ||
                      character == '.' || character == '+' ||
                      character == '_' || character == '-';
           });
}

bool IsErrorToken(const char* value) {
    return value != nullptr &&
           std::all_of(value, value + std::strlen(value), [](char character) {
               return (character >= 'a' && character <= 'z') ||
                      (character >= '0' && character <= '9') ||
                      character == '.' || character == '_' || character == '-';
           });
}

bool IsKnownPhase(FirmwareOtaAttemptPhase phase) {
    return phase >= FirmwareOtaAttemptPhase::kNone &&
           phase <= FirmwareOtaAttemptPhase::kFailed;
}

bool IsAllowedTransition(FirmwareOtaAttemptPhase from,
                         FirmwareOtaAttemptPhase to) {
    if (from == to) return true;
    if (IsTerminalFirmwareOtaAttemptPhase(from)) return false;
    if (IsTerminalFirmwareOtaAttemptPhase(to)) {
        return from != FirmwareOtaAttemptPhase::kNone;
    }
    switch (from) {
        case FirmwareOtaAttemptPhase::kStaged:
            return to == FirmwareOtaAttemptPhase::kRebooting ||
                   to == FirmwareOtaAttemptPhase::kPendingHealth;
        case FirmwareOtaAttemptPhase::kRebooting:
            return to == FirmwareOtaAttemptPhase::kPendingHealth ||
                   to == FirmwareOtaAttemptPhase::kRollbackRequested;
        case FirmwareOtaAttemptPhase::kPendingHealth:
            return to == FirmwareOtaAttemptPhase::kRollbackRequested;
        case FirmwareOtaAttemptPhase::kRollbackRequested:
            return to == FirmwareOtaAttemptPhase::kPendingHealth;
        case FirmwareOtaAttemptPhase::kNone:
        case FirmwareOtaAttemptPhase::kActive:
        case FirmwareOtaAttemptPhase::kRolledBack:
        case FirmwareOtaAttemptPhase::kFailed:
            return false;
    }
    return false;
}

}  // namespace

bool IsTerminalFirmwareOtaAttemptPhase(FirmwareOtaAttemptPhase phase) {
    return phase == FirmwareOtaAttemptPhase::kActive ||
           phase == FirmwareOtaAttemptPhase::kRolledBack ||
           phase == FirmwareOtaAttemptPhase::kFailed;
}

FirmwareOtaAttemptRecord MakeDefaultFirmwareOtaAttemptRecord() {
    FirmwareOtaAttemptRecord record{};
    SealFirmwareOtaAttemptRecord(&record);
    return record;
}

void SealFirmwareOtaAttemptRecord(FirmwareOtaAttemptRecord* record) {
    if (record == nullptr) return;
    record->crc32 = Crc32(record, offsetof(FirmwareOtaAttemptRecord, crc32));
}

bool IsValidFirmwareOtaAttemptRecord(const FirmwareOtaAttemptRecord& record) {
    if (record.record_version != kFirmwareOtaAttemptRecordVersion ||
        record.last_attempt_id > kMaximumFirmwareOtaAttemptId ||
        record.has_attempt > 1 || !IsKnownPhase(record.phase) ||
        !IsTerminated(record.from_version) ||
        !IsTerminated(record.to_version) ||
        !IsTerminated(record.error_code) ||
        !IsErrorToken(record.error_code) || record.reserved != 0 ||
        record.crc32 !=
            Crc32(&record, offsetof(FirmwareOtaAttemptRecord, crc32))) {
        return false;
    }
    if (record.has_attempt == 0) {
        return record.attempt_id == 0 && record.security_epoch == 0 &&
               record.expected_bytes == 0 &&
               record.phase == FirmwareOtaAttemptPhase::kNone &&
               record.from_slot == 0 && record.to_slot == 0 &&
               record.from_version[0] == '\0' &&
               record.to_version[0] == '\0' && record.error_code[0] == '\0';
    }
    if (record.attempt_id == 0 ||
        record.attempt_id > record.last_attempt_id ||
        record.security_epoch == 0 || record.expected_bytes == 0 ||
        record.phase == FirmwareOtaAttemptPhase::kNone ||
        record.from_slot > 1 || record.to_slot > 1 ||
        record.from_slot == record.to_slot ||
        !IsVersionToken(record.from_version) ||
        !IsVersionToken(record.to_version) ||
        std::strcmp(record.from_version, record.to_version) == 0) {
        return false;
    }
    const bool error_required =
        record.phase == FirmwareOtaAttemptPhase::kRollbackRequested ||
        record.phase == FirmwareOtaAttemptPhase::kRolledBack ||
        record.phase == FirmwareOtaAttemptPhase::kFailed;
    return error_required ? record.error_code[0] != '\0'
                          : record.error_code[0] == '\0';
}

bool BeginFirmwareOtaAttempt(FirmwareOtaAttemptRecord* record,
                             const char* from_version,
                             const char* to_version,
                             std::uint8_t from_slot,
                             std::uint8_t to_slot,
                             std::uint32_t security_epoch,
                             std::uint32_t expected_bytes) {
    if (record == nullptr || !IsValidFirmwareOtaAttemptRecord(*record) ||
        record->has_attempt != 0 ||
        record->last_attempt_id >= kMaximumFirmwareOtaAttemptId ||
        !IsVersionToken(from_version) || !IsVersionToken(to_version) ||
        std::strcmp(from_version, to_version) == 0 || from_slot > 1 ||
        to_slot > 1 || from_slot == to_slot || security_epoch == 0 ||
        expected_bytes == 0) {
        return false;
    }
    const std::uint32_t next_id = record->last_attempt_id + 1;
    FirmwareOtaAttemptRecord next{};
    next.last_attempt_id = next_id;
    next.attempt_id = next_id;
    next.security_epoch = security_epoch;
    next.expected_bytes = expected_bytes;
    next.has_attempt = 1;
    next.phase = FirmwareOtaAttemptPhase::kStaged;
    next.from_slot = from_slot;
    next.to_slot = to_slot;
    std::snprintf(next.from_version, sizeof(next.from_version), "%s",
                  from_version);
    std::snprintf(next.to_version, sizeof(next.to_version), "%s", to_version);
    SealFirmwareOtaAttemptRecord(&next);
    if (!IsValidFirmwareOtaAttemptRecord(next)) return false;
    *record = next;
    return true;
}

bool AdvanceFirmwareOtaAttempt(FirmwareOtaAttemptRecord* record,
                               FirmwareOtaAttemptPhase phase,
                               const char* error_code) {
    if (record == nullptr || !IsValidFirmwareOtaAttemptRecord(*record) ||
        record->has_attempt == 0 || !IsKnownPhase(phase) ||
        phase == FirmwareOtaAttemptPhase::kNone ||
        !IsAllowedTransition(record->phase, phase)) {
        return false;
    }
    const bool error_required =
        phase == FirmwareOtaAttemptPhase::kRollbackRequested ||
        phase == FirmwareOtaAttemptPhase::kRolledBack ||
        phase == FirmwareOtaAttemptPhase::kFailed;
    const char* bounded_error = error_code == nullptr ? "" : error_code;
    if ((error_required && bounded_error[0] == '\0') ||
        (!error_required && bounded_error[0] != '\0') ||
        std::strlen(bounded_error) >= sizeof(record->error_code) ||
        !IsErrorToken(bounded_error)) {
        return false;
    }
    record->phase = phase;
    std::snprintf(record->error_code, sizeof(record->error_code), "%s",
                  bounded_error);
    SealFirmwareOtaAttemptRecord(record);
    return IsValidFirmwareOtaAttemptRecord(*record);
}

bool ClearFirmwareOtaAttempt(FirmwareOtaAttemptRecord* record) {
    if (record == nullptr || !IsValidFirmwareOtaAttemptRecord(*record) ||
        record->has_attempt == 0 ||
        !IsTerminalFirmwareOtaAttemptPhase(record->phase)) {
        return false;
    }
    const std::uint32_t last_attempt_id = record->last_attempt_id;
    *record = MakeDefaultFirmwareOtaAttemptRecord();
    record->last_attempt_id = last_attempt_id;
    SealFirmwareOtaAttemptRecord(record);
    return IsValidFirmwareOtaAttemptRecord(*record);
}

}  // namespace veetee::settings
