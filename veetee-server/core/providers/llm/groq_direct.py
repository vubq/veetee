"""Groq direct provider with quota-aware multi-key routing.

Standalone module: it does NOT import the OmniRoute provider. The wire
contract (control markers, emotion tags, typed turn events, persona
persistence semantics) is intentionally identical so TurnRunner, session
lifecycle, receipts and existing behavior tests keep passing; only the
transport changes from one local gateway key to routed Groq API keys.

Every inference — chat, correction, control, recovery, idle farewell —
goes through the shared router: estimate whole-request tokens, reserve
quota atomically, pick an eligible group, bound failover, settle with
actual usage. No request is ever sent to a group known to be exhausted.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Tuple

import aiohttp

from core.ai_contract import (
    ASR_CORRECTION_PROMPT,
    CONFIRMATION_TOOL_NAME,
    INLINE_CONVERSATION_CONTROL_PROMPT,
    MEMORY_TOOL_NAME,
    SEMANTIC_SYSTEM_PROMPT,
)
from core.intent import Intent
from core.providers.llm.base import BaseLLM
from core.providers.llm.router import GroqRouter, Lease, QuotaExhausted, RouteTarget
from core.providers.llm.quota import QuotaLedger
from core.providers.llm.stream_parser import SSEDecoder, NativeToolCallAccumulator
from core.providers.llm.token_budget import estimate_request_tokens
from core.tools.base import READ_ONLY_TOOL_DESCRIPTION_MARKER
from core.turn_events import (
    CompletedEvent,
    ConfirmationDecisionEvent,
    ControlEvent,
    FailedEvent,
    MemoryProposalEvent,
    SpeechSegmentEvent,
    ToolCallReadyEvent,
)
from core.turn_metrics import mark_current

logger = logging.getLogger("GroqDirectLLM")

GROQ_API_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_ENV_PREFIX = "GROQ_API_KEY_"


class SpeechSegmentSplitter:
    """Split streamed text into speakable clauses (same policy as before)."""

    def __init__(self):
        self.buffer = ""
        self._segments_emitted = 0
        self.min_segment_chars = 28
        self.clause_target_chars = 150
        self.clause_min_chars = 80
        self.first_clause_min_chars = 36
        self.first_clause_min_words = 5
        self.hard_max_segment_chars = 240
        self.first_segment_min_chars = 8
        self.first_segment_min_words = 2

    def _is_sentence_boundary(self, index: int) -> bool:
        if index < 0 or index >= len(self.buffer):
            return False
        char = self.buffer[index]
        if char in ".!?":
            return True
        return False

    def _find_sentence_cut(self) -> int:
        for index in range(len(self.buffer) - 1, -1, -1):
            if self._is_sentence_boundary(index):
                return index + 1
        return -1

    def _find_clause_cut(self) -> int:
        for index in range(len(self.buffer) - 1, -1, -1):
            if self.buffer[index] in ",;:":
                return index + 1
        return -1

    def _is_safe_word_boundary(self, idx: int) -> bool:
        if idx <= 0 or idx >= len(self.buffer):
            return False
        left = self.buffer[idx - 1]
        right = self.buffer[idx]
        if left.isspace() or right.isspace():
            return True
        if not (left.isalnum() or left == "_") or not (right.isalnum() or right == "_"):
            return True
        return False

    def _find_hard_cut(self) -> int:
        limit = min(len(self.buffer), self.hard_max_segment_chars)
        for index in range(limit - 1, 0, -1):
            if self._is_safe_word_boundary(index):
                return index
        return limit

    def add_token(self, token: str) -> List[str]:
        self.buffer += token
        segments: List[str] = []
        while True:
            cut = self._find_sentence_cut()
            if cut > 0:
                segments.append(self.buffer[:cut])
                self.buffer = self.buffer[cut:]
                self._segments_emitted += 1
                continue
            visible_chars = len(self.buffer.strip())
            if self._segments_emitted > 0 and visible_chars >= self.min_segment_chars:
                cut = self._find_clause_cut()
                if cut > 0:
                    segments.append(self.buffer[:cut])
                    self.buffer = self.buffer[cut:]
                    self._segments_emitted += 1
                    continue
            if self._segments_emitted == 0 and visible_chars >= self.first_segment_min_chars:
                match = re.search(r"([^\W\d_]+)$", self.buffer, flags=re.UNICODE)
                words = re.findall(r"[^\W_]+", self.buffer, flags=re.UNICODE)
                if match is None or len(words) >= self.first_segment_min_words:
                    cut = self._find_clause_cut()
                    if cut > 0:
                        segments.append(self.buffer[:cut])
                        self.buffer = self.buffer[cut:]
                        self._segments_emitted += 1
                        continue
            if len(self.buffer) >= self.hard_max_segment_chars:
                cut = self._find_hard_cut()
                segments.append(self.buffer[:cut])
                self.buffer = self.buffer[cut:]
                self._segments_emitted += 1
                continue
            break
        return [segment for segment in segments if segment.strip()]

    def flush(self) -> List[str]:
        if self.buffer.strip():
            remainder = self.buffer
            self.buffer = ""
            self._segments_emitted += 1
            return [remainder]
        return []


def build_engine_from_config(llm_config, *, server_dir: str):
    """Build a GroqDirectLLM from LLMConfig. Raises on unusable pool."""
    import os as _os

    from core.providers.llm.quota import QuotaLedger

    pool_entries = [
        {"id": item.id, "api_key_env": item.api_key_env,
         "quota_group": item.quota_group, "enabled": item.enabled}
        for item in (llm_config.key_pool or [])
    ]
    targets = build_targets_from_env(pool_entries or None)
    if not targets:
        raise ValueError(
            "Groq key pool is empty: set llm.key_pool or export GROQ_API_KEY_<alias>")
    groups = {
        name: {str(dim): float(value) for dim, value in caps.items()}
        for name, caps in (llm_config.quota_groups or {}).items()
    }
    routing = llm_config.routing
    ledger = QuotaLedger(
        groups,
        discovery_max_inflight=int(getattr(
            routing, "discovery_max_inflight", 1)),
    )
    router = GroqRouter(
        targets,
        ledger,
        headroom_pct=float(getattr(routing, "headroom_pct", 10.0)),
        admission_wait_ms=float(getattr(routing, "admission_wait_ms", 50.0)),
        inflight_penalty_s=float(getattr(routing, "inflight_penalty_s", 0.4)),
    )
    allowed = [llm_config.model] + [
        m for m in (getattr(llm_config, "extra_models", []) or [])
        if m and m != llm_config.model
    ]
    engine = GroqDirectLLM(
        router,
        model=llm_config.model,
        temperature=float(llm_config.temperature),
        max_tokens=int(llm_config.max_tokens),
        base_prompt=llm_config.base_prompt,
        prompt_template_path=_os.path.join(
            server_dir, llm_config.prompt_template),
        base_prompt_state_path=_os.path.join(server_dir, "data", "base-prompt.txt"),
        max_attempts=int(getattr(routing, "max_attempts", 2)),
        reasoning_effort=str(getattr(llm_config, "reasoning_effort", "none")),
        allowed_models=allowed,
        model_state_path=_os.path.join(server_dir, "data", "llm-model.txt"),
    )
    engine.set_model_effort_overrides(
        getattr(llm_config, "model_reasoning_effort", {}) or {})
    logger.info(
        "Groq direct ready: model=%s groups=%s",
        llm_config.model,
        sorted({t["quota_group"] for t in router.aliases()}),
    )
    return engine


def build_targets_from_env(
    explicit_pool: Optional[List[Dict[str, Any]]] = None,
    *,
    base_url: str = GROQ_API_BASE_URL,
) -> List[RouteTarget]:
    """Build route targets from config pool, or scan GROQ_API_KEY_* env.

    Each target keeps its own quota group by default (independent quotas);
    config may map several aliases onto one shared group. Missing/empty
    secrets disable that target with a warning, never crash boot.
    """
    targets: List[RouteTarget] = []
    pool = list(explicit_pool or [])
    if not pool:
        for name, value in sorted(os.environ.items()):
            if name.startswith(GROQ_ENV_PREFIX) and value and value.strip():
                alias = name[len(GROQ_ENV_PREFIX):] or name
                pool.append({"id": alias, "api_key_env": name})
    for entry in pool:
        alias = str(entry.get("id") or entry.get("api_key_env") or "").strip()
        env_name = str(entry.get("api_key_env") or "").strip()
        secret = os.environ.get(env_name, "") if env_name else ""
        if not alias:
            logger.warning("Skipping Groq key pool entry without id")
            continue
        if not secret or not secret.strip():
            logger.warning("Groq key %s disabled: env %s missing/empty", alias, env_name)
            continue
        targets.append(RouteTarget(
            alias=alias,
            api_key=secret.strip(),
            quota_group=str(entry.get("quota_group") or f"g{alias}"),
            base_url=base_url,
            enabled=bool(entry.get("enabled", True)),
        ))
    return targets


class GroqDirectLLM(BaseLLM):
    # Shared contract text lives in core.ai_contract; kept as class
    # attributes for getattr/diagnostics compatibility.
    ASR_CORRECTION_PROMPT = ASR_CORRECTION_PROMPT

    INLINE_CONVERSATION_CONTROL_PROMPT = INLINE_CONVERSATION_CONTROL_PROMPT

    def __init__(
        self,
        router: GroqRouter,
        *,
        model: str = "qwen/qwen3.6-27b",
        temperature: float = 0.7,
        max_tokens: int = 256,
        base_prompt: str = "",
        prompt_template_path: Optional[str] = None,
        base_prompt_state_path: Optional[str] = None,
        max_attempts: int = 2,
        session_factory: Optional[Callable[[], Any]] = None,
        reasoning_effort: str = "none",
        allowed_models: Optional[List[str]] = None,
        model_state_path: Optional[str] = None,
    ):
        self._router = router
        self.model = model
        self._allowed_models = [m for m in (allowed_models or [model]) if m]
        self._model_state_path = model_state_path
        saved_model = self._load_saved_model()
        if saved_model and saved_model in self._allowed_models:
            self.model = saved_model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.prompt_template_path = prompt_template_path
        self.base_prompt_state_path = base_prompt_state_path
        self.prompt_template = self._load_prompt_template()
        self.base_prompt = self._load_saved_base_prompt() or base_prompt.strip()
        self.system_prompt = self._render_system_prompt(self.base_prompt)
        self.persona_version = 0
        self._max_attempts = max(1, int(max_attempts))
        self._reasoning_effort = reasoning_effort
        self._model_effort_overrides: Dict[str, str] = {}
        self._session_factory = session_factory
        self._http_session: Optional[aiohttp.ClientSession] = None

    def list_models(self) -> List[str]:
        """Switchable models: default first, then extras."""
        return list(self._allowed_models)

    def _load_saved_model(self) -> str:
        if not self._model_state_path:
            return ""
        try:
            with open(self._model_state_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except FileNotFoundError:
            return ""
        except OSError as e:
            logger.warning(f"Could not read saved model: {e}")
            return ""

    def set_model(self, model: str, persist: bool = True) -> str:
        """Switch model for subsequent turns; persists across restarts."""
        cleaned = (model or "").strip()
        if not cleaned:
            raise ValueError("model must not be empty")
        if cleaned not in self._allowed_models:
            raise ValueError(f"model not allowed: {cleaned!r}")
        self.model = cleaned
        if persist and self._model_state_path:
            state_dir = os.path.dirname(self._model_state_path)
            os.makedirs(state_dir, exist_ok=True)
            temp_path = f"{self._model_state_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(cleaned)
            os.replace(temp_path, self._model_state_path)
        return self.model

    # -- persona persistence (same semantics as before) --------------------

    def _load_prompt_template(self) -> str:
        if not self.prompt_template_path:
            return "{{base_prompt}}"
        try:
            with open(self.prompt_template_path, "r", encoding="utf-8") as f:
                template = f.read()
        except OSError as e:
            logger.warning(f"Could not read prompt template {self.prompt_template_path}: {e}")
            return "{{base_prompt}}"
        if "{{base_prompt}}" not in template:
            logger.warning("Prompt template is missing {{base_prompt}}; using base prompt directly")
            return "{{base_prompt}}"
        return template

    def _load_saved_base_prompt(self) -> str:
        if not self.base_prompt_state_path:
            return ""
        try:
            with open(self.base_prompt_state_path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except FileNotFoundError:
            return ""
        except OSError as e:
            logger.warning(f"Could not read saved base prompt: {e}")
            return ""

    def _render_system_prompt(self, base_prompt: str) -> str:
        return self.prompt_template.replace("{{base_prompt}}", base_prompt.strip())

    def get_base_prompt(self) -> str:
        return self.base_prompt

    def set_base_prompt(
        self,
        base_prompt: str,
        persist: bool = True,
        *,
        max_bytes: int = 32 * 1024,
        max_tokens_estimate: int = 8000,
        chars_per_token: int = 4,
    ) -> None:
        cleaned = (base_prompt or "").strip()
        if not cleaned:
            raise ValueError("base_prompt must not be empty")
        raw_bytes = len(cleaned.encode("utf-8", "ignore"))
        if raw_bytes > max_bytes:
            raise ValueError(f"base_prompt exceeds byte budget ({raw_bytes} > {max_bytes})")
        estimated = int(len(cleaned.encode("utf-8", "ignore")) / max(1, chars_per_token) * 1.25) + 1
        if estimated > max_tokens_estimate:
            raise ValueError(
                f"base_prompt exceeds token budget (est. {estimated} > {max_tokens_estimate})"
            )
        self.base_prompt = cleaned
        self.system_prompt = self._render_system_prompt(cleaned)
        self.persona_version += 1

        if persist and self.base_prompt_state_path:
            state_dir = os.path.dirname(self.base_prompt_state_path)
            os.makedirs(state_dir, exist_ok=True)
            temp_path = f"{self.base_prompt_state_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(cleaned)
            os.replace(temp_path, self.base_prompt_state_path)

    # -- text hygiene (contract markers never reach TTS) -------------------

    @staticmethod
    def _clean_text(text: str) -> str:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)
        text = text.replace("**", "").replace("*", "").replace("#", "").replace("`", "")
        text = re.sub(r"\[[^\[\]\n]{1,32}\]", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip(" ,")

    @staticmethod
    def _clean_control_sentence(text: str, max_chars: int = 180) -> str:
        cleaned = str(text or "").strip().strip('"“”').strip()
        cleaned = re.sub(r"\[[^\[\]\n]{1,32}\]", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,")
        if not cleaned or len(cleaned) > max_chars or "\n" in cleaned:
            return ""
        return cleaned

    def _extract_emotion(self, text: str) -> Tuple[str, str]:
        match = re.match(r"^\[([a-zA-Z]+)\]\s*(.*)$", text.strip(), re.DOTALL)
        if match:
            emotion = match.group(1).lower()
            remaining = match.group(2)
            valid_emotions = {"happy", "neutral", "sad", "surprised", "thinking", "angry", "relaxed"}
            if emotion in valid_emotions:
                return emotion, remaining
            logger.warning(f"Ignoring invalid emotion tag: [{emotion}]")
            return "neutral", remaining
        return "neutral", text

    @staticmethod
    def _parse_inline_control_prefix(buffer: str) -> Tuple[bool, bool, str]:
        """Parse the hidden [end]/[continue] prefix without leaking it to TTS."""
        stripped = buffer.lstrip()
        match = re.match(r"^\[(end|continue)\]\s*", stripped, flags=re.IGNORECASE)
        if match:
            return True, match.group(1).lower() == "end", stripped[match.end():]
        if "]" not in stripped and len(stripped) < 24:
            return False, False, ""
        return True, False, stripped

    def set_model_effort_overrides(self, overrides: Dict[str, str]) -> None:
        self._model_effort_overrides = dict(overrides or {})

    def _reasoning_params(self) -> Dict[str, str]:
        """Reasoning params valid for the active model.

        Qwen-style models accept effort "none" (suppresses <think>);
        gpt-oss only accepts low/medium/high, so "none" means omit.
        """
        effort = self._model_effort_overrides.get(self.model, self._reasoning_effort)
        if effort == "none":
            return {"reasoning_format": "hidden", "reasoning_effort": "none"}
        return {"reasoning_effort": effort}

    # -- transport ----------------------------------------------------------

    async def _get_http_session(self):
        if self._session_factory is not None:
            return self._session_factory()
        if self._http_session is None or self._http_session.closed:
            connector = aiohttp.TCPConnector(limit=32, keepalive_timeout=30)
            self._http_session = aiohttp.ClientSession(connector=connector)
        return self._http_session

    async def warmup(self):
        """Single lightweight models call on the first enabled key."""
        for target in self._router.aliases():
            if not target["enabled"]:
                continue
            match = next(
                (t for t in self._router._targets
                 if t.alias == target["alias"] and t.enabled and t.api_key),
                None,
            )
            if match is None:
                continue
            try:
                session = await self._get_http_session()
                async with session.get(
                    f"{match.base_url}/models",
                    headers={"Authorization": f"Bearer {match.api_key}"},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    await resp.read()
                    logger.info(f"Groq direct warmup completed (HTTP {resp.status})")
            except Exception as e:
                logger.warning(f"Groq direct warmup skipped: {e}")
            return
        logger.warning("Groq direct warmup skipped: no enabled key")

    async def close(self):
        if self._http_session is not None and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None

    def _parse_retry_after(self, resp) -> float:
        try:
            value = resp.headers.get("retry-after")
            return max(0.0, float(str(value).strip().rstrip("s")))
        except (TypeError, ValueError, AttributeError):
            return 1.0

    def _learn_from_headers(self, lease: Lease, resp) -> None:
        async def _apply():
            limits: Dict[str, float] = {}
            remaining: Dict[str, float] = {}
            headers = getattr(resp, "headers", {}) or {}
            lowered = {str(k).lower(): v for k, v in dict(headers).items()}
            pairs = (
                ("x-ratelimit-limit-requests", "rpd", limits),
                ("x-ratelimit-limit-tokens", "tpm", limits),
                ("x-ratelimit-remaining-requests", "rpd", remaining),
                ("x-ratelimit-remaining-tokens", "tpm", remaining),
            )
            for header, dim, dest in pairs:
                raw = lowered.get(header)
                try:
                    number = float(str(raw))
                except (TypeError, ValueError):
                    continue
                if number >= 0:
                    dest[dim] = number
            if limits:
                await self._router._ledger.note_limits(lease.quota_group, limits)
            if remaining:
                await self._router._ledger.note_remaining(lease.quota_group, remaining)
        return _apply()  # type: ignore[return-value]

    def _usage_tokens(self, usage: Optional[Dict[str, Any]]) -> Tuple[int, int]:
        if not isinstance(usage, dict):
            return 0, 0
        try:
            prompt = int(usage.get("prompt_tokens") or 0)
        except (TypeError, ValueError):
            prompt = 0
        try:
            completion = int(usage.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            completion = 0
        return max(0, prompt), max(0, completion)

    async def _dispatch(
        self,
        payload: Dict[str, Any],
        *,
        timeout_s: float,
        purpose: str,
        output_budget: int,
        stream: bool,
    ):
        """Yield (lease, response) for one attempt; bounded failover inside.

        Caller consumes the response fully, then calls the matching settle
        helper below exactly once per attempt used.
        """
        from core.providers.llm.token_budget import estimate_request_tokens

        estimated = estimate_request_tokens(
            payload.get("messages") or [],
            tools=payload.get("tools"),
            output_budget=output_budget,
        )
        exclude: set[str] = set()
        attempts = 0
        session = await self._get_http_session()
        while attempts < self._router_max_attempts():
            try:
                lease = await self._router.acquire(
                    estimated, purpose=purpose, exclude_groups=frozenset(exclude))
            except QuotaExhausted as exc:
                raise _CapacityBusy(str(exc)) from exc
            attempts += 1
            started = time.monotonic()
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {lease.api_key}",
            }
            try:
                resp = await session.post(
                    f"{lease.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=timeout_s),
                )
            except asyncio.CancelledError:
                await self._router.settle_uncertain(lease)
                raise
            except Exception as exc:
                await self._router.settle_uncertain(lease)
                if attempts >= self._router_max_attempts():
                    raise _UpstreamError(str(exc)) from exc
                continue
            if resp.status == 429:
                retry_after = self._parse_retry_after(resp)
                try:
                    await resp.read()
                except Exception:
                    pass
                await self._router.cooldown(lease.quota_group, retry_after or 1.0, reason="429")
                await self._router.settle_rejected(lease)
                exclude.add(lease.quota_group)
                try:
                    resp.release()
                except Exception:
                    pass
                continue
            if resp.status == 401:
                self._router.disable_alias(lease.alias, reason="401")
                await self._router.settle_rejected(lease)
                exclude.add(lease.quota_group)
                try:
                    resp.release()
                except Exception:
                    pass
                continue
            if resp.status == 400:
                await self._router.settle_rejected(lease)
                try:
                    resp.release()
                except Exception:
                    pass
                raise _UpstreamError("Groq HTTP 400 (payload/model)")
            # 200 or other 5xx: hand to caller; caller settles exactly once.
            # The finally releases the connection when the caller is done,
            # including on cancellation inside the consumer.
            try:
                yield lease, resp, started
            finally:
                try:
                    resp.release()
                except Exception:
                    pass
            return
        raise _CapacityBusy("quota attempts exhausted")

    def _router_max_attempts(self) -> int:
        return max(1, int(getattr(self, "_max_attempts", 2)))

    async def _settle_stream_usage(
        self, lease: Lease, usage: Optional[Dict[str, Any]], started: float,
        *, first_event_at: Optional[float] = None,
    ) -> None:
        prompt, completion = self._usage_tokens(usage)
        await self._router.settle_ok(lease, actual_tokens=prompt + completion)
        if first_event_at is not None:
            self._router.note_latency(
                lease.quota_group, max(0.0, first_event_at - started))

    # -- non-streaming calls -------------------------------------------------

    async def _single_completion(
        self,
        payload: Dict[str, Any],
        *,
        timeout_s: float,
        purpose: str,
    ) -> str:
        async for lease, resp, started in self._dispatch(
            payload, timeout_s=timeout_s, purpose=purpose,
            output_budget=int(payload.get("max_tokens") or 0), stream=False,
        ):
            try:
                if resp.status != 200:
                    await self._router.settle_uncertain(lease)
                    return ""
                data = await resp.json(content_type=None)
            except asyncio.CancelledError:
                await self._router.settle_uncertain(lease)
                raise
            except Exception:
                await self._router.settle_uncertain(lease)
                return ""
            await self._learn_from_headers(lease, resp)
            prompt, completion = self._usage_tokens(data.get("usage"))
            await self._router.settle_ok(lease, actual_tokens=prompt + completion)
            content = str(
                data.get("choices", [{}])[0].get("message", {}).get("content", "") or "")
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
            content = re.sub(r"<think>.*", "", content, flags=re.DOTALL)
            return content.strip()
        return ""

    async def correct_transcript(self, transcript: str) -> str:
        original = (transcript or "").strip()
        if not original:
            return original
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self.ASR_CORRECTION_PROMPT},
                {"role": "user", "content": original},
            ],
            "temperature": 0.0,
            "max_tokens": 96,
            "stream": False,
            **self._reasoning_params(),
        }
        try:
            corrected = await self._single_completion(
                payload, timeout_s=2.0, purpose="correction")
        except (_CapacityBusy, _UpstreamError):
            return original
        corrected = corrected.strip('"“”').strip()
        if not corrected or "\n" in corrected:
            return original
        min_len = max(1, int(len(original) * 0.55))
        max_len = max(len(original) + 24, int(len(original) * 1.45))
        if not (min_len <= len(corrected) <= max_len):
            logger.warning("Rejected oversized ASR correction: %r -> %r", original, corrected)
            return original
        return corrected

    async def _control_completion(
        self,
        instruction: str,
        user_content: str,
        *,
        temperature: float = 0.2,
        max_tokens: int = 160,
        timeout_seconds: float = 2.0,
        include_persona: bool = True,
    ) -> str:
        messages = []
        if include_persona:
            messages.append({"role": "system", "content": self.system_prompt})
        messages.extend([
            {"role": "system", "content": instruction},
            {"role": "user", "content": user_content},
        ])
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
            **self._reasoning_params(),
        }
        try:
            return await self._single_completion(
                payload, timeout_s=timeout_seconds, purpose="control")
        except (_CapacityBusy, _UpstreamError):
            return ""

    async def generate_recovery_message(self) -> str:
        instruction = (
            "Tạo đúng một câu cực ngắn để trợ lý giọng nói dùng khi một lượt xử lý bị lỗi. "
            "Câu phải tự nhiên, theo personality hiện tại, không nêu lỗi kỹ thuật, không thêm nhãn. "
            "Mặc định dùng tiếng Việt. Chỉ 6-9 từ và tối đa 55 ký tự."
        )
        raw = await self._control_completion(
            instruction,
            "Hãy tạo câu recovery dùng chung, không phụ thuộc một câu hỏi cụ thể.",
            temperature=max(0.35, self.temperature),
            max_tokens=24,
        )
        return self._clean_control_sentence(raw, max_chars=64)

    # -- streaming turns -----------------------------------------------------

    async def stream_turn(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict]] = None,
        detect_end_intent: bool = True,
        tool_choice: Optional[Any] = None,
    ):
        """Stream one typed LLM turn, including native function calls."""
        request_tools = list(tools or [])
        full_messages = [{"role": "system", "content": self.system_prompt}]
        full_messages.append({"role": "system", "content": SEMANTIC_SYSTEM_PROMPT})
        if detect_end_intent:
            full_messages.append({
                "role": "system",
                "content": self.INLINE_CONVERSATION_CONTROL_PROMPT,
            })
        full_messages.extend(messages)

        payload = {
            "model": self.model,
            "messages": full_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
            **self._reasoning_params(),
        }
        if request_tools:
            payload["tools"] = request_tools
            payload["tool_choice"] = tool_choice or "auto"
        elif tool_choice is not None:
            payload["tool_choice"] = tool_choice

        splitter = SpeechSegmentSplitter()
        decoder = SSEDecoder()
        tool_calls = NativeToolCallAccumulator()
        control_decided = not detect_end_intent
        control_emitted = False
        control_buffer = ""
        inline_end_intent = False
        emotion_emitted = False
        finish_reason = None
        saw_done = False
        usage = None
        saw_content = False
        first_token_marked = False
        first_event_at: Optional[float] = None
        read_only_tool_names = {
            str(function.get("name") or "")
            for tool in request_tools
            if isinstance(tool, dict)
            for function in [tool.get("function") or {}]
            if isinstance(function, dict)
            and READ_ONLY_TOOL_DESCRIPTION_MARKER in str(function.get("description") or "")
        }

        def ensure_control(emotion: str = "neutral", *, tool: bool = False):
            nonlocal control_emitted
            if control_emitted:
                return None
            control_emitted = True
            intent = Intent.TOOL_REQUEST.value if tool else (
                Intent.END_CONVERSATION.value if inline_end_intent else Intent.CHAT.value
            )
            return ControlEvent(
                intent=intent,
                lifecycle="end" if inline_end_intent else "continue",
                emotion=emotion,
            )

        def speech_events(token: str):
            nonlocal emotion_emitted
            events = []
            for clause in splitter.add_token(token):
                emotion = None
                if not emotion_emitted:
                    detected_emotion, cleaned = self._extract_emotion(clause)
                    emotion_emitted = True
                    clause = cleaned
                    emotion = detected_emotion
                    control = ensure_control(emotion)
                    if control is not None:
                        events.append(control)
                        mark_current("llm_control", intent=control.intent, lifecycle=control.lifecycle)
                clean_s = self._clean_text(clause)
                if clean_s:
                    events.append(SpeechSegmentEvent(clean_s, emotion=emotion))
            return events

        mark_current(
            "llm_request_start",
            model=self.model,
            tool_count=len(request_tools),
        )
        attempted = False
        try:
            async for lease, resp, started in self._dispatch(
                payload, timeout_s=15, purpose="chat",
                output_budget=self.max_tokens, stream=True,
            ):
                attempted = True
                try:
                    mark_current("llm_headers", status=resp.status)
                    await self._learn_from_headers(lease, resp)
                    if resp.status != 200:
                        await self._router.settle_uncertain(lease)
                        yield FailedEvent(f"LLM HTTP {resp.status}")
                        return

                    async def consume_event(data_text: str):
                        nonlocal control_decided, control_buffer, inline_end_intent
                        nonlocal finish_reason, saw_done, usage, saw_content, first_token_marked
                        nonlocal first_event_at
                        if data_text == "[DONE]":
                            saw_done = True
                            return []
                        chunk = json.loads(data_text)
                        usage = chunk.get("usage") or usage
                        choice = (chunk.get("choices") or [{}])[0]
                        if choice.get("finish_reason") is not None:
                            finish_reason = choice.get("finish_reason")
                        delta = choice.get("delta") or {}
                        native_calls = delta.get("tool_calls") or []
                        if native_calls:
                            tool_calls.add_delta(native_calls)
                        token = delta.get("content") or ""
                        if not token or "<think>" in token:
                            return []
                        saw_content = True
                        if not first_token_marked:
                            first_token_marked = True
                            first_event_at = time.monotonic()
                            mark_current("llm_first_content_token")
                        if not control_decided:
                            control_buffer += token
                            decided, inline_end_intent, content = self._parse_inline_control_prefix(control_buffer)
                            if not decided:
                                return []
                            control_decided = True
                            token = content
                        return speech_events(token)

                    async for raw_chunk in resp.content.iter_any():
                        for event_text in decoder.feed(raw_chunk):
                            try:
                                for event in await consume_event(event_text):
                                    if isinstance(event, SpeechSegmentEvent):
                                        mark_current("llm_speech_segment", chars=len(event.text))
                                    yield event
                            except (ValueError, json.JSONDecodeError) as exc:
                                logger.warning("Invalid LLM stream event: %s", exc)
                                await self._router.settle_uncertain(lease)
                                yield FailedEvent(str(exc))
                                return

                    # Some providers close the stream right after the terminal
                    # finish_reason without a trailing [DONE] sentinel.
                    if not saw_done and finish_reason in {"stop", "tool_calls"}:
                        saw_done = True
                    for event_text in decoder.flush():
                        try:
                            for event in await consume_event(event_text):
                                if isinstance(event, SpeechSegmentEvent):
                                    mark_current("llm_speech_segment", chars=len(event.text))
                                yield event
                        except (ValueError, json.JSONDecodeError) as exc:
                            logger.warning("Invalid trailing LLM stream event: %s", exc)
                            await self._router.settle_uncertain(lease)
                            yield FailedEvent(str(exc))
                            return
                    await self._settle_stream_usage(
                        lease, usage, started, first_event_at=first_event_at)
                except asyncio.CancelledError:
                    await self._router.settle_uncertain(lease)
                    raise
                except Exception as exc:
                    logger.error("Error communicating with Groq LLM: %s", exc)
                    await self._router.settle_uncertain(lease)
                    yield FailedEvent(str(exc))
                    return
        except asyncio.CancelledError:
            raise
        except _CapacityBusy as exc:
            yield FailedEvent(f"LLM capacity exhausted: {exc}")
            return
        except _UpstreamError as exc:
            yield FailedEvent(str(exc))
            return
        if not attempted:
            yield FailedEvent("LLM capacity exhausted")
            return

        if not control_decided and control_buffer:
            control_decided = True
            inline_end_intent = False
            for event in speech_events(control_buffer):
                if isinstance(event, SpeechSegmentEvent):
                    mark_current("llm_speech_segment", chars=len(event.text))
                yield event

        for clause in splitter.flush():
            emotion = None
            if not emotion_emitted:
                detected_emotion, cleaned = self._extract_emotion(clause)
                emotion_emitted = True
                clause = cleaned
                emotion = detected_emotion
                control = ensure_control(emotion)
                if control is not None:
                    mark_current("llm_control", intent=control.intent, lifecycle=control.lifecycle)
                    yield control
            clean_s = self._clean_text(clause)
            if clean_s:
                mark_current("llm_speech_segment", chars=len(clean_s))
                yield SpeechSegmentEvent(clean_s, emotion=emotion)

        if not saw_done or finish_reason not in {"stop", "tool_calls"}:
            yield FailedEvent("LLM stream ended without a valid terminal event")
            return

        try:
            ready_calls = tool_calls.finalize()
        except ValueError as exc:
            yield FailedEvent(str(exc))
            return

        if ready_calls:
            if finish_reason != "tool_calls":
                yield FailedEvent("tool calls require finish_reason=tool_calls")
                return
            if saw_content:
                non_read_only_calls = [
                    name for _, name, _ in ready_calls
                    if name not in read_only_tool_names
                ]
                if non_read_only_calls:
                    yield FailedEvent("action turn emitted content before structured action")
                    return
                logger.warning(
                    "Allowing mixed content before read-only tool call(s): %s",
                    ", ".join(name for _, name, _ in ready_calls),
                )
            control = ensure_control(tool=True)
            if control is not None:
                mark_current("llm_control", intent=control.intent, lifecycle=control.lifecycle)
                yield control
            for call_id, name, arguments in ready_calls:
                if name == MEMORY_TOOL_NAME:
                    mark_current("llm_memory_action_ready", action=arguments.get("action"))
                    yield MemoryProposalEvent(
                        call_id=call_id,
                        action=str(arguments.get("action") or ""),
                        value=str(arguments.get("value") or ""),
                        fact_id=str(arguments.get("fact_id") or ""),
                        revision=arguments.get("revision"),
                        evidence=str(arguments.get("evidence") or ""),
                    )
                    continue
                if name == CONFIRMATION_TOOL_NAME:
                    mark_current(
                        "llm_confirmation_ready",
                        action_id=arguments.get("action_id"),
                        decision=arguments.get("decision"),
                    )
                    yield ConfirmationDecisionEvent(
                        call_id=call_id,
                        action_id=str(arguments.get("action_id") or ""),
                        decision=str(arguments.get("decision") or ""),
                    )
                    continue
                mark_current("llm_tool_call_ready", tool=name)
                yield ToolCallReadyEvent(call_id=call_id, name=name, arguments=arguments)
        elif finish_reason == "tool_calls":
            yield FailedEvent("finish_reason=tool_calls without a complete tool call")
            return
        elif not control_emitted:
            control = ensure_control()
            if control is not None:
                mark_current("llm_control", intent=control.intent, lifecycle=control.lifecycle)
                yield control

        mark_current("llm_stream_end", finish_reason=finish_reason, saw_content=saw_content)
        yield CompletedEvent(finish_reason=finish_reason, usage=usage)

    async def _stream_chat_clauses(
        self,
        messages: List[Dict[str, str]],
        *,
        detect_end_intent: bool,
    ):
        full_messages = [{"role": "system", "content": self.system_prompt}]
        full_messages.append({"role": "system", "content": SEMANTIC_SYSTEM_PROMPT})
        if detect_end_intent:
            full_messages.append({
                "role": "system",
                "content": self.INLINE_CONVERSATION_CONTROL_PROMPT,
            })
        full_messages.extend(messages)
        payload = {
            "model": self.model,
            "messages": full_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
            **self._reasoning_params(),
        }
        splitter = SpeechSegmentSplitter()
        decoder = SSEDecoder()
        emotion_emitted = False
        finish_reason = None
        saw_done = False
        try:
            async for lease, resp, started in self._dispatch(
                payload, timeout_s=15, purpose="chat",
                output_budget=self.max_tokens, stream=True,
            ):
                first_event_at: Optional[float] = None
                try:
                    if resp.status != 200:
                        await self._router.settle_uncertain(lease)
                        return
                    await self._learn_from_headers(lease, resp)

                    async def handle_token(token: str):
                        nonlocal emotion_emitted, first_event_at
                        for clause in splitter.add_token(token):
                            emotion = None
                            if not emotion_emitted:
                                detected_emotion, cleaned = self._extract_emotion(clause)
                                emotion_emitted = True
                                clause = cleaned
                                emotion = detected_emotion
                            clean_s = self._clean_text(clause)
                            if clean_s:
                                if first_event_at is None:
                                    first_event_at = time.monotonic()
                                yield clean_s, emotion

                    async for raw_chunk in resp.content.iter_any():
                        for event_text in decoder.feed(raw_chunk):
                            if event_text == "[DONE]":
                                saw_done = True
                                continue
                            try:
                                chunk = json.loads(event_text)
                            except (ValueError, json.JSONDecodeError):
                                continue
                            choice = (chunk.get("choices") or [{}])[0]
                            if choice.get("finish_reason") is not None:
                                finish_reason = choice.get("finish_reason")
                            delta = choice.get("delta") or {}
                            token = delta.get("content") or ""
                            if not token or "<think>" in token:
                                continue
                            async for item in handle_token(token):
                                yield item
                    await self._settle_stream_usage(
                        lease, None, started, first_event_at=first_event_at)
                except asyncio.CancelledError:
                    await self._router.settle_uncertain(lease)
                    raise
                except Exception as exc:
                    logger.error("Error communicating with Groq LLM: %s", exc)
                    await self._router.settle_uncertain(lease)
                    return
        except (_CapacityBusy, _UpstreamError):
            return

    async def stream_chat(
        self,
        messages: List[Dict[str, str]]
    ) -> AsyncGenerator[Tuple[str, Optional[str]], None]:
        async for clause, emotion in self._stream_chat_clauses(
            messages,
            detect_end_intent=False,
        ):
            yield clause, emotion

    async def stream_chat_with_control(self, messages: List[Dict[str, str]]):
        """Use one streamed LLM call for both response text and end-intent."""
        async for item in self._stream_chat_clauses(messages, detect_end_intent=True):
            yield item


class _CapacityBusy(Exception):
    """Raised when no quota group can serve (pre-dispatch, nothing billed)."""


class _UpstreamError(Exception):
    """Raised for non-retryable upstream failures (payload/model errors)."""


def engine_for_model(base_engine: "GroqDirectLLM", model: str) -> "GroqDirectLLM":
    """Clone an engine sharing router/ledger/persona for a different model.

    Used by A/B tests: quota stays shared, default model untouched.
    """
    clone = GroqDirectLLM(
        base_engine._router,
        model=model,
        temperature=base_engine.temperature,
        max_tokens=base_engine.max_tokens,
        base_prompt=base_engine.base_prompt,
        reasoning_effort=base_engine._reasoning_effort,
    )
    clone.system_prompt = base_engine.system_prompt
    clone.prompt_template = base_engine.prompt_template
    clone.set_model_effort_overrides(base_engine._model_effort_overrides)
    return clone
