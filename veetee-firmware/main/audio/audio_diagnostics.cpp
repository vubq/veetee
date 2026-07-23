#include "audio/audio_diagnostics.h"

#include <algorithm>
#include <cmath>
#include <limits>

namespace veetee::audio {

bool AudioDiagnostics::Start(std::uint32_t duration_seconds,
                             std::uint64_t now_ms) {
    CompleteIfExpired(now_ms);
    if (duration_seconds < kMinimumDurationSeconds ||
        duration_seconds > kMaximumDurationSeconds ||
        state_ == AudioDiagnosticState::kRunning) {
        return false;
    }

    session_id_ = next_session_id_++;
    if (next_session_id_ == 0) next_session_id_ = 1;
    duration_seconds_ = duration_seconds;
    started_ms_ = now_ms;
    ends_ms_ = now_ms + static_cast<std::uint64_t>(duration_seconds) * 1000;
    session_pcm_frames_ = 0;
    session_samples_ = 0;
    session_sum_squares_ = 0;
    session_sum_ = 0;
    session_peak_absolute_ = 0;
    session_clipped_samples_ = 0;
    session_playback_queue_high_water_ = 0;
    session_start_counters_ = lifetime_;
    state_ = AudioDiagnosticState::kRunning;
    return true;
}

void AudioDiagnostics::ObservePcm(const std::int16_t* samples,
                                  std::size_t sample_count,
                                  std::uint64_t now_ms) {
    if (samples == nullptr || sample_count == 0) return;
    ++lifetime_.mic_frames;
    lifetime_.mic_samples += sample_count;

    CompleteIfExpired(now_ms);
    if (state_ != AudioDiagnosticState::kRunning) return;

    ++session_pcm_frames_;
    session_samples_ += sample_count;
    for (std::size_t index = 0; index < sample_count; ++index) {
        const std::int32_t sample = samples[index];
        const std::int32_t absolute =
            sample == std::numeric_limits<std::int16_t>::min()
                ? 32768
                : std::abs(sample);
        session_sum_ += sample;
        session_sum_squares_ +=
            static_cast<std::uint64_t>(static_cast<std::int64_t>(sample) *
                                       sample);
        session_peak_absolute_ =
            std::max(session_peak_absolute_, absolute);
        if (absolute >= 32760) ++session_clipped_samples_;
    }
}

void AudioDiagnostics::Increment(AudioCounter counter) {
    switch (counter) {
        case AudioCounter::kMicReadError:
            ++lifetime_.mic_read_errors;
            break;
        case AudioCounter::kMicReadTimeout:
            ++lifetime_.mic_read_timeouts;
            break;
        case AudioCounter::kDetectorFrameDrop:
            ++lifetime_.detector_frame_drops;
            break;
        case AudioCounter::kOpusEncodeFailure:
            ++lifetime_.opus_encode_failures;
            break;
        case AudioCounter::kUplinkDrop:
            ++lifetime_.uplink_drops;
            break;
        case AudioCounter::kPlaybackQueueDrop:
            ++lifetime_.playback_queue_drops;
            break;
        case AudioCounter::kOpusDecodeFailure:
            ++lifetime_.opus_decode_failures;
            break;
        case AudioCounter::kSpeakerWriteFailure:
            ++lifetime_.speaker_write_failures;
            break;
    }
}

void AudioDiagnostics::ObservePlaybackQueueDepth(std::uint32_t depth) {
    lifetime_.playback_queue_high_water =
        std::max(lifetime_.playback_queue_high_water, depth);
    if (state_ == AudioDiagnosticState::kRunning) {
        session_playback_queue_high_water_ =
            std::max(session_playback_queue_high_water_, depth);
    }
}

AudioDiagnosticSnapshot AudioDiagnostics::Snapshot(std::uint64_t now_ms) {
    CompleteIfExpired(now_ms);
    AudioDiagnosticSnapshot snapshot{
        .state = state_,
        .session_id = session_id_,
        .duration_seconds = duration_seconds_,
        .started_ms = started_ms_,
        .ends_ms = ends_ms_,
        .pcm_frames = session_pcm_frames_,
        .sample_count = session_samples_,
        .peak_absolute = session_peak_absolute_,
        .clipped_samples = session_clipped_samples_,
        .counters = Difference(lifetime_, session_start_counters_),
    };
    snapshot.counters.playback_queue_high_water =
        session_playback_queue_high_water_;
    if (session_samples_ > 0) {
        snapshot.rms = std::sqrt(
            static_cast<double>(session_sum_squares_) / session_samples_);
        snapshot.dc_offset =
            static_cast<double>(session_sum_) / session_samples_;
        snapshot.clipping_percent =
            static_cast<double>(session_clipped_samples_) * 100.0 /
            session_samples_;
    }
    return snapshot;
}

void AudioDiagnostics::CompleteIfExpired(std::uint64_t now_ms) {
    if (state_ == AudioDiagnosticState::kRunning && now_ms >= ends_ms_) {
        state_ = AudioDiagnosticState::kCompleted;
    }
}

AudioCounters AudioDiagnostics::Difference(const AudioCounters& value,
                                           const AudioCounters& baseline) {
    return AudioCounters{
        .mic_frames = value.mic_frames - baseline.mic_frames,
        .mic_samples = value.mic_samples - baseline.mic_samples,
        .mic_read_errors = value.mic_read_errors - baseline.mic_read_errors,
        .mic_read_timeouts = value.mic_read_timeouts - baseline.mic_read_timeouts,
        .detector_frame_drops =
            value.detector_frame_drops - baseline.detector_frame_drops,
        .opus_encode_failures =
            value.opus_encode_failures - baseline.opus_encode_failures,
        .uplink_drops = value.uplink_drops - baseline.uplink_drops,
        .playback_queue_drops =
            value.playback_queue_drops - baseline.playback_queue_drops,
        .opus_decode_failures =
            value.opus_decode_failures - baseline.opus_decode_failures,
        .speaker_write_failures =
            value.speaker_write_failures - baseline.speaker_write_failures,
    };
}

const char* ToString(AudioDiagnosticState state) {
    switch (state) {
        case AudioDiagnosticState::kNotRun:
            return "not_run";
        case AudioDiagnosticState::kRunning:
            return "running";
        case AudioDiagnosticState::kCompleted:
            return "completed";
    }
    return "not_run";
}

}  // namespace veetee::audio
