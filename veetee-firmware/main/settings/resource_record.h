#pragma once

#include <cstddef>
#include <cstdint>

namespace veetee::settings {

constexpr std::uint32_t kResourceRecordVersion = 1;

enum class ResourceRecordPhase : std::uint8_t {
    kStable = 0,
    kDownloading = 1,
    kStaged = 2,
    kPendingHealth = 3,
};

struct ResourceRecord {
    std::uint32_t version = kResourceRecordVersion;
    ResourceRecordPhase phase = ResourceRecordPhase::kStable;
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

ResourceRecord MakeDefaultResourceRecord(std::uint32_t minimum_security_epoch);
void SealResourceRecord(ResourceRecord* record);
bool IsValidResourceRecord(const ResourceRecord& record);

bool BeginResourceDownload(ResourceRecord* record, const char* desired_version,
                           const char* bundle_id, const char* payload_sha256,
                           std::uint32_t expected_bytes,
                           std::uint32_t security_epoch);
bool UpdateResourceDownloadProgress(ResourceRecord* record,
                                    std::uint32_t downloaded_bytes);
bool ResetResourceDownloadProgress(ResourceRecord* record);
bool StageResourceDownload(ResourceRecord* record);
bool ActivateStagedResource(ResourceRecord* record);
bool ConfirmActiveResource(ResourceRecord* record);
bool RollbackResource(ResourceRecord* record);

}  // namespace veetee::settings
