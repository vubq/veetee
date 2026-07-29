#include "settings/resource_record.h"

#include <algorithm>
#include <cstddef>
#include <cstdio>
#include <cstring>
#include <type_traits>

namespace veetee::settings {
namespace {

static_assert(std::is_trivially_copyable_v<ResourceRecord>);
static_assert(sizeof(ResourceRecord) == 656,
              "Resource record layout is a versioned NVS contract");

struct ResourceRecordV1 {
    std::uint32_t version = 1;
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

static_assert(std::is_trivially_copyable_v<ResourceRecordV1>);
static_assert(sizeof(ResourceRecordV1) == 268,
              "Legacy resource record layout is an NVS migration contract");

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

bool IsWakeNetModelId(const char* value) {
    if (value == nullptr) return false;
    const std::size_t length = std::strlen(value);
    if (length < 3 || length > 64 || value[0] != 'w' || value[1] != 'n') {
        return false;
    }
    return std::all_of(value + 2, value + length, [](char character) {
        return (character >= 'a' && character <= 'z') ||
               (character >= 'A' && character <= 'Z') ||
               (character >= '0' && character <= '9') ||
               character == '.' || character == '_' || character == '-';
    });
}

bool IsValidDetectorInventory(const ResourceDetectorInventory& inventory) {
    if (inventory.activation_model_id[0] == '\0') {
        return inventory.interrupt_model_id[0] == '\0';
    }
    return IsWakeNetModelId(inventory.activation_model_id) &&
           (inventory.interrupt_model_id[0] == '\0' ||
            (IsWakeNetModelId(inventory.interrupt_model_id) &&
             std::strcmp(inventory.activation_model_id,
                         inventory.interrupt_model_id) != 0));
}

bool CopyDetectorInventory(ResourceDetectorInventory* destination,
                           const char* activation_model_id,
                           const char* interrupt_model_id) {
    if (destination == nullptr) return false;
    ResourceDetectorInventory candidate{};
    const bool empty_activation =
        activation_model_id == nullptr || activation_model_id[0] == '\0';
    const bool empty_interrupt =
        interrupt_model_id == nullptr || interrupt_model_id[0] == '\0';
    if (empty_activation) {
        if (!empty_interrupt) return false;
    } else {
        if (std::strlen(activation_model_id) >=
                sizeof(candidate.activation_model_id) ||
            (!empty_interrupt &&
             std::strlen(interrupt_model_id) >=
                 sizeof(candidate.interrupt_model_id))) {
            return false;
        }
        std::snprintf(candidate.activation_model_id,
                      sizeof(candidate.activation_model_id), "%s",
                      activation_model_id);
        if (!empty_interrupt) {
            std::snprintf(candidate.interrupt_model_id,
                          sizeof(candidate.interrupt_model_id), "%s",
                          interrupt_model_id);
        }
    }
    if (!IsValidDetectorInventory(candidate)) return false;
    *destination = candidate;
    return true;
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
    record->desired_detectors = {};
}

bool HasBoundedStrings(const ResourceRecord& record) {
    return IsTerminated(record.active_version) &&
           IsTerminated(record.previous_version) &&
           IsTerminated(record.desired_version) &&
           IsTerminated(record.payload_sha256) && IsTerminated(record.bundle_id) &&
           IsTerminated(record.active_detectors.activation_model_id) &&
           IsTerminated(record.active_detectors.interrupt_model_id) &&
           IsTerminated(record.previous_detectors.activation_model_id) &&
           IsTerminated(record.previous_detectors.interrupt_model_id) &&
           IsTerminated(record.desired_detectors.activation_model_id) &&
           IsTerminated(record.desired_detectors.interrupt_model_id);
}

bool HasValidPhasePayload(const ResourceRecord& record) {
    if (record.active_slot > 1 || record.previous_slot > 1 ||
        record.target_slot > 1 || record.active_version[0] == '\0' ||
        record.previous_version[0] == '\0' ||
        record.active_security_epoch == 0 ||
        record.previous_security_epoch == 0) {
        return false;
    }
    if (!IsValidDetectorInventory(record.active_detectors) ||
        !IsValidDetectorInventory(record.previous_detectors) ||
        !IsValidDetectorInventory(record.desired_detectors)) {
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
                   record.payload_sha256[0] == '\0' &&
                   record.bundle_id[0] == '\0' &&
                   !HasResourceDetectorInventory(record.desired_detectors);
        case ResourceRecordPhase::kDownloading:
            return record.target_slot != record.active_slot &&
                   record.expected_bytes > 0 &&
                   record.downloaded_bytes <= record.expected_bytes &&
                   record.desired_security_epoch >= record.security_epoch_floor &&
                   record.desired_version[0] != '\0' &&
                   IsSha256(record.payload_sha256) && record.bundle_id[0] != '\0';
        case ResourceRecordPhase::kStaged:
            return record.target_slot != record.active_slot &&
                   record.expected_bytes > 0 &&
                   record.downloaded_bytes == record.expected_bytes &&
                   record.desired_security_epoch >= record.security_epoch_floor &&
                   record.desired_version[0] != '\0' &&
                   IsSha256(record.payload_sha256) && record.bundle_id[0] != '\0';
        case ResourceRecordPhase::kPendingHealth:
            return record.active_slot != record.previous_slot &&
                   record.target_slot == record.active_slot &&
                   record.expected_bytes > 0 &&
                   record.downloaded_bytes == record.expected_bytes &&
                   record.desired_security_epoch == record.active_security_epoch &&
                   record.desired_security_epoch >= record.security_epoch_floor &&
                   std::strcmp(record.desired_version, record.active_version) == 0 &&
                   ResourceDetectorInventoryMatches(
                       record.active_detectors,
                       record.desired_detectors.activation_model_id,
                       record.desired_detectors.interrupt_model_id) &&
                   IsSha256(record.payload_sha256) && record.bundle_id[0] != '\0';
    }
    return false;
}

}  // namespace

ResourceRecord MakeDefaultResourceRecord(std::uint32_t minimum_security_epoch,
                                         const char* default_version,
                                         const char* default_activation_model_id,
                                         const char* default_interrupt_model_id) {
    ResourceRecord record{};
    record.active_security_epoch = std::max<std::uint32_t>(1, minimum_security_epoch);
    record.previous_security_epoch = record.active_security_epoch;
    record.security_epoch_floor = record.active_security_epoch;
    if (default_version != nullptr && default_version[0] != '\0' &&
        std::strlen(default_version) < sizeof(record.active_version)) {
        std::snprintf(record.active_version, sizeof(record.active_version), "%s",
                      default_version);
        std::snprintf(record.previous_version,
                      sizeof(record.previous_version), "%s", default_version);
    }
    if (!CopyDetectorInventory(&record.active_detectors,
                               default_activation_model_id,
                               default_interrupt_model_id)) {
        record.active_detectors = {};
    }
    record.previous_detectors = record.active_detectors;
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

bool MigrateResourceRecordV1(const void* data, std::size_t size,
                             const char* default_version,
                             const char* default_activation_model_id,
                             const char* default_interrupt_model_id,
                             ResourceRecord* record) {
    if (data == nullptr || size != sizeof(ResourceRecordV1) || record == nullptr) {
        return false;
    }
    ResourceRecordV1 legacy{};
    std::memcpy(&legacy, data, sizeof(legacy));
    if (legacy.version != 1 ||
        !IsTerminated(legacy.active_version) ||
        !IsTerminated(legacy.previous_version) ||
        !IsTerminated(legacy.desired_version) ||
        !IsTerminated(legacy.payload_sha256) ||
        !IsTerminated(legacy.bundle_id) ||
        legacy.crc32 != Crc32(&legacy, offsetof(ResourceRecordV1, crc32))) {
        return false;
    }

    ResourceRecord migrated{};
    migrated.phase = legacy.phase;
    migrated.active_slot = legacy.active_slot;
    migrated.previous_slot = legacy.previous_slot;
    migrated.target_slot = legacy.target_slot;
    migrated.expected_bytes = legacy.expected_bytes;
    migrated.downloaded_bytes = legacy.downloaded_bytes;
    migrated.active_security_epoch = legacy.active_security_epoch;
    migrated.previous_security_epoch = legacy.previous_security_epoch;
    migrated.desired_security_epoch = legacy.desired_security_epoch;
    migrated.security_epoch_floor = legacy.security_epoch_floor;
    std::memcpy(migrated.active_version, legacy.active_version,
                sizeof(migrated.active_version));
    std::memcpy(migrated.previous_version, legacy.previous_version,
                sizeof(migrated.previous_version));
    std::memcpy(migrated.desired_version, legacy.desired_version,
                sizeof(migrated.desired_version));
    std::memcpy(migrated.payload_sha256, legacy.payload_sha256,
                sizeof(migrated.payload_sha256));
    std::memcpy(migrated.bundle_id, legacy.bundle_id,
                sizeof(migrated.bundle_id));

    if (default_version != nullptr && default_activation_model_id != nullptr &&
        migrated.active_slot == 0 &&
        std::strcmp(migrated.active_version, default_version) == 0) {
        CopyDetectorInventory(&migrated.active_detectors,
                              default_activation_model_id,
                              default_interrupt_model_id);
    }
    if (migrated.previous_slot == migrated.active_slot) {
        migrated.previous_detectors = migrated.active_detectors;
    }
    SealResourceRecord(&migrated);
    if (!IsValidResourceRecord(migrated)) return false;
    *record = migrated;
    return true;
}

bool HasResourceDetectorInventory(
    const ResourceDetectorInventory& inventory) {
    return inventory.activation_model_id[0] != '\0';
}

bool ResourceDetectorInventoryMatches(
    const ResourceDetectorInventory& inventory,
    const char* activation_model_id, const char* interrupt_model_id) {
    const char* activation = activation_model_id == nullptr
                                 ? ""
                                 : activation_model_id;
    const char* interrupt = interrupt_model_id == nullptr
                                ? ""
                                : interrupt_model_id;
    return std::strcmp(inventory.activation_model_id, activation) == 0 &&
           std::strcmp(inventory.interrupt_model_id, interrupt) == 0;
}

namespace {

bool BindResourceDetectorInventory(ResourceRecord* record,
                                   ResourceDetectorInventory* current,
                                   const char* activation_model_id,
                                   const char* interrupt_model_id,
                                   bool* changed) {
    if (changed != nullptr) *changed = false;
    if (record == nullptr || current == nullptr ||
        !IsValidResourceRecord(*record)) {
        return false;
    }
    ResourceDetectorInventory incoming{};
    if (!CopyDetectorInventory(&incoming, activation_model_id,
                               interrupt_model_id)) {
        return false;
    }
    if (HasResourceDetectorInventory(*current)) {
        return ResourceDetectorInventoryMatches(
            *current, incoming.activation_model_id,
            incoming.interrupt_model_id);
    }
    if (!HasResourceDetectorInventory(incoming)) {
        return true;
    }
    *current = incoming;
    SealResourceRecord(record);
    if (changed != nullptr) *changed = true;
    return true;
}

}  // namespace

bool BindActiveResourceDetectorInventory(
    ResourceRecord* record, const char* activation_model_id,
    const char* interrupt_model_id, bool* changed) {
    return record != nullptr &&
           BindResourceDetectorInventory(
               record, &record->active_detectors, activation_model_id,
               interrupt_model_id, changed);
}

bool BindDesiredResourceDetectorInventory(
    ResourceRecord* record, const char* activation_model_id,
    const char* interrupt_model_id, bool* changed) {
    if (record == nullptr ||
        (record->phase != ResourceRecordPhase::kDownloading &&
         record->phase != ResourceRecordPhase::kStaged)) {
        if (changed != nullptr) *changed = false;
        return false;
    }
    return BindResourceDetectorInventory(
        record, &record->desired_detectors, activation_model_id,
        interrupt_model_id, changed);
}

bool BeginResourceDownload(ResourceRecord* record, const char* desired_version,
                           const char* bundle_id, const char* payload_sha256,
                           std::uint32_t expected_bytes,
                           std::uint32_t security_epoch,
                           const char* activation_model_id,
                           const char* interrupt_model_id) {
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
        std::strcmp(record->payload_sha256, payload_sha256) == 0 &&
        ResourceDetectorInventoryMatches(
            record->desired_detectors, activation_model_id,
            interrupt_model_id)) {
        return true;
    }

    ResourceRecord candidate = *record;
    candidate.phase = ResourceRecordPhase::kDownloading;
    candidate.target_slot = static_cast<std::uint8_t>(1U - candidate.active_slot);
    if (candidate.previous_slot == candidate.target_slot) {
        // The inactive download is about to erase the old fallback slot. Its
        // version remains useful for reporting, but its detector inventory can
        // no longer authorize a boot/runtime fallback.
        candidate.previous_detectors = {};
    }
    candidate.expected_bytes = expected_bytes;
    candidate.downloaded_bytes = 0;
    candidate.desired_security_epoch = security_epoch;
    if (!CopyString(candidate.desired_version, desired_version) ||
        !CopyString(candidate.bundle_id, bundle_id) ||
        !CopyString(candidate.payload_sha256, payload_sha256) ||
        !IsSha256(candidate.payload_sha256) ||
        !CopyDetectorInventory(&candidate.desired_detectors,
                               activation_model_id,
                               interrupt_model_id)) {
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
    record->previous_detectors = record->active_detectors;
    std::snprintf(record->previous_version, sizeof(record->previous_version), "%s",
                  record->active_version);
    record->active_slot = record->target_slot;
    record->active_security_epoch = record->desired_security_epoch;
    record->active_detectors = record->desired_detectors;
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
        record->active_detectors = record->previous_detectors;
        std::snprintf(record->active_version, sizeof(record->active_version), "%s",
                      record->previous_version);
    }
    record->phase = ResourceRecordPhase::kStable;
    ClearDesired(record);
    SealResourceRecord(record);
    return true;
}

}  // namespace veetee::settings
