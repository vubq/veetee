#include <cassert>
#include <cstring>
#include <iostream>

#include "settings/firmware_ota_attempt_record.h"

int main() {
    using veetee::settings::AdvanceFirmwareOtaAttempt;
    using veetee::settings::BeginFirmwareOtaAttempt;
    using veetee::settings::ClearFirmwareOtaAttempt;
    using veetee::settings::FirmwareOtaAttemptPhase;
    using veetee::settings::IsValidFirmwareOtaAttemptRecord;
    using veetee::settings::MakeDefaultFirmwareOtaAttemptRecord;

    auto record = MakeDefaultFirmwareOtaAttemptRecord();
    assert(IsValidFirmwareOtaAttemptRecord(record));
    assert(BeginFirmwareOtaAttempt(&record, "0.3.1", "0.4.0", 0, 1, 2,
                                   1532480));
    assert(record.attempt_id == 1);
    assert(record.phase == FirmwareOtaAttemptPhase::kStaged);
    assert(!BeginFirmwareOtaAttempt(&record, "0.3.1", "0.5.0", 0, 1, 2,
                                    1532480));
    assert(AdvanceFirmwareOtaAttempt(&record,
                                     FirmwareOtaAttemptPhase::kRebooting));
    assert(AdvanceFirmwareOtaAttempt(&record,
                                     FirmwareOtaAttemptPhase::kPendingHealth));
    assert(AdvanceFirmwareOtaAttempt(
        &record, FirmwareOtaAttemptPhase::kRollbackRequested,
        "boot_health_failed"));
    assert(AdvanceFirmwareOtaAttempt(&record,
                                     FirmwareOtaAttemptPhase::kPendingHealth));
    assert(record.error_code[0] == '\0');
    assert(AdvanceFirmwareOtaAttempt(&record,
                                     FirmwareOtaAttemptPhase::kActive));
    assert(!AdvanceFirmwareOtaAttempt(
        &record, FirmwareOtaAttemptPhase::kFailed, "late_failure"));
    assert(!AdvanceFirmwareOtaAttempt(
        &record, FirmwareOtaAttemptPhase::kRolledBack,
        "late_rollback"));
    assert(record.phase == FirmwareOtaAttemptPhase::kActive);
    assert(AdvanceFirmwareOtaAttempt(&record,
                                     FirmwareOtaAttemptPhase::kActive));
    assert(ClearFirmwareOtaAttempt(&record));
    assert(record.has_attempt == 0);
    assert(record.last_attempt_id == 1);

    assert(BeginFirmwareOtaAttempt(&record, "0.4.0", "0.5.0", 1, 0, 3,
                                   1600000));
    assert(record.attempt_id == 2);
    assert(AdvanceFirmwareOtaAttempt(&record,
                                     FirmwareOtaAttemptPhase::kRebooting));
    assert(AdvanceFirmwareOtaAttempt(
        &record, FirmwareOtaAttemptPhase::kRolledBack,
        "bootloader_rollback"));
    assert(!AdvanceFirmwareOtaAttempt(&record,
                                     FirmwareOtaAttemptPhase::kActive));
    assert(!AdvanceFirmwareOtaAttempt(
        &record, FirmwareOtaAttemptPhase::kFailed, "late_failure"));
    assert(record.phase == FirmwareOtaAttemptPhase::kRolledBack);
    assert(IsValidFirmwareOtaAttemptRecord(record));

    auto corrupted = record;
    corrupted.to_slot = corrupted.from_slot;
    assert(!IsValidFirmwareOtaAttemptRecord(corrupted));
    corrupted = record;
    corrupted.crc32 ^= 1U;
    assert(!IsValidFirmwareOtaAttemptRecord(corrupted));

    std::cout << "firmware_ota_attempt_record_test: passed\n";
    return 0;
}
