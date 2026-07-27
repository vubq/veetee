# Local development runbook

Tài liệu này là cách khởi động Veetee sau khi bật máy, cho cả người phát triển và AI
đang làm việc trong repository. V1 là single-node: các process app/model chạy trực tiếp
trên host; PostgreSQL/Redis dùng host-local runtime hoặc Docker; 9Router là dependency
loopback. Không chạy Manager API trong đường audio từng frame.

## 0. Kiểm tra đúng source trước khi chạy

Luôn chạy từ worktree mà bạn muốn kiểm thử:

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee

git branch --show-current
git rev-parse --short HEAD
git status --short
```

Bản baseline VieNeu đã nghiệm thu là commit `e1618d7` (`fix improve VieNeu realtime
speech quality`). Nhánh chính local phải trỏ tới commit này hoặc commit mới hơn có
chứa nó. Nếu `main` chỉ ở `5e597d8`, đang chạy bản trước khi sửa sentence batching.
Không dùng worktree/nhánh cũ để khởi động nhầm service.

Kiểm tra port trước khi mở process mới; không mở hai voice-server cùng port:

```bash
ss -lntp | grep -E ':(20128|5432|6379|8000|8001|8081)\\b' || true
```

## 1. Cài đặt lần đầu (chỉ một lần trên máy)

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee/veetee-server
npm ci
uv sync --project apps/voice-server --locked --all-groups

# Chọn một cách cho PostgreSQL/Redis:
# Docker:
npm run infra:up

# Hoặc host-local, không cần Docker daemon:
npm run infra:host:prepare
npm run infra:host:up
```

Nếu chưa có Manager config local, tạo một lần. Script từ chối ghi đè config cũ:

```bash
npm run env:local:init
```

Lệnh này tạo `apps/manager-api/.env` và `data/local-admin.txt` ở dạng ignored, mode
`0600`. Không copy nội dung hai file này vào chat, log hoặc commit.

Áp dụng schema và seed control plane:

```bash
npm run db:deploy --workspace @veetee/manager-api
npm run db:seed --workspace @veetee/manager-api
```

Chuẩn bị model local và voice environment:

```bash
npm run models:prepare
npm run env:voice:sync
```

`env:voice:sync` đọc Manager service token và API key active do local 9Router quản lý,
chỉ ghi `apps/voice-server/.env` ignored mode `0600`, không in secret. Nếu chưa có key
active hoặc Manager `.env` không hợp lệ, sửa dependency đó trước; không điền key vào
source/example.

Tuỳ chọn, chạy benchmark local sau khi model đã tải:

```bash
npm run models:benchmark
```

Nếu host đã chuẩn bị VieNeu native và muốn kiểm thử native, chạy thêm:

```bash
npm run models:prepare-native
```

Sau đó đổi `VEETEE_TTS_BACKEND=native` trong file ignored
`apps/voice-server/.env`, kiểm tra readiness và benchmark. Baseline portable đã nghiệm
thu là `VEETEE_TTS_BACKEND=onnx`; native không được tự fallback khi thiếu library/model.

### 1.1 Nguồn cấu hình và quy tắc secret

Có ba lớp cấu hình local, không được nhập nhằng:

1. `veetee-server/.env.example` là template tổng hợp cho hạ tầng và speech baseline.
2. `apps/manager-api/.env.example` mô tả control-plane URL/secret; lệnh
   `env:local:init` sinh `apps/manager-api/.env` và `data/local-admin.txt` ignored,
   mode `0600`, và từ chối ghi đè.
3. `apps/voice-server/.env.example` là nguồn mà `env:voice:sync` render thành
   `apps/voice-server/.env`. Script chỉ thay host/reload, Manager service token,
   origin allowlist và 9Router API key; các baseline còn lại đến từ example.

Chỉ commit tên biến và giá trị development không nhạy cảm. Không commit/in/log giá trị
của `*_SECRET`, `*_TOKEN`, `*_API_KEY`, `*_PASSWORD`, master/signing key, nội dung
`local-admin.txt`, Authorization header, transcript hoặc audio. Khi kiểm tra effective
config, dùng allowlist tên biến không nhạy cảm thay vì `cat .env`.

