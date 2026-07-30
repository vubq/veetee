# Local development runbook

Tài liệu này là cách khởi động Veetee sau khi bật máy, cho cả người phát triển và AI
đang làm việc trong repository. V1 là single-node: các process app/model chạy trực tiếp
trên host; PostgreSQL/Redis dùng host-local runtime hoặc Docker; CLIProxyAPI là LLM
gateway local được gọi qua loopback. 9Router đang tạm dừng và không thuộc startup
profile này. Không chạy Manager API trong đường audio từng frame.

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
ss -lntp | grep -E ':(8317|5432|6379|8000|8001|8081)([^0-9]|$)' || true
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

Device config và resource dev còn cần private Ed25519 signer ignored khớp đúng public
key đã build trong firmware:

```bash
stat -c '%a %n' data/signing/veetee-dev-release-2026-01.pem
# expected: 600; tuyệt đối không in nội dung file
```

Không tự sinh một key khác rồi giữ nguyên firmware: chữ ký sẽ bị device từ chối. Nếu
rotate signer, phải đổi `key_id`/security epoch, cập nhật trust root bằng signed firmware
OTA và publish lại artifact/config theo `docs/12-dynamic-config-and-artifacts.md`.
Local init mới không tự gán một resource manifest `stable`; resource chỉ xuất hiện sau
một release + rollout immutable có signed detector inventory hợp lệ.

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

`env:voice:sync` đọc Manager service token và CLIProxyAPI client key từ trusted local
config, chỉ ghi `apps/voice-server/.env` ignored mode `0600`, không in secret. Nếu
CLIProxyAPI config không có key hoặc Manager `.env` không hợp lệ, sửa dependency đó
trước; không điền key vào source/example hay command line.

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
   origin allowlist và CLIProxyAPI base URL/model/client key; các baseline còn lại đến
   từ example. Script không đọc 9Router store.

Chỉ commit tên biến và giá trị development không nhạy cảm. Không commit/in/log giá trị
của `*_SECRET`, `*_TOKEN`, `*_API_KEY`, `*_PASSWORD`, master/signing key, nội dung
`local-admin.txt`, Authorization header, transcript hoặc audio. Khi kiểm tra effective
config, dùng allowlist tên biến không nhạy cảm thay vì `cat .env`.

Baseline portable phải đồng bộ giữa example, docs và runtime:
`OPENBLAS_NUM_THREADS=1`, ONNX/2 TTS threads, Trúc Ly, `tu_nhien`, speed `1.0`, lead-in
16, watermark bật, 24 kHz, native ref codes bật, playback queue 5 giây, ASR 2 threads,
VAD 1 thread, LLM prewarm 12 giây và planner ceiling 15 giây. OpenBLAS là process-wide
cap phải có trước khi Python import NumPy; `VEETEE_TTS_THREADS=2` chỉ giới hạn ONNX
Runtime. Manager agent snapshot có thể override voice/style/rate/volume và provider
deadlines cho session; nó không đổi process-wide backend/thread count hoặc BLAS cap.
Device-mic admission mặc định yêu cầu 2/3 support độc lập từ RMS `-28 dBFS`, SNR `8 dB`
và VAD mean/ratio `0.55/0.55`; candidate tối đa 3 ký tự/1.200 ms yêu cầu 3/3. Đây là
quality baseline có thể cấu hình, không phải phrase/noise rule và không áp dụng typed text.

`VEETEE_WAKE_AUDIO_PRE_ROLL_MAX_MS=2000` chỉ là trần RAM tạm ở Voice cho sequence
device `listen:detect -> binary -> listen:start`; nó không tự bật thu âm. Quyền gửi vẫn
đến duy nhất từ `send_wake_audio=true` trong wake profile/device snapshot đã ký và mặc
định vẫn tắt.

Sau khi nâng từ schema prototype, `db:deploy` có thể bỏ riêng một `wakeProfile` desired
state còn dùng logical detector alias và tăng version. Đây là recovery fail-closed có
chủ đích: resource, agent, Wi-Fi và identity không đổi; bootstrap trả config ký hợp lệ
với wake profile rỗng để thiết bị vẫn boot/button-only. Không sửa alias trực tiếp trong
database thành một model ID phỏng đoán; publish lại profile qua Manager sau khi artifact
đã khai báo exact signed detector inventory.

| Nhóm biến | Owner/source | Secret | Khi có hiệu lực |
|---|---|---|---|
| `DATABASE_URL`, `REDIS_URL`, Manager host/port/public URL, CORS, Voice WS URLs | Manager `.env`; sinh bởi `env:local:init` lần đầu rồi chỉnh local có chủ đích | URL có thể chứa credential; không in | restart Manager API |
| `VEETEE_AUTH_SECRET`, Lab/device token secret, master key, internal service token, bootstrap password | Manager `.env` ignored `0600` | bắt buộc secret | restart/rotate theo runbook; không sync ra Web/firmware |
| `VEETEE_CLIPROXY_API_KEY`, `VEETEE_MANAGER_INTERNAL_TOKEN` | `env:voice:sync` lấy từ trusted local stores | bắt buộc secret | restart Voice Server sau sync |
| `OPENBLAS_NUM_THREADS`, ASR/VAD/TTS backend/thread/model path, prewarm/planner, inactivity, queue | `apps/voice-server/.env.example` -> ignored Voice `.env` | không, trừ provider key | process-wide; phải có trước Python startup, restart Voice Server |
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

Hạ tầng và CLIProxyAPI là dependency riêng. Giữ PostgreSQL/Redis và CLIProxyAPI chạy
trong suốt các vòng restart app. Kiểm tra process/port trước để không mở gateway thứ hai:

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee/veetee-server
npm run infra:host:up                 # idempotent, nếu dùng host-local
systemctl --user --no-pager --type=service --state=running | grep -Ei 'cliproxy' || true
ss -lntp | grep -E ':8317([^0-9]|$)' || true
```

CLIProxyAPI được quản lý ngoài repository; nếu port `8317` chưa có owner, khởi động nó
theo cấu hình local đã cài thay vì đoán command hoặc mở process duplicate. Veetee gọi
`http://127.0.0.1:8317/v1`; gateway phải có client-key authentication. Không in key và
không proxy port `8317` ra LAN, Tailscale hay public ingress. `env:voice:sync` xác nhận
trusted config có key; Voice prewarm thực hiện authenticated inference. Voice Server sẽ
không ready nếu model catalog/inference upstream không hoạt động. Port `20128` phải để
trống trong profile này.

