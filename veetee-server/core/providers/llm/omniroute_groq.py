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
    ASR_CORRECTION_PROMPT = """Bạn là tầng hiệu chỉnh cuối của ASR cho một trợ lý giọng nói tiếng Việt. Đầu vào là transcript máy nhận dạng âm thanh, có thể sai 1-3 từ vì các âm gần nhau, nhất là từ đầu câu, tên riêng, thương hiệu, từ tiếng Anh và chữ cái đọc rời.

Hãy khôi phục câu người dùng có khả năng thực sự đã nói dựa trên toàn bộ câu và ngữ cảnh đây là lời nói với trợ lý giọng nói. Được phép sửa từ nghe nhầm khi câu hiện tại không tự nhiên hoặc không tạo thành ý định hợp lý. Với tên người, ứng dụng, nghệ sĩ, thương hiệu và chữ viết tắt, chuẩn hóa về tên quen thuộc khi ngữ cảnh cho độ chắc chắn cao. Không trả lời câu hỏi, không thực hiện lệnh, không thêm chi tiết ngoài câu nói. Nếu câu đã tự nhiên hoặc không đủ chắc chắn thì giữ nguyên. Chỉ xuất đúng transcript cuối cùng, không giải thích, không dấu ngoặc kép."""

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

        if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", corrected) and not re.search(
            r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", original
        ):
            logger.warning("Rejected ASR correction containing an unexpected CJK/Hangul script")
            return original

        return corrected

    def _should_enforce_vietnamese(self, messages: List[Dict[str, str]]) -> bool:
        """Keep default Vietnamese replies free of accidental CJK leakage."""
        latest_user = ""
        for message in reversed(messages):
            if message.get("role") == "user":
                latest_user = str(message.get("content", ""))
                break

        lowered = latest_user.lower()
        foreign_language_markers = (
            "tiếng trung", "tiếng hoa", "tiếng nhật", "tiếng hàn",
            "chinese", "japanese", "korean", "中文", "日本語", "한국어",
            "kanji", "hiragana", "katakana",
        )
        if any(marker in lowered for marker in foreign_language_markers):
            return False

        # If the user themselves supplied CJK/Hangul text, preserve those
        # scripts so translation/explanation requests still work naturally.
        if re.search(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", latest_user):
            return False
        return True

    def _clean_text(self, text: str, enforce_vietnamese: bool = False) -> str:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        text = re.sub(r"<think>.*", "", text, flags=re.DOTALL)
        text = text.replace("**", "").replace("*", "").replace("#", "").replace("`", "")
        if enforce_vietnamese:
            cleaned = re.sub(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+", " ", text)
            if cleaned != text:
                logger.warning("Removed unrequested CJK/Hangul characters from Vietnamese LLM output")
            text = cleaned
            text = re.sub(r"\s+([,.;:!?])", r"\1", text)
            text = re.sub(r"\s{2,}", " ", text)
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
        enforce_vietnamese = self._should_enforce_vietnamese(messages)

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
                                    clean_s = self._clean_text(cleaned, enforce_vietnamese=enforce_vietnamese)
                                    if clean_s:
                                        yield clean_s, detected_emotion
                                else:
                                    clean_s = self._clean_text(clause, enforce_vietnamese=enforce_vietnamese)
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
                clean_s = self._clean_text(cleaned, enforce_vietnamese=enforce_vietnamese)
                if clean_s:
                    yield clean_s, detected_emotion
            else:
                clean_s = self._clean_text(clause, enforce_vietnamese=enforce_vietnamese)
                if clean_s:
                    yield clean_s, None