Baseline portable phải đồng bộ giữa example, docs và runtime: ONNX/2 TTS threads,
Trúc Ly, `tu_nhien`, speed `1.0`, lead-in 16, watermark bật, 24 kHz, native ref codes
bật, playback queue 5 giây, ASR 2 threads, VAD 1 thread, LLM prewarm 12 giây và planner
ceiling 15 giây. Manager agent snapshot có thể override voice/style/rate/volume và
provider deadlines cho session; nó không đổi process-wide backend/thread count.

| Nhóm biến | Owner/source | Secret | Khi có hiệu lực |
|---|---|---|---|
| `DATABASE_URL`, `REDIS_URL`, Manager host/port/public URL, CORS, Voice WS URLs | Manager `.env`; sinh bởi `env:local:init` lần đầu rồi chỉnh local có chủ đích | URL có thể chứa credential; không in | restart Manager API |
| `VEETEE_AUTH_SECRET`, Lab/device token secret, master key, internal service token, bootstrap password | Manager `.env` ignored `0600` | bắt buộc secret | restart/rotate theo runbook; không sync ra Web/firmware |
| `VEETEE_9ROUTER_API_KEY`, `VEETEE_MANAGER_INTERNAL_TOKEN` | `env:voice:sync` lấy từ trusted local stores | bắt buộc secret | restart Voice Server sau sync |
| ASR/VAD/TTS backend/thread/model path, prewarm/planner, inactivity, queue | `apps/voice-server/.env.example` -> ignored Voice `.env` | không, trừ provider key | process-wide; restart Voice Server |
| agent voice/style/rate/volume, provider/deadline policy | immutable published Manager snapshot | secret chỉ qua reference | session mới/reload snapshot |
| `VEETEE_WEB_ALLOWED_HOSTS`, `VITE_MANAGER_API_URL` | Manager Web process environment | không | restart Vite/build |
| Tailscale socket/state/cert/key và Serve/Funnel map | external userspace Tailscale state | state/key/cert private | daemon/Serve config; không nằm trong app `.env` |

Kiểm tra file permission mà không đọc giá trị:

```bash
stat -c '%a %n' apps/manager-api/.env apps/voice-server/.env data/local-admin.txt
# expected: 600 cho ba file generated local
```

Root `.env.example` là template tổng hợp, không phải file Pydantic/Node tự động load cho
mọi app. Manager dev load `apps/manager-api/.env`; Voice Server load env theo working
directory/script. Nếu hai example drift, source code validation và app-specific example
là authoritative; sửa root template cùng commit thay vì “vá” ignored env.

## 2. Khởi động sau mỗi lần reboot

Hạ tầng và 9Router là dependency riêng. Kiểm tra chúng trước:

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee/veetee-server
npm run infra:host:up                 # idempotent, nếu dùng host-local
curl --fail --silent --show-error http://127.0.0.1:20128/api/health
curl --fail --silent --show-error http://127.0.0.1:20128/v1/models >/dev/null
```

Nếu 9Router được cài bằng user service, kiểm tra service thay vì mở bản thứ hai:

```bash
systemctl --user --no-pager --type=service --state=running | grep -Ei '9router|cliproxy' || true
```

Nếu service không tự lên, khởi động dependency theo hướng dẫn cài đặt local của máy,
không đưa credential vào lệnh hoặc log. Voice-server sẽ không ready nếu LLM upstream
không hoạt động.

Mở ba terminal app riêng, hoặc dùng process supervisor tương đương. Chạy đúng thứ tự
sau khi các health check dependency trước đã pass:

**Terminal 1 — Manager API (control plane, port 8001):**

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee/veetee-server
npm run dev --workspace @veetee/manager-api
```

Chờ `http://127.0.0.1:8001/health/ready` trả `200` trước khi mở Voice Server.

**Terminal 2 — Voice Server (hot path, port 8000):**

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee/veetee-server
npm run env:voice:sync
npm run dev:voice
```

**Terminal 3 — Manager Web (operator console, port 8081):**

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee/veetee-server
npm run dev --workspace @veetee/manager-web
```

