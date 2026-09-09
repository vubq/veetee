import asyncio
import aiohttp
import json
import logging
import os
import re
from typing import Any, List, Dict, AsyncGenerator, Tuple, Optional
from core.providers.llm.base import BaseLLM
from core.intent import Intent
from core.tools.base import READ_ONLY_TOOL_DESCRIPTION_MARKER
from core.ai_contract import (
    CONFIRMATION_TOOL_NAME,
    MEMORY_TOOL_NAME,
    SEMANTIC_SYSTEM_PROMPT,
)
from core.providers.llm.stream_parser import SSEDecoder, NativeToolCallAccumulator
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

logger = logging.getLogger("OmnirouteGroqLLM")

class SpeechSegmentSplitter:
    """
    Splits streamed LLM text into natural TTS-sized speech segments.

    VieNeu starts a fresh inference for every emitted segment. Each emitted
    segment therefore needs to be a prosodic unit, not just a fixed-size text
    chunk. Prefer complete sentences, allow a clause boundary only for long
    sentences, and use a word-boundary fallback only for genuinely huge input.
    """
    def __init__(self):
        self.buffer = ""
        self.end_puncts = {".", "!", "?", "\n", "…", "。", "！", "？"}
        self.clause_puncts = {";", "；", ":", "：", ",", "，", "—"}
        self.min_segment_chars = 28
        # Do not cut merely because a normal conversational sentence reaches a
        # target length. We can afford to wait a little longer for punctuation;
        # that preserves VieNeu's intonation while still streaming sentence by
        # sentence from the LLM.
        self.clause_target_chars = 150
        self.clause_min_chars = 80
        self.first_clause_min_chars = 36
        self.first_clause_min_words = 5
        self.hard_max_segment_chars = 240
        self.hard_cut_search_back = 45
        self.first_segment_min_chars = 8
        self.first_segment_min_words = 2
        self._segments_emitted = 0
        self._common_abbreviations = {
            "mr", "mrs", "ms", "dr", "ts", "ths", "tp", "q", "p", "st", "vs",
        }

        # A fallback split immediately around these words sounds especially
        # unnatural in Vietnamese because they bind the two phrases together.
        self.boundary_words = {
            "và", "nhưng", "hoặc", "hay", "nên", "mà", "thì", "vì", "nếu",
            "khi", "rồi", "cũng", "để", "của", "cho", "với", "là", "đã",
            "đang", "sẽ", "cuối", "cùng",
        }

    def _is_sentence_boundary(self, index: int) -> bool:
        char = self.buffer[index]
        if char != ".":
            return True

        # A period needs one-character look-ahead. This avoids emitting "3."
        # before the following token turns it into "3.14", and avoids cuts in
        # compact abbreviations such as TP.HCM.
        if index + 1 >= len(self.buffer):
            return False
        prev_char = self.buffer[index - 1] if index > 0 else ""
        next_char = self.buffer[index + 1]
        if prev_char.isdigit() and next_char.isdigit():
            return False
        if next_char.isalpha() and not next_char.isspace():
            return False

        left = self.buffer[:index].rstrip()
        match = re.search(r"([^\W\d_]+)$", left, flags=re.UNICODE)
        if match and match.group(1).lower() in self._common_abbreviations:
            return False
        return True

    def _find_sentence_cut(self) -> int:
        """Return a natural sentence boundary, including short first sentences."""
        visible_chars = 0
        for i, char in enumerate(self.buffer):
            if not char.isspace():
                visible_chars += 1
            if char not in self.end_puncts or not self._is_sentence_boundary(i):
                continue
            if self._segments_emitted > 0 and visible_chars >= self.min_segment_chars:
                return i
            if self._segments_emitted == 0 and visible_chars >= self.first_segment_min_chars:
                words = re.findall(r"[^\W_]+", self.buffer[: i + 1], flags=re.UNICODE)
                if len(words) >= self.first_segment_min_words:
                    return i
        return -1

    def _find_clause_cut(self) -> int:
        """Use punctuation as a latency-friendly cut in a long sentence.

        The first spoken clause may be emitted much earlier so TTS can start
        while the LLM is still producing the rest of the sentence. Later
        clauses keep the larger target to preserve natural prosody.
        """
        if self._segments_emitted == 0:
            visible_chars = 0
            for i, char in enumerate(self.buffer):
                if not char.isspace():
                    visible_chars += 1
                if char not in self.clause_puncts or visible_chars < self.first_clause_min_chars:
                    continue
                words = re.findall(r"[^\W_]+", self.buffer[: i + 1], flags=re.UNICODE)
                if len(words) >= self.first_clause_min_words:
                    return i

        if len(self.buffer) < self.clause_target_chars:
            return -1

        search_start = self.clause_min_chars
        search_end = min(len(self.buffer), self.clause_target_chars + 1)
        for i in range(search_end - 1, search_start - 1, -1):
            if self.buffer[i] in self.clause_puncts:
                return i
        return -1

    def _is_safe_word_boundary(self, idx: int) -> bool:
        """Reject hard cuts next to Vietnamese connector/function words."""
        left = self.buffer[:idx].rstrip()
        right = self.buffer[idx:].lstrip()
        left_match = re.search(r"([^\W\d_]+)$", left, flags=re.UNICODE)
        right_match = re.match(r"([^\W\d_]+)", right, flags=re.UNICODE)
        left_word = left_match.group(1).lower() if left_match else ""
        right_word = right_match.group(1).lower() if right_match else ""
        return left_word not in self.boundary_words and right_word not in self.boundary_words

    def _find_hard_cut(self) -> int:
        """Last-resort protection for extremely long unpunctuated text."""
        if len(self.buffer) < self.hard_max_segment_chars:
            return -1

        search_start = max(0, self.hard_max_segment_chars - self.hard_cut_search_back)
        search_end = min(len(self.buffer), self.hard_max_segment_chars + 1)

        for i in range(search_end - 1, search_start - 1, -1):
            if self.buffer[i] in self.clause_puncts:
                return i

        for i in range(search_end - 1, search_start - 1, -1):
            if self.buffer[i].isspace() and self._is_safe_word_boundary(i):
                return i

        # If all nearby spaces are bad semantic boundaries, prefer a slightly
        # later whitespace over cutting through a connector phrase.
        for i in range(search_end, min(len(self.buffer), self.hard_max_segment_chars + 40)):
            if self.buffer[i].isspace() and self._is_safe_word_boundary(i):
                return i

        return -1

    def add_token(self, token: str) -> List[str]:
        self.buffer += token
        clauses = []
        
        while True:
            found_idx = self._find_sentence_cut()

            if found_idx == -1:
                found_idx = self._find_clause_cut()

            if found_idx == -1:
                found_idx = self._find_hard_cut()
            
            if found_idx != -1:
                clause = self.buffer[:found_idx + 1].strip()
                self.buffer = self.buffer[found_idx + 1:]
                if clause:
                    clauses.append(clause)
                    self._segments_emitted += 1
            else:
                break
                
        return clauses

    def flush(self) -> List[str]:
        remaining = self.buffer.strip()
        self.buffer = ""
        if remaining:
            self._segments_emitted += 1
            return [remaining]
        return []

