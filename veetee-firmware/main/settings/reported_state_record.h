#pragma once

#include <cstdint>

namespace veetee::settings {

constexpr std::uint32_t kReportedStateRecordVersion = 1;
constexpr std::uint32_t kMaximumReportedStateVersion = 2147483647U;

enum class ReportedResourcePhase : std::uint8_t {
    kChecking = 0,
    kDownloading = 1,
    kVerifying = 2,
    kStaged = 3,
    kApplying = 4,
    kActive = 5,
    kFailed = 6,
    kRolledBack = 7,
};

enum class ReportedArtifactKind : std::uint8_t {
    kWakeResource = 0,
    kUiPack = 1,
};

struct ReportedResourceState {
    ReportedResourcePhase phase = ReportedResourcePhase::kChecking;
    std::uint8_t active_slot = 0;
    std::uint8_t target_slot = 0;
    ReportedArtifactKind artifact_kind = ReportedArtifactKind::kWakeResource;
    std::uint32_t expected_bytes = 0;
    std::uint32_t downloaded_bytes = 0;
    std::uint32_t security_epoch = 0;
    char current_version[33] = {};
    char desired_version[33] = {};
    char error_code[33] = {};
};

struct ReportedStateRecord {
    std::uint32_t version = kReportedStateRecordVersion;
    std::uint32_t last_issued_version = 0;
    std::uint32_t pending_version = 0;
    std::uint8_t has_pending = 0;
    std::uint8_t reserved[3] = {};
    ReportedResourceState pending{};
    std::uint32_t crc32 = 0;
};

const char* ReportedResourcePhaseName(ReportedResourcePhase phase);
bool IsTerminalReportedResourcePhase(ReportedResourcePhase phase);
bool IsValidReportedResourceState(const ReportedResourceState& state);

ReportedStateRecord MakeDefaultReportedStateRecord();
void SealReportedStateRecord(ReportedStateRecord* record);
bool IsValidReportedStateRecord(const ReportedStateRecord& record);
bool IssueReportedStateVersion(ReportedStateRecord* record,
                               std::uint32_t* issued_version);
bool StagePendingReportedState(ReportedStateRecord* record,
                               const ReportedResourceState& state,
                               std::uint32_t* issued_version);
bool ClearPendingReportedState(ReportedStateRecord* record,
                               std::uint32_t delivered_version);

}  // namespace veetee::settings