Nếu Voice Server đã chạy trước Manager, `/health/live` vẫn có thể pass nhưng
`/health/ready` chỉ pass khi mọi component bắt buộc sẵn sàng. Không gửi audio trước
readiness và không mở process Voice thứ hai để “sửa” dependency chưa ready.

## 3. Readiness và smoke check

Chạy từ terminal thứ năm, không cần đăng nhập và không in secret:

```bash
curl --fail --silent --show-error http://127.0.0.1:8001/health/live
curl --fail --silent --show-error http://127.0.0.1:8001/health/ready
curl --fail --silent --show-error http://127.0.0.1:8000/health/live
curl --fail --silent --show-error http://127.0.0.1:8000/health/ready
curl --fail --silent --show-error http://127.0.0.1:8081/ >/dev/null
```

Kết quả tối thiểu:

- Manager API: `live=200`, `ready=200`, database và Redis `ok`.
- Voice Server: `live=200`, `ready=200`, ASR/VAD/TTS/LLM/Manager API healthy.
- Manager Web: HTTP `200` ở port 8081.
- 9Router: health và model list thành công trước khi kiểm thử hội thoại.

`/health/ready` của Voice Server có prewarm LLM và local model. Lần khởi động lạnh có
thể mất lâu hơn; không gửi request audio khi chưa ready. Nếu `ready` không pass, đọc
lỗi component trong terminal Voice Server, không tăng thread hoặc mở process thứ hai.

Mở trình duyệt bằng `http://127.0.0.1:8081` trên cùng máy. Với LAN/Tailscale, dùng
địa chỉ host được cấu hình trong Manager/Web, giữ allowlist origin chính xác; không tắt
host checking hoặc expose port 20128 ra LAN.

Realtime Lab có hai mức kiểm thử:

1. Text chat để kiểm tra admission/LLM/MCP/TTS thật mà không cần microphone.
2. Audio Replay/Live Mic để kiểm tra PCM browser path; Live Mic cần HTTPS hoặc
   localhost. Browser PCM không thay thế kiểm thử Opus/AEC/loa ESP32.

Trên mobile, thao tác `Bắt đầu phiên thử` phải unlock/resume `AudioContext` trước khi
Manager cấp token một lần. Nếu banner `Điện thoại chưa cho phép phát âm thanh` còn hiện,
chạm `Bật âm thanh`; không coi session là audio-pass cho tới khi banner biến mất và
console không có playback error. `401 /api/v1/auth/refresh` trước đăng nhập là expected;
`favicon.ico` 404 không liên quan voice. Sau turn, xác nhận timeline có
`admission.final`, `llm.delta`, `tts.start`, `tts.first_audio`, `tts.stop`,
`listen.start` và không có `turn.error`.

### 3.1 Tailscale HTTPS trên host hiện tại

Host đã nghiệm thu dùng userspace `tailscaled`, không phải system service toàn máy:

```text
binary: ~/.local/lib/veetee-tailscale/tailscale_1.98.9_amd64/{tailscale,tailscaled}
socket: /run/user/1000/veetee-tailscaled.sock
state:  ~/.local/state/veetee-tailscale/          # private; không commit
DNS:    veetee-dev.tail52a635.ts.net
```

Kiểm tra đúng daemon/socket thay vì kết luận từ `systemctl status tailscaled`:

```bash
TS="$HOME/.local/lib/veetee-tailscale/tailscale_1.98.9_amd64/tailscale"
SOCK="/run/user/$(id -u)/veetee-tailscaled.sock"
"$TS" --socket="$SOCK" status --self=true --peers=false
"$TS" --socket="$SOCK" serve status --json
"$TS" --socket="$SOCK" funnel status --json
```

Topology đã kiểm tra ngày 2026-07-28:

```text
https://veetee-dev.tail52a635.ts.net:443   -> http://127.0.0.1:8081  Manager Web
https://veetee-dev.tail52a635.ts.net:8443  -> http://127.0.0.1:8001  Manager API
wss://veetee-dev.tail52a635.ts.net:10000   -> http://127.0.0.1:8000  Voice Server
```

