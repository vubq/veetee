#include "settings/resource_record.h"

#include <cassert>
#include <cstring>

namespace {

constexpr char kHash[] =
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

void TestDownloadActivationAndHealth() {
    auto record = veetee::settings::MakeDefaultResourceRecord(2);
    assert(veetee::settings::IsValidResourceRecord(record));
    assert(record.active_slot == 0);
    assert(record.active_security_epoch == 2);

    assert(veetee::settings::BeginResourceDownload(
        &record, "1.0.0", "bundle-1", kHash, 4096, 3));
    assert(record.phase == veetee::settings::ResourceRecordPhase::kDownloading);
    assert(record.target_slot == 1);
    assert(record.downloaded_bytes == 0);

    assert(veetee::settings::UpdateResourceDownloadProgress(&record, 2048));
    const auto resumable = record;
    assert(veetee::settings::BeginResourceDownload(
        &record, "1.0.0", "bundle-1", kHash, 4096, 3));
    assert(record.downloaded_bytes == resumable.downloaded_bytes);
    assert(!veetee::settings::UpdateResourceDownloadProgress(&record, 1024));
    assert(veetee::settings::UpdateResourceDownloadProgress(&record, 4096));
    assert(veetee::settings::StageResourceDownload(&record));
    assert(veetee::settings::ActivateStagedResource(&record));
    assert(record.phase == veetee::settings::ResourceRecordPhase::kPendingHealth);
    assert(record.active_slot == 1);
    assert(record.previous_slot == 0);
    assert(record.active_security_epoch == 3);
    assert(record.security_epoch_floor == 3);
    assert(veetee::settings::ConfirmActiveResource(&record));
    assert(record.phase == veetee::settings::ResourceRecordPhase::kStable);
    assert(record.active_slot == 1);
    assert(std::strcmp(record.active_version, "1.0.0") == 0);
    assert(veetee::settings::IsValidResourceRecord(record));
}

void TestReplacementAndRollback() {
    auto record = veetee::settings::MakeDefaultResourceRecord(1);
    assert(veetee::settings::BeginResourceDownload(
        &record, "1.0.0", "bundle-1", kHash, 8192, 1));
    assert(veetee::settings::UpdateResourceDownloadProgress(&record, 4096));

    constexpr char kOtherHash[] =
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    assert(veetee::settings::BeginResourceDownload(
        &record, "2.0.0", "bundle-2", kOtherHash, 16384, 2));
    assert(record.downloaded_bytes == 0);
    assert(record.expected_bytes == 16384);
    assert(std::strcmp(record.desired_version, "2.0.0") == 0);
    assert(veetee::settings::RollbackResource(&record));
    assert(record.phase == veetee::settings::ResourceRecordPhase::kStable);
    assert(record.active_slot == 0);

    assert(veetee::settings::BeginResourceDownload(
        &record, "2.0.0", "bundle-2", kOtherHash, 16384, 2));
    assert(veetee::settings::UpdateResourceDownloadProgress(&record, 16384));
    assert(veetee::settings::StageResourceDownload(&record));
    assert(veetee::settings::ActivateStagedResource(&record));
    assert(veetee::settings::RollbackResource(&record));
    assert(record.active_slot == 0);
    assert(std::strcmp(record.active_version, "factory-bringup") == 0);
    assert(record.active_security_epoch == 1);
    assert(record.security_epoch_floor == 2);
    assert(!veetee::settings::BeginResourceDownload(
        &record, "1.5.0", "bundle-old", kHash, 4096, 1));

    assert(veetee::settings::BeginResourceDownload(
        &record, "3.0.0", "bundle-3", kHash, 4096, 2));
    assert(veetee::settings::UpdateResourceDownloadProgress(&record, 4096));
    assert(veetee::settings::StageResourceDownload(&record));
    assert(veetee::settings::ActivateStagedResource(&record));
    assert(veetee::settings::ConfirmActiveResource(&record));
    assert(record.active_slot == 1);
    assert(veetee::settings::RollbackResource(&record));
    assert(record.active_slot == 0);
    assert(record.security_epoch_floor == 2);
}

void TestCorruptionAndDowngrade() {
    auto record = veetee::settings::MakeDefaultResourceRecord(4);
    assert(!veetee::settings::BeginResourceDownload(
        &record, "1.0.0", "bundle-1", kHash, 4096, 3));

    auto corrupted = record;
    corrupted.active_slot = 7;
    assert(!veetee::settings::IsValidResourceRecord(corrupted));
    corrupted = record;
    corrupted.crc32 ^= 1U;
    assert(!veetee::settings::IsValidResourceRecord(corrupted));
}

}  // namespace

int main() {
    TestDownloadActivationAndHealth();
    TestReplacementAndRollback();
    TestCorruptionAndDowngrade();
    return 0;
}
