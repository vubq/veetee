#include "settings/resource_record.h"

#include <algorithm>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <type_traits>

namespace veetee::settings {
namespace {

static_assert(std::is_trivially_copyable_v<ResourceRecord>);
static_assert(sizeof(ResourceRecord) == 268,
              "Resource record layout is a versioned NVS contract");

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

bool IsSha256(const char* value) {
    return value != nullptr && std::strlen(value) == 64 &&
           std::all_of(value, value + 64, [](char character) {
               return (character >= '0' && character <= '9') ||
                      (character >= 'a' && character <= 'f');
           });
}

template <std::size_t Size>
bool CopyString(char (&destination)[Size], const char* source) {
    if (source == nullptr || source[0] == '\0' ||
        std::strlen(source) >= Size) {
        return false;
    }
    std::snprintf(destination, Size, "%s", source);
    return true;
}

void ClearDesired(ResourceRecord* record) {
    record->target_slot = static_cast<std::uint8_t>(1U - record->active_slot);
    record->expected_bytes = 0;
    record->downloaded_bytes = 0;
    record->desired_security_epoch = 0;
    record->desired_version[0] = '\0';
    record->payload_sha256[0] = '\0';
    record->bundle_id[0] = '\0';
}

bool HasBoundedStrings(const ResourceRecord& record) {
    return IsTerminated(record.active_version) &&
           IsTerminated(record.previous_version) &&
           IsTerminated(record.desired_version) &&
           IsTerminated(record.payload_sha256) && IsTerminated(record.bundle_id);
}

bool HasValidPhasePayload(const ResourceRecord& record) {
    if (record.active_slot > 1 || record.previous_slot > 1 ||
        record.target_slot > 1 || record.active_version[0] == '\0' ||
        record.previous_version[0] == '\0' ||
        record.active_security_epoch == 0 ||
        record.previous_security_epoch == 0) {
        return false;
    }
    if (record.security_epoch_floor < record.active_security_epoch ||
        record.security_epoch_floor < record.previous_security_epoch) {
        return false;
    }
    switch (record.phase) {
        case ResourceRecordPhase::kStable:
            return record.expected_bytes == 0 && record.downloaded_bytes == 0 &&
                   record.desired_security_epoch == 0 &&
                   record.desired_version[0] == '\0' &&
                   record.payload_sha256[0] == '\0' && record.bundle_id[0] == '\0';
        case ResourceRecordPhase::kDownloading:
            return record.target_slot != record.active_slot &&
                   record.expected_bytes > 0 &&
                   record.downloaded_bytes <= record.expected_bytes &&
                   record.desired_security_epoch >= record.active_security_epoch &&
                   record.desired_version[0] != '\0' &&
                   IsSha256(record.payload_sha256) && record.bundle_id[0] != '\0';
        case ResourceRecordPhase::kStaged:
            return record.target_slot != record.active_slot &&
                   record.expected_bytes > 0 &&
                   record.downloaded_bytes == record.expected_bytes &&
                   record.desired_security_epoch >= record.active_security_epoch &&
                   record.desired_version[0] != '\0' &&
                   IsSha256(record.payload_sha256) && record.bundle_id[0] != '\0';
        case ResourceRecordPhase::kPendingHealth:
            return record.active_slot != record.previous_slot &&
                   record.target_slot == record.active_slot &&
                   record.expected_bytes > 0 &&
                   record.downloaded_bytes == record.expected_bytes &&
                   record.desired_security_epoch == record.active_security_epoch &&
                   std::strcmp(record.desired_version, record.active_version) == 0 &&
                   IsSha256(record.payload_sha256) && record.bundle_id[0] != '\0';
    }
    return false;
}

}  // namespace

ResourceRecord MakeDefaultResourceRecord(std::uint32_t minimum_security_epoch) {
    ResourceRecord record{};
    record.active_security_epoch = std::max<std::uint32_t>(1, minimum_security_epoch);
    record.previous_security_epoch = record.active_security_epoch;
    record.security_epoch_floor = record.active_security_epoch;
    SealResourceRecord(&record);
    return record;
}

void SealResourceRecord(ResourceRecord* record) {
    if (record == nullptr) return;
    record->crc32 = Crc32(record, offsetof(ResourceRecord, crc32));
}

bool IsValidResourceRecord(const ResourceRecord& record) {
    return record.version == kResourceRecordVersion &&
           HasBoundedStrings(record) && HasValidPhasePayload(record) &&
           record.crc32 == Crc32(&record, offsetof(ResourceRecord, crc32));
}

bool BeginResourceDownload(ResourceRecord* record, const char* desired_version,
                           const char* bundle_id, const char* payload_sha256,
                           std::uint32_t expected_bytes,
                           std::uint32_t security_epoch) {
    if (record == nullptr || !IsValidResourceRecord(*record) ||
        expected_bytes == 0 || security_epoch < record->security_epoch_floor) {
        return false;
    }
    if (record->phase == ResourceRecordPhase::kDownloading &&
        record->target_slot != record->active_slot &&
        record->expected_bytes == expected_bytes &&
        record->desired_security_epoch == security_epoch &&
        desired_version != nullptr && bundle_id != nullptr &&
        payload_sha256 != nullptr &&
        std::strcmp(record->desired_version, desired_version) == 0 &&
        std::strcmp(record->bundle_id, bundle_id) == 0 &&
        std::strcmp(record->payload_sha256, payload_sha256) == 0) {
        return true;
    }

    ResourceRecord candidate = *record;
    candidate.phase = ResourceRecordPhase::kDownloading;
    candidate.target_slot = static_cast<std::uint8_t>(1U - candidate.active_slot);
    candidate.expected_bytes = expected_bytes;
    candidate.downloaded_bytes = 0;
    candidate.desired_security_epoch = security_epoch;
    if (!CopyString(candidate.desired_version, desired_version) ||
        !CopyString(candidate.bundle_id, bundle_id) ||
        !CopyString(candidate.payload_sha256, payload_sha256) ||
        !IsSha256(candidate.payload_sha256)) {
        return false;
    }
    SealResourceRecord(&candidate);
    *record = candidate;
    return true;
}

bool UpdateResourceDownloadProgress(ResourceRecord* record,
                                    std::uint32_t downloaded_bytes) {
    if (record == nullptr || !IsValidResourceRecord(*record) ||
        record->phase != ResourceRecordPhase::kDownloading ||
        downloaded_bytes < record->downloaded_bytes ||
        downloaded_bytes > record->expected_bytes) {
        return false;
    }
    record->downloaded_bytes = downloaded_bytes;
    SealResourceRecord(record);
    return true;
}

bool ResetResourceDownloadProgress(ResourceRecord* record) {
    if (record == nullptr || !IsValidResourceRecord(*record) ||
        record->phase != ResourceRecordPhase::kDownloading) {
        return false;
    }
    record->downloaded_bytes = 0;
    SealResourceRecord(record);
    return true;
}

bool StageResourceDownload(ResourceRecord* record) {
    if (record == nullptr || !IsValidResourceRecord(*record) ||
        record->phase != ResourceRecordPhase::kDownloading ||
        record->downloaded_bytes != record->expected_bytes) {
        return false;
    }
    record->phase = ResourceRecordPhase::kStaged;
    SealResourceRecord(record);
    return true;
}

bool ActivateStagedResource(ResourceRecord* record) {
    if (record == nullptr || !IsValidResourceRecord(*record) ||
        record->phase != ResourceRecordPhase::kStaged) {
        return false;
    }
    record->previous_slot = record->active_slot;
    record->previous_security_epoch = record->active_security_epoch;
    std::snprintf(record->previous_version, sizeof(record->previous_version), "%s",
                  record->active_version);
    record->active_slot = record->target_slot;
    record->active_security_epoch = record->desired_security_epoch;
    record->security_epoch_floor = std::max(
        record->security_epoch_floor, record->desired_security_epoch);
    std::snprintf(record->active_version, sizeof(record->active_version), "%s",
                  record->desired_version);
    record->phase = ResourceRecordPhase::kPendingHealth;
    SealResourceRecord(record);
    return true;
}

bool ConfirmActiveResource(ResourceRecord* record) {
    if (record == nullptr || !IsValidResourceRecord(*record) ||
        record->phase != ResourceRecordPhase::kPendingHealth) {
        return false;
    }
    record->phase = ResourceRecordPhase::kStable;
    ClearDesired(record);
    SealResourceRecord(record);
    return true;
}

bool RollbackResource(ResourceRecord* record) {
    if (record == nullptr || !IsValidResourceRecord(*record) ||
        (record->phase == ResourceRecordPhase::kStable &&
         record->active_slot == record->previous_slot)) {
        return false;
    }
    if (record->phase == ResourceRecordPhase::kPendingHealth ||
        record->phase == ResourceRecordPhase::kStable) {
        record->active_slot = record->previous_slot;
        record->active_security_epoch = record->previous_security_epoch;
        std::snprintf(record->active_version, sizeof(record->active_version), "%s",
                      record->previous_version);
    }
    record->phase = ResourceRecordPhase::kStable;
    ClearDesired(record);
    SealResourceRecord(record);
    return true;
}

}  // namespace veetee::settings
