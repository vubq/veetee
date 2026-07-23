#pragma once

#include <cstddef>
#include <cstdint>

namespace veetee::audio {

enum class AudioDiagnosticState : std::uint8_t {
    kNotRun,
    kRunning,
    kCompleted,
};

enum class AudioCounter : std::uint8_t {
    kMicReadError,
    kMicReadTimeout,
    kDetectorFrameDrop,
    kOpusEncodeFailure,
    kUplinkDrop,
    kPlaybackQueueDrop,
    kOpusDecodeFailure,
    kSpeakerWriteFailure,
};

struct AudioCounters {
    std::uint64_t mic_frames = 0;
    std::uint64_t mic_samples = 0;
    std::uint64_t mic_read_errors = 0;
    std::uint64_t mic_read_timeouts = 0;
    std::uint64_t detector_frame_drops = 0;
    std::uint64_t opus_encode_failures = 0;
    std::uint64_t uplink_drops = 0;
    std::uint64_t playback_queue_drops = 0;
    std::uint32_t playback_queue_high_water = 0;
    std::uint64_t opus_decode_failures = 0;
    std::uint64_t speaker_write_failures = 0;
};

struct AudioDiagnosticSnapshot {
    AudioDiagnosticState state = AudioDiagnosticState::kNotRun;
    std::uint32_t session_id = 0;
    std::uint32_t duration_seconds = 0;
    std::uint64_t started_ms = 0;
    std::uint64_t ends_ms = 0;
    std::uint64_t pcm_frames = 0;
    std::uint64_t sample_count = 0;
    double rms = 0.0;
    std::int32_t peak_absolute = 0;
    double dc_offset = 0.0;
    std::uint64_t clipped_samples = 0;
    double clipping_percent = 0.0;
    AudioCounters counters{};
};

struct AudioRuntimeHealth {
    bool capture_task_running = false;
    bool playback_task_running = false;
    AudioCounters lifetime{};
    AudioDiagnosticSnapshot diagnostic{};
};

class AudioDiagnostics {
public:
    static constexpr std::uint32_t kMinimumDurationSeconds = 1;
    static constexpr std::uint32_t kMaximumDurationSeconds = 30;

    bool Start(std::uint32_t duration_seconds, std::uint64_t now_ms);
    void ObservePcm(const std::int16_t* samples, std::size_t sample_count,
                    std::uint64_t now_ms);
    void Increment(AudioCounter counter);
    void ObservePlaybackQueueDepth(std::uint32_t depth);

    AudioDiagnosticSnapshot Snapshot(std::uint64_t now_ms);
    [[nodiscard]] AudioCounters LifetimeCounters() const {
        return lifetime_;
    }

private:
    void CompleteIfExpired(std::uint64_t now_ms);
    static AudioCounters Difference(const AudioCounters& value,
                                    const AudioCounters& baseline);

    AudioDiagnosticState state_ = AudioDiagnosticState::kNotRun;
    std::uint32_t next_session_id_ = 1;
    std::uint32_t session_id_ = 0;
    std::uint32_t duration_seconds_ = 0;
    std::uint64_t started_ms_ = 0;
    std::uint64_t ends_ms_ = 0;
    std::uint64_t session_pcm_frames_ = 0;
    std::uint64_t session_samples_ = 0;
    std::uint64_t session_sum_squares_ = 0;
    std::int64_t session_sum_ = 0;
    std::int32_t session_peak_absolute_ = 0;
    std::uint64_t session_clipped_samples_ = 0;
    std::uint32_t session_playback_queue_high_water_ = 0;
    AudioCounters session_start_counters_{};
    AudioCounters lifetime_{};
};

const char* ToString(AudioDiagnosticState state);

}  // namespace veetee::audio