Trên host baseline, kiểm tra power profile trước khi benchmark hoặc mở Voice Server:

```bash
powerprofilesctl get
# Khi đang cắm AC và cần nghiệm thu realtime:
powerprofilesctl set performance
powerprofilesctl get
```

Ngày 2026-07-29, `power-saver` giữ CPU quanh 900 MHz và làm cùng VieNeu profile có RTF
2,695 / estimated starvation 2,746 giây; `performance` cho RTF 0,804 và starvation 0.
Đây là host-runtime requirement đã đo, không phải lý do tăng TTS thread. Nếu máy đang dùng
pin hoặc người vận hành không muốn đổi profile, dừng realtime acceptance và ghi rõ giới
hạn thay vì tuning app để che nó. Có thể trả về `balanced` sau khi không còn chạy local
AI; task yêu cầu để stack realtime hoạt động thì giữ `performance` trong thời gian đó.
Power profile chỉ là một precondition: A/B dài cùng ngày còn tìm thấy OpenBLAS
oversubscription độc lập. Luôn xác minh cap bằng effective env và `/proc/<pid>/environ`;
không kết luận mọi slow/high-CPU đều do `power-saver`.

Mở ba terminal app riêng, hoặc dùng process supervisor tương đương. Chạy đúng thứ tự
sau khi các health check dependency trước đã pass:

**Terminal 1 — Manager API (control plane, port 8001):**

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee/veetee-server
npm run dev --workspace @veetee/manager-api
```

Chờ `http://127.0.0.1:8001/health/ready` trả `200` trước khi mở Voice Server.

**Terminal 2 — Voice Server (hot path, port 8000):**

Sau khi Manager API ready và CLIProxyAPI đang có owner ở `8317`, render lại Voice `.env`.
Lệnh này lấy Manager internal token và CLIProxyAPI client key từ trusted local stores,
đồng thời khôi phục các backend/thread mặc định từ
`apps/voice-server/.env.example`; chỉnh tay trước đó có thể bị thay thế.

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee/veetee-server
npm run env:voice:sync
stat -c '%a %n' apps/voice-server/.env
# expected: 600 apps/voice-server/.env

python3 - <<'PY'
from pathlib import Path

allowed = {
    "OPENBLAS_NUM_THREADS",
    "VEETEE_ASR_THREADS",
    "VEETEE_VAD_THREADS",
    "VEETEE_ADMISSION_MIN_SIGNAL_SUPPORTS",
    "VEETEE_ADMISSION_STRONG_SIGNAL_RMS_DBFS",
    "VEETEE_ADMISSION_CLEAN_SNR_DB",
    "VEETEE_ADMISSION_DENSE_VAD_MEAN_PROBABILITY",
    "VEETEE_ADMISSION_DENSE_VAD_SPEECH_RATIO",
    "VEETEE_ADMISSION_SHORT_TRANSCRIPT_CHARACTERS",
    "VEETEE_ADMISSION_SHORT_UTTERANCE_MS",
    "VEETEE_ADMISSION_SHORT_MIN_SIGNAL_SUPPORTS",
    "VEETEE_ADMISSION_CONTEXTUAL_VAD_THRESHOLD_FACTOR",
    "VEETEE_ADMISSION_CONTEXTUAL_VAD_PEAK_PROBABILITY",
    "VEETEE_TTS_THREADS",
    "VEETEE_TTS_BACKEND",
    "VEETEE_TTS_VOICE",
    "VEETEE_TTS_STYLE",
    "VEETEE_TTS_SPEED",
    "VEETEE_TTS_STREAM_LEADIN_FRAMES",
    "VEETEE_TTS_OUTPUT_SAMPLE_RATE",
    "VEETEE_TTS_APPLY_WATERMARK",
    "VEETEE_TTS_PLAYBACK_QUEUE_SECONDS",
    "VEETEE_CLIPROXY_BASE_URL",
    "VEETEE_CLIPROXY_MODEL",
    "VEETEE_LLM_PREWARM",
    "VEETEE_LLM_PREWARM_SECONDS",
    "VEETEE_PLANNER_SECONDS",
}
values = {}
for raw_line in Path("apps/voice-server/.env").read_text().splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    if key in allowed:
        values[key] = value
for key in sorted(allowed):
    print(f"{key}={values.get(key, '<missing>')}")
PY

npm run dev:voice
```

Đoạn kiểm tra chỉ in allowlist không nhạy cảm; không thay bằng `cat`, `env` hoặc grep toàn
file. `dev:voice` pin cap ở npm command và cũng dùng `uv --env-file`, nên OpenBLAS được
đặt trước khi Python/NumPy khởi tạo ngay cả khi shell cha có giá trị khác; đặt biến muộn
bên trong Pydantic settings không có giá trị tương đương. Chờ log
`vieneu_tts_prewarm_complete` và `/health/ready=200` rồi mới mở phiên audio.

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

voice_pid=$(ss -H -lntp 'sport = :8000' | sed -nE 's/.*pid=([0-9]+).*/\1/p' | head -n1)
test -n "$voice_pid"
tr '\0' '\n' < "/proc/$voice_pid/environ" | \
  grep -E '^(OPENBLAS_NUM_THREADS=1|VEETEE_TTS_THREADS=2)$'
```

Kết quả tối thiểu:

- Manager API: `live=200`, `ready=200`, database và Redis `ok`.
- Voice Server: `live=200`, `ready=200`, ASR/VAD/TTS/LLM/Manager API healthy.
- Manager Web: HTTP `200` ở port 8081.
- CLIProxyAPI: authenticated LLM prewarm thành công qua loopback trước khi kiểm thử hội thoại.

