#include "network/provisioning_status.h"

#include <cstdio>

namespace veetee::network {

std::uint32_t ProvisioningStatus::Pack(std::uint32_t attempt_id,
                                       ProvisioningPhase phase,
                                       ProvisioningFailure failure,
                                       bool retryable) {
    return (attempt_id & kAttemptMask) |
           (static_cast<std::uint32_t>(phase) << kPhaseShift) |
           (static_cast<std::uint32_t>(failure) << kFailureShift) |
           (static_cast<std::uint32_t>(retryable) << kRetryableShift);
}

ProvisioningStatusSnapshot ProvisioningStatus::Unpack(std::uint32_t state) {
    return {
        .attempt_id = state & kAttemptMask,
        .phase = static_cast<ProvisioningPhase>((state >> kPhaseShift) & 0x07U),
        .failure =
            static_cast<ProvisioningFailure>((state >> kFailureShift) & 0x07U),
        .retryable = ((state >> kRetryableShift) & 0x01U) != 0,
    };
}

std::uint32_t ProvisioningStatus::BeginAttempt() {
    std::uint32_t current = state_.load(std::memory_order_acquire);
    for (;;) {
        const ProvisioningStatusSnapshot snapshot = Unpack(current);
        std::uint32_t next_attempt = (snapshot.attempt_id + 1) & kAttemptMask;
        if (next_attempt == 0) next_attempt = 1;
        const std::uint32_t next =
            Pack(next_attempt, ProvisioningPhase::kSaved,
                 ProvisioningFailure::kNone, false);
        if (state_.compare_exchange_weak(current, next,
                                         std::memory_order_acq_rel,
                                         std::memory_order_acquire)) {
            return next_attempt;
        }
    }
}

bool ProvisioningStatus::MarkSaved(std::uint32_t attempt_id) {
    return Update(attempt_id, ProvisioningPhase::kSaved,
                  ProvisioningFailure::kNone, false);
}

bool ProvisioningStatus::MarkConnecting(std::uint32_t attempt_id) {
    return Update(attempt_id, ProvisioningPhase::kConnecting,
                  ProvisioningFailure::kNone, false);
}

bool ProvisioningStatus::MarkConnected(std::uint32_t attempt_id) {
    return Update(attempt_id, ProvisioningPhase::kConnected,
                  ProvisioningFailure::kNone, false);
}

bool ProvisioningStatus::RememberFailure(std::uint32_t attempt_id,
                                         ProvisioningFailure failure) {
    std::uint32_t current = state_.load(std::memory_order_acquire);
    for (;;) {
        const ProvisioningStatusSnapshot snapshot = Unpack(current);
        if (attempt_id == 0 || snapshot.attempt_id != attempt_id) return false;
        const std::uint32_t next =
            Pack(attempt_id, snapshot.phase, failure, snapshot.retryable);
        if (state_.compare_exchange_weak(current, next,
                                         std::memory_order_acq_rel,
                                         std::memory_order_acquire)) {
            return true;
        }
    }
}

bool ProvisioningStatus::MarkFailed(std::uint32_t attempt_id,
                                    ProvisioningFailure failure,
                                    bool retryable) {
    return Update(attempt_id, ProvisioningPhase::kFailed, failure, retryable);
}

bool ProvisioningStatus::CanBeginAttempt() const {
    const ProvisioningPhase phase = Snapshot().phase;
    return phase == ProvisioningPhase::kIdle ||
           phase == ProvisioningPhase::kFailed;
}

ProvisioningStatusSnapshot ProvisioningStatus::Snapshot() const {
    return Unpack(state_.load(std::memory_order_acquire));
}

void ProvisioningStatus::Reset() {
    std::uint32_t current = state_.load(std::memory_order_acquire);
    for (;;) {
        const ProvisioningStatusSnapshot snapshot = Unpack(current);
        const std::uint32_t next =
            Pack(snapshot.attempt_id, ProvisioningPhase::kIdle,
                 ProvisioningFailure::kNone, false);
        if (state_.compare_exchange_weak(current, next,
                                         std::memory_order_acq_rel,
                                         std::memory_order_acquire)) {
            return;
        }
    }
}

bool ProvisioningStatus::Update(std::uint32_t attempt_id,
                                ProvisioningPhase phase,
                                ProvisioningFailure failure,
                                bool retryable) {
    std::uint32_t current = state_.load(std::memory_order_acquire);
    for (;;) {
        const ProvisioningStatusSnapshot snapshot = Unpack(current);
        if (attempt_id == 0 || snapshot.attempt_id != attempt_id) return false;
        const std::uint32_t next =
            Pack(attempt_id, phase, failure, retryable);
        if (state_.compare_exchange_weak(current, next,
                                         std::memory_order_acq_rel,
                                         std::memory_order_acquire)) {
            return true;
        }
    }
}

ProvisioningFailure ClassifyProvisioningTimeout(
    bool station_associated, ProvisioningFailure remembered_failure) {
    if (remembered_failure != ProvisioningFailure::kNone) {
        return remembered_failure;
    }
    return station_associated ? ProvisioningFailure::kDhcpTimeout
                              : ProvisioningFailure::kConnectionTimeout;
}

bool SerializeProvisioningStatus(const ProvisioningStatusSnapshot& status,
                                 char* destination, std::size_t capacity) {
    if (destination == nullptr || capacity == 0) return false;
    const int written = status.failure == ProvisioningFailure::kNone
        ? std::snprintf(destination, capacity,
                        "{\"version\":%u,\"attempt_id\":%u,\"phase\":\"%s\",\"retryable\":%s}",
                        ProvisioningStatusSnapshot::kSchemaVersion,
                        static_cast<unsigned>(status.attempt_id),
                        ToString(status.phase), status.retryable ? "true" : "false")
        : std::snprintf(destination, capacity,
                        "{\"version\":%u,\"attempt_id\":%u,\"phase\":\"%s\",\"reason\":\"%s\",\"retryable\":%s}",
                        ProvisioningStatusSnapshot::kSchemaVersion,
                        static_cast<unsigned>(status.attempt_id),
                        ToString(status.phase), ToString(status.failure),
                        status.retryable ? "true" : "false");
    return written >= 0 && static_cast<std::size_t>(written) < capacity;
}

const char* ToString(ProvisioningPhase phase) {
    switch (phase) {
        case ProvisioningPhase::kIdle: return "idle";
        case ProvisioningPhase::kSaved: return "saved";
        case ProvisioningPhase::kConnecting: return "connecting";
        case ProvisioningPhase::kConnected: return "connected";
        case ProvisioningPhase::kFailed: return "failed";
    }
    return "idle";
}

const char* ToString(ProvisioningFailure failure) {
    switch (failure) {
        case ProvisioningFailure::kNone: return "";
        case ProvisioningFailure::kAuthenticationFailed:
            return "authentication_failed";
        case ProvisioningFailure::kNetworkNotFound:
            return "network_not_found";
        case ProvisioningFailure::kDhcpTimeout: return "dhcp_timeout";
        case ProvisioningFailure::kConnectionTimeout:
            return "connection_timeout";
        case ProvisioningFailure::kUnknown: return "unknown";
    }
    return "unknown";
}

}  // namespace veetee::network
