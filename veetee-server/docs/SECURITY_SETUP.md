# Cấu hình sau audit

Cập nhật: **2026-09-18**

## Kết nối đã xác thực

Không còn shared `VEETEE_WS_TOKEN`. Mỗi thiết bị stock được cấp một credential riêng sau OTA pairing. Sau khi quản trị viên duyệt mã 6 số, credential được phát đúng một lần ở OTA check kế tiếp; firmware stock persist cấu hình WebSocket nên các OTA check thường lệ không trả lại bearer token. Re-pair sẽ rotate credential mới. WebSocket chỉ nhận credential đúng với cặp `Device-Id` + `Client-Id`; revoke thiết bị làm credential đó mất hiệu lực.

Firmware stock tại baseline hiện tại đã đọc `websocket.token` và tự gửi `Authorization: Bearer`, `Device-Id`, `Client-Id`, nên không cần sửa/build firmware cho flow này. Dashboard dùng `VEETEE_MANAGEMENT_TOKEN` riêng để gọi `/api/session`, nhận cookie HttpOnly/SameSite=Strict có hạn một giờ; management token không được lưu trong URL/localStorage/sessionStorage.

Triển khai qua HTTPS/WSS khi ra ngoài máy. Khi proxy terminate TLS, cấu hình Secure cookie ở proxy hoặc TLS backend. Chỉ thêm origin tin cậy vào `server.ws_allowed_origins`; mặc định browser phải cùng host.

Giới hạn mặc định: 8 kết nối chung hai listener, 5 giây nhận hello, frame 64 KiB. Tăng có chủ đích theo tài nguyên ASR/TTS. Không thay đổi cấu hình local hoặc restart service tự động khi áp dụng source.

## Bộ nhớ

Durable memory chỉ được bind vào session đã xác thực. Mỗi paired device có thể có `owner_id` riêng; session ưu tiên owner này và fallback `memory.trusted_owner_id` cho thiết bị legacy/chưa gán. Không tự suy owner từ `Device-Id`/`Client-Id` tự khai báo.

## Công cụ

`music_play` chỉ nhận ID YouTube 11 ký tự ASCII chữ/số/`_`/`-`; URL và địa chỉ mạng riêng bị từ chối trước subprocess. Resolver tự xây URL YouTube cố định và bỏ qua config yt-dlp ngoài ứng dụng.

Schema combinators anyOf/oneOf/allOf được kiểm tra cùng các ràng buộc sibling, không bỏ qua maximum/required/additionalProperties.
