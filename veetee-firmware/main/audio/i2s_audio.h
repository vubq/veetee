#pragma once

#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

#include "driver/i2s_std.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/task.h"

namespace veetee::audio {

class I2sAudio {
public:
    using EncodedAudioSink = bool (*)(const std::uint8_t* packet,
                                      std::size_t length, void* context);
    using PcmFrameSink = bool (*)(const std::int16_t* samples,
                                  std::size_t sample_count, void* context);
    using PlaybackFinishedSink = bool (*)(void* context);

    esp_err_t Initialize(EncodedAudioSink encoded_sink,
                         PcmFrameSink pcm_frame_sink,
                         PlaybackFinishedSink playback_finished_sink,
                         void* sink_context,
                         void* pcm_frame_context);
    esp_err_t Start(bool play_boot_chime);

    void SetCaptureEnabled(bool enabled);
    void BeginPlayback();
    bool QueueOpusPlayback(const std::uint8_t* packet, std::size_t length);
    void EndPlayback();
    void AbortPlayback();
    bool SetVolumePercent(int volume_percent);
    [[nodiscard]] int volume_percent() const { return volume_percent_.load(); }

private:
    enum class PlaybackItemKind : std::uint8_t {
        kBegin,
        kPacket,
        kEnd,
        kAbort,
    };

    struct PlaybackItem {
        PlaybackItemKind kind;
        std::uint32_t generation;
        std::uint16_t length = 0;
        std::array<std::uint8_t, 1500> data{};
    };

    static void CaptureTaskEntry(void* context);
    static void PlaybackTaskEntry(void* context);

    void RunCapture();
    void RunPlayback();
    void PlayBootChime();
    bool WriteChimeNote(double frequency_hz, int frame_count);
    void WriteSilence();
    bool QueuePlaybackControl(PlaybackItemKind kind,
                              std::uint32_t generation);

    static constexpr std::size_t kMicReadSamples = 320;
    static constexpr std::size_t kUplinkFrameSamples = 960;
    static constexpr std::size_t kDownlinkFrameSamples = 1440;
    static constexpr std::size_t kSpeakerDmaSamples = 480;
    static constexpr std::size_t kToneFrameSamples = 240;

    i2s_chan_handle_t rx_handle_ = nullptr;
    i2s_chan_handle_t tx_handle_ = nullptr;
    void* encoder_ = nullptr;
    void* decoder_ = nullptr;
    QueueHandle_t playback_queue_ = nullptr;
    TaskHandle_t capture_task_ = nullptr;
    TaskHandle_t playback_task_ = nullptr;
    EncodedAudioSink encoded_sink_ = nullptr;
    PcmFrameSink pcm_frame_sink_ = nullptr;
    PlaybackFinishedSink playback_finished_sink_ = nullptr;
    void* sink_context_ = nullptr;
    void* pcm_frame_context_ = nullptr;

    std::array<std::int32_t, kMicReadSamples> mic_dma_buffer_{};
    std::array<std::int16_t, kMicReadSamples> detector_pcm_{};
    std::array<std::int16_t, kUplinkFrameSamples> capture_pcm_{};
    std::array<std::uint8_t, 1500> encoded_buffer_{};
    std::array<std::int16_t, kDownlinkFrameSamples> playback_pcm_{};
    std::array<std::int32_t, kDownlinkFrameSamples> speaker_dma_buffer_{};
    std::array<std::int32_t, kToneFrameSamples> tone_dma_buffer_{};

    std::atomic<bool> capture_enabled_{false};
    std::atomic<std::uint32_t> capture_generation_{0};
    std::atomic<bool> playback_accepting_{false};
    std::atomic<std::uint32_t> playback_generation_{0};
    std::atomic<int> volume_percent_{70};
    bool play_boot_chime_ = false;
};

}  // namespace veetee::audio