`/health/ready` của Voice Server có prewarm LLM và local model. TTS chỉ được coi là đã
prewarm sau khi một fixed phrase không nhạy cảm chạy hết đường synthesis thật và toàn bộ
PCM được drain/discard trong provider; warmup này không phát tới Lab, browser, device hay
loa. Mỗi process thành công có đúng một log `vieneu_tts_prewarm_complete`. CPU cao trước
log đó là bình thường nếu kết thúc sau prewarm. Log/readiness chứng minh đường chức năng
sinh được PCM, nhưng không tự bảo đảm realtime: effective process phải có
`OPENBLAS_NUM_THREADS=1`; với host baseline đang ở `performance`, prewarm/request RTF
phải gần hoặc dưới `1`, và benchmark phải không có estimated starvation. CPU idle cao kéo
dài hoặc request đầu sau readiness vẫn có RTF `2--3` / first audio `3--4` giây thì kiểm
tra cap OpenBLAS, process duplicate và `powerprofilesctl get` trước. Lần khởi động lạnh có
thể mất lâu hơn; không gửi request audio khi chưa ready. Nếu `ready` không pass, đọc lỗi
component trong terminal Voice Server, không tăng thread hoặc mở process thứ hai.

Mở trình duyệt bằng `http://127.0.0.1:8081` trên cùng máy. Với LAN/Tailscale, dùng
địa chỉ host được cấu hình trong Manager/Web, giữ allowlist origin chính xác; không tắt
host checking hoặc expose port `8317` ra LAN.

Realtime Lab có hai mức kiểm thử:

1. Text chat để kiểm tra admission/LLM/MCP/TTS thật mà không cần microphone.
2. Audio Replay/Live Mic để kiểm tra PCM browser path; Live Mic cần HTTPS hoặc
   localhost. Browser PCM không thay thế kiểm thử Opus/AEC/loa ESP32.

### 3.1 Probe và chạy YouTube Music

Voice local được enable bằng `VEETEE_MEDIA_PROVIDER=youtube_music`. `env:voice:sync`
lấy giá trị này từ `.env.example`, vẫn giữ `OPENBLAS_NUM_THREADS=1`; `yt-dlp` nằm trong
`uv.lock`, còn FFmpeg là dependency host phải có trước startup. Không cài/cập nhật
extractor trong lúc Voice đang phục vụ nếu chưa probe read-only:

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee/veetee-server
command -v ffmpeg
uv sync --project apps/voice-server --all-groups
npm run media:probe:youtube -- \
  --title "<tên bài>" --artist "<nghệ sĩ>" --decode-seconds 5
