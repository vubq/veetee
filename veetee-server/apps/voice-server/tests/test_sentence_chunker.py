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


def test_sentence_chunker_bounds_a_long_sentence_when_punctuation_arrives() -> None:
    chunker = SentenceChunker(
        min_characters=8,
        target_characters=24,
        max_characters=32,
        punctuation_min_characters=24,
    )

    chunks = chunker.push(
        "Đây là một câu rất dài được mô hình gửi liền mạch cho tới khi dấu chấm xuất hiện."
    )

    assert len(chunks) > 1
    assert all(len(chunk) <= 32 for chunk in chunks)
    assert " ".join(chunks) == (
        "Đây là một câu rất dài được mô hình gửi liền mạch cho tới khi dấu chấm "
        "xuất hiện."
    )


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


def test_sentence_bounded_chunker_keeps_kho_khan_in_one_request() -> None:
    chunker = SentenceChunker(
        min_characters=8,
        target_characters=24,
        max_characters=40,
        mode="sentence_bounded",
        emergency_max_characters=112,
    )

    assert chunker.push("Đây là một tình huống có nhiều điều khó") == []
    assert chunker.push(" khăn nhưng vẫn xử lý được.") == []
    assert chunker.push(" Câu sau") == []
    assert chunker.flush() == (
        "Đây là một tình huống có nhiều điều khó khăn nhưng vẫn xử lý được. Câu sau"
    )


def test_sentence_bounded_chunker_ignores_clause_punctuation() -> None:
    chunker = SentenceChunker(
        min_characters=8,
        target_characters=20,
        max_characters=32,
        mode="sentence_bounded",
        emergency_max_characters=96,
    )

    assert chunker.push("Đầu tiên, tôi kiểm tra: dữ liệu; rồi tiếp tục") == []
    assert chunker.push(".") == []
    assert chunker.push(" Câu mới") == []
    assert chunker.flush() == "Đầu tiên, tôi kiểm tra: dữ liệu; rồi tiếp tục. Câu mới"


def test_sentence_bounded_chunker_handles_decimal_abbreviation_and_closer() -> None:
    chunker = SentenceChunker(
        min_characters=8,
        target_characters=24,
        max_characters=40,
        mode="sentence_bounded",
        emergency_max_characters=112,
    )

    assert chunker.push("PGS. An dùng phiên bản 3.") == []
    assert chunker.push("14 và nói: \"Ổn rồi!") == []
    assert chunker.push("\" Câu sau") == []
    assert chunker.flush() == 'PGS. An dùng phiên bản 3.14 và nói: "Ổn rồi!" Câu sau'


def test_sentence_bounded_chunker_groups_short_complete_sentences() -> None:
    chunker = SentenceChunker(
        min_characters=8,
        target_characters=24,
        max_characters=40,
        mode="sentence_bounded",
        emergency_max_characters=160,
        sentence_batch_max_characters=72,
    )

    assert chunker.push("Câu đầu ngắn. Câu sau") == []
    assert chunker.push(" cũng ngắn. Câu thứ ba dài hơn một chút") == []
    assert chunker.push(" để làm tràn batch. Câu tiếp") == [
        "Câu đầu ngắn. Câu sau cũng ngắn."
    ]
    assert chunker.flush() == "Câu thứ ba dài hơn một chút để làm tràn batch. Câu tiếp"


def test_sentence_bounded_chunker_keeps_a_161_to_256_character_sentence_intact() -> None:
    sentence = "Một " + ("ý " * 94) + "xong."
    assert 161 <= len(sentence) <= 256
    chunker = SentenceChunker(
        min_characters=8,
        target_characters=24,
        max_characters=40,
        mode="sentence_bounded",
        emergency_max_characters=256,
        sentence_batch_max_characters=256,
    )

    assert chunker.push(f"{sentence} Câu sau") == []
    assert chunker.flush() == f"{sentence} Câu sau"


def test_sentence_bounded_chunker_drops_entirely_punctuation_only_stream() -> None:
    chunker = SentenceChunker(
        min_characters=8,
        target_characters=24,
        max_characters=40,
        mode="sentence_bounded",
        emergency_max_characters=256,
        sentence_batch_max_characters=256,
    )

    assert chunker.push("…?!") == []
    assert chunker.flush_chunks() == []


def test_sentence_bounded_chunker_splits_final_tail_over_batch_limit() -> None:
    chunker = SentenceChunker(
        min_characters=8,
        target_characters=24,
        max_characters=40,
        mode="sentence_bounded",
        emergency_max_characters=160,
        sentence_batch_max_characters=40,
    )

    assert chunker.push("Đây là một câu hoàn chỉnh. Câu cuối còn dài") == []
    assert [(chunk.text, chunk.reason) for chunk in chunker.flush_chunks()] == [
        ("Đây là một câu hoàn chỉnh.", "sentence"),
        ("Câu cuối còn dài", "final"),
    ]


def test_sentence_bounded_chunker_absorbs_punctuation_only_tail() -> None:
    chunker = SentenceChunker(
        min_characters=8,
        target_characters=24,
        max_characters=40,
        mode="sentence_bounded",
        emergency_max_characters=160,
        sentence_batch_max_characters=72,
    )

    assert chunker.push("Câu hoàn chỉnh. Tiếp") == []
    assert chunker.push("?!") == []
    assert chunker.flush() == "Câu hoàn chỉnh. Tiếp?!"


def test_sentence_bounded_chunker_uses_emergency_whitespace_boundary() -> None:
    chunker = SentenceChunker(
        min_characters=8,
        target_characters=20,
        max_characters=32,
        mode="sentence_bounded",
        emergency_max_characters=40,
    )

    chunks = chunker.push_chunks(
        "Đây là một dòng không có dấu câu và tiếp tục vượt giới hạn an toàn"
    )

    assert [(chunk.text, chunk.reason) for chunk in chunks] == [
        ("Đây là một dòng không có dấu câu và", "emergency")
    ]
    assert chunker.flush() == "tiếp tục vượt giới hạn an toàn"


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


def test_sentence_chunker_releases_a_small_lead_chunk_then_uses_steady_batches() -> None:
    chunker = SentenceChunker(
        min_characters=8,
        target_characters=48,
        max_characters=72,
        punctuation_min_characters=48,
        initial_target_characters=16,
        initial_max_characters=24,
        initial_punctuation_min_characters=8,
    )

    assert chunker.push("Tôi nghe rồi. ") == ["Tôi nghe rồi."]
    assert chunker.push("Đây là câu ngắn chưa đủ target. ") == []
    assert chunker.push("Thêm một ý nữa nhé.") == [
        "Đây là câu ngắn chưa đủ target. Thêm một ý nữa nhé."
    ]
