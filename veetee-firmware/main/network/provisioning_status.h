#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>

namespace veetee::network {

enum class ProvisioningPhase : std::uint8_t {
    kIdle,
    kSaved,
    kConnecting,
    kConnected,
    kFailed,
};

enum class ProvisioningFailure : std::uint8_t {
    kNone,
    kAuthenticationFailed,
    kNetworkNotFound,
    kDhcpTimeout,
    kConnectionTimeout,
    kUnknown,
};

struct ProvisioningStatusSnapshot {
    static constexpr std::uint8_t kSchemaVersion = 1;

    std::uint32_t attempt_id = 0;
    ProvisioningPhase phase = ProvisioningPhase::kIdle;
    ProvisioningFailure failure = ProvisioningFailure::kNone;
    bool retryable = false;
};

class ProvisioningStatus {
public:
    std::uint32_t BeginAttempt();
    bool MarkSaved(std::uint32_t attempt_id);
    bool MarkConnecting(std::uint32_t attempt_id);
    bool MarkConnected(std::uint32_t attempt_id);
    bool RememberFailure(std::uint32_t attempt_id,
                         ProvisioningFailure failure);
    bool MarkFailed(std::uint32_t attempt_id, ProvisioningFailure failure,
                    bool retryable = true);
    bool CanBeginAttempt() const;
    ProvisioningStatusSnapshot Snapshot() const;
    void Reset();

private:
    static constexpr std::uint32_t kAttemptMask = 0x00FFFFFFU;
    static constexpr std::uint32_t kPhaseShift = 24;
    static constexpr std::uint32_t kFailureShift = 27;
    static constexpr std::uint32_t kRetryableShift = 30;

    static std::uint32_t Pack(std::uint32_t attempt_id,
                              ProvisioningPhase phase,
                              ProvisioningFailure failure, bool retryable);
    static ProvisioningStatusSnapshot Unpack(std::uint32_t state);
    bool Update(std::uint32_t attempt_id, ProvisioningPhase phase,
                ProvisioningFailure failure, bool retryable);

    std::atomic<std::uint32_t> state_{0};
};

constexpr std::size_t kProvisioningStatusJsonBytes = 192;

ProvisioningFailure ClassifyProvisioningTimeout(
    bool station_associated, ProvisioningFailure remembered_failure);
bool SerializeProvisioningStatus(const ProvisioningStatusSnapshot& status,
                                 char* destination, std::size_t capacity);
const char* ToString(ProvisioningPhase phase);
const char* ToString(ProvisioningFailure failure);

}  // namespace veetee::network
