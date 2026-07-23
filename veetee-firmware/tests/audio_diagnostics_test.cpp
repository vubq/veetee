#include <cmath>
#include <cstdlib>
#include <iostream>
#include <limits>

#include "audio/audio_diagnostics.h"

namespace {

void Expect(bool condition, const char* description) {
    if (!condition) {
        std::cerr << "FAILED: " << description << '\n';
        std::exit(1);
    }
}

void TestBoundsAndBusySession() {
    veetee::audio::AudioDiagnostics diagnostics;
    Expect(!diagnostics.Start(0, 100), "zero-second session is rejected");
    Expect(!diagnostics.Start(31, 100), "overlong session is rejected");
    Expect(diagnostics.Start(3, 100), "bounded session starts");
    Expect(!diagnostics.Start(1, 101), "concurrent session is rejected");
    Expect(diagnostics.Snapshot(3'099).state ==
               veetee::audio::AudioDiagnosticState::kRunning,
           "session remains running before deadline");
    Expect(diagnostics.Snapshot(3'100).state ==
               veetee::audio::AudioDiagnosticState::kCompleted,
           "session completes at deadline");
    Expect(diagnostics.Start(1, 3'100), "next session starts after completion");
}

void TestPcmMetricsAndCounterDelta() {
    veetee::audio::AudioDiagnostics diagnostics;
    diagnostics.Increment(veetee::audio::AudioCounter::kMicReadTimeout);
    Expect(diagnostics.Start(1, 5'000), "metrics session starts");

    const std::int16_t samples[] = {
        std::numeric_limits<std::int16_t>::min(), -1, 1,
        std::numeric_limits<std::int16_t>::max(),
    };
    diagnostics.ObservePcm(samples, 4, 5'100);
    diagnostics.Increment(veetee::audio::AudioCounter::kMicReadTimeout);
    diagnostics.Increment(veetee::audio::AudioCounter::kDetectorFrameDrop);
    diagnostics.Increment(veetee::audio::AudioCounter::kPlaybackQueueDrop);
    diagnostics.ObservePlaybackQueueDepth(7);

    const auto snapshot = diagnostics.Snapshot(6'000);
    Expect(snapshot.state ==
               veetee::audio::AudioDiagnosticState::kCompleted,
           "metrics session completes");
    Expect(snapshot.pcm_frames == 1 && snapshot.sample_count == 4,
           "PCM volume is bounded and counted");
    Expect(snapshot.peak_absolute == 32768, "INT16_MIN peak is handled safely");
    Expect(snapshot.clipped_samples == 2 &&
               std::abs(snapshot.clipping_percent - 50.0) < 0.001,
           "clipping metrics are exact");
    Expect(std::abs(snapshot.dc_offset + 0.25) < 0.001,
           "DC offset is signed");
    Expect(snapshot.rms > 23'000.0 && snapshot.rms < 24'000.0,
           "RMS is calculated from PCM samples");
    Expect(snapshot.counters.mic_read_timeouts == 1,
           "session counter excludes pre-session failures");
    Expect(snapshot.counters.detector_frame_drops == 1 &&
               snapshot.counters.playback_queue_drops == 1,
           "session drop counters are reported");
    Expect(snapshot.counters.playback_queue_high_water == 7,
           "session queue high-water is reported");
    Expect(diagnostics.LifetimeCounters().mic_read_timeouts == 2,
           "lifetime counter retains pre-session failures");
}

void TestSamplesAfterDeadlineAreExcluded() {
    veetee::audio::AudioDiagnostics diagnostics;
    Expect(diagnostics.Start(1, 0), "deadline session starts");
    const std::int16_t before[] = {100};
    const std::int16_t after[] = {1000};
    diagnostics.ObservePcm(before, 1, 999);
    diagnostics.ObservePcm(after, 1, 1'000);
    const auto snapshot = diagnostics.Snapshot(1'000);
    Expect(snapshot.sample_count == 1 && snapshot.peak_absolute == 100,
           "frame observed at deadline is excluded from completed session");
    Expect(diagnostics.LifetimeCounters().mic_samples == 2,
           "lifetime metrics continue after diagnostic completion");
}

}  // namespace

int main() {
    TestBoundsAndBusySession();
    TestPcmMetricsAndCounterDelta();
    TestSamplesAfterDeadlineAreExcluded();
    std::cout << "audio_diagnostics_test: passed\n";
    return 0;
}