Không proxy `20128` (9Router). Manager runtime dùng WSS Lab URL cổng `10000`; HTTPS Web
không đặt `VITE_MANAGER_API_URL` thì dùng same-origin `/api`/`/health` proxy, tránh mixed
content. Vite vẫn phải khởi động với exact allowlist:

```bash
VEETEE_WEB_ALLOWED_HOSTS=veetee-dev.tail52a635.ts.net \
  npm run dev --workspace @veetee/manager-web
```

`VEETEE_MANAGER_CORS_ORIGIN` và `VEETEE_LAB_ALLOWED_ORIGINS` phải chứa exact HTTPS
origin. Không đặt wildcard và không tắt host check. Cấu hình 2026-07-28 có Funnel được
allow/active trên các HTTPS listener, nghĩa là endpoint có thể public Internet chứ không
chỉ private tailnet. Chỉ dùng khi chủ máy chủ ý chấp nhận exposure; để private, chuyển
sang `tailscale serve` theo CLI hiện hành và xác minh lại bằng `serve status` lẫn
`funnel status`. Không reset/đổi Funnel trong lúc có người test mà chưa xác nhận.

## 4. Kiểm tra VieNeu bị ngắt hoặc CPU cao

Trước khi kết luận do code, ghi lại các thông tin không nhạy cảm sau:

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee
printf 'branch='; git branch --show-current
printf 'commit='; git rev-parse --short HEAD
printf 'uptime='; uptime
ps -eo pid,ppid,etimes,%cpu,%mem,rss,nlwp,stat,comm,args --sort=-%cpu | \
  grep -E 'python|uvicorn|node|9router|veetee' | head -30
