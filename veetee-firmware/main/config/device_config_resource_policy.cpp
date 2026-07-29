#include "config/device_config_resource_policy.h"

#include <cstring>

namespace veetee::config {

DeviceConfigResourceLinkError ValidateDeviceConfigResourceLink(
    const DeviceConfig& config, const char* resource_version,
    const settings::ResourceDetectorInventory& inventory) {
    if (!config.has_wake_profile) {
        return DeviceConfigResourceLinkError::kOk;
    }
    if (!config.activation.enabled ||
        config.activation.model_id[0] == '\0' ||
        config.required_resource_version[0] == '\0') {
        return DeviceConfigResourceLinkError::kInvalidConfig;
    }
    if (resource_version == nullptr || resource_version[0] == '\0' ||
        std::strcmp(config.required_resource_version.data(),
                    resource_version) != 0) {
        return DeviceConfigResourceLinkError::kResourceVersionMismatch;
    }
    if (inventory.activation_model_id[0] == '\0') {
        return DeviceConfigResourceLinkError::kActivationDetectorMissing;
    }
    if (inventory.interrupt_model_id[0] != '\0' &&
        std::strcmp(inventory.activation_model_id,
                    inventory.interrupt_model_id) == 0) {
        return DeviceConfigResourceLinkError::kDetectorRoleCollision;
    }
    if (config.interrupt.enabled &&
        std::strcmp(config.activation.model_id.data(),
                    config.interrupt.model_id.data()) == 0) {
        return DeviceConfigResourceLinkError::kDetectorRoleCollision;
    }
    if (inventory.interrupt_model_id[0] != '\0' &&
        std::strcmp(config.activation.model_id.data(),
                    inventory.interrupt_model_id) == 0) {
        return DeviceConfigResourceLinkError::kActivationDetectorWrongRole;
    }
    if (std::strcmp(config.activation.model_id.data(),
                    inventory.activation_model_id) != 0) {
        return DeviceConfigResourceLinkError::kActivationDetectorMismatch;
    }
    // A signed resource may carry an optional interrupt detector even when
    // this config version leaves interrupt disabled. The unselected detector
    // is not loaded as a role and therefore does not weaken exact linkage.
    if (!config.interrupt.enabled) {
        return DeviceConfigResourceLinkError::kOk;
    }
    if (inventory.interrupt_model_id[0] == '\0') {
        return DeviceConfigResourceLinkError::kInterruptDetectorMissing;
    }
    if (std::strcmp(config.interrupt.model_id.data(),
                    inventory.interrupt_model_id) != 0) {
        return DeviceConfigResourceLinkError::kInterruptDetectorMismatch;
    }
    return DeviceConfigResourceLinkError::kOk;
}

const char* DeviceConfigResourceLinkErrorName(
    DeviceConfigResourceLinkError error) {
    switch (error) {
        case DeviceConfigResourceLinkError::kOk:
            return "ok";
        case DeviceConfigResourceLinkError::kInvalidConfig:
            return "invalid_config";
        case DeviceConfigResourceLinkError::kResourceVersionMismatch:
            return "resource_version_mismatch";
        case DeviceConfigResourceLinkError::kActivationDetectorMissing:
            return "activation_detector_missing";
        case DeviceConfigResourceLinkError::kActivationDetectorMismatch:
            return "activation_detector_mismatch";
        case DeviceConfigResourceLinkError::kActivationDetectorWrongRole:
            return "activation_detector_wrong_role";
        case DeviceConfigResourceLinkError::kInterruptDetectorMissing:
            return "interrupt_detector_missing";
        case DeviceConfigResourceLinkError::kInterruptDetectorMismatch:
            return "interrupt_detector_mismatch";
        case DeviceConfigResourceLinkError::kDetectorRoleCollision:
            return "detector_role_collision";
    }
    return "unknown";
}

DeviceConfigResourceApplyDecision DecideDeviceConfigResourceApply(
    DeviceConfigResourceLinkError link_error,
    bool detector_inventory_known) {
    if (link_error == DeviceConfigResourceLinkError::kOk) {
        return DeviceConfigResourceApplyDecision::kApply;
    }
    if (link_error ==
            DeviceConfigResourceLinkError::kResourceVersionMismatch ||
        (link_error ==
             DeviceConfigResourceLinkError::kActivationDetectorMissing &&
         !detector_inventory_known)) {
        return DeviceConfigResourceApplyDecision::kWaitForResource;
    }
    return DeviceConfigResourceApplyDecision::kReject;
}

}  // namespace veetee::config
