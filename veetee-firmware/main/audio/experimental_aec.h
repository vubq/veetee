#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>

#include "esp_err.h"
#include "freertos/FreeRTOS.h"

namespace veetee::audio {

// Opt-in ESP-SR AEC path used only to collect physical-board evidence. It
// cleans the local detector stream while real speaker playback is active; it
// does not enable conversation uplink during SPEAKING and is not advertised in
// the V1 hello capability.
class ExperimentalAec {
public:
    ~ExperimentalAec();

    esp_err_t Initialize();
    void BeginPlayback();
    void EndPlayback();
    bool PushPlayback24k(const std::int16_t* samples,
                         std::size_t sample_count, int volume_percent);
    bool ProcessMicrophone16k(std::int16_t* samples,
                              std::size_t sample_count);

    [[nodiscard]] bool enabled() const { return enabled_.load(); }

private:
    static constexpr std::size_t kMaximumAecChunkSamples = 1024;
    static constexpr std::size_t kMaximumMicrophoneFrameSamples = 512;
    static constexpr std::size_t kMaximumPlaybackFrameSamples = 1440;
    static constexpr std::size_t kMaximumDownsampledFrameSamples = 960;
    static constexpr std::size_t kReferenceRingSamples = 3840;

    void Release();
    void ResetProcessingState(std::uint32_t generation);
    void ResetReferenceRing();
    std::size_t PopReference(std::int16_t* destination,
                             std::size_t sample_count);
    bool AppendProcessedOutput(const std::int16_t* samples,
                               std::size_t sample_count);
    std::int16_t PopProcessedOutput();

    void* handle_ = nullptr;
    std::size_t chunk_samples_ = 0;
    std::int16_t* microphone_chunk_ = nullptr;
    std::int16_t* reference_chunk_ = nullptr;
    std::int16_t* processed_chunk_ = nullptr;
    std::int16_t* processed_fifo_ = nullptr;
    std::int16_t* reference_ring_ = nullptr;
    std::int16_t* reference_scratch_ = nullptr;
    std::int16_t* downsample_scratch_ = nullptr;

    std::size_t chunk_fill_ = 0;
    std::size_t processed_read_ = 0;
    std::size_t processed_write_ = 0;
    std::size_t processed_count_ = 0;
    std::size_t reference_read_ = 0;
    std::size_t reference_write_ = 0;
    std::size_t reference_count_ = 0;
    std::uint32_t processing_generation_ = 0;

    std::atomic<bool> enabled_{false};
    std::atomic<bool> playback_active_{false};
    std::atomic<std::uint32_t> reference_generation_{0};
    portMUX_TYPE reference_mux_ = portMUX_INITIALIZER_UNLOCKED;
};

}  // namespace veetee::audio