```

Expected là JSON `status=decoded`, provider item ID 11 ký tự, PCM 24 kHz và số byte
lớn hơn 0; không có warning subprocess transport và không tạo media file. Dùng `--query`
thay cho title/artist để kiểm `any_track`. Nếu specific search trả `needs_selection`,
probe không tự chọn trừ khi operator thêm `--accept-first`; trong hội thoại AI hỏi lại
dựa trên alternatives/context.

Sau restart, `/health/ready` có component optional `youtube-music=healthy` và catalog
session mới có `media.play` với `operationClass=streaming`. Test một lượt metadata-only,
một lượt decode/play và một lượt button/abort giữa bài; abort phải có đúng một
`tts.stop`, không PCM stale, không còn process `yt-dlp`/FFmpeg mồ côi. Tool không nhận
URL, cookie hay downloader argument từ AI. Không dùng cơ chế bypass DRM/private/
age-restricted content; khi YouTube thay đổi, pin phiên bản mới chỉ sau probe + test
cancellation, không fallback sang scraper/arbitrary URL.

Lượt play hoàn chỉnh phải có thứ tự `planner -> LLM/TTS before -> mcp.start -> media
tts.start/audio/tts.stop -> LLM/TTS after -> listen.start`. Phase `before` là thông báo
ngắn rằng stream sắp bắt đầu; phase `after` đọc kết quả thật và khi `played/completed`
thì báo đã phát hết, đồng thời hỏi tự nhiên có muốn nghe thêm hay không. Câu chữ do LLM
tạo theo persona/locale, không hard-code trong media adapter. Nếu phase `before` không
phát được audio thì fail-closed trước dispatch. Abort ở media lifecycle hủy turn nên
không phát câu completion giả, nhưng vẫn phải có cancelled `tts.stop`, `abort.complete`,
`listen.start` và zero child process.

Anonymous mode có thể trả `youtube_music_rate_limited` do `429`/bot challenge. Không
retry loop vì sẽ kéo dài block. Nếu operator chọn authenticated mode, export Netscape
cookies từ một tài khoản YouTube riêng, đặt file ngoài repo với mode `0600`, rồi cấu hình
`VEETEE_MEDIA_YOUTUBE_COOKIE_FILE=/absolute/private/path`. Không trỏ vào browser profile
cá nhân, không commit/backup chung/log cookie path hoặc content, và không dùng cookie để
bypass DRM/private/age-restricted content. Chạy lại probe một lần sau cấu hình; nếu vẫn
fail thì disable provider hoặc chờ upstream, không đổi qua proxy/scraper tùy ý.

Khi render lại local env, truyền path qua process environment ở lần đầu; các lần sau
`env:voice:sync` giữ giá trị không rỗng đang có trong Voice `.env` ignored, nhưng process
environment luôn có ưu tiên cao hơn. Script không in path. YouTube có thể trả `403` cho
DASH audio dù metadata/challenge đã pass; adapter ưu tiên HLS có audio với băng thông
thấp rồi mới fallback về audio-only và dùng FFmpeg downloader qua stdout để không để
fragment HLS trong working directory. Đây là selector nội bộ cố định, không phải option
được expose cho AI. Sau abort, chỉ chấp nhận PCM đã in-flight trước `abort.complete`;
sau event này phải có zero PCM và zero child `yt-dlp`/FFmpeg.

Trên mobile, thao tác `Bắt đầu phiên thử` phải unlock/resume `AudioContext` trước khi
Manager cấp token một lần. Nếu banner `Điện thoại chưa cho phép phát âm thanh` còn hiện,
chạm `Bật âm thanh`; không coi session là audio-pass cho tới khi banner biến mất và
console không có playback error. `401 /api/v1/auth/refresh` trước đăng nhập là expected;
`favicon.ico` 404 không liên quan voice. Sau turn, xác nhận timeline có
`admission.final`, `llm.delta`, `tts.start`, `tts.first_audio`, `tts.stop`,
`listen.start` và không có `turn.error`.

### 3.2 Smoke hội thoại và soak output dài representative

Chỉ dùng Realtime Lab đã được Manager cấp one-use token hoặc ESP32 đã authenticated;
không bypass auth và không publish/ghi đè agent version chỉ để chạy soak. Giữ active
agent có CLIProxyAPI primary; nếu mục tiêu là nghiệm thu primary thì mọi fallback đều phải
được báo, không được tính chung thành primary pass.

Mặc định không giới hạn response, truyện hoặc nội dung đọc từ file ở 5 hay 10 phút:
`max_session_seconds=0`, `total_turn_seconds=0`, `llm_total_seconds=0` và
`tts_total_seconds=0`. First-token/first-audio deadline chỉ chặn startup treo;
stream-idle deadline được refresh theo mỗi token/audio chunk. Speech queue và playback
queue bounded áp backpressure để RAM không tăng theo độ dài output; queue 5 giây là
buffer, không phải giới hạn thời lượng nói.

`maxCompletionTokens` là giới hạn tài nguyên cho một request CLIProxyAPI, không phải
giới hạn số phút. Nếu `conversation_llm_stream_complete` có
`finish_reason=length|max_tokens`, cycle chưa hoàn tất dù phần TTS partial nghe được:
runtime phải drain phần đó, báo `llm_output_truncated` và không ghi partial context/
memory. Nội dung generated tùy ý dài cần segment/cursor resume explicit; đọc file dài
phải stream source text theo offset qua sentence chunker -> TTS, không nhét cả file vào
prompt/RAM hoặc tăng token ceiling vô hạn.

Chạy ba bài riêng trên cùng process đã ready:

1. **Hội thoại thường:** ít nhất ba turn ngắn liên tiếp. Mỗi turn phải có planner + prose,
   một lifecycle `tts.start` -> binary PCM -> `tts.stop`, rồi `listen.start` để nhận turn
   tiếp theo. Không chỉ kiểm tra WebSocket connect.
2. **Kể chuyện dài representative:** yêu cầu một response bắt đầu kể ngay, dùng câu tự
   nhiên và tạo ít nhất 5 phút audio; 5--10 phút chỉ là cửa sổ soak thuận tiện. Gate
   theo **thời lượng PCM thật**, không theo số ký tự prompt/response hay wall time. Với
   PCM mono signed 16-bit ở 24 kHz, thời lượng là
   `binary_pcm_bytes / (24000 * 2)`. `>=300` giây là pass về độ dài; output trên 600
   giây vẫn hợp lệ và phải tiếp tục nếu stream còn progress.
3. **Synthetic dài hơn 10 phút:** dùng progressive fake LLM/TTS để test nhanh output
   khoảng 4.200 từ với queue/context rất nhỏ và idle deadline ngắn. Bài này chứng minh
   không có absolute cutoff và memory tail vẫn bounded; nó không thay thế nghe PCM/loa
   thật.

Chạy fixture synthetic mà không gọi provider hoặc lưu transcript/audio:

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee/veetee-server
OPENBLAS_NUM_THREADS=1 uv run --project apps/voice-server \
  pytest apps/voice-server/tests/test_conversation_engine.py -q \
  -k 'long_but_active_stream_is_unbounded_and_retains_only_context_tail or truncated_llm_stream_is_reported_and_not_committed_as_memory or llm_idle_deadline_does_not_limit_long_tts_backpressure or tts_idle_deadline_refreshes_while_audio_keeps_progressing'
```

Trong khi chạy, lấy mẫu theo interval thay vì đọc một snapshot `%CPU`:

```bash
voice_pid=$(ss -H -lntp 'sport = :8000' | sed -nE 's/.*pid=([0-9]+).*/\1/p' | head -n1)
test -n "$voice_pid"
pidstat -p "$voice_pid" 1
# Ctrl-C sau khi đã lấy cả synthesis tail; nếu pidstat chưa cài, dùng sampler delta tương đương.
```

Ghi lại dữ liệu bounded, không lưu transcript/audio:

- provider adapter/model, HTTP status và số request planner/prose; xác nhận port `8317`
  là CLIProxyAPI owner và `20128` không có listener;
- request -> first LLM delta, `tts.start`, first binary, last binary, `tts.stop`,
  `listen.start`, PCM frame/byte/audio-duration và wall/audio;
- `conversation_llm_stream_complete.finish_reason`; `length|max_tokens` phải được ghi
  là incomplete/`llm_output_truncated`, không được tính thành long-turn pass;
- `schedule_gap_count`, tổng/max gap, low-water, turn error, deadline, stale/cancel marker;
- Voice CPU interval avg/p95/peak, RSS đầu/peak/cuối/tail, thread count, host frequency,
  temperature/throttle guard và tải nền có thể làm nhiễu phép đo.

Acceptance cho server/Lab:

- ba normal turns và long turn đều kết thúc bằng `tts.stop`; normal turn quay lại
  `listen.start`, không có stale output hoặc turn error;
- long turn có ít nhất 300 giây PCM, zero scheduler gap và không có provider deadline;
  không fail/truncate chỉ vì vượt 600 giây;
- khi đang nghiệm thu CLIProxyAPI primary, planner/prose đều đi local CLIProxyAPI HTTP
  200 và không fallback; 9Router vẫn dừng;
- sau event `vieneu_tts_completed` cuối cùng của turn, CPU interval của Voice phải về
  gần idle ở sample kế tiếp và muộn nhất trong cửa sổ 2 giây; RSS tail 30--60 giây
  phẳng hoặc plateau qua các turn tương đương; không có orphan/duplicate;
