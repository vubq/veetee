#include <array>
#include <cassert>
#include <cstdint>
#include <iostream>

#include "audio/experimental_aec_policy.h"

int main() {
    using namespace veetee::audio;

    static_assert(kAecSampleRateHz == 16000);
    static_assert(kSpeakerReferenceSampleRateHz == 24000);
    static_assert(!kAdvertiseExperimentalAec);
    static_assert(!kCaptureConversationWhileSpeaking);

    constexpr std::array<std::int16_t, 6> input{
        1200, 600, 300, -1200, -600, -300,
    };
    std::array<std::int16_t, 4> output{};
    assert(DownsampleAecReference24kTo16k(
               input.data(), input.size(), 100, output.data(), output.size()) ==
           output.size());
    assert((output == std::array<std::int16_t, 4>{1200, 450, -1200, -450}));

    output.fill(7);
    assert(DownsampleAecReference24kTo16k(
               input.data(), input.size(), 50, output.data(), output.size()) ==
           output.size());
    assert((output == std::array<std::int16_t, 4>{600, 225, -600, -225}));

    output.fill(7);
    assert(DownsampleAecReference24kTo16k(
               input.data(), input.size(), 0, output.data(), output.size()) ==
           output.size());
    assert((output == std::array<std::int16_t, 4>{0, 0, 0, 0}));

    assert(DownsampleAecReference24kTo16k(
               input.data(), input.size() - 1, 100, output.data(),
               output.size()) == 0);
    assert(DownsampleAecReference24kTo16k(
               input.data(), input.size(), 101, output.data(), output.size()) ==
           0);
    assert(DownsampleAecReference24kTo16k(
               input.data(), input.size(), 100, output.data(),
               output.size() - 1) == 0);

    std::cout << "experimental_aec_policy_test: passed\n";
    return 0;
}
