"""Context budget estimation and hierarchy-preserving history compaction."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from veetee_server.dialogue.history import DialogueHistory, DialogueTurn

TokenEstimator = Callable[[str], int]


def default_token_estimator(text: str) -> int:
    """Estimates tokens using UTF-8 character length / 4 with minimum 1 token."""
    if not text:
        return 0
    return max(1, len(text) // 4)


@dataclass(frozen=True, slots=True)
class ContextBudget:
    """Configurable token budget limits."""

    max_total_tokens: int = 4096
    system_reserve_tokens: int = 512
    memory_reserve_tokens: int = 512
    tools_reserve_tokens: int = 512
    history_limit_tokens: int = 2048


def compact_dialogue_history(
    history: DialogueHistory,
    max_tokens: int,
    estimator: TokenEstimator = default_token_estimator,
) -> list[DialogueTurn]:
    """Compacts dialogue history to fit within max_tokens.

    Guarantees:
    1. Tool call / tool response hierarchy is never broken: an assistant turn with
       tool_calls and its corresponding tool response turns are kept or dropped together.
    2. Keeps recent turns over older turns.
    """
    turns = history.get_turns()
    if not turns:
        return []

    # Group turns into atomic blocks (user-assistant pairs or tool call-response groups)
    blocks: list[list[DialogueTurn]] = []
    current_block: list[DialogueTurn] = []

    for turn in turns:
        if turn.role == "user":
            if current_block:
                blocks.append(current_block)
                current_block = []
            current_block.append(turn)
        elif turn.role == "assistant":
            current_block.append(turn)
        elif turn.role == "tool":
            current_block.append(turn)
        else:
            if current_block:
                blocks.append(current_block)
                current_block = []
            blocks.append([turn])

    if current_block:
        blocks.append(current_block)

    # Calculate token count per block from newest to oldest
    selected_turns: list[DialogueTurn] = []
    used_tokens = 0

    for block in reversed(blocks):
        block_text = "".join(t.content for t in block)
        block_tokens = estimator(block_text)
        if used_tokens + block_tokens <= max_tokens:
            selected_turns.extend(reversed(block))
            used_tokens += block_tokens
        else:
            # If block doesn't fit, stop adding older turns
            break

    # Restore chronological order
    selected_turns.reverse()
    return selected_turns
