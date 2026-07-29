#include <cassert>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <thread>
#include <string>

#include "network/provisioning_status.h"

int main() {
    using veetee::network::ClassifyProvisioningTimeout;
    using veetee::network::ProvisioningFailure;
    using veetee::network::ProvisioningPhase;
    using veetee::network::ProvisioningStatus;
    using veetee::network::ProvisioningStatusSnapshot;
    using veetee::network::SerializeProvisioningStatus;
    using veetee::network::ToString;

    ProvisioningStatus status;
    assert(status.Snapshot().phase == ProvisioningPhase::kIdle);
    assert(status.CanBeginAttempt());

    const std::uint32_t first = status.BeginAttempt();
    assert(!status.CanBeginAttempt());
    assert(first != 0);
    assert(status.Snapshot().phase == ProvisioningPhase::kSaved);
    assert(status.MarkConnecting(first));
    assert(status.MarkConnected(first));
    assert(status.Snapshot().phase == ProvisioningPhase::kConnected);
    assert(!status.CanBeginAttempt());
    status.Reset();
    assert(status.CanBeginAttempt());

    const std::uint32_t second = status.BeginAttempt();
    assert(second != first);
    assert(!status.MarkFailed(first, ProvisioningFailure::kUnknown));
    assert(status.Snapshot().attempt_id == second);
    assert(status.Snapshot().phase == ProvisioningPhase::kSaved);
    assert(status.MarkConnecting(second));
    assert(status.RememberFailure(
        second, ProvisioningFailure::kAuthenticationFailed));
    assert(status.Snapshot().phase == ProvisioningPhase::kConnecting);
    assert(status.MarkFailed(second,
                             ProvisioningFailure::kAuthenticationFailed));
    assert(status.Snapshot().retryable);
    assert(status.CanBeginAttempt());
    assert(std::strcmp(ToString(status.Snapshot().phase), "failed") == 0);
    assert(std::strcmp(ToString(status.Snapshot().failure),
                       "authentication_failed") == 0);
    assert(ClassifyProvisioningTimeout(
               false, ProvisioningFailure::kAuthenticationFailed) ==
           ProvisioningFailure::kAuthenticationFailed);
    assert(ClassifyProvisioningTimeout(false, ProvisioningFailure::kNone) ==
           ProvisioningFailure::kConnectionTimeout);
    assert(ClassifyProvisioningTimeout(true, ProvisioningFailure::kNone) ==
           ProvisioningFailure::kDhcpTimeout);

    char json[veetee::network::kProvisioningStatusJsonBytes] = {};
    assert(SerializeProvisioningStatus(status.Snapshot(), json, sizeof(json)));
    const std::string serialized(json);
    assert(serialized.size() < 1024);
    assert(serialized.find("\"reason\":\"authentication_failed\"") !=
           std::string::npos);
    for (const char* secret : {"password", "ssid", "bootstrap", "ip", "token",
                               "challenge", "activation_code"}) {
        assert(serialized.find(secret) == std::string::npos);
    }
    char too_small[8] = {};
    assert(!SerializeProvisioningStatus(ProvisioningStatusSnapshot{}, too_small,
                                        sizeof(too_small)));

    status.Reset();
    assert(status.Snapshot().phase == ProvisioningPhase::kIdle);
    assert(status.Snapshot().attempt_id == second);

    std::atomic<bool> start{false};
    std::thread late_events([&] {
        while (!start.load(std::memory_order_acquire)) {
        }
        for (std::uint32_t index = 0; index < 10000; ++index) {
            status.MarkConnected(second);
            status.MarkFailed(second, ProvisioningFailure::kUnknown);
        }
    });
    start.store(true, std::memory_order_release);
    const std::uint32_t current = status.BeginAttempt();
    late_events.join();
    const auto concurrent = status.Snapshot();
    assert(current != second);
    assert(concurrent.attempt_id == current);
    assert(concurrent.phase == ProvisioningPhase::kSaved);
    assert(concurrent.failure == ProvisioningFailure::kNone);
    return 0;
}
