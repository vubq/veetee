#pragma once

#include <cstdint>

namespace veetee::audio {

enum class PlaybackItemKind : std::uint8_t {
    kBegin,
    kPacket,
    kEnd,
    kAbort,
    kAbortAndFinish,
};

constexpr bool ResetsPlaybackDecoder(PlaybackItemKind kind) {
    return kind == PlaybackItemKind::kBegin ||
           kind == PlaybackItemKind::kAbort ||
           kind == PlaybackItemKind::kAbortAndFinish;
}

constexpr bool FinishesPlayback(PlaybackItemKind kind) {
    return kind == PlaybackItemKind::kEnd ||
           kind == PlaybackItemKind::kAbortAndFinish;
}

constexpr PlaybackItemKind PlaybackEndOverflowRecovery() {
    return PlaybackItemKind::kAbortAndFinish;
}

}  // namespace veetee::audio
