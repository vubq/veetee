# Veetee Monorepo

Dự án hợp nhất 2 thành phần Xiaozhi trong một Repository duy nhất:
- **`xiaozhi-esp32`**: Firmware cho thiết bị ESP32 (Upstream: `https://github.com/78/xiaozhi-esp32.git`)
- **`xiaozhi-esp32-server`**: Server backend (Upstream: `https://github.com/xinnan-tech/xiaozhi-esp32-server.git`)

---

## 🚀 Hướng dẫn Cập nhật (Sync Upstream)

Khi upstream có phiên bản mới, bạn chỉ cần chạy các lệnh sau để đồng bộ về repo của bạn:

### 1. Cập nhật `xiaozhi-esp32`:
```bash
git fetch upstream-esp32 main
git subtree pull --prefix=xiaozhi-esp32 upstream-esp32 main --squash
```

### 2. Cập nhật `xiaozhi-esp32-server`:
```bash
git fetch upstream-server main
git subtree pull --prefix=xiaozhi-esp32-server upstream-server main --squash
```

### 3. Push thay đổi lên repository cá nhân:
```bash
git push origin main
```
