"""Deterministic unit tests for Vietnamese-aware TTSTokenSegmenter."""

from __future__ import annotations

from veetee_server.pipeline.segmenter import (
    TTSSegmenterConfig,
    TTSTokenSegmenter,
    normalize_spoken_text,
)


def test_normalize_spoken_text_markdown_urls_and_emojis() -> None:
    text = (
        "# Tiêu đề chính\n"
        "Chào mừng bạn đến với **Veetee**! Đây là *hướng dẫn* sử dụng `trợ lý` giọng nói.\n"
        "Truy cập [trang chủ](https://veetee.ai) hoặc https://veetee.internal/docs ngay 😀!\n"
        "Ký hiệu & bản quyền © 2026."
    )
    normalized = normalize_spoken_text(text)

    assert "Tiêu đề chính" in normalized
    assert "Veetee" in normalized
    assert "**" not in normalized
    assert "*" not in normalized
    assert "`" not in normalized
    assert "trang chủ" in normalized
    assert "https://veetee.ai" not in normalized
    assert "liên kết" in normalized
    assert "và bản quyền" in normalized
    assert "😀" not in normalized
    assert "©" not in normalized


def test_segmenter_vietnamese_abbreviations() -> None:
    config = TTSSegmenterConfig(first_min_chars=10, min_chars=20, max_wait_seconds=1.0)
    segmenter = TTSTokenSegmenter(config=config)

    deltas = [
        "PGS. TS. Nguyễn Văn A và ThS. Trần Thị B ",
        "đến từ Tp. HCM đã họp với Dr. Smith tại Q. 1, P. Bến Nghé. ",
        "Cuộc họp diễn ra tốt đẹp.",
    ]
    segments: list[str] = []
    for d in deltas:
        segments.extend(segmenter.feed(d))
    segments.extend(segmenter.finish())

    # Ensure abbreviations don't prematurely slice segments
    assert len(segments) >= 1
    full = " ".join(segments)
    assert "PGS. TS. Nguyễn" in full
    assert "Tp. HCM" in full
    assert "Dr. Smith" in full


def test_segmenter_numbers_dates_decimals() -> None:
    config = TTSSegmenterConfig(first_min_chars=15, min_chars=30, max_wait_seconds=1.0)
    segmenter = TTSTokenSegmenter(config=config)

    input_text = (
        "Giá trị xấp xỉ pi là 3.14159 và doanh số đạt 1.000.000 USD. "
        "Họp vào ngày 21.08.2026 với tỉ lệ 3,14 phần trăm."
    )
    segments = segmenter.feed(input_text)
    segments.extend(segmenter.finish())

    full = " ".join(segments)
    assert "3.14159" in full
    assert "1.000.000" in full
    assert "21.08.2026" in full
    assert "3,14" in full


def test_segmenter_decimal_fragment_does_not_split() -> None:
    config = TTSSegmenterConfig(first_min_chars=10, min_chars=20, max_wait_seconds=1.0)
    segmenter = TTSTokenSegmenter(config=config)

    assert segmenter.feed("Giá hôm nay là 3.") == []
    segments = segmenter.feed("14 triệu đồng. Phí đã gồm thuế.")
    segments.extend(segmenter.finish())

    assert segments[0] == "Giá hôm nay là 3.14 triệu đồng."


def test_segmenter_quote_and_bracket_balancing() -> None:
    config = TTSSegmenterConfig(first_min_chars=10, min_chars=20, max_wait_seconds=1.0)
    segmenter = TTSTokenSegmenter(config=config)

    # Sentence terminal '.' inside parenthesis/quotes must not split early
    text = 'Hệ thống báo rằng: "Chúng ta cần (xem xét kỹ. Đừng vội!)" trước khi tiếp tục.'
    segments = segmenter.feed(text)
    segments.extend(segmenter.finish())

    assert segments[0] == 'Hệ thống báo rằng: "Chúng ta cần (xem xét kỹ. Đừng vội!)"'
    assert segments[1] == "trước khi tiếp tục."


def test_segmenter_first_min_chars_and_min_chars() -> None:
    config = TTSSegmenterConfig(first_min_chars=15, min_chars=40, max_wait_seconds=1.0)
    segmenter = TTSTokenSegmenter(config=config)

    # First segment can cut at comma if length >= 15
    deltas = [
        "Xin chào quý khách, ",
        "chúng tôi rất vui vì được chào đón và phục vụ bạn hôm nay. ",
        "Chúc bạn một ngày tốt lành!",
    ]
    s0 = segmenter.feed(deltas[0])
    assert len(s0) == 1
    assert s0[0] == "Xin chào quý khách,"

    s1 = segmenter.feed(deltas[1])
    assert len(s1) == 1
    assert "rất vui vì được chào đón" in s1[0]


def test_segmenter_max_wait_seconds() -> None:
    current_time = 100.0

    def mock_clock() -> float:
        return current_time

    config = TTSSegmenterConfig(first_min_chars=20, min_chars=40, max_wait_seconds=0.35)
    segmenter = TTSTokenSegmenter(config=config, clock=mock_clock)

    # Feed incomplete sentence (no punctuation, length < 40)
    s1 = segmenter.feed("Hôm nay tôi muốn nói với bạn rằng ")
    assert s1 == []
    assert segmenter.has_pending

    # Advance time by 0.2s -> not due yet
    current_time += 0.2
    assert segmenter.flush_due() == []

    # Advance time by 0.2s (total 0.4s > 0.35s) -> flush due emits pending segment
    current_time += 0.2
    s_flushed = segmenter.flush_due()
    assert len(s_flushed) == 1
    assert s_flushed[0] == "Hôm nay tôi muốn nói với bạn rằng"
    assert not segmenter.has_pending


def test_segmenter_max_chars_hard_boundary() -> None:
    config = TTSSegmenterConfig(
        first_min_chars=20, min_chars=40, max_chars=100, max_wait_seconds=10.0
    )
    segmenter = TTSTokenSegmenter(config=config)

    long_text = (
        "Đây là một câu rất dài không hề có bất kỳ dấu ngắt câu nào nhưng nó dài vượt quá giới hạn "
        "max_chars của cấu hình và phải tự động bị hard split tại một vị trí thích hợp."
    )
    segments = segmenter.feed(long_text)
    assert len(segments) >= 1
    assert len(segments[0]) <= 100


def test_segmenter_fragmented_deltas() -> None:
    config = TTSSegmenterConfig(first_min_chars=10, min_chars=20, max_wait_seconds=1.0)
    segmenter = TTSTokenSegmenter(config=config)

    full_str = "Xin chào các bạn. Hôm nay trời rất đẹp."
    segments: list[str] = []
    for char in full_str:
        segments.extend(segmenter.feed(char))
    segments.extend(segmenter.finish())

    assert len(segments) == 2
    assert segments[0] == "Xin chào các bạn."
    assert segments[1] == "Hôm nay trời rất đẹp."


def test_segmenter_no_punctuation() -> None:
    config = TTSSegmenterConfig(first_min_chars=20, min_chars=40, max_wait_seconds=1.0)
    segmenter = TTSTokenSegmenter(config=config)

    segmenter.feed("Câu này hoàn toàn không có dấu chấm hay bất kỳ dấu ngắt câu nào")
    flushed = segmenter.finish()
    assert len(flushed) == 1
    assert flushed[0] == "Câu này hoàn toàn không có dấu chấm hay bất kỳ dấu ngắt câu nào"
