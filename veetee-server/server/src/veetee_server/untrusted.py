"""Pure sanitizers for untrusted user, memory, knowledge, and tool data."""

from __future__ import annotations


def sanitize_untrusted_text(text: str) -> str:
    """Escapes prompt delimiters and known system-instruction markers."""
    if not text:
        return ""
    sanitized = text.replace("<untrusted_memory>", "&lt;untrusted_memory&gt;")
    sanitized = sanitized.replace("</untrusted_memory>", "&lt;/untrusted_memory&gt;")
    sanitized = sanitized.replace("<untrusted_knowledge>", "&lt;untrusted_knowledge&gt;")
    sanitized = sanitized.replace("</untrusted_knowledge>", "&lt;/untrusted_knowledge&gt;")
    sanitized = sanitized.replace("<untrusted_tool_output>", "&lt;untrusted_tool_output&gt;")
    sanitized = sanitized.replace("</untrusted_tool_output>", "&lt;/untrusted_tool_output&gt;")
    sanitized = sanitized.replace("<untrusted_provider>", "&lt;untrusted_provider&gt;")
    sanitized = sanitized.replace("</untrusted_provider>", "&lt;/untrusted_provider&gt;")
    sanitized = sanitized.replace("[SYSTEM INSTRUCTION]", "[DATA_TEXT]")
    sanitized = sanitized.replace("System:", "Data:")
    return sanitized
