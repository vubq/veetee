from __future__ import annotations

import pytest

from veetee_voice_server.conversation.sentence_chunker import SentenceChunker


def test_sentence_chunker_keeps_short_sentences_together() -> None:
    chunker = SentenceChunker(min_characters=8, target_characters=20, max_characters=40)

    assert chunker.push("Ừ. Tôi nghe đây.") == ["Ừ. Tôi nghe đây."]
    assert chunker.flush() is None


def test_sentence_chunker_emits_natural_clause_before_model_finishes() -> None:
    chunker = SentenceChunker(min_characters=8, target_characters=20, max_characters=80)

    assert chunker.push("Đầu tiên tôi sẽ kiểm tra dữ liệu, sau đó") == [
        "Đầu tiên tôi sẽ kiểm tra dữ liệu,"
    ]
    assert chunker.push(" tôi sẽ nói rõ kết quả.") == ["sau đó tôi sẽ nói rõ kết quả."]


def test_sentence_chunker_bounds_stream_without_punctuation() -> None:
    chunker = SentenceChunker(min_characters=8, target_characters=20, max_characters=32)

    chunks = chunker.push("Đây là một câu trả lời rất dài nhưng mô hình chưa kịp thêm dấu")

    assert chunks == ["Đây là một câu trả lời rất dài"]
    assert len(chunker.flush() or "") < 32


def test_sentence_chunker_never_exceeds_max_after_late_clause_pause() -> None:
    chunker = SentenceChunker(min_characters=8, target_characters=20, max_characters=32)

    chunks = chunker.push(
        "Đây là một đoạn đủ dài, có thêm nhiều thông tin trước dấu phẩy thứ hai, "
        "rồi tiếp tục"
    )

    assert chunks
    assert all(len(chunk) <= 32 for chunk in chunks)


def test_sentence_chunker_bounds_unbroken_token_stream() -> None:
    chunker = SentenceChunker(min_characters=4, target_characters=8, max_characters=16)

    chunks = chunker.push("abcdefghijklmnopq")

    assert chunks == ["abcdefghijklmnop"]
    assert chunker.flush() == "q"


@pytest.mark.parametrize(
    ("min_characters", "target_characters", "max_characters"),
    [(0, 20, 40), (20, 10, 40), (20, 40, 30)],
)
def test_sentence_chunker_rejects_invalid_bounds(
    min_characters: int,
    target_characters: int,
    max_characters: int,
) -> None:
    with pytest.raises(ValueError):
        SentenceChunker(
            min_characters,
            target_characters=target_characters,
            max_characters=max_characters,
        )


def test_sentence_chunker_can_coalesce_short_sentences_for_cloud_tts() -> None:
    chunker = SentenceChunker(
        min_characters=8,
        target_characters=48,
        max_characters=96,
        punctuation_min_characters=48,
    )

    assert chunker.push("Câu đầu ngắn. Câu thứ hai cũng ngắn. ") == []
    assert chunker.push("Đủ thành một đoạn tự nhiên.") == [
        "Câu đầu ngắn. Câu thứ hai cũng ngắn. Đủ thành một đoạn tự nhiên."
    ]