class OmnirouteGroqLLM(BaseLLM):
    ASR_CORRECTION_PROMPT = """Bạn là tầng hiệu chỉnh cuối của ASR cho một trợ lý giọng nói tiếng Việt. Đầu vào là transcript máy nhận dạng âm thanh, có thể sai 1-3 từ vì các âm gần nhau, nhất là từ đầu câu, tên riêng, thương hiệu, từ tiếng Anh và chữ cái đọc rời.

Hãy khôi phục câu người dùng có khả năng thực sự đã nói dựa trên toàn bộ câu và ngữ cảnh đây là lời nói với trợ lý giọng nói. Được phép sửa từ nghe nhầm khi câu hiện tại không tự nhiên hoặc không tạo thành ý định hợp lý. Với tên người, ứng dụng, nghệ sĩ, thương hiệu và chữ viết tắt, chuẩn hóa về tên quen thuộc khi ngữ cảnh cho độ chắc chắn cao. Không trả lời câu hỏi, không thực hiện lệnh, không thêm chi tiết ngoài câu nói. Nếu câu đã tự nhiên hoặc không đủ chắc chắn thì giữ nguyên. Chỉ xuất đúng transcript cuối cùng, không giải thích, không dấu ngoặc kép."""

    INLINE_CONVERSATION_CONTROL_PROMPT = """Trong chính lượt này, tự quyết định người dùng có muốn kết thúc phiên hiện tại không. Nếu cần gọi tool, gọi tool trực tiếp ngay; không phát câu chờ và không cần [end]/[continue] trước tool call. Với lượt trả lời bằng nội dung nói, đầu ra bắt buộc mở đầu bằng [end] hoặc [continue], rồi thẻ cảm xúc và nội dung nói.
[end] chỉ khi lời mới nhất thể hiện rõ muốn dừng/kết thúc phiên; sau đó nói một câu chào ngắn đúng persona.
[continue] cho mọi trường hợp khác, kể cả hỏi/nhắc về việc tạm biệt hay đi ngủ.
Định dạng: [continue][happy]Nội dung... hoặc [end][relaxed]Nội dung.... Hai nhãn điều khiển là metadata nội bộ, không nhắc lại trong lời nói."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:20128/v1",
        api_key: str = "local-omniroute",
        model: str = "groq/qwen/qwen3.6-27b",
        temperature: float = 0.7,
        max_tokens: int = 256,
        reasoning_format: str = "hidden",
        base_prompt: str = "",
        prompt_template_path: Optional[str] = None,
        base_prompt_state_path: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.reasoning_format = reasoning_format
        self.prompt_template_path = prompt_template_path
        self.base_prompt_state_path = base_prompt_state_path
        self.prompt_template = self._load_prompt_template()
        self.base_prompt = self._load_saved_base_prompt() or base_prompt.strip()
        self.system_prompt = self._render_system_prompt(self.base_prompt)
        self._http_session: Optional[aiohttp.ClientSession] = None

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

    def set_base_prompt(self, base_prompt: str, persist: bool = True) -> None:
        cleaned = (base_prompt or "").strip()
        if not cleaned:
            raise ValueError("base_prompt must not be empty")
        self.base_prompt = cleaned
        self.system_prompt = self._render_system_prompt(cleaned)

        if persist and self.base_prompt_state_path:
            state_dir = os.path.dirname(self.base_prompt_state_path)
            os.makedirs(state_dir, exist_ok=True)
            temp_path = f"{self.base_prompt_state_path}.tmp"
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(cleaned)
            os.replace(temp_path, self.base_prompt_state_path)

    async def _get_http_session(self) -> aiohttp.ClientSession:
        if self._http_session is None or self._http_session.closed:
            connector = aiohttp.TCPConnector(limit=32, keepalive_timeout=30)
            self._http_session = aiohttp.ClientSession(connector=connector)
        return self._http_session

    async def warmup(self):
        """Warm the persistent local connection to OmniRoute."""
        session = await self._get_http_session()
        try:
            async with session.get(
                f"{self.base_url}/models",
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                await resp.read()
                logger.info(f"OmniRoute connection warmup completed (HTTP {resp.status})")
        except Exception as e:
            logger.warning(f"OmniRoute connection warmup skipped: {e}")

    async def close(self):
        if self._http_session is not None and not self._http_session.closed:
            await self._http_session.close()
        self._http_session = None

    async def correct_transcript(self, transcript: str) -> str:
        """Use the fast LLM path only as a conservative ASR post-corrector."""
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
            "reasoning_format": self.reasoning_format,
            "reasoning_effort": "none",
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        session = await self._get_http_session()
        try:
            async with session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=2.0),
            ) as resp:
                if resp.status != 200:
                    logger.warning("ASR correction skipped because OmniRoute returned HTTP %s", resp.status)
                    return original
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.warning("ASR correction request failed: %s", exc)
            return original

        corrected = str(
            data.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
        )
        corrected = re.sub(r"<think>.*?</think>", "", corrected, flags=re.DOTALL)
        corrected = re.sub(r"<think>.*", "", corrected, flags=re.DOTALL).strip()
        corrected = corrected.strip('"“”').strip()
        if not corrected or "\n" in corrected:
            return original

        # The corrector may repair a few acoustically confused words, but it
        # must never turn the transcript into a fresh answer or long rewrite.
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
            "reasoning_format": self.reasoning_format,
            "reasoning_effort": "none",
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        session = await self._get_http_session()
        try:
            async with session.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=timeout_seconds),
            ) as resp:
                if resp.status != 200:
                    logger.warning("AI conversation control returned HTTP %s", resp.status)
                    return ""
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.warning("AI conversation control request failed: %s", exc)
            return ""

        content = str(data.get("choices", [{}])[0].get("message", {}).get("content", "") or "")
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        content = re.sub(r"<think>.*", "", content, flags=re.DOTALL)
        return content.strip()

    @staticmethod
    def _clean_control_sentence(text: str, max_chars: int = 180) -> str:
        cleaned = str(text or "").strip().strip('"“”').strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        if not cleaned or len(cleaned) > max_chars or "\n" in cleaned:
            return ""
        return cleaned

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

    def _clean_text(self, text: str) -> str:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)
        text = text.replace("**", "").replace("*", "").replace("#", "").replace("`", "")
        return text.strip()

    def _extract_emotion(self, text: str) -> Tuple[str, str]:
        match = re.match(r"^\[([a-zA-Z]+)\]\s*(.*)$", text.strip(), re.DOTALL)
        if match:
            emotion = match.group(1).lower()
            remaining = match.group(2)
            valid_emotions = {"happy", "neutral", "sad", "surprised", "thinking", "angry", "relaxed"}
            if emotion in valid_emotions:
                return emotion, remaining
            # Never send an invented technical tag such as [chinh] to TTS.
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

        # While the model is still streaming the first few characters, keep
        # buffering. If it ignored the contract, fail open as a normal chat
        # response instead of delaying the turn indefinitely.
        if "]" not in stripped and len(stripped) < 24:
            return False, False, ""
        return True, False, stripped

    async def _stream_chat_impl(
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
        
        # All LLM traffic goes through local OmniRoute; it owns key/provider fallback.
        payload = {
            "model": self.model,
            "messages": full_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
            "reasoning_format": self.reasoning_format,
            "reasoning_effort": "none"
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        url = f"{self.base_url}/chat/completions"
        splitter = SpeechSegmentSplitter()
        emotion_emitted = False
        control_decided = not detect_end_intent
        control_buffer = ""
        inline_end_intent = False
        control_result_emitted = False

        def format_yield(clause: str, emotion: Optional[str]):
            nonlocal control_result_emitted
            result = (
                inline_end_intent
                if detect_end_intent and not control_result_emitted
                else None
            )
            control_result_emitted = True
            return clause, emotion, result

        def add_content_token(token: str):
            nonlocal emotion_emitted
            items = []
            for clause in splitter.add_token(token):
                if not emotion_emitted:
                    detected_emotion, cleaned = self._extract_emotion(clause)
                    emotion_emitted = True
                    clean_s = self._clean_text(cleaned)
                    if clean_s:
                        items.append(format_yield(clean_s, detected_emotion))
                else:
                    clean_s = self._clean_text(clause)
                    if clean_s:
                        items.append(format_yield(clean_s, None))
            return items

        session = await self._get_http_session()
        try:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        err_body = await resp.text()
                        logger.error(f"Omniroute LLM request failed ({resp.status}): {err_body}")
                        return

                    async for line in resp.content:
                        line_str = line.decode("utf-8").strip()
                        if not line_str.startswith("data: ") or line_str == "data: [DONE]":
                            continue
                        try:
                            chunk = json.loads(line_str[6:])
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            token = delta.get("content", "")
                            if not token or "<think>" in token:
                                continue

                            if not control_decided:
                                control_buffer += token
                                decided, inline_end_intent, token = self._parse_inline_control_prefix(
                                    control_buffer
                                )
                                if not decided:
                                    continue
                                control_decided = True
                                logger.info(
                                    "Inline conversation control decided end_intent=%s",
                                    inline_end_intent,
                                )
                            for item in add_content_token(token):
                                yield item
                        except Exception as e:
                            logger.debug(f"Error parsing SSE chunk: {e}")

        except Exception as e:
            logger.error(f"Error communicating with Omniroute LLM: {e}")
            return

        if not control_decided and control_buffer:
            control_decided = True
            inline_end_intent = False
            logger.warning("LLM response ended before inline conversation control prefix was complete")
            for item in add_content_token(control_buffer):
                yield item

        for clause in splitter.flush():
            if not emotion_emitted:
                detected_emotion, cleaned = self._extract_emotion(clause)
                emotion_emitted = True
                clean_s = self._clean_text(cleaned)
                if clean_s:
                    yield format_yield(clean_s, detected_emotion)
            else:
                clean_s = self._clean_text(clause)
                if clean_s:
                    yield format_yield(clean_s, None)

    async def stream_turn(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict]] = None,
        detect_end_intent: bool = True,
        tool_choice: Optional[Any] = None,
    ):
        """Stream one typed LLM turn, including native function calls.

        This is the primary low-latency path used by the unified runner. It
        consumes arbitrary SSE/TCP chunk boundaries and never launches a
        separate intent-classifier inference.
        """
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
            "reasoning_format": self.reasoning_format,
            "reasoning_effort": "none",
        }
        if request_tools:
            payload["tools"] = request_tools
            payload["tool_choice"] = tool_choice or "auto"
        elif tool_choice is not None:
            payload["tool_choice"] = tool_choice

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        url = f"{self.base_url}/chat/completions"
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

        session = await self._get_http_session()
        mark_current(
            "llm_request_start",
            model=self.model,
            tool_count=len(request_tools),
        )
        try:
            async with session.post(
                url,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                mark_current("llm_headers", status=resp.status)
                if resp.status != 200:
                    err_body = await resp.text()
                    logger.error("Omniroute LLM request failed (%s): %s", resp.status, err_body)
                    yield FailedEvent(f"LLM HTTP {resp.status}")
                    return

                async def consume_event(data_text: str):
                    nonlocal control_decided, control_buffer, inline_end_intent
                    nonlocal finish_reason, saw_done, usage, saw_content, first_token_marked
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
                            yield FailedEvent(str(exc))
                            return

                for event_text in decoder.flush():
                    try:
                        for event in await consume_event(event_text):
                            if isinstance(event, SpeechSegmentEvent):
                                mark_current("llm_speech_segment", chars=len(event.text))
                            yield event
                    except (ValueError, json.JSONDecodeError) as exc:
                        logger.warning("Invalid trailing LLM stream event: %s", exc)
                        yield FailedEvent(str(exc))
                        return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Error communicating with Omniroute LLM: %s", exc)
            yield FailedEvent(str(exc))
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
                    # Side-effecting/semantic actions must not be dispatched
                    # after the model has already spoken an unvalidated claim.
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

    async def stream_chat(
        self,
        messages: List[Dict[str, str]]
    ) -> AsyncGenerator[Tuple[str, Optional[str]], None]:
        async for clause, emotion, _ in self._stream_chat_impl(
            messages,
            detect_end_intent=False,
        ):
            yield clause, emotion

    async def stream_chat_with_control(self, messages: List[Dict[str, str]]):
        """Use one streamed LLM call for both response text and end-intent."""
        async for item in self._stream_chat_impl(messages, detect_end_intent=True):
            yield item
