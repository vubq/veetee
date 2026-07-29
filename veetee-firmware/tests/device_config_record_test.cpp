#include <cassert>
#include <cstdio>
#include <cstring>
#include <iostream>
#include <string_view>

#include "settings/device_config_record.h"
#include "settings/device_config_store_policy.h"

namespace {

veetee::config::DeviceConfig Config(std::uint32_t version) {
    veetee::config::DeviceConfig config{};
    config.version = version;
    config.security_epoch = 3;
    config.has_wake_profile = true;
    std::snprintf(config.wake_profile_id.data(), config.wake_profile_id.size(),
                  "%s", "wake-profile-v1");
    config.wake_profile_version = 4;
    std::snprintf(config.required_resource_version.data(),
                  config.required_resource_version.size(), "%s", "1.2.0");
    config.activation.enabled = true;
    std::snprintf(config.activation.model_id.data(),
                  config.activation.model_id.size(), "%s", "wn9s_hiesp");
    config.activation.threshold_ppm = 640000;
    config.activation.cooldown_ms = 1200;
    return config;
}

}  // namespace

int main() {
    using namespace veetee::settings;
    static_assert(std::string_view(kDeviceConfigNvsNamespace) != "veetee");
    static_assert(std::string_view(kDeviceConfigNvsRecordKey) == "state");

    // The retained board NVS contained an older 508-byte blob at the same key.
    // Both larger and smaller unknown layouts must invalidate only config so
    // authenticated bootstrap can fetch a current signed snapshot.
    constexpr std::size_t kObservedLegacyRecordBytes = 508;
    static_assert(kDeviceConfigRecordVersion == 2);
    static_assert(sizeof(DeviceConfigRecord) == 348);
    static_assert(DecideDeviceConfigBlobAction(
                      DeviceConfigBlobStatus::kFound,
                      sizeof(DeviceConfigRecord), sizeof(DeviceConfigRecord)) ==
                  DeviceConfigBlobAction::kLoadCurrent);
    static_assert(DecideDeviceConfigBlobAction(
                      DeviceConfigBlobStatus::kFound,
                      kObservedLegacyRecordBytes,
                      sizeof(DeviceConfigRecord)) ==
                  DeviceConfigBlobAction::kResetConfigOnly);
    static_assert(DecideDeviceConfigBlobAction(
                      DeviceConfigBlobStatus::kFound,
                      sizeof(DeviceConfigRecord) - 1,
                      sizeof(DeviceConfigRecord)) ==
                  DeviceConfigBlobAction::kResetConfigOnly);
    static_assert(DecideDeviceConfigBlobAction(
                      DeviceConfigBlobStatus::kLengthMismatch,
                      kObservedLegacyRecordBytes,
                      sizeof(DeviceConfigRecord)) ==
                  DeviceConfigBlobAction::kResetConfigOnly);
    static_assert(DecideDeviceConfigBlobAction(
                      DeviceConfigBlobStatus::kTypeMismatch, 0,
                      sizeof(DeviceConfigRecord)) ==
                  DeviceConfigBlobAction::kResetConfigOnly);
    static_assert(DecideDeviceConfigBlobAction(
                      DeviceConfigBlobStatus::kNotFound, 0,
                      sizeof(DeviceConfigRecord)) ==
                  DeviceConfigBlobAction::kResetConfigOnly);
    static_assert(DecideDeviceConfigBlobAction(
                      DeviceConfigBlobStatus::kStorageError, 0,
                      sizeof(DeviceConfigRecord)) ==
                  DeviceConfigBlobAction::kFail);

    DeviceConfigRecord in_place_default{};
    in_place_default.applied_version = 123;
    in_place_default.has_wake_profile = 1;
    in_place_default.activation.enabled = 1;
    std::snprintf(in_place_default.etag, sizeof(in_place_default.etag), "%s",
                  "stale-record");
    assert(InitializeDefaultDeviceConfigRecord(&in_place_default, 5));
    assert(IsValidDeviceConfigRecord(in_place_default));
    assert(in_place_default.record_version == kDeviceConfigRecordVersion);
    assert(in_place_default.applied_version == 0);
    assert(in_place_default.security_epoch_floor == 5);
    assert(in_place_default.has_wake_profile == 0);
    assert(in_place_default.wake_audio_privacy_revoked == 1);
    assert(in_place_default.activation.enabled == 0);
    assert(in_place_default.etag[0] == '\0');
    assert(!InitializeDefaultDeviceConfigRecord(nullptr, 5));
    assert(!InitializeDefaultDeviceConfigRecord(&in_place_default, 0));

    constexpr char kFirstEtag[] =
        "cfg1-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    constexpr char kDifferentEtag[] =
        "cfg1-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
    constexpr char kOlderEtag[] =
        "cfg1-ccccccccccccccccccccccccccccccccccccccccccc";
    constexpr char kOldEpochEtag[] =
        "cfg1-ddddddddddddddddddddddddddddddddddddddddddd";
    constexpr char kDisabledEtag[] =
        "cfg1-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
    constexpr char kReenabledEtag[] =
        "cfg1-fffffffffffffffffffffffffffffffffffffffffff";
    DeviceConfigRecord record = MakeDefaultDeviceConfigRecord(1);
    assert(IsValidDeviceConfigRecord(record));
    assert(record.applied_version == 0);
    assert(record.wake_audio_privacy_revoked == 1);
    assert(DeviceConfigWakeAudioPrivacyRevoked(record));

    const auto first = Config(8);
    assert(!StageAppliedDeviceConfig(&record, first, "cfg1-short"));
    assert(StageAppliedDeviceConfig(&record, first, kFirstEtag));
    assert(record.applied_version == 8);
    assert(record.security_epoch_floor == 3);
    assert(IsValidDeviceConfigRecord(record));

    veetee::config::DeviceConfig loaded{};
    assert(LoadAppliedDeviceConfig(record, &loaded));
    assert(loaded.version == first.version);
    assert(loaded.has_wake_profile);
    assert(std::strcmp(loaded.activation.model_id.data(), "wn9s_hiesp") == 0);

    const DeviceConfigRecord immutable = record;
    auto same_version = first;
    same_version.activation.cooldown_ms = 1400;
    assert(!StageAppliedDeviceConfig(&record, same_version, kDifferentEtag));
    assert(std::memcmp(&record, &immutable, sizeof(record)) == 0);
    assert(StageAppliedDeviceConfig(&record, same_version, kFirstEtag));
    assert(std::memcmp(&record, &immutable, sizeof(record)) == 0);

    auto older = Config(7);
    assert(!StageAppliedDeviceConfig(&record, older, kOlderEtag));
    auto old_epoch = Config(9);
    old_epoch.security_epoch = 2;
    assert(!StageAppliedDeviceConfig(&record, old_epoch, kOldEpochEtag));

    DeviceConfigRecord corrupted = record;
    corrupted.activation.cooldown_ms = 10001;
    assert(!IsValidDeviceConfigRecord(corrupted));
    corrupted = record;
    corrupted.send_wake_audio = 2;
    SealDeviceConfigRecord(&corrupted);
    assert(!IsValidDeviceConfigRecord(corrupted));
    corrupted = record;
    corrupted.wake_audio_privacy_revoked = 2;
    SealDeviceConfigRecord(&corrupted);
    assert(!IsValidDeviceConfigRecord(corrupted));
    corrupted = record;
    corrupted.reserved = 1;
    SealDeviceConfigRecord(&corrupted);
    assert(!IsValidDeviceConfigRecord(corrupted));
    assert(!MarkWakeAudioPrivacyRevoked(nullptr));

    auto wake_audio = Config(9);
    wake_audio.send_wake_audio = true;
    assert(StageAppliedDeviceConfig(&record, wake_audio, kDifferentEtag));
    assert(IsValidDeviceConfigRecord(record));
    assert(record.send_wake_audio == 1);
    assert(record.wake_audio_privacy_revoked == 0);
    assert(!DeviceConfigWakeAudioPrivacyRevoked(record));
    assert(LoadAppliedDeviceConfig(record, &loaded));
    assert(loaded.send_wake_audio);

    // A verified opt-out durably revokes an older applied opt-in without
    // mutating its signed version/ETag. A reboot therefore loads the old
    // config data but still forces the effective runtime flag off.
    assert(MarkWakeAudioPrivacyRevoked(&record));
    assert(record.wake_audio_privacy_revoked == 1);
    assert(DeviceConfigWakeAudioPrivacyRevoked(record));
    const DeviceConfigRecord rebooted_after_revocation = record;
    assert(IsValidDeviceConfigRecord(rebooted_after_revocation));
    assert(LoadAppliedDeviceConfig(rebooted_after_revocation, &loaded));
    assert(loaded.send_wake_audio);
    assert(DeviceConfigWakeAudioPrivacyRevoked(rebooted_after_revocation));
    const bool reboot_effective_send_wake_audio =
        loaded.send_wake_audio &&
        !DeviceConfigWakeAudioPrivacyRevoked(rebooted_after_revocation);
    assert(!reboot_effective_send_wake_audio);

    // Reload failure or a superseded candidate never mutates the durable
    // record, and an idempotent SaveApplied of the old opt-in cannot clear it.
    const DeviceConfigRecord failed_opt_out = record;
    assert(DeviceConfigWakeAudioPrivacyRevoked(failed_opt_out));
    const DeviceConfigRecord superseded_opt_out = failed_opt_out;
    assert(DeviceConfigWakeAudioPrivacyRevoked(superseded_opt_out));
    assert(StageAppliedDeviceConfig(&record, wake_audio, kDifferentEtag));
    assert(record.wake_audio_privacy_revoked == 1);

    // Only a newer successfully applied opt-in clears the latch.
    auto reenabled = Config(10);
    reenabled.send_wake_audio = true;
    assert(StageAppliedDeviceConfig(&record, reenabled, kReenabledEtag));
    assert(record.wake_audio_privacy_revoked == 0);
    assert(!DeviceConfigWakeAudioPrivacyRevoked(record));
    assert(LoadAppliedDeviceConfig(record, &loaded));
    const bool reenabled_effective_send_wake_audio =
        loaded.send_wake_audio && !DeviceConfigWakeAudioPrivacyRevoked(record);
    assert(reenabled_effective_send_wake_audio);

    auto disabled = Config(11);
    disabled.has_wake_profile = false;
    disabled.activation = {};
    disabled.wake_profile_id = {};
    disabled.required_resource_version = {};
    disabled.wake_profile_version = 0;
    assert(StageAppliedDeviceConfig(&record, disabled, kDisabledEtag));
    assert(IsValidDeviceConfigRecord(record));
    assert(record.has_wake_profile == 0);
    assert(record.wake_audio_privacy_revoked == 1);
    assert(DeviceConfigWakeAudioPrivacyRevoked(record));

    DeviceConfigRecord epoch_migration = immutable;
    assert(ReconcileDeviceConfigSecurityFloor(&epoch_migration, 3) ==
           DeviceConfigRecordMigration::kUnchanged);
    assert(ReconcileDeviceConfigSecurityFloor(&epoch_migration, 4) ==
           DeviceConfigRecordMigration::kResetForSecurityEpoch);
    assert(epoch_migration.applied_version == 0);
    assert(epoch_migration.security_epoch_floor == 4);
    assert(epoch_migration.etag[0] == '\0');
    assert(epoch_migration.wake_audio_privacy_revoked == 1);
    assert(IsValidDeviceConfigRecord(epoch_migration));
    assert(ReconcileDeviceConfigSecurityFloor(nullptr, 4) ==
           DeviceConfigRecordMigration::kInvalid);

    std::cout << "device_config_record_test: passed\n";
    return 0;
}
