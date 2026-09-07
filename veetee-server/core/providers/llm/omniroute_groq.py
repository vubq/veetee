import aiohttp
import json
import logging
import os
import re
from typing import List, Dict, AsyncGenerator, Tuple, Optional
from core.providers.llm.base import BaseLLM

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
        self.hard_max_segment_chars = 240
        self.hard_cut_search_back = 45

        # A fallback split immediately around these words sounds especially
        # unnatural in Vietnamese because they bind the two phrases together.
        self.boundary_words = {
            "và", "nhưng", "hoặc", "hay", "nên", "mà", "thì", "vì", "nếu",
            "khi", "rồi", "cũng", "để", "của", "cho", "với", "là", "đã",
            "đang", "sẽ", "cuối", "cùng",
        }

    def _find_sentence_cut(self) -> int:
        """Return a sentence boundary large enough to sound continuous."""
        visible_chars = 0
        for i, char in enumerate(self.buffer):
            if not char.isspace():
                visible_chars += 1
            if char in self.end_puncts and visible_chars >= self.min_segment_chars:
                return i
        return -1

    def _find_clause_cut(self) -> int:
        """Use punctuation as a latency-friendly cut in a long sentence.

        We deliberately wait past the target before considering clause
        punctuation. This look-ahead gives a sentence-ending mark a chance to
        arrive first, and avoids chopping ordinary 100-140 character replies.
        """
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
            else:
                break
                
        return clauses

    def flush(self) -> List[str]:
        remaining = self.buffer.strip()
        self.buffer = ""
        if remaining:
            return [remaining]
        return []

class OmnirouteGroqLLM(BaseLLM):
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:20128/v1",
        api_key: str = "local-omniroute",
        model: str = "qwen/qwen3.6-27b",
        temperature: float = 0.7,
        max_tokens: int = 256,
        base_prompt: str = "",
        prompt_template_path: Optional[str] = None,
        base_prompt_state_path: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
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
        return "neutral", text

    async def stream_chat(
        self,
        messages: List[Dict[str, str]]
    ) -> AsyncGenerator[Tuple[str, Optional[str]], None]:
        full_messages = [{"role": "system", "content": self.system_prompt}] + messages
        
        # All LLM traffic goes through local OmniRoute; it owns key/provider fallback.
        payload = {
            "model": self.model,
            "messages": full_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
            "reasoning_format": "hidden",
            "reasoning_effort": "none"
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }
        url = f"{self.base_url}/chat/completions"
        splitter = SpeechSegmentSplitter()
        emotion_emitted = False

        session = await self._get_http_session()
        try:
            async with session.post(url, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        err_body = await resp.text()
                        logger.error(f"Omniroute LLM request failed ({resp.status}): {err_body}")
                        yield "Xin lỗi, đã xảy ra lỗi kết nối.", "sad"
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
                            
                            for clause in splitter.add_token(token):
                                if not emotion_emitted:
                                    detected_emotion, cleaned = self._extract_emotion(clause)
                                    emotion_emitted = True
                                    clean_s = self._clean_text(cleaned)
                                    if clean_s:
                                        yield clean_s, detected_emotion
                                else:
                                    clean_s = self._clean_text(clause)
                                    if clean_s:
                                        yield clean_s, None
                        except Exception as e:
                            logger.debug(f"Error parsing SSE chunk: {e}")

        except Exception as e:
            logger.error(f"Error communicating with Omniroute LLM: {e}")
            yield "Xin lỗi, không thể kết nối tới mô hình AI.", "sad"
            return

        for clause in splitter.flush():
            if not emotion_emitted:
                detected_emotion, cleaned = self._extract_emotion(clause)
                emotion_emitted = True
                clean_s = self._clean_text(cleaned)
                if clean_s:
                    yield clean_s, detected_emotion
            else:
                clean_s = self._clean_text(clause)
                if clean_s:
                    yield clean_s, None
