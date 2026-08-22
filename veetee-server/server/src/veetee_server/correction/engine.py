"""Deterministic ASR text correction engine and runtime hook."""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from veetee_server.persistence.correction import StoredCorrectionRule


class CorrectionEngine:
    """Applies exact and phrase replacement rules in strict ordinal sequence.

    Rules never interpret user input as regex: patterns are escaped literally,
    so ``exact``/``phrase`` stay deterministic text replacement per policy.
    """

    def __init__(self, max_input_chars: int = 4096) -> None:
        self.max_input_chars = max_input_chars

    def apply_rules(
        self,
        text: str,
        rules: Sequence[StoredCorrectionRule],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Applies enabled rules in ordinal order.

        Returns (corrected_text, applied_rules_summary).
        """
        if not text:
            return "", []

        current_text = text[: self.max_input_chars]
        applied: list[dict[str, Any]] = []

        # Sort rules by ordinal ASC
        sorted_rules = sorted(
            [r for r in rules if r.enabled], key=lambda r: (r.ordinal, str(r.id))
        )

        for rule in sorted_rules:
            pattern = rule.pattern
            replacement = rule.replacement
            if not pattern:
                continue

            before = current_text
            if rule.rule_type == "exact":
                if (rule.case_sensitive and current_text == pattern) or (
                    not rule.case_sensitive and current_text.casefold() == pattern.casefold()
                ):
                    current_text = replacement
            elif rule.rule_type == "phrase":
                # Phrase substring replacement
                flags = 0 if rule.case_sensitive else re.IGNORECASE
                phrase_pattern = re.escape(pattern)
                current_text = re.sub(
                    phrase_pattern,
                    lambda m, _replacement=replacement: _replacement,
                    current_text,
                    flags=flags,
                )

            if current_text != before:
                applied.append(
                    {
                        "rule_id": str(rule.id),
                        "ordinal": rule.ordinal,
                        "rule_type": rule.rule_type,
                        "pattern": pattern,
                        "replacement": replacement,
                        "before": before,
                        "after": current_text,
                    }
                )

        return current_text, applied


def apply_asr_correction_hook(
    asr_normalized_text: str,
    rules: Sequence[StoredCorrectionRule],
    engine: CorrectionEngine | None = None,
) -> tuple[str, dict[str, Any]]:
    """Runtime boundary hook called between ASR normalized text and LLM model prompt."""
    eng = engine or CorrectionEngine()
    corrected_text, applied = eng.apply_rules(asr_normalized_text, rules)
    provenance = {
        "original_asr_text": asr_normalized_text,
        "corrected_text": corrected_text,
        "corrections_applied": len(applied),
        "applied_rules": applied,
    }
    return corrected_text, provenance
