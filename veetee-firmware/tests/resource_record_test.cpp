#include "settings/resource_record.h"

#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <type_traits>

namespace {

constexpr char kHash[] =
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";

struct LegacyResourceRecordV1 {
    std::uint32_t version = 1;
    veetee::settings::ResourceRecordPhase phase =
        veetee::settings::ResourceRecordPhase::kStable;
    std::uint8_t active_slot = 0;
    std::uint8_t previous_slot = 0;
    std::uint8_t target_slot = 1;
    std::uint32_t expected_bytes = 0;
    std::uint32_t downloaded_bytes = 0;
    std::uint32_t active_security_epoch = 1;
    std::uint32_t previous_security_epoch = 1;
    std::uint32_t desired_security_epoch = 0;
    std::uint32_t security_epoch_floor = 1;
    char active_version[33] = "factory-bringup";
    char previous_version[33] = "factory-bringup";
    char desired_version[33] = {};
    char payload_sha256[65] = {};
    char bundle_id[65] = {};
    std::uint8_t reserved[3] = {};
    std::uint32_t crc32 = 0;
};

static_assert(std::is_trivially_copyable_v<LegacyResourceRecordV1>);
static_assert(sizeof(LegacyResourceRecordV1) == 268);

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

void Seal(LegacyResourceRecordV1* record) {
    record->crc32 = Crc32(record, offsetof(LegacyResourceRecordV1, crc32));
}

void TestDownloadActivationAndHealth() {
    auto record = veetee::settings::MakeDefaultResourceRecord(2);
    assert(veetee::settings::IsValidResourceRecord(record));
    assert(record.active_slot == 0);
    assert(record.active_security_epoch == 2);

    assert(veetee::settings::BeginResourceDownload(
        &record, "1.0.0", "bundle-1", kHash, 4096, 3,
        "wn9s_veetee", "wn9s_interrupt"));
    assert(record.phase == veetee::settings::ResourceRecordPhase::kDownloading);
    assert(record.target_slot == 1);
    assert(record.downloaded_bytes == 0);
    assert(std::strcmp(record.desired_detectors.activation_model_id,
                       "wn9s_veetee") == 0);
    assert(std::strcmp(record.desired_detectors.interrupt_model_id,
                       "wn9s_interrupt") == 0);

    assert(veetee::settings::UpdateResourceDownloadProgress(&record, 2048));
    const auto resumable = record;
    assert(veetee::settings::BeginResourceDownload(
        &record, "1.0.0", "bundle-1", kHash, 4096, 3,
        "wn9s_veetee", "wn9s_interrupt"));
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
    assert(std::strcmp(record.active_detectors.activation_model_id,
                       "wn9s_veetee") == 0);
    assert(std::strcmp(record.active_detectors.interrupt_model_id,
                       "wn9s_interrupt") == 0);
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

void TestPendingHealthMismatchRollbackUnblocksReplacement() {
    auto record = veetee::settings::MakeDefaultResourceRecord(1, "wake-v1");
    assert(veetee::settings::BeginResourceDownload(
        &record, "wake-v2", "bundle-v2", kHash, 4096, 2));
    assert(veetee::settings::UpdateResourceDownloadProgress(&record, 4096));
    assert(veetee::settings::StageResourceDownload(&record));
    assert(veetee::settings::ActivateStagedResource(&record));
    assert(record.phase ==
           veetee::settings::ResourceRecordPhase::kPendingHealth);

    // A persisted config may require neither wake-v2 nor wake-v1 after a
    // power-loss boundary. The runtime stays button-only, while terminating
    // this journal is what lets authenticated bootstrap fetch the right pack.
    assert(veetee::settings::RollbackResource(&record));
    assert(record.phase == veetee::settings::ResourceRecordPhase::kStable);
    constexpr char kReplacementHash[] =
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    assert(veetee::settings::BeginResourceDownload(
        &record, "wake-v3", "bundle-v3", kReplacementHash, 8192, 2));
    assert(record.phase ==
           veetee::settings::ResourceRecordPhase::kDownloading);
    assert(std::strcmp(record.desired_version, "wake-v3") == 0);
}

void TestLegacyMigrationPreservesActiveAndHydratesInventory() {
    LegacyResourceRecordV1 legacy{};
    legacy.active_slot = 1;
    legacy.previous_slot = 0;
    legacy.target_slot = 0;
    legacy.active_security_epoch = 2;
    legacy.previous_security_epoch = 1;
    legacy.security_epoch_floor = 2;
    std::strcpy(legacy.active_version, "1.0.1");
    std::strcpy(legacy.previous_version, "factory-bringup");
    Seal(&legacy);

    veetee::settings::ResourceRecord migrated{};
    assert(veetee::settings::MigrateResourceRecordV1(
        &legacy, sizeof(legacy), "factory-bringup", "wn9s_hiesp", nullptr,
        &migrated));
    assert(veetee::settings::IsValidResourceRecord(migrated));
    assert(migrated.active_slot == 1);
    assert(migrated.previous_slot == 0);
    assert(std::strcmp(migrated.active_version, "1.0.1") == 0);
    assert(std::strcmp(migrated.previous_version, "factory-bringup") == 0);
    assert(migrated.active_security_epoch == 2);
    assert(migrated.security_epoch_floor == 2);
    assert(!veetee::settings::HasResourceDetectorInventory(
        migrated.active_detectors));
    assert(!veetee::settings::HasResourceDetectorInventory(
        migrated.previous_detectors));

    // An authenticated signed manifest for the immutable active version can
    // hydrate the missing V1 inventory without erasing or downloading slot 1.
    bool changed = false;
    assert(veetee::settings::BindActiveResourceDetectorInventory(
        &migrated, "wn9s_hiesp", nullptr, &changed));
    assert(changed);
    assert(migrated.active_slot == 1);
    assert(std::strcmp(migrated.active_version, "1.0.1") == 0);
    const auto hydrated = migrated;
    changed = true;
    assert(veetee::settings::BindActiveResourceDetectorInventory(
        &migrated, "wn9s_hiesp", nullptr, &changed));
    assert(!changed);
    assert(std::memcmp(&migrated, &hydrated, sizeof(migrated)) == 0);

    assert(!veetee::settings::BindActiveResourceDetectorInventory(
        &migrated, "wn9s_other", nullptr, &changed));
    assert(std::memcmp(&migrated, &hydrated, sizeof(migrated)) == 0);

    auto sentinel = veetee::settings::MakeDefaultResourceRecord(
        7, "sentinel", "wn9s_sentinel", nullptr);
    const auto unchanged = sentinel;
    assert(!veetee::settings::MigrateResourceRecordV1(
        &legacy, sizeof(legacy) - 1, "factory-bringup", "wn9s_hiesp",
        nullptr, &sentinel));
    assert(std::memcmp(&sentinel, &unchanged, sizeof(sentinel)) == 0);
    legacy.crc32 ^= 1U;
    assert(!veetee::settings::MigrateResourceRecordV1(
        &legacy, sizeof(legacy), "factory-bringup", "wn9s_hiesp", nullptr,
        &sentinel));
    assert(std::memcmp(&sentinel, &unchanged, sizeof(sentinel)) == 0);
}

void TestLegacyStagedInventoryHydrationAndRollback() {
    LegacyResourceRecordV1 legacy{};
    legacy.phase = veetee::settings::ResourceRecordPhase::kStaged;
    legacy.target_slot = 1;
    legacy.expected_bytes = 4096;
    legacy.downloaded_bytes = 4096;
    legacy.desired_security_epoch = 2;
    legacy.security_epoch_floor = 1;
    std::strcpy(legacy.desired_version, "2.0.0");
    std::strcpy(legacy.payload_sha256, kHash);
    std::strcpy(legacy.bundle_id, "bundle-2");
    Seal(&legacy);

    veetee::settings::ResourceRecord migrated{};
    assert(veetee::settings::MigrateResourceRecordV1(
        &legacy, sizeof(legacy), "factory-bringup", "wn9s_hiesp", nullptr,
        &migrated));
    assert(migrated.phase ==
           veetee::settings::ResourceRecordPhase::kStaged);
    assert(migrated.target_slot == 1);
    assert(migrated.expected_bytes == 4096);
    assert(migrated.downloaded_bytes == 4096);
    assert(migrated.active_security_epoch == 1);
    assert(migrated.previous_security_epoch == 1);
    assert(migrated.desired_security_epoch == 2);
    assert(!veetee::settings::HasResourceDetectorInventory(
        migrated.desired_detectors));
    bool changed = false;
    assert(veetee::settings::BindDesiredResourceDetectorInventory(
        &migrated, "wn9s_new", "wn9s_stop", &changed));
    assert(changed);
    assert(veetee::settings::ActivateStagedResource(&migrated));
    assert(std::strcmp(migrated.active_detectors.activation_model_id,
                       "wn9s_new") == 0);
    assert(std::strcmp(migrated.previous_detectors.activation_model_id,
                       "wn9s_hiesp") == 0);
    assert(veetee::settings::RollbackResource(&migrated));
    assert(migrated.active_slot == 0);
    assert(std::strcmp(migrated.active_detectors.activation_model_id,
                       "wn9s_hiesp") == 0);
    assert(!veetee::settings::HasResourceDetectorInventory(
        migrated.desired_detectors));
}

void TestDetectorInventoryRejectsRoleCollision() {
    auto record = veetee::settings::MakeDefaultResourceRecord(
        1, "factory-bringup", "wn9s_hiesp", nullptr);
    const auto original = record;
    assert(!veetee::settings::BeginResourceDownload(
        &record, "2.0.0", "bundle-2", kHash, 4096, 2,
        "wn9s_same", "wn9s_same"));
    assert(std::memcmp(&record, &original, sizeof(record)) == 0);
}

void TestDownloadInvalidatesOverwrittenPreviousInventory() {
    auto record = veetee::settings::MakeDefaultResourceRecord(
        1, "factory-bringup", "wn9s_hiesp", nullptr);
    assert(veetee::settings::BeginResourceDownload(
        &record, "2.0.0", "bundle-2", kHash, 4096, 2,
        "wn9s_new", nullptr));
    assert(veetee::settings::UpdateResourceDownloadProgress(&record, 4096));
    assert(veetee::settings::StageResourceDownload(&record));
    assert(veetee::settings::ActivateStagedResource(&record));
    assert(veetee::settings::ConfirmActiveResource(&record));
    assert(record.active_slot == 1 && record.previous_slot == 0);
    assert(std::strcmp(record.previous_detectors.activation_model_id,
                       "wn9s_hiesp") == 0);

    constexpr char kNextHash[] =
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    assert(veetee::settings::BeginResourceDownload(
        &record, "3.0.0", "bundle-3", kNextHash, 4096, 2,
        "wn9s_next", nullptr));
    assert(!veetee::settings::HasResourceDetectorInventory(
        record.previous_detectors));
    assert(veetee::settings::RollbackResource(&record));
    assert(record.active_slot == 1);
    assert(std::strcmp(record.active_detectors.activation_model_id,
                       "wn9s_new") == 0);
    assert(!veetee::settings::HasResourceDetectorInventory(
        record.previous_detectors));
}

void TestDesiredSecurityEpochCannotFallBelowFloor() {
    auto record = veetee::settings::MakeDefaultResourceRecord(
        4, "factory-bringup", "wn9s_hiesp", nullptr);
    assert(veetee::settings::BeginResourceDownload(
        &record, "2.0.0", "bundle-2", kHash, 4096, 4,
        "wn9s_new", nullptr));
    record.desired_security_epoch = 3;
    veetee::settings::SealResourceRecord(&record);
    assert(!veetee::settings::IsValidResourceRecord(record));
}

}  // namespace

int main() {
    TestDownloadActivationAndHealth();
    TestReplacementAndRollback();
    TestCorruptionAndDowngrade();
    TestPendingHealthMismatchRollbackUnblocksReplacement();
    TestLegacyMigrationPreservesActiveAndHydratesInventory();
    TestLegacyStagedInventoryHydrationAndRollback();
    TestDetectorInventoryRejectsRoleCollision();
    TestDownloadInvalidatesOverwrittenPreviousInventory();
    TestDesiredSecurityEpochCannotFallBelowFloor();
    return 0;
}
