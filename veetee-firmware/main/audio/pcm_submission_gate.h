#pragma once

#include <atomic>

namespace veetee::audio {

// A close/drain gate for a single producer callback and a runtime owner. The
// second accepting check closes the race where a producer observes open just
// before the owner closes an otherwise idle gate.
class PcmSubmissionGate {
public:
    bool TryEnter() {
        if (!accepting_.load(std::memory_order_acquire)) return false;
        in_flight_.fetch_add(1, std::memory_order_acq_rel);
        if (accepting_.load(std::memory_order_acquire)) return true;
        Leave();
        return false;
    }

    void Leave() { in_flight_.fetch_sub(1, std::memory_order_acq_rel); }
    void Close() { accepting_.store(false, std::memory_order_release); }
    void Open() { accepting_.store(true, std::memory_order_release); }

    [[nodiscard]] bool drained() const {
        return in_flight_.load(std::memory_order_acquire) == 0;
    }

private:
    std::atomic<bool> accepting_{true};
    std::atomic<unsigned> in_flight_{0};
};

}  // namespace veetee::audio
