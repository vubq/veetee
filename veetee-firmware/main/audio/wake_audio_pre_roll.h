#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>
#include <mutex>

namespace veetee::audio {

constexpr std::size_t kWakeAudioPreRollPacketCapacity = 32;
constexpr std::size_t kWakeAudioPreRollMaximumPacketBytes = 1500;
constexpr std::uint32_t kWakeAudioPreRollDurationMs = 32U * 60U;

class WakeAudioSource {
public:
    virtual ~WakeAudioSource() = default;
    virtual bool PopWakeAudioPacket(std::uint8_t* destination,
                                    std::size_t capacity,
                                    std::size_t* length) = 0;
    virtual void DiscardWakeAudio() = 0;
};

class WakeAudioPreRoll final : public WakeAudioSource {
public:
    WakeAudioPreRoll() = default;
    ~WakeAudioPreRoll();

    WakeAudioPreRoll(const WakeAudioPreRoll&) = delete;
    WakeAudioPreRoll& operator=(const WakeAudioPreRoll&) = delete;

    bool Configure(bool enabled);
    bool SetRecording(bool recording);
    bool Store(const std::uint8_t* packet, std::size_t length,
               std::uint32_t generation);

    bool PopWakeAudioPacket(std::uint8_t* destination,
                            std::size_t capacity,
                            std::size_t* length) override;
    void DiscardWakeAudio() override;

    [[nodiscard]] bool configured() const {
        return configured_.load(std::memory_order_acquire);
    }
    [[nodiscard]] bool recording() const {
        return recording_.load(std::memory_order_acquire);
    }
    [[nodiscard]] std::uint32_t generation() const {
        return generation_.load(std::memory_order_acquire);
    }
    [[nodiscard]] std::size_t packet_count() const;
    [[nodiscard]] std::size_t allocated_bytes() const;
    [[nodiscard]] std::uint64_t overwritten_packets() const {
        return overwritten_packets_.load(std::memory_order_relaxed);
    }
    [[nodiscard]] std::uint32_t high_water_packets() const {
        return high_water_packets_.load(std::memory_order_relaxed);
    }

private:
    struct Packet {
        std::uint16_t length = 0;
        std::uint8_t data[kWakeAudioPreRollMaximumPacketBytes] = {};
    };

    void ClearLocked();
    void ReleaseLocked();

    mutable std::mutex mutex_;
    Packet* packets_ = nullptr;
    std::size_t head_ = 0;
    std::size_t size_ = 0;
    std::atomic<bool> configured_{false};
    std::atomic<bool> recording_{false};
    std::atomic<std::uint32_t> generation_{0};
    std::atomic<std::uint64_t> overwritten_packets_{0};
    std::atomic<std::uint32_t> high_water_packets_{0};
};

}  // namespace veetee::audio