curl --fail --silent --show-error http://127.0.0.1:8000/health/ready
```

Đọc log đang ở terminal Voice Server và tìm các event bounded sau, không paste transcript
hoặc Authorization header. Nếu app được chạy bằng `nohup` theo local supervisor thủ công,
log hiện nằm dưới ignored `veetee-server/tmp/runtime/app-logs/`; không commit các file này:

- `conversation_tts_text_chunk_ready`
- `conversation_tts_request`, `conversation_tts_first_audio`,
  `conversation_tts_batch_first_audio`
- `vieneu_tts_completed` (`request_wall_rtf`, audio duration, normalized/internal starts,
  clipping; không có text/audio payload)
- `lab_playback_schedule_summary` (browser timeline estimate)
- `tts.paced_sender_summary` (device queue starvation và scheduler lateness)
- `conversation_timeout` / `conversation_provider_deadline`
- `conversation_abort`
- `conversation_turn_error`
- `tts:start` / `tts.stop`
- `generation` hoặc `stale` rejection

Schedule-gap/starvation/lateness là diagnostic phía server/scheduler. Không báo là browser
hay ESP32 speaker underrun nếu chưa có playback acknowledgement/firmware counter.

Một request TTS bị ngắt giữa chừng cần phân biệt:

- `abort`/generation đổi: người dùng hoặc firmware barge-in, output cũ bị bỏ đúng thiết kế;
- `conversation_timeout`/provider deadline: CPU hoặc 9Router/TTS chậm vượt idle deadline;
- WebSocket disconnect: lỗi transport/reconnect hoặc browser audio lifecycle;
- nhiều TTS request nhỏ/punctuation-only: đang chạy code cũ hoặc chunk policy sai;
- process Voice Server có RSS khoảng 1 GiB nhưng CPU cao liên tục khi không có turn: cần
  kiểm tra model prewarm/inference loop và không tăng thread một cách mù quáng.

Với baseline ONNX đã nghiệm thu, giữ các giá trị an toàn: backend `onnx`, speed `1.0`,
TTS threads `2`, ASR threads `2`, VAD threads `1`, sentence batching theo provider,
playback queue `5` giây và `native_use_ref_codes=true`. WSOLA speed từ `1.2x` trở lên
có cảnh báo giảm rõ phụ âm/dấu tiếng Việt; volume PCM trên `1.0` có nguy cơ clipping.

Nếu lỗi chỉ xuất hiện sau reboot, ưu tiên kiểm tra theo thứ tự:

1. process có chạy từ đúng `main`/commit hay worktree cũ không;
2. 9Router đã ready và đúng model chưa;
3. Manager API/Redis/PostgreSQL đã ready chưa;
4. `apps/voice-server/.env` có được sync lại sau rotate key không (chỉ so sánh tên biến,
   không in giá trị);
5. có process duplicate chiếm port hoặc CPU không;
6. cuối cùng mới đổi backend/thread/speed. Không đổi nhiều biến cùng lúc.

### 4.1 Evidence nghiệm thu hiện tại

Ngày 2026-07-28, HTTPS Manager Web/API và Voice Server readiness đều trả `200`; ASR,
VAD, TTS, LLM, Manager API, PostgreSQL và Redis healthy. Mobile/user listening check
đánh giá VieNeu hiện tại là chấp nhận được. Hai turn tự nhiên có turn-first-audio khoảng
1,18--1,72 giây và schedule-gap estimate bằng 0. Hai headless text probes rất ngắn vẫn
pass toàn lifecycle/no-error nhưng first audio 2,51--3,03 giây, request wall RTF
1,695--1,953 và schedule gap estimate 53--1.278 ms (53 ms đến 1,278 giây). Giữ cả hai nhóm evidence để không
cherry-pick; chi tiết và giới hạn nằm ở `docs/15-local-ai-runtime.md`.

A/B long fixture 2.352 ký tự đã giảm outer/internal starts 23 xuống 12 bằng hybrid
first-160/steady-256, nhưng aggregate RTF xấu hơn 0,858 lên 0,870 nên đã rollback.
Baseline ONNX vẫn natural 160 và emergency punctuation-free 256. Không bật lại hybrid,
đổi sampler hoặc tăng buffer nếu chưa có benchmark mới vượt gate.

## 5. Dừng và khởi động lại sạch

Trong từng terminal app, nhấn `Ctrl-C` và chờ process con thoát. Kiểm tra port trước
khi chạy lại:

```bash
ss -lntp | grep -E ':(8000|8001|8081)\\b' || true
```

Không xoá `data/`, `tmp/`, model cache, NVS hay database để “sửa” lỗi nếu chưa có backup
và chưa xác định nguyên nhân. Hạ tầng host-local có lệnh idempotent; chỉ dùng lệnh down
khi thật sự muốn dừng PostgreSQL/Redis:

```bash
npm run infra:host:down
# hoặc nếu đang dùng Docker:
npm run infra:down
```

## 6. Hướng dẫn ngắn cho AI

Khi giao task chạy/điều tra cho AI, dùng prompt này:

> Đọc `AGENTS.md`, `CLAUDE.md` và `docs/21-local-development-runbook.md`. Làm việc từ
> `/home/vubq/Project/EmYeuKhoaHoc/veetee`, kiểm tra branch/commit và working tree trước.
> Dùng đúng thứ tự cold-start, không in hoặc commit `.env`, key, token, transcript/audio.
> Chỉ bắt đầu Realtime Lab/ESP32 sau khi 9Router, Manager API và Voice Server đều
> `/health/ready=200`. Khi voice bị ngắt, ghi CPU/RSS/PID/port và event names/timings đã
> redact; không thay đổi backend/thread/speed nếu chưa có evidence. Không flash/monitor
> firmware, không push/deploy/commit nếu chưa được yêu cầu rõ.

AI chỉ được báo “chạy được” khi ghi rõ: commit đã chạy, process/port, các health check,
command đã chạy, test result, và phần nào vẫn cần nghe trực tiếp trên ESP32.

## 7. Tắt máy

Trước khi tắt máy, dừng ba app bằng `Ctrl-C`. PostgreSQL/Redis và 9Router có thể được
để user service/host runtime quản lý. Sau reboot, quay lại mục 2; không giả định các
process app thủ công tự khởi động chỉ vì systemd của Tailscale/9Router đang chạy.