- retry, fallback hoặc deadline làm cycle ban đầu fail và phải được báo riêng. Chỉ chạy
  một controlled retry khi task cho phép, không cherry-pick hoặc giấu lần fail.

Binary cuối có thể tới trước `tts.stop` trong lúc browser/device drain phần audio đã
buffer. Đây không phải inference còn chạy nếu `vieneu_tts_completed` đã có, interval CPU
đã idle và queue đang giảm đúng thời lượng. Ngược lại, CPU còn cao mà không có progress
event là lỗi cần điều tra. `lab_playback_schedule_summary` chỉ là scheduler evidence;
nghe browser và loa ESP32 ít nhất 5 phút vẫn là acceptance vật lý riêng.

Với Device WebSocket sink 60 ms và `VEETEE_TTS_PLAYBACK_QUEUE_SECONDS=5`, nếu queue đầy
ở lúc inference cuối hoàn tất thì paced sender thường cần khoảng 5,1 giây để drain rồi
mới phát JSON `tts.stop` ra wire (5 giây queue cộng frame/scheduling overhead nhỏ).
Telemetry engine `tts.stop` được ghi khi yêu cầu stop đi vào sink, trước drain;
`tts.paced_sender_summary`/`listen.start` mới đánh dấu sink đã drain. CPU phải idle
trong drain này. Delay dài hơn đáng kể ở Device sink cần kiểm queue/progress;
Realtime Lab có thể giữ toàn bộ browser playback timeline nên khoảng last-PCM tới
`tts.stop` của Lab có thể dài hơn nhiều và không dùng gate 5,1 giây này.

### 3.3 Tailscale HTTPS trên host hiện tại

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

Không proxy `8317` (CLIProxyAPI); port `20128` của 9Router đang tạm dừng. Manager runtime
dùng WSS Lab URL cổng `10000`; HTTPS Web
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
  grep -E 'python|uvicorn|node|cli-proxy-api|veetee' | head -30
curl --fail --silent --show-error http://127.0.0.1:8000/health/ready

voice_pid=$(ss -H -lntp 'sport = :8000' | sed -nE 's/.*pid=([0-9]+).*/\1/p' | head -n1)
test -n "$voice_pid"
tr '\0' '\n' < "/proc/$voice_pid/environ" | \
  grep -E '^(OPENBLAS_NUM_THREADS=1|VEETEE_TTS_THREADS=2)$'
