from dataclasses import dataclass
import unicodedata

from config.settings import ConversationConfig


@dataclass(frozen=True)
class ConversationRoute:
    kind: str
    source: str
    original_text: str
    normalized_text: str


def normalize_command_text(text: str) -> str:
    normalized = unicodedata.normalize("NFC", str(text or "")).casefold()
    normalized = " ".join(normalized.split())
    start = 0
    end = len(normalized)
    while start < end and _is_boundary_char(normalized[start]):
        start += 1
    while end > start and _is_boundary_char(normalized[end - 1]):
        end -= 1
    return normalized[start:end].strip()


def _is_boundary_char(char: str) -> bool:
    category = unicodedata.category(char)
    return category.startswith("P") or category.startswith("Z")


def classify_conversation_text(
    text: str,
    config: ConversationConfig,
    *,
    source: str,
    allow_wake: bool = True,
    allow_exit: bool = True,
) -> ConversationRoute:
    original = str(text or "")
    normalized = normalize_command_text(original)
    if not normalized:
        kind = "empty"
    elif not config.enabled:
        kind = "chat"
    else:
        wake_words = {normalize_command_text(item) for item in config.wake_words}
        exit_commands = {normalize_command_text(item) for item in config.exit_commands}
        if allow_wake and normalized in wake_words:
            kind = "wake"
        elif allow_exit and normalized in exit_commands:
            kind = "exit"
        else:
            kind = "chat"
    return ConversationRoute(
        kind=kind,
        source=source,
        original_text=original,
        normalized_text=normalized,
    )
