#pragma once

#include <cstdint>

#include "config/device_config.h"
#include "settings/resource_record.h"

namespace veetee::config {

enum class DeviceConfigResourceLinkError : std::uint8_t {
    kOk,
    kInvalidConfig,
    kResourceVersionMismatch,
    kActivationDetectorMissing,
    kActivationDetectorMismatch,
    kActivationDetectorWrongRole,
    kInterruptDetectorMissing,
    kInterruptDetectorMismatch,
    kDetectorRoleCollision,
};

enum class DeviceConfigResourceApplyDecision : std::uint8_t {
    kApply,
    kWaitForResource,
    kReject,
};

DeviceConfigResourceLinkError ValidateDeviceConfigResourceLink(
    const DeviceConfig& config, const char* resource_version,
    const settings::ResourceDetectorInventory& inventory);

const char* DeviceConfigResourceLinkErrorName(
    DeviceConfigResourceLinkError error);

DeviceConfigResourceApplyDecision DecideDeviceConfigResourceApply(
    DeviceConfigResourceLinkError link_error,
    bool detector_inventory_known);

}  // namespace veetee::config