pidstat -p "$voice_pid" -u -r -w 1 5
```

`ps %CPU` là trung bình tích lũy từ lúc process khởi động nên có thể giảm rất chậm sau
một burst TTS; không dùng nó để kết luận CPU hiện vẫn bận. `pidstat` ở trên đo interval;
trên Linux `100%` tương đương một logical CPU, nên `188%` là khoảng 1,88 core. Nếu host
không có `pidstat`, dùng sampler delta theo `/proc` hoặc công cụ interval tương đương.
Đo process Voice riêng với global CPU/I/O/thermal; không tự dừng desktop, GPU, RustDesk
hoặc process ngoài Veetee chỉ vì chúng làm nhiễu phép đo.

Đọc log đang ở terminal Voice Server và tìm các event bounded sau, không paste transcript
hoặc Authorization header. Nếu app được chạy bằng `nohup` theo local supervisor thủ công,
log hiện nằm dưới ignored `veetee-server/tmp/runtime/app-logs/`; không commit các file này:

- startup `vieneu_tts_prewarm_complete` (profile, counts, duration, RTF; không có phrase/audio)
- `conversation_tts_text_chunk_ready`
- `conversation_tts_request`, `conversation_tts_first_audio`,
  `conversation_tts_batch_first_audio`
- `conversation_llm_stream_complete` (`finish_reason`, `incomplete`) và
  `conversation_llm_output_truncated`; không log prose payload
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
- `conversation_timeout`/provider deadline: CPU hoặc CLIProxyAPI/TTS chậm vượt idle deadline;
- WebSocket disconnect: lỗi transport/reconnect hoặc browser audio lifecycle;
- nhiều TTS request nhỏ/punctuation-only: đang chạy code cũ hoặc chunk policy sai;
- process Voice Server có RSS khoảng 1 GiB nhưng CPU cao liên tục khi không có turn: cần
  kiểm tra model prewarm/inference loop và không tăng thread một cách mù quáng.

RSS khoảng 1 GiB không bắt buộc giảm sau turn vì model resident và OpenBLAS/ONNX arena
giữ high-water. So sánh plateau qua nhiều turn tương đương và tail 30--60 giây; chỉ một
snapshot RSS cao không chứng minh leak. Tương tự, `tts.stop` có thể tới muộn hơn PCM cuối
do playback drain; nếu interval CPU đã idle và synthesis complete thì đó không phải TTS
vẫn inference.

Với baseline ONNX đã nghiệm thu, giữ các giá trị an toàn: backend `onnx`, speed `1.0`,
OpenBLAS threads `1`, TTS/ONNX threads `2`, ASR threads `2`, VAD threads `1`, sentence
batching theo provider, playback queue `5` giây và `native_use_ref_codes=true`. WSOLA
speed từ `1.2x` trở lên có cảnh báo giảm rõ phụ âm/dấu tiếng Việt; volume PCM trên `1.0`
có nguy cơ clipping.

Nếu lỗi chỉ xuất hiện sau reboot, ưu tiên kiểm tra theo thứ tự:

1. process có chạy từ đúng `main`/commit hay worktree cũ không;
2. live process có đúng `OPENBLAS_NUM_THREADS=1` và `VEETEE_TTS_THREADS=2` không;
3. có process duplicate chiếm port hoặc CPU không;
4. CLIProxyAPI có owner ở `8317`, authenticated prewarm pass và đúng model chưa;
5. Manager API/Redis/PostgreSQL đã ready chưa;
6. `apps/voice-server/.env` có được sync lại sau rotate key không (chỉ so sánh tên biến,
   không in giá trị), power profile/tải nền/thermal có đúng điều kiện benchmark không;
7. cuối cùng mới đổi backend/thread/speed/buffer. Không đổi nhiều biến cùng lúc.

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

Ngày 2026-07-29, real prewarm + ba request fresh-process ở power profile `performance`
đạt first audio median 1,047 giây, RTF 0,804 và estimated starvation 0. Fixture 2.374 ký
tự (`sha256[:16]=ff611923af5ccce5`) chạy 22 batch, first audio 1,260 giây, aggregate
RTF 0,812 và starvation 0. Local Opus E2E cũng pass lifecycle TTS với request RTF 0,817.
Sau khi route 9Router cũ không còn được chọn, agent version 4 đã publish chain
`openai-compatible-cliproxyapi:gpt-5.6-terra -> groq-cloud:llama-3.3-70b-versatile`.
Đây là evidence lịch sử của lần đo đó, không còn là default vận hành: quyết định sau
cùng giữ agent hiện hành ở CLIProxyAPI-only và không seed/publish Groq fallback.
`env:voice:sync` và default readiness cũng dùng CLIProxyAPI trực tiếp; 9Router không còn
là dependency khởi động. Một `provider_deadline` của CLIProxyAPI vẫn làm cycle fail dù
TTS cleanup đúng; không tăng TTS thread/deadline để che lỗi upstream.

A/B dài ngày 2026-07-29 cho thấy nếu không cap OpenBLAS, CPU Voice avg/p95/peak là
584/690/714%, 34 threads và 112 schedule gap (62,4 giây); wall/audio 1,285. Với
`OPENBLAS_NUM_THREADS=1`, CPU còn 124/174/208%, 27 threads, zero gap và wall/audio
1,022; CPU work/audio giảm khoảng 83%. Long-story qua CLIProxyAPI sau đó tạo 308,56
giây PCM/323 frame, zero gap/error; CPU 120,9/174/188,3% và RSS
1055,5 -> 1058,8 MiB rồi plateau. PCM cuối đến `tts.stop` cách khoảng 61 giây do Lab
playback drain; CPU đã idle. Ba turn thường chạy trước long soak trong cùng validation
window dùng sáu CLIProxyAPI POST, tổng 20,16 giây PCM, zero gap/error và mỗi `tts.stop`
-> `listen.start` khoảng 0,1 ms. Gate “ba turn sau long response” vẫn phải chạy lại khi
nghiệm thu release theo mục 3.1, không được suy ra từ thứ tự phép đo này.

Ba cold restart cycle cũng pass: startup/readiness khoảng 7,8--9,7 giây, mỗi process có
đúng một LLM prewarm và một TTS prewarm, shutdown sạch, không orphan, idle CPU
0,1--0,3%. Đây là dated evidence của host này, không thay thế việc chạy lại gate sau khi
model, dependency, agent config hoặc phần cứng đổi.

Gate release cuối ngày 2026-07-29 đã chạy lại sau một full-stack restart với
`OPENBLAS_NUM_THREADS=1`, `VEETEE_TTS_THREADS=2` và CLIProxyAPI
`maxCompletionTokens=2048` (fixture kể chuyện dài này cần ít nhất `2048`; mức `1024`
cắt câu trả lời quá sớm). Đây chỉ là per-request budget của fixture lịch sử, không phải
duration cap; terminal `length|max_tokens` hiện phải bị ghi incomplete như mục 3.1.
Lượt dài sinh 8.407 ký tự, 428 PCM frame tương đương
481,44 giây audio, zero schedule gap/error, rồi ba lượt thường liên tiếp đều hoàn tất
`tts.stop -> listen.start`. Trong lúc sinh audio, CPU Voice avg/p95/peak là
154,80/167/174%; toàn cửa sổ soak là 78,12/166/174%. Sau synthesis, 300 mẫu idle dưới
2% có trung bình 0,140% và 10 mẫu cuối đều 0,000%. Process giữ 27 thread; RSS tăng từ
853.672 KiB tới 1.057.424 KiB do model/arena resident nhưng 300 giây cuối chỉ đổi
1.120 KiB, tức plateau thay vì leak. PCM cuối tới `tts.stop` chậm khoảng 204,87 giây
do Realtime Lab drain audio đã buffer theo thời lượng phát; CPU đã về idle nên không
phải VieNeu còn inference. Có một cycle trước đó chạm đúng CLIProxyAPI deadline 15 giây
khi proxy đồng thời phục vụ nhiều request Codex dài; cycle đó bị tính fail và được báo
riêng, không tăng deadline hay che bằng fallback.

Lần recheck cuối trên merged tree cùng ngày dùng agent `Veetee Việt` version 6 đã
publish với đúng một LLM binding
`openai-compatible-cliproxyapi:gpt-5.6-terra`; resolver/DB không có Groq fallback.
Runtime giữ nguyên cấu hình nói trên và không chạy build khác trong cửa sổ đo. Cycle
đầu bị fail rõ ràng vì prose CLIProxyAPI của lượt dài
không có first token trong budget 5 giây dù HTTP đã trả `200`; không Groq/9Router
fallback và không tăng deadline. Một controlled retry có nhãn riêng pass hai warmup,
một stream LLM 8.086 ký tự tạo 416 PCM frame / 464,56 giây audio và ba follow-up bình
thường. Cả sáu turn của retry đều có planner + prose qua `127.0.0.1:8317` HTTP `200`,
`tts.stop -> listen.start`, zero schedule gap/error/stale; port `20128` vẫn trống.
CPU Voice ở riêng long-generation avg/p95/peak là 151,184/169/178%, còn toàn retry là
82,064/165/178%. Sau event synthesis cuối có một mẫu chuyển tiếp 32%, mẫu kế tiếp là
0%; quãng 202,512 giây từ PCM cuối tới `tts.stop` chỉ avg 0,274%, p95 1%, nên đó là
playback drain. RSS toàn sampler 834,29 MiB -> peak 1.044,54 MiB -> tail 1.020,93 MiB;
61 mẫu cuối không đổi một KiB, và thread count giữ 27 từ đầu tới cuối.

Device-session recheck ngày 2026-07-30 với cùng cap process ghi 30 mẫu 1 giây trong
generation TTS active: CPU avg/p95/peak `119,633/157/165%`, 27 thread, RSS mẫu
`1.133.320--1.157.964 KiB` và high-water `1.158.036 KiB`. Từ telemetry engine
`tts.stop` tới `tts.paced_sender_summary`/`listen.start` là `5,115 s`, đúng với queue
5 giây; starvation bằng 0, scheduler lateness 2 lần/tổng 44 ms/max 27 ms. Tail 20 giây
sau drain có CPU trung bình `2,75%`. Sampler bị thiếu khoảng giữa synthesis và drain,
nên lần đo này **không** chứng minh exact first post-inference sample hoặc gate <=2 giây;
gate đó phải được lấy lại ở lần release kế tiếp. `5,115 s` là server paced-queue drain,
không phải acknowledgement rằng loa ESP32 đã phát xong.

## 5. Dừng và khởi động lại sạch

Dùng đúng terminal/process handle đã mở; không dùng `pkill` rộng. Thứ tự dừng app là
Manager Web -> Voice Server -> Manager API. Giữ CLIProxyAPI, PostgreSQL và Redis chạy
giữa các cycle. Trong từng terminal nhấn `Ctrl-C`, chờ toàn bộ process con thoát, rồi
xác nhận ba port app đã rảnh và `8317` vẫn có đúng một owner:

```bash
ss -lntp | grep -E ':(8317|8000|8001|8081)([^0-9]|$)' || true
```

Để nghiệm thu cold-start/restart, giữ PostgreSQL/Redis chạy và thực hiện ba cycle độc lập:

1. Xác nhận ba port app đang rảnh, `8317` có đúng một CLIProxyAPI owner và `20128` trống.
2. Start Manager API, chờ `/health/ready=200`.
3. Chạy `env:voice:sync`, kiểm tra mode `0600` và allowlist không nhạy cảm, rồi start
   Voice Server. Xác nhận live process có `OPENBLAS_NUM_THREADS=1`; yêu cầu đúng một
   `llm_prewarm_complete`, một `vieneu_tts_prewarm_complete` và
   `/health/ready=200` với ASR/VAD/TTS/LLM/Manager healthy.
4. Start Manager Web, yêu cầu HTTP `200` ở `8081`, rồi kiểm tra mỗi port chỉ có một owner.
5. Chạy một Text Lab turn được cấp quyền. Ở cycle cuối, thêm response nhiều TTS batch.
   Chỉ ghi event name, response character count/hash, first-audio, request RTF,
   schedule-gap summary, trạng thái `tts.stop`, PID/CPU/RSS/thread và idle CPU; không ghi
   transcript, audio, token, key hay Authorization header.
6. Reject cycle nếu có provider deadline, turn error, stale output, thiếu terminal
   `tts.stop`, duplicate process, cold RTF `2--3` sau readiness hoặc CPU cao kéo dài khi
   turn đã kết thúc.
7. Dừng ba app theo thứ tự reverse nêu trên, yêu cầu ba port app rảnh và giữ CLIProxyAPI
   ở `8317` trước cycle tiếp theo.

Sau cycle thứ ba, nếu mục tiêu của task là để project sẵn sàng sử dụng, khởi động lại một
lần cuối theo đúng thứ tự và để CLIProxyAPI, Manager API, Voice Server và Manager Web
chạy ở các port chuẩn. Báo PID/health cuối cùng; không gọi một cycle là pass nếu chỉ có
`/health/live` mà chưa có readiness và turn thật.

Không xoá `data/`, `tmp/`, model cache, NVS hay database để “sửa” lỗi nếu chưa có backup
và chưa xác định nguyên nhân. Hạ tầng host-local có lệnh idempotent; chỉ dùng lệnh down
khi thật sự muốn dừng PostgreSQL/Redis:

```bash
npm run infra:host:down
# hoặc nếu đang dùng Docker:
npm run infra:down
```

### 5.1 Boot firmware sau khi đổi layout device-config NVS

`DeviceConfigRecord` hiện có schema version 2 và kích thước 348 byte. Board từng chạy
prototype có thể còn blob `veetee_config/state` 508 byte dù schema cũ cũng mang version
1. Firmware phải probe type/length trước khi đọc và chỉ thay record config không tương
thích; không erase NVS partition hoặc namespace `veetee`, vì Wi-Fi và device identity
nằm ở store riêng và phải được giữ lại. Nhánh tạo default khởi tạo thẳng vào record
persistent; `CONFIG_ESP_MAIN_TASK_STACK_SIZE=8192` là boot-time headroom cho migration,
không phải lý do tăng stack các task audio/realtime.

Khi flash bản mới lên board đã provision/pair, không dùng `erase-flash`. Kiểm tra hai
lần boot độc lập:

1. Boot đầu có thể ghi đúng một lần
   `Replacing incompatible device-config blob stored_bytes=508 expected_bytes=348 ...`.
   Phải đồng thời còn `provisioned=yes paired=yes`, không có stack overflow/panic/watchdog
   và state đi qua `activating` tới `idle`.
2. Reset/reboot lần hai không còn log replace; record 348 byte/version 2 phải load bình
   thường, Wi-Fi/identity vẫn còn và firmware lại tới `idle`.

Nếu một gate fail, giữ nguyên NVS để điều tra và lưu reset reason, blob status/length,
boot log cùng state transition đã redact. Không chữa reboot loop bằng full erase trước
khi xác nhận record nào lỗi; host test migration chỉ chứng minh policy, không thay thế
hai boot trên ESP32-S3 thật.

### 5.2 Firmware RAM/CPU A/B và wake từ loa host

Chạy host regression trước khi flash. Mỗi build A/B dùng directory/sdkconfig riêng;
chi tiết profile và command đầy đủ nằm ở `veetee-firmware/profiles/README.md`:

```bash
cd /home/vubq/Project/EmYeuKhoaHoc/veetee/veetee-firmware
cmake -S tests -B build/host-tests
cmake --build build/host-tests -j2
ctest --test-dir build/host-tests --output-on-failure

