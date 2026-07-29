#include "audio/wake_audio_pre_roll.h"

#include <algorithm>
#include <cstdlib>
#include <cstring>

#ifdef ESP_PLATFORM
#include "esp_heap_caps.h"
#endif

namespace veetee::audio {
namespace {

void* AllocateBuffer(std::size_t bytes) {
#ifdef ESP_PLATFORM
    return heap_caps_calloc(1, bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
#else
    return std::calloc(1, bytes);
#endif
}

void ReleaseBuffer(void* buffer) {
#ifdef ESP_PLATFORM
    heap_caps_free(buffer);
#else
    std::free(buffer);
#endif
}

}  // namespace

WakeAudioPreRoll::~WakeAudioPreRoll() {
    std::lock_guard<std::mutex> lock(mutex_);
    ReleaseLocked();
}

bool WakeAudioPreRoll::Configure(bool enabled) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (!enabled) {
        recording_.store(false, std::memory_order_release);
        generation_.fetch_add(1, std::memory_order_acq_rel);
        ReleaseLocked();
        configured_.store(false, std::memory_order_release);
        return true;
    }
    if (packets_ == nullptr) {
        packets_ = static_cast<Packet*>(AllocateBuffer(
            sizeof(Packet) * kWakeAudioPreRollPacketCapacity));
        if (packets_ == nullptr) return false;
    }
    ClearLocked();
    recording_.store(false, std::memory_order_release);
    generation_.fetch_add(1, std::memory_order_acq_rel);
    configured_.store(true, std::memory_order_release);
    return true;
}

bool WakeAudioPreRoll::SetRecording(bool recording) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (recording && packets_ == nullptr) return false;
    const bool previous = recording_.load(std::memory_order_relaxed);
    if (previous == recording) return true;
    generation_.fetch_add(1, std::memory_order_acq_rel);
    if (recording) ClearLocked();
    recording_.store(recording, std::memory_order_release);
    return true;
}

bool WakeAudioPreRoll::Store(const std::uint8_t* packet, std::size_t length,
                             std::uint32_t generation) {
    if (packet == nullptr || length == 0 ||
        length > kWakeAudioPreRollMaximumPacketBytes ||
        !recording_.load(std::memory_order_acquire) ||
        generation_.load(std::memory_order_acquire) != generation) {
        return false;
    }
    std::lock_guard<std::mutex> lock(mutex_);
    if (packets_ == nullptr ||
        !recording_.load(std::memory_order_relaxed) ||
        generation_.load(std::memory_order_relaxed) != generation) {
        return false;
    }

    std::size_t target = (head_ + size_) % kWakeAudioPreRollPacketCapacity;
    if (size_ == kWakeAudioPreRollPacketCapacity) {
        target = head_;
        head_ = (head_ + 1) % kWakeAudioPreRollPacketCapacity;
        overwritten_packets_.fetch_add(1, std::memory_order_relaxed);
    } else {
        ++size_;
        const std::uint32_t depth = static_cast<std::uint32_t>(size_);
        std::uint32_t high_water =
            high_water_packets_.load(std::memory_order_relaxed);
        while (depth > high_water &&
               !high_water_packets_.compare_exchange_weak(
                   high_water, depth, std::memory_order_relaxed)) {
        }
    }
    packets_[target].length = static_cast<std::uint16_t>(length);
    std::memcpy(packets_[target].data, packet, length);
    return true;
}

bool WakeAudioPreRoll::PopWakeAudioPacket(std::uint8_t* destination,
                                          std::size_t capacity,
                                          std::size_t* length) {
    if (destination == nullptr || length == nullptr) return false;
    *length = 0;
    std::lock_guard<std::mutex> lock(mutex_);
    if (packets_ == nullptr || size_ == 0) return false;
    const Packet& packet = packets_[head_];
    if (packet.length == 0 || packet.length > capacity) return false;
    std::memcpy(destination, packet.data, packet.length);
    *length = packet.length;
    head_ = (head_ + 1) % kWakeAudioPreRollPacketCapacity;
    --size_;
    return true;
}

void WakeAudioPreRoll::DiscardWakeAudio() {
    std::lock_guard<std::mutex> lock(mutex_);
    ClearLocked();
    generation_.fetch_add(1, std::memory_order_acq_rel);
}

std::size_t WakeAudioPreRoll::packet_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return size_;
}

std::size_t WakeAudioPreRoll::allocated_bytes() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return packets_ == nullptr
               ? 0
               : sizeof(Packet) * kWakeAudioPreRollPacketCapacity;
}

void WakeAudioPreRoll::ClearLocked() {
    head_ = 0;
    size_ = 0;
}

void WakeAudioPreRoll::ReleaseLocked() {
    ReleaseBuffer(packets_);
    packets_ = nullptr;
    ClearLocked();
}

}  // namespace veetee::audio
