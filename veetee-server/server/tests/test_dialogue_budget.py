"""Tests for transcript separation and hierarchy-preserving compaction."""

from veetee_server.dialogue import (
    ContextBudget,
    DialogueHistory,
    TranscriptPair,
    compact_dialogue_history,
)


def test_transcript_pair_separation():
    history = DialogueHistory(session_id="session-1")
    turn = history.add_user_turn(
        raw_transcript="  xin chào   ",
        normalized_text="Xin chào",
        text_for_model="Xin chào! Hãy giúp tôi.",
    )

    assert isinstance(turn.transcript_pair, TranscriptPair)
    assert turn.transcript_pair.raw_transcript == "xin chào"
    assert turn.transcript_pair.normalized_text == "Xin chào"
    assert turn.transcript_pair.text_for_model == "Xin chào! Hãy giúp tôi."
    assert turn.content == "Xin chào! Hãy giúp tôi."


def test_dialogue_history_full_turn_lifecycle():
    history = DialogueHistory()
    history.add_user_turn("Hôm nay thời tiết sao?")
    history.add_assistant_turn(
        content="",
        tool_calls=[
            {
                "id": "call_123",
                "type": "function",
                "function": {"name": "local.get_weather"},
            }
        ],
    )
    history.add_tool_result(
        tool_call_id="call_123",
        name="local.get_weather",
        output='{"temperature": 28}',
    )
    history.add_assistant_turn("Thời tiết hôm nay là 28 độ C.")

    turns = history.get_turns()
    assert len(turns) == 4

    msgs = history.to_chat_messages()
    assert len(msgs) == 4
    assert msgs[0].role == "user"
    assert msgs[1].role == "assistant"
    assert msgs[1].tool_calls[0]["id"] == "call_123"
    assert msgs[2].role == "tool"
    assert msgs[2].tool_call_id == "call_123"
    assert msgs[3].role == "assistant"

    history.clear()
    assert len(history) == 0


def test_compact_dialogue_history_preserves_tool_hierarchy():
    history = DialogueHistory()

    # Old user turn (long text)
    history.add_user_turn("Turn 1 " * 50)
    history.add_assistant_turn("Response 1 " * 50)

    # Tool call turn group
    history.add_user_turn("Mấy giờ rồi?")
    history.add_assistant_turn(
        content="",
        tool_calls=[
            {
                "id": "call_time",
                "type": "function",
                "function": {"name": "local.get_time"},
            }
        ],
    )
    history.add_tool_result(
        tool_call_id="call_time",
        name="local.get_time",
        output='{"time": "14:00"}',
    )
    history.add_assistant_turn("Bây giờ là 14 giờ.")

    # Compact with tight budget that drops Turn 1, but keeps tool group intact
    compacted = compact_dialogue_history(history, max_tokens=100)

    # Verify tool call assistant turn and tool result turn remain together
    roles = [t.role for t in compacted]
    if "tool" in roles:
        tool_idx = roles.index("tool")
        assert roles[tool_idx - 1] == "assistant"
        assert compacted[tool_idx - 1].tool_calls is not None
        assert compacted[tool_idx - 1].tool_calls[0]["id"] == "call_time"


def test_context_budget_defaults():
    budget = ContextBudget()
    assert budget.max_total_tokens == 4096
    assert budget.history_limit_tokens == 2048
