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

}  // namespace

int main() {
    TestMonotonicIssueAndDurableTerminal();
    TestValidationAndCorruption();
    return 0;
}
