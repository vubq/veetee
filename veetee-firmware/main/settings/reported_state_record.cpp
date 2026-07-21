#include "settings/reported_state_record.h"

#include <algorithm>
#include <cstddef>
#include <cstring>
#include <type_traits>

namespace veetee::settings {
namespace {

static_assert(std::is_trivially_copyable_v<ReportedResourceState>);
static_assert(std::is_trivially_copyable_v<ReportedStateRecord>);
static_assert(sizeof(ReportedResourceState) == 116,
              "Reported resource layout is a versioned contract");
static_assert(sizeof(ReportedStateRecord) == 136,
              "Reported-state NVS layout is a versioned contract");

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
    if (value == nullptr || value[0] == '\0') return false;
    return std::all_of(value, value + std::strlen(value), [](char character) {
        return (character >= 'a' && character <= 'z') ||
               (character >= 'A' && character <= 'Z') ||
               (character >= '0' && character <= '9') || character == '.' ||
               character == '+' || character == '_' || character == '-';
    });
}

bool IsErrorToken(const char* value) {
    if (value == nullptr) return false;
    return std::all_of(value, value + std::strlen(value), [](char character) {
        return (character >= 'a' && character <= 'z') ||
               (character >= '0' && character <= '9') || character == '.' ||
               character == '_' || character == '-';
    });
}

bool IsZeroed(const ReportedResourceState& state) {
    const ReportedResourceState empty{};
    return std::memcmp(&state, &empty, sizeof(state)) == 0;
}

}  // namespace

const char* ReportedResourcePhaseName(ReportedResourcePhase phase) {
    switch (phase) {
        case ReportedResourcePhase::kChecking:
            return "checking";
        case ReportedResourcePhase::kDownloading:
            return "downloading";
        case ReportedResourcePhase::kVerifying:
            return "verifying";
        case ReportedResourcePhase::kStaged:
            return "staged";
        case ReportedResourcePhase::kApplying:
            return "applying";
        case ReportedResourcePhase::kActive:
            return "active";
        case ReportedResourcePhase::kFailed:
            return "failed";
        case ReportedResourcePhase::kRolledBack:
            return "rolled_back";
    }
    return nullptr;
}

bool IsTerminalReportedResourcePhase(ReportedResourcePhase phase) {
    return phase == ReportedResourcePhase::kActive ||
           phase == ReportedResourcePhase::kFailed ||
           phase == ReportedResourcePhase::kRolledBack;
}

bool IsValidReportedResourceState(const ReportedResourceState& state) {
    if (ReportedResourcePhaseName(state.phase) == nullptr ||
        state.active_slot > 1 || state.target_slot > 1 ||
        state.downloaded_bytes > state.expected_bytes ||
        !IsTerminated(state.current_version) ||
        !IsTerminated(state.desired_version) ||
        !IsTerminated(state.error_code) ||
        !IsVersionToken(state.current_version) ||
        !IsVersionToken(state.desired_version) || !IsErrorToken(state.error_code)) {
        return false;
    }
    const bool failure = state.phase == ReportedResourcePhase::kFailed ||
                         state.phase == ReportedResourcePhase::kRolledBack;
    return failure ? state.error_code[0] != '\0' : state.error_code[0] == '\0';
}

ReportedStateRecord MakeDefaultReportedStateRecord() {
    ReportedStateRecord record{};
    SealReportedStateRecord(&record);
    return record;
}

void SealReportedStateRecord(ReportedStateRecord* record) {
    if (record == nullptr) return;
    record->crc32 = Crc32(record, offsetof(ReportedStateRecord, crc32));
}

bool IsValidReportedStateRecord(const ReportedStateRecord& record) {
    if (record.version != kReportedStateRecordVersion ||
        record.last_issued_version > kMaximumReportedStateVersion ||
        record.has_pending > 1 ||
        record.crc32 != Crc32(&record, offsetof(ReportedStateRecord, crc32))) {
        return false;
    }
    if (record.has_pending == 0) {
        return record.pending_version == 0 && IsZeroed(record.pending);
    }
    return record.pending_version > 0 &&
           record.pending_version <= record.last_issued_version &&
           IsTerminalReportedResourcePhase(record.pending.phase) &&
           IsValidReportedResourceState(record.pending);
}

bool IssueReportedStateVersion(ReportedStateRecord* record,
                               std::uint32_t* issued_version) {
    if (record == nullptr || issued_version == nullptr ||
        !IsValidReportedStateRecord(*record) ||
        record->last_issued_version >= kMaximumReportedStateVersion) {
        return false;
    }
    ++record->last_issued_version;
    *issued_version = record->last_issued_version;
    SealReportedStateRecord(record);
    return true;
}

bool StagePendingReportedState(ReportedStateRecord* record,
                               const ReportedResourceState& state,
                               std::uint32_t* issued_version) {
    if (record == nullptr || issued_version == nullptr ||
        !IsValidReportedStateRecord(*record) || record->has_pending != 0 ||
        !IsTerminalReportedResourcePhase(state.phase) ||
        !IsValidReportedResourceState(state) ||
        record->last_issued_version >= kMaximumReportedStateVersion) {
        return false;
    }
    ++record->last_issued_version;
    record->pending_version = record->last_issued_version;
    record->has_pending = 1;
    record->pending = state;
    *issued_version = record->pending_version;
    SealReportedStateRecord(record);
    return true;
}

bool ClearPendingReportedState(ReportedStateRecord* record,
                               std::uint32_t delivered_version) {
    if (record == nullptr || !IsValidReportedStateRecord(*record) ||
        record->has_pending == 0 ||
        delivered_version != record->pending_version) {
        return false;
    }
    record->has_pending = 0;
    record->pending_version = 0;
    record->pending = ReportedResourceState{};
    SealReportedStateRecord(record);
    return true;
}

}  // namespace veetee::settings
