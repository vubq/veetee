#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>

#include "audio/wake_audio_pre_roll.h"

namespace {

using veetee::audio::WakeAudioPreRoll;
using veetee::audio::kWakeAudioPreRollMaximumPacketBytes;
using veetee::audio::kWakeAudioPreRollPacketCapacity;

void TestPrivacyDefaultAllocatesAndStoresNothing() {
    WakeAudioPreRoll buffer;
    const std::uint8_t packet[] = {1, 2, 3};
    assert(!buffer.configured());
    assert(buffer.allocated_bytes() == 0);
    assert(!buffer.SetRecording(true));
    assert(!buffer.Store(packet, sizeof(packet), buffer.generation()));
    assert(buffer.packet_count() == 0);
}

void TestOptInUsesOneBoundedRingAndKeepsNewestPackets() {
    WakeAudioPreRoll buffer;
    assert(buffer.Configure(true));
    assert(buffer.configured());
    assert(buffer.allocated_bytes() <=
           kWakeAudioPreRollPacketCapacity *
               (kWakeAudioPreRollMaximumPacketBytes + sizeof(std::uint16_t) + 2));
    assert(buffer.SetRecording(true));
    const std::uint32_t generation = buffer.generation();

    for (std::size_t index = 0;
         index < kWakeAudioPreRollPacketCapacity + 3; ++index) {
        const std::uint8_t packet[] = {
            static_cast<std::uint8_t>(index), 0x55,
        };
        assert(buffer.Store(packet, sizeof(packet), generation));
    }
    assert(buffer.packet_count() == kWakeAudioPreRollPacketCapacity);
    assert(buffer.overwritten_packets() == 3);
    assert(buffer.high_water_packets() == kWakeAudioPreRollPacketCapacity);

    std::array<std::uint8_t, kWakeAudioPreRollMaximumPacketBytes> packet{};
    std::size_t length = 0;
    assert(buffer.SetRecording(false));
    assert(buffer.PopWakeAudioPacket(packet.data(), packet.size(), &length));
    assert(length == 2);
    assert(packet[0] == 3);
}

void TestGenerationAndDiscardRejectStaleFrames() {
    WakeAudioPreRoll buffer;
    assert(buffer.Configure(true));
    assert(buffer.SetRecording(true));
    const std::uint32_t old_generation = buffer.generation();
    const std::uint8_t packet[] = {7};
    assert(buffer.Store(packet, sizeof(packet), old_generation));
    assert(buffer.SetRecording(false));
    assert(!buffer.Store(packet, sizeof(packet), old_generation));

    buffer.DiscardWakeAudio();
    assert(buffer.packet_count() == 0);
    assert(buffer.SetRecording(true));
    assert(!buffer.Store(packet, sizeof(packet), old_generation));
    assert(buffer.Store(packet, sizeof(packet), buffer.generation()));

    assert(buffer.Configure(false));
    assert(!buffer.configured());
    assert(buffer.allocated_bytes() == 0);
    assert(buffer.packet_count() == 0);
}

void TestOptOutImmediatelyClearsAndReleasesTheRing() {
    WakeAudioPreRoll buffer;
    assert(buffer.Configure(true));
    assert(buffer.SetRecording(true));
    const std::uint32_t enabled_generation = buffer.generation();
    const std::uint8_t cached_packet[] = {0x11, 0x22, 0x33};
    assert(buffer.Store(cached_packet, sizeof(cached_packet),
                        enabled_generation));
    assert(buffer.packet_count() == 1);
    assert(buffer.allocated_bytes() > 0);

    assert(buffer.Configure(false));
    assert(!buffer.configured());
    assert(!buffer.recording());
    assert(buffer.generation() != enabled_generation);
    assert(buffer.packet_count() == 0);
    assert(buffer.allocated_bytes() == 0);

    std::array<std::uint8_t, kWakeAudioPreRollMaximumPacketBytes> packet{};
    std::size_t length = 99;
    assert(!buffer.PopWakeAudioPacket(packet.data(), packet.size(), &length));
    assert(length == 0);
    assert(!buffer.Store(cached_packet, sizeof(cached_packet),
                         enabled_generation));

    // Repeated false reload/rollback remains allocation-free and idempotent.
    assert(buffer.Configure(false));
    assert(buffer.allocated_bytes() == 0);
}

}  // namespace

int main() {
    TestPrivacyDefaultAllocatesAndStoresNothing();
    TestOptInUsesOneBoundedRingAndKeepsNewestPackets();
    TestGenerationAndDiscardRejectStaleFrames();
    TestOptOutImmediatelyClearsAndReleasesTheRing();
    std::cout << "wake_audio_pre_roll_test: passed\n";
    return 0;
}
