# User-text audit (M0.1 plan AI-không-hardcode)

Mọi nơi đọc lời user / sinh speech trong `core/`, ai quyết định, có chạy
trước AI không, có gây mutation/close không. Đối chiếu HEAD `69c0a14`.

| # | Vị trí | Đọc gì | Ai quyết định | Trước AI? | Mutation/close? |
|---|---|---|---|---|---|
| 1 | `session._handle_text_json` listen:detect | text wake + câu hỏi kèm | AI (giữ nguyên câu cho turn) |Detect là event transport; không match whitelist, passthrough | Không |
| 2 | `session._handle_text_json` listen:start/stop/abort | lifecycle transport | Server deterministic (cancel/capture) | N/A (không phải ngữ nghĩa) | Abort chỉ cancel, không suy intent |
| 3 | `session._handle_text_json` text/chat | câu user | AI unified turn | Không bypass | Không |
| 4 | `session._on_speech_started` + echo guard | VAD speech-start | Server: discard theo playback-tail估计 (acoustic timing, không ngữ nghĩa) | Có (bỏ echo trước AI) | Không |
| 5 | `session._on_asr_transcript` final | transcript cuối | AI turn (correction tắt ở fast profile) | Không route | Không |
| 6 | `dialogue.get_messages_for_llm` | history đưa vào prompt | AI đọc context; server chỉ budget/trim | N/A (input cho AI) | Không |
| 7 | `turn_runner.stream` control `[end]/[continue]`, `[emotion]` | output AI sinh | AI sinh; server validate enum + strip tag trước TTS | Sau AI | End→lifecycle close theo contract |
| 8 | memory proposal (AI sinh) | fact/evidence AI đề xuất | AI đề xuất; server validate schema/owner/revision rồi mới commit | Sau AI | Chỉ sau write barrier + receipt |
| 9 | confirmation approve/reject (AI sinh) | decision + action ID | AI; server match pending ID/TTL/args-hash | Sau AI | Chỉ action đã confirm |
| 10 | tool calls (AI sinh) | name/args/ID | AI; server validate schema + permission + ownership + terminal | Sau AI | Dispatch có receipt; timeout = unknown |
| 11 | idle farewell shape check | câu chào AI sinh | AI sinh; user yêu cầu farewell≠question; server validate hình thức + retry + fallback | Sau AI | Phát xong đóng phiên (deadline) |
| 12 | `_clean_text` bracket strip | output AI sinh | Vệ sinh TTS (không đọc lời user) | Sau AI | Không |
| 13 | retrieval lexical (FTS/LIKE/token) | query user | Baseline lấy ứng viên; AI synthesis quyết định dùng | Trước AI (candidate only) | Không write từ index |
| 14 | `wake_words/exit_commands/greeting_text/...` | config legacy | INERT — 0 hit match trong runtime (grep) | Không chạy | Không |
| 15 | management prompt API | operator persona | Operator (không phải user speech); budget validate | N/A | Persist persona có version |

Kết luận: không còn nhánh match keyword/regex/whitelist lên lời user để
chọn intent/tool/memory/confirm/end/language. Các list còn lại là validate
output AI, schema, hoặc baseline retrieval có AI synthesis sau cùng.
