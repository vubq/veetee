class MemoryPolicy:
    """Deterministic schema guard for AI-proposed memory actions.

    This class intentionally does not inspect user text. Semantic decisions
    are produced by the LLM contract; the server only validates the structured
    action before applying it.
    """

    @staticmethod
    def valid_action(action: str) -> bool:
        return str(action or "").strip().lower() in {"upsert", "forget", "forget_all"}
