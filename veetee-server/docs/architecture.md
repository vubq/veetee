# Tong quan kien truc server tham khao

## Thanh phan

```text
ESP32 / test client
  -> WebSocket truc tiep hoac MQTT gateway
  -> Python xiaozhi-server
       -> VAD -> ASR -> intent/LLM -> tool/MCP -> TTS
       -> HTTP OTA va vision
       -> tuy chon goi manager-api

Web/mobile console
  -> Java manager-api
       -> MySQL
       -> Redis
       -> cau hinh agent/device/model/OTA
```

| Thanh phan | Vai tro quan sat duoc |
| --- | --- |
| `xiaozhi-server` | Duong realtime, session thiet bi, AI pipeline va streaming audio |
| `manager-api` | Quan tri user, device, agent, model, OTA va cau hinh tap trung |
| `manager-web` | Console Vue 2 cho trinh duyet |
| `manager-mobile` | Console Vue 3/uni-app cho H5/app/mini-program |
| `digital-human` | Test/browser client va wake-word runtime, khong bat buoc production |

## Hai che do van hanh

### Toi gian

Chi chay Python realtime server. Cau hinh doc tu YAML local, khong can MySQL/Redis.
HTTP server Python tu cung cap OTA discovery/download don gian va vision endpoint.

### Day du

Python realtime server goi Java manager API de lay cau hinh chung va cau hinh rieng theo
device/agent. Java API dung MySQL, Redis va phuc vu web/mobile console. Cach nay tang
kha nang quan tri nhung them coupling, failure mode va yeu cau bao mat noi bo.

## Entry point

Python `app.py`:

1. Kiem tra FFmpeg.
2. Load config va auth key.
3. Khoi dong GC manager.
4. Chay WebSocket server va HTTP server dong thoi.
5. Bat SIGINT/SIGTERM, huy task va cleanup.

Spring Boot `AdminApplication.java` la entry point manager API. Web va mobile co entry
point rieng (`src/main.js`, `src/main.ts`) va chi giao tiep qua HTTP API.

## Session boundary

`WebSocketServer` giu provider co the dung chung va tao `ConnectionHandler` cho moi
device. `ConnectionHandler` so huu state rieng:

- `session_id`, socket, header, device ID va IP.
- Trang thai bind, listen, speaking, abort va AEC.
- Audio buffer, VAD window, ASR queue va speaker identity.
- Dialogue, prompt, memory va agent config.
- TTS sentence, IoT descriptors, MCP client va tool handler.
- Timeout task, executor va reporting queue.

Provider local nang co the dung chung de tiet kiem bo nho; provider co stream/session
state phai duoc tao rieng. Boundary nay can duoc ghi ro khi viet provider moi.

## Concurrency

- `asyncio` xu ly socket va orchestration I/O.
- `ThreadPoolExecutor`/thread xu ly SDK hoac model blocking.
- Queue noi audio/reporting voi worker.
- Cleanup memory/title duoc day sang daemon thread trong implementation tham khao.

Can tranh goi blocking model/API trong event loop. Thread khong duoc sua session state
ma khong co co che dong bo; khi scale nhieu process, state trong memory khong con dung
chung.

## Boundary de xuat cho Veetee

Source tham khao goi y bon boundary nghiep vu, nhung Veetee can quyet dinh lai:

| Boundary | Trach nhiem |
| --- | --- |
| Device gateway | Auth, protocol, session, backpressure va reconnect |
| Conversation engine | VAD/ASR, dialogue, LLM, intent, tool va TTS |
| Control plane | User/device/agent/model/config/OTA management |
| Client applications | Web/mobile UX, khong chua business secret |

Khong bat buoc moi boundary la mot service rieng. Ban dau co the cung mot deployable
nhung can tach hop dong va ownership de de test va scale sau nay.

## Source doi chieu

- `../references/xiaozhi-esp32-server/main/xiaozhi-server/app.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/websocket_server.py`
- `../references/xiaozhi-esp32-server/main/xiaozhi-server/core/connection.py`
- `../references/xiaozhi-esp32-server/main/manager-api/pom.xml`
- `../references/xiaozhi-esp32-server/main/README_en.md`