source /home/vubq/.espressif/v6.0.2/esp-idf/export.sh
idf.py -DIDF_TARGET=esp32s3 build
```

Thứ tự A/B bắt buộc:

1. `mem-control` + 160 MHz, stats off.
2. Chỉ khi cần điều tra một incident đã tái hiện, chạy `mem-t2-r64` + 160 MHz
   stats off như reproduction; **không** coi đây là candidate release. Board A/B
   ngày 2026-07-30 đã loại profile này sau WebSocket hello/`listening` rồi
   `LoadProhibited` trong `esp_transport_read`/`ws_read_header`.
3. Chỉ khi cần bằng chứng CPU, thêm `benchmark-runtime-stats` cho control;
   sampler log mỗi 5 giây theo state với CPU per-core/per-task, core id, stack
   watermark, internal heap/largest block và PSRAM.
4. Chỉ khi allocator gate 160 MHz pass và DSP/AEC có workload cụ thể, so lại
   `mem-control` ở 240 MHz. Không tăng clock để che fragmentation/drop.
5. Rebuild `mem-control` với stats off; chỉ build này được dùng cho final heap gate.

Runtime-stats bật FreeRTOS trace/TCB overhead và làm thay đổi heap, vì vậy không lấy
largest/minimum heap của build này làm quyết định release. Nó chỉ trả lời task/core nào
đang dùng CPU và còn bao nhiêu stack.

Flash application partition, tuyệt đối không erase NVS/resource/UI:

```bash
idf.py -B build/ab-<variant> -p /dev/ttyACM0 app-flash
idf.py -B build/ab-<variant> -p /dev/ttyACM0 monitor
```

Không dùng `erase-flash`, `flash` toàn partition table hoặc ghi `resource_0/1` giữa hai
candidate; nếu làm vậy A/B không còn cùng Wi-Fi, identity và wake model. Sau mỗi
`app-flash`, chờ state `idle` và xác nhận không panic/watchdog/reset loop.

Khi board dùng ESP-SR bring-up `Hi ESP`, loa host có thể đánh thức nó dù không có người
ở cạnh. Đây là automation kỹ thuật, không phải acceptance cho custom `Hey VeeTee` hay
chất lượng mic/loa:

```bash
command -v spd-say
spd-say -w -l en -t female1 -r -15 -i 75 "Hi E S P"
```

Chờ serial chuyển sang `listening`, sau đó có thể phát một câu tiếng Việt ngắn từ cùng
loa để chạy full ASR -> LLM -> TTS. Không phát query trước `listening`; capture chỉ mở ở
state đó. Mỗi candidate chạy nhiều lần open/close độc lập và ít nhất một full turn;
kiểm log `WebSocket I/O preflight passed`, hello ready,
`listening -> evaluating -> thinking -> speaking -> listening`, rồi đóng session và
kiểm `WebSocket I/O stack minimum free`.

Gate stats-off sau warm-up:

- largest internal block luôn ít nhất 16 KiB, mục tiêu 20 KiB; WebSocket floor 10 KiB
  chỉ là recovery/admission;
- capture, playback, wake, WebSocket control và WebSocket I/O còn ít nhất 2 KiB stack;
- current/minimum heap đạt plateau, không giảm thêm sau mỗi turn tương đương;
- delta mic/detector/uplink/playback drop, Opus decode và speaker-write failure bằng 0;
- không watchdog, panic, unexpected reset; button/wake không bị mất;
- maintenance HTTP đang chạy phải quiesce trong 150 ms trước voice TLS; nếu quá budget,
  lần mở đó fail bounded qua `transport_lost`, không overlap TLS, và desired/report vẫn
  resume ở idle. Đọc log elapsed đo từ trước socket close qua handler cleanup; board soak
  phải xác nhận application/button path không bị giữ quá budget bởi close handshake.

Ghi host/build/serial evidence riêng với nghiệm thu vật lý. Host speaker có thể chứng
minh wake event và full wire flow; nó không chứng minh âm thanh loa nghe hay, LCD đúng,
AEC, FAR/FRR tiếng Việt hoặc soak nghe liên tục.

## 6. Hướng dẫn ngắn cho AI

Khi giao task chạy/điều tra cho AI, dùng prompt này:

> Đọc `AGENTS.md`, `CLAUDE.md` và `docs/21-local-development-runbook.md`. Làm việc từ
> `/home/vubq/Project/EmYeuKhoaHoc/veetee`, kiểm tra branch/commit và working tree trước.
> Dùng đúng thứ tự cold-start, không in hoặc commit `.env`, key, token, transcript/audio.
> Giữ 9Router tạm dừng; chỉ bắt đầu Realtime Lab/ESP32 sau khi CLIProxyAPI prewarm,
> Manager API và Voice Server đều healthy/`ready=200`. Khi voice bị ngắt, ghi
> live `OPENBLAS_NUM_THREADS=1`, interval CPU/RSS/thread/PID/port và event names/timings
> đã redact; không dùng lifetime `ps %CPU` hoặc thay đổi backend/thread/speed nếu chưa có
> A/B evidence. Với soak dài, đo ít nhất 300 giây PCM representative rồi ba follow-up
> turn; không truncate/fail chỉ vì vượt 600 giây, và chạy synthetic progressive stream
> tương đương hơn 10 phút để kiểm total caps off + bounded queue/context. Báo mọi
> retry/fallback/deadline/`finish_reason=length|max_tokens`; không coi partial output là
> complete. Không flash/monitor
> firmware, không push/deploy/commit nếu chưa được yêu cầu rõ.

AI chỉ được báo “chạy được” khi ghi rõ: commit đã chạy, process/port, các health check,
command đã chạy, test result, và phần nào vẫn cần nghe trực tiếp trên ESP32.

## 7. Tắt máy

Trước khi tắt máy, dừng ba app bằng `Ctrl-C`. PostgreSQL/Redis và CLIProxyAPI có thể được
để host runtime quản lý. Sau reboot, quay lại mục 2; không giả định các process app thủ
công tự khởi động chỉ vì systemd userspace của Tailscale hoặc CLIProxyAPI đang chạy.
