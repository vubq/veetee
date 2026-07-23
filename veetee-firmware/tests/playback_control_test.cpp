#include <cstdlib>
#include <iostream>

#include "audio/playback_control.h"

namespace {

void Expect(bool condition, const char* message) {
    if (!condition) {
        std::cerr << "FAIL: " << message << '\n';
        std::exit(1);
    }
}

}  // namespace

int main() {
    using veetee::audio::FinishesPlayback;
    using veetee::audio::PlaybackEndOverflowRecovery;
    using veetee::audio::PlaybackItemKind;
    using veetee::audio::ResetsPlaybackDecoder;

    Expect(FinishesPlayback(PlaybackItemKind::kEnd),
           "normal end must finish playback");
    Expect(!ResetsPlaybackDecoder(PlaybackItemKind::kEnd),
           "normal end must preserve queued audio");
    Expect(!FinishesPlayback(PlaybackItemKind::kAbort),
           "user abort must not impersonate a normal playback completion");
    Expect(ResetsPlaybackDecoder(PlaybackItemKind::kAbort),
           "user abort must reset the decoder");

    const PlaybackItemKind recovery = PlaybackEndOverflowRecovery();
    Expect(recovery == PlaybackItemKind::kAbortAndFinish,
           "end overflow must select the abort-and-finish recovery");
    Expect(ResetsPlaybackDecoder(recovery),
           "end overflow recovery must discard the incomplete stream");
    Expect(FinishesPlayback(recovery),
           "end overflow recovery must release the speaking state");
    return 0;
}
