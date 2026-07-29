#include <cassert>
#include <cstdio>
#include <cstring>

#include "settings/reported_state_record.h"

namespace {

veetee::settings::ReportedResourceState MakeState(
    veetee::settings::ReportedResourcePhase phase, const char* error = "") {
    veetee::settings::ReportedResourceState state{};
    state.phase = phase;
    state.artifact_kind = veetee::settings::ReportedArtifactKind::kWakeResource;
    state.active_slot = 0;
    state.target_slot = 1;
    state.expected_bytes = 4096;
    state.downloaded_bytes = phase == veetee::settings::ReportedResourcePhase::kActive
                                 ? 4096
                                 : 2048;
    state.security_epoch = 2;
    std::snprintf(state.current_version, sizeof(state.current_version), "%s",
                  "factory-bringup");
    std::snprintf(state.desired_version, sizeof(state.desired_version), "%s",
                  "1.0.0");
    std::snprintf(state.error_code, sizeof(state.error_code), "%s", error);
    return state;
}

void TestMonotonicIssueAndDurableTerminal() {
    auto record = veetee::settings::MakeDefaultReportedStateRecord();
    assert(veetee::settings::IsValidReportedStateRecord(record));

    std::uint32_t version = 0;
    assert(veetee::settings::IssueReportedStateVersion(&record, &version));
    assert(version == 1);
    assert(record.has_pending == 0);

    const auto active = MakeState(
        veetee::settings::ReportedResourcePhase::kActive);
    assert(veetee::settings::StagePendingReportedState(&record, active,
                                                       &version));
    assert(version == 2);
    assert(record.has_pending == 1);
    assert(std::strcmp(record.pending.desired_version, "1.0.0") == 0);
    assert(!veetee::settings::StagePendingReportedState(&record, active,
                                                        &version));
    assert(!veetee::settings::ClearPendingReportedState(&record, 1));
    assert(veetee::settings::ClearPendingReportedState(&record, 2));
    assert(record.has_pending == 0);
    assert(record.last_issued_version == 2);
    assert(veetee::settings::IsValidReportedStateRecord(record));
}

void TestValidationAndCorruption() {
    auto downloading = MakeState(
        veetee::settings::ReportedResourcePhase::kDownloading);
    assert(veetee::settings::IsValidReportedResourceState(downloading));
    std::uint32_t version = 0;
    auto record = veetee::settings::MakeDefaultReportedStateRecord();
    assert(!veetee::settings::StagePendingReportedState(
        &record, downloading, &version));

    auto failed = MakeState(veetee::settings::ReportedResourcePhase::kFailed,
                            "payload_sha256_mismatch");
    assert(veetee::settings::IsValidReportedResourceState(failed));
    assert(veetee::settings::StagePendingReportedState(&record, failed,
                                                       &version));
    auto corrupted = record;
    corrupted.pending.active_slot = 7;
    assert(!veetee::settings::IsValidReportedStateRecord(corrupted));
    corrupted = record;
    corrupted.crc32 ^= 1U;
    assert(!veetee::settings::IsValidReportedStateRecord(corrupted));
}

void TestBootBoundaryPhasesAreDurableAndSupersedable() {
    auto record = veetee::settings::MakeDefaultReportedStateRecord();
    std::uint32_t version = 0;
    const auto rebooting = MakeState(
        veetee::settings::ReportedResourcePhase::kRebooting);
    assert(veetee::settings::StagePendingReportedState(
        &record, rebooting, &version));
    assert(version == 1);
    const auto rolled_back = MakeState(
        veetee::settings::ReportedResourcePhase::kRolledBack,
        "boot_health_failed");
    assert(veetee::settings::ReplacePendingReportedState(
        &record, rolled_back, &version));
    assert(version == 2);
    assert(record.pending.phase ==
           veetee::settings::ReportedResourcePhase::kRolledBack);
    assert(veetee::settings::IsValidReportedStateRecord(record));

    auto unrelated = rolled_back;
    unrelated.artifact_kind =
        veetee::settings::ReportedArtifactKind::kFirmware;
    assert(!veetee::settings::ReplacePendingReportedState(
        &record, unrelated, &version));
}

void TestDurableBootBoundaryBlocksLateIntermediateVersion() {
    auto record = veetee::settings::MakeDefaultReportedStateRecord();
    const auto staged = MakeState(
        veetee::settings::ReportedResourcePhase::kStaged);
    const auto rebooting = MakeState(
        veetee::settings::ReportedResourcePhase::kRebooting);

    // Reproduce the SMP interleaving where the reporter has already popped the
    // coalescable staged state, but the application task persists rebooting
    // before the reporter assigns staged a sequence number.
    std::uint32_t rebooting_version = 0;
    assert(veetee::settings::StagePendingReportedState(
        &record, rebooting, &rebooting_version));
    assert(rebooting_version == 1);

    std::uint32_t staged_version = 0;
    assert(veetee::settings::IsValidReportedResourceState(staged));
    assert(!veetee::settings::IssueReportedStateVersion(&record,
                                                        &staged_version));
    assert(record.last_issued_version == rebooting_version);
    assert(record.pending.phase ==
           veetee::settings::ReportedResourcePhase::kRebooting);

    assert(veetee::settings::ClearPendingReportedState(
        &record, rebooting_version));
    assert(veetee::settings::IssueReportedStateVersion(&record,
                                                       &staged_version));
    assert(staged_version == 2);
}

void TestDeviceConfigReportValidation() {
    veetee::settings::ReportedResourceState state{};
    state.artifact_kind =
        veetee::settings::ReportedArtifactKind::kDeviceConfig;
    state.phase = veetee::settings::ReportedResourcePhase::kApplying;
    std::snprintf(state.current_version, sizeof(state.current_version), "%s",
                  "7");
    std::snprintf(state.desired_version, sizeof(state.desired_version), "%s",
                  "8");
    assert(veetee::settings::IsValidReportedResourceState(state));
    state.phase = veetee::settings::ReportedResourcePhase::kActive;
    assert(!veetee::settings::IsValidReportedResourceState(state));
    std::snprintf(state.current_version, sizeof(state.current_version), "%s",
                  "8");
    assert(veetee::settings::IsValidReportedResourceState(state));
    std::snprintf(state.current_version, sizeof(state.current_version), "%s",
                  "7");
    state.phase = veetee::settings::ReportedResourcePhase::kDownloading;
    assert(!veetee::settings::IsValidReportedResourceState(state));
    state.phase = veetee::settings::ReportedResourcePhase::kFailed;
    std::snprintf(state.error_code, sizeof(state.error_code), "%s",
                  "apply_failed");
    assert(veetee::settings::IsValidReportedResourceState(state));
    state.desired_version[0] = 'v';
    assert(!veetee::settings::IsValidReportedResourceState(state));
}

}  // namespace

int main() {
    TestMonotonicIssueAndDurableTerminal();
    TestValidationAndCorruption();
    TestBootBoundaryPhasesAreDurableAndSupersedable();
    TestDurableBootBoundaryBlocksLateIntermediateVersion();
    TestDeviceConfigReportValidation();
    return 0;
}
