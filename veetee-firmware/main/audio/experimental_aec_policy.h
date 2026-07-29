#pragma once

#include <cstddef>
#include <cstdint>

namespace veetee::audio {

// This is an opt-in board-validation path, not a production full-duplex
// capability. These gates stay false until physical ERLE/false-wake/latency
// acceptance has passed on the exact INMP441 + MAX98357A assembly.
inline constexpr bool kAdvertiseExperimentalAec = false;
inline constexpr bool kCaptureConversationWhileSpeaking = false;
inline constexpr std::uint32_t kAecSampleRateHz = 16000;
inline constexpr std::uint32_t kSpeakerReferenceSampleRateHz = 24000;
inline constexpr std::size_t kAecReferenceInputGroupSamples = 3;
inline constexpr std::size_t kAecReferenceOutputGroupSamples = 2;

constexpr std::int16_t ScaleAecReferenceSample(std::int16_t sample,
                                               int volume_percent) {
    if (volume_percent <= 0) return 0;
    if (volume_percent >= 100) return sample;
    const std::int32_t scaled =
        static_cast<std::int32_t>(sample) * volume_percent / 100;
    return static_cast<std::int16_t>(scaled);
}

// Deterministic 3:2 linear resampling for the actual mono playback PCM. Input
// frames in Veetee are 60 ms/1440 samples (and local tones are also a multiple
// of three), so no hidden cross-call tail is needed. Returning zero rejects a
// malformed frame instead of silently shifting the AEC reference clock.
inline std::size_t DownsampleAecReference24kTo16k(
    const std::int16_t* input, std::size_t input_samples, int volume_percent,
    std::int16_t* output, std::size_t output_capacity) {
    if (input == nullptr || output == nullptr || volume_percent < 0 ||
        volume_percent > 100 || input_samples == 0 ||
        input_samples % kAecReferenceInputGroupSamples != 0) {
        return 0;
    }
    const std::size_t required =
        input_samples / kAecReferenceInputGroupSamples *
        kAecReferenceOutputGroupSamples;
    if (required > output_capacity) return 0;

    std::size_t destination = 0;
    for (std::size_t source = 0; source < input_samples; source += 3) {
        const std::int16_t first =
            ScaleAecReferenceSample(input[source], volume_percent);
        const std::int16_t second =
            ScaleAecReferenceSample(input[source + 1], volume_percent);
        const std::int16_t third =
            ScaleAecReferenceSample(input[source + 2], volume_percent);
        output[destination++] = first;
        output[destination++] = static_cast<std::int16_t>(
            (static_cast<std::int32_t>(second) + third) / 2);
    }
    return destination;
}

}  // namespace veetee::audio
