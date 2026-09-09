# VeeTee Realtime Voice Assistant Backend Server (Local Non-Docker)

VeeTee Server phục vụ ESP32/Xiaozhi firmware nguyên bản và Web Client qua WebSocket. Hướng phát triển hiện tại là **tối ưu toàn bộ ở phía server, không yêu cầu sửa hoặc flash firmware tùy biến**. Pipeline Intent/Memory/Tools được theo dõi tại [task plan](../task-plans/2026-09-08-pipeline-600ms-intent-memory-tools.md).

- **LLM:** Omniroute Groq Qwen 3.6 27B streaming.
- **ASR mặc định:** NVIDIA Parakeet CTC 0.6B Vietnamese + Silero VAD local; Deepgram là provider dự phòng.
- **TTS:** VieNeu-TTS v3 Turbo local.
- **VAD:** end silence 450 ms; với frame Silero 32 ms, ngưỡng thực tế được vượt khoảng 480 ms.
- **Pipeline hợp nhất:** chat thường dùng đúng một LLM call để sinh speech/control và quyết định intent. Lượt có tool/memory/confirmation được phép thêm đúng một vòng AI synthesis sau receipt thật, tổng tối đa 2 rounds và vòng 2 ép `tool_choice=none`.
- **Barge-in mặc định:** `barge_in_policy=client_only`. ESP32 ngắt lượt bằng các message chuẩn đã có như `abort` hoặc `listen:start`.
- **Audio pacing:** server giới hạn lượng TTS gửi trước bằng `tts.send_ahead_ms`, mặc định 120 ms, thay cho fixed sleep 5 ms.
- **TTS backpressure:** queue stream VieNeu có giới hạn, mặc định `tts.stream_queue_max_chunks=4`.
- **Hội thoại do AI quyết định:** text từ `listen:detect`, `chat`, `text` và ASR final đều đi qua AI theo ngữ cảnh; server không match wake/exit phrase để tự quyết định ý định.
- **Semantic/tool routing do AI quyết định:** server đưa context và tool schema cho LLM với `tool_choice=auto`; không dùng keyword, exact phrase, regex hay whitelist trên câu người dùng để ép intent hoặc chọn function/tool. Deterministic code chỉ giữ protocol/lifecycle, validation, permission/ownership, deadline/cancel và receipt/state invariants.
- **Kết thúc tự nhiên:** semantic end do AI quyết định trong unified turn. Idle timeout tạo một semantic evaluation có giới hạn cho mỗi inactivity epoch; logical idle vẫn giữ WebSocket stock để thiết bị có thể bắt đầu lượt mới.
- **Memory:** session memory chạy local; durable personal memory chỉ được bật khi operator cấu hình `memory.trusted_owner_id`. `Device-Id`/`Client-Id` tự khai báo không được dùng làm owner tin cậy.
- **Tools:** built-in `calculate`/`get_current_time`, native streamed tool calls và adapter MCP stock tùy chọn. MCP thiếu/không được board quảng bá không làm hỏng chat.
- **Môi trường:** chạy trực tiếp trên máy, không Docker.

## Cấu trúc chính

```text
veetee-server/
├── config/
│   └── settings.py
├── core/
│   ├── audio_pacing.py
│   ├── audio_utils.py
│   ├── ai_contract.py
│   ├── context_builder.py
│   ├── intent.py
│   ├── memory/
│   ├── protocol.py
│   ├── response_audio_cache.py
│   ├── session.py
│   ├── tools/
│   ├── turn_events.py
│   ├── turn_metrics.py
│   ├── turn_runner.py
│   └── providers/
├── docs/
│   ├── ARCHITECTURE.md
│   ├── API_PROTOCOL.md
│   ├── ESP32_CONFIG.md
│   ├── PLAN.md
│   ├── SETUP.md
│   ├── TESTING.md
│   └── VOICE_PIPELINE_STATUS.md
├── patches/
│   └── xiaozhi-esp32-barge-in.patch  # Artifact thử nghiệm cũ, không cần áp
├── tests/
├── test_e2e.py
├── server.py
└── start.sh
```

## Khởi động

```bash
cd /home/quangvu/Project/veetee/veetee-server
./start.sh
```

- WebSocket cho ESP32/client: `ws://<IP_MAY_TINH>:8000/`
- OTA config endpoint: `http://<IP_MAY_TINH>:8003/ota/`
- Web dashboard: `http://<IP_MAY_TINH>:8003/`

## Kiểm thử

Regression/unit:

```bash
cd /home/quangvu/Project/veetee/veetee-server
/home/quangvu/Project/venv/bin/python -m unittest discover -s tests -v
```

Runtime E2E:

```bash
cd /home/quangvu/Project/veetee/veetee-server
PYTHONPATH=. /home/quangvu/Project/venv/bin/python test_e2e.py
```

`test_e2e.py` trả exit code lỗi khi thiếu marker/thứ tự sai và trả `BLOCKED` khi runtime/model cần thiết chưa sẵn sàng; không coi timeout là PASS.

Benchmark latency có nhãn speech-end dùng `scripts/benchmark_pipeline.py`. Metric SLA là `speech_end_to_first_audio_received_ms`; `post_asr_first_audio_sent_ms` chỉ là metric server-side để chẩn đoán. Binary đầu server gửi cũng không chứng minh loa ESP32 đã bắt đầu phát hoặc đã dừng vật lý.

## Hội thoại tự nhiên phía server

Trong `config.yaml`, bật:

```yaml
conversation:
  enabled: true
  greeting_enabled: true
  greeting_ai_enabled: true
  audio_cache_enabled: true
  idle_timeout_seconds: 120
  goodbye_enabled: true
  goodbye_ai_enabled: true
  end_intent_ai_enabled: true
```

Nếu firmware stock gửi `listen:detect` kèm text, server giữ nguyên text đó và đưa vào AI như một lượt ngữ nghĩa sau cửa sổ phối hợp `listen:start`. Server không dùng `wake_words` để phân loại nội dung. Nếu board không phát `listen:detect`, hội thoại qua mic/ASR vẫn hoạt động bình thường và không cần sửa firmware.

Greeting/goodbye bình thường do AI tạo theo cùng persona/context của turn; không có template hay round-robin pool quyết định nội dung. Recovery khi turn lỗi là một câu do AI sinh lúc startup/persona refresh rồi cache cả audio với provenance `ai:<model>`; nếu cache chưa sẵn sàng thì server báo degraded thay vì chèn câu literal. Các field `wake_words`, `exit_commands`, `greeting_text`, `goodbye_text`, `greeting_pool_size` chỉ còn là cấu hình legacy/inert để đọc config cũ, không tham gia semantic routing.

`latency.unified_turn_enabled=true` là profile mặc định. Chat thường dùng 1 LLM call; action/tool/memory/confirmation có thể dùng vòng 2 để AI diễn đạt receipt thật, với `tools.max_llm_rounds_per_turn=2`, `tools.tool_result_synthesis=true` và `tool_choice=none` ở vòng synthesis. Durable memory tự hạ về tắt nếu không có `memory.trusted_owner_id` đáng tin cậy.

Built-in `get_current_time` cũng tuân theo rule này: mô tả tool và semantic prompt hướng dẫn model dùng clock khi cần dữ liệu hiện tại, nhưng server không dò các câu như “mấy giờ rồi”/“hôm nay ngày mấy” để force tool. Nếu cần cải thiện độ chính xác, ưu tiên sửa prompt, tool description, context hoặc model/provider thay vì thêm matcher câu chữ.

## Tương thích firmware nguyên bản

Server không yêu cầu `features.device_aec`, không gửi `interrupt=true` trong đường mặc định và không yêu cầu `ResetDecoder()` custom. Hello stock có thể không có `features`, có `mcp`, hoặc có `aec` theo cấu hình firmware.

`features.aec=true` chỉ cho biết firmware chọn hướng server-side AEC; VeeTee hiện chưa có tầng server-side AEC nên cờ này **không** tự bật automatic speech barge-in. `mode=realtime` cũng không đủ để chứng minh echo cancellation đang hoạt động.

Khi client gửi `abort` hoặc `listen:start` lúc server đang nói, server hủy turn hiện tại, ngừng gửi audio mới và gửi `tts:stop` chuẩn. Firmware stock không có playback flush ACK chung, nên server chỉ có thể giảm đuôi âm bằng pacing; thời điểm loa thật sự dừng phải đo trên board.

## Tài liệu

- [Kiến trúc & kế hoạch voice pipeline](docs/PLAN.md)
- [Trạng thái triển khai](docs/VOICE_PIPELINE_STATUS.md)
- [Hướng dẫn ESP32 firmware nguyên bản](docs/ESP32_CONFIG.md)
- [Đặc tả protocol](docs/API_PROTOCOL.md)
- [Cài đặt server](docs/SETUP.md)
