# Ghi chu ky thuat server

## Muc dich

Thu muc nay tong hop cac thanh phan, phuong thuc va giao thuc dang co trong source
tham khao `../references/xiaozhi-esp32-server`. Tai lieu dung de nghien cuu va lap ke
hoach cho Veetee; no khong dinh nghia kien truc chinh thuc cua server Veetee.

Source tham khao la monorepo gom Python realtime server, Java management API, Vue web,
uni-app mobile va digital-human test client. Veetee khong mac dinh phai su dung tat ca
cac thanh phan hoac cung cong nghe.

## Danh muc

| Tai lieu | Noi dung |
| --- | --- |
| [Tong quan kien truc](architecture.md) | Thanh phan, boundary va deployment mode |
| [Realtime AI pipeline](realtime-ai-pipeline.md) | Connection, audio, VAD, ASR, LLM, tool va TTS |
| [Giao thuc va API](protocols-and-apis.md) | Device WebSocket, HTTP/OTA/vision, MCP va manager API |
| [Provider va cau hinh](providers-and-configuration.md) | Plugin factory, selected modules va config precedence |
| [Bao mat, van hanh va kiem thu](security-operations-testing.md) | Auth, secret, scale, observability va test gap |

## Ban do source tham khao

| Thanh phan | Vi tri upstream |
| --- | --- |
| Python realtime server | `../references/xiaozhi-esp32-server/main/xiaozhi-server/` |
| Java management API | `../references/xiaozhi-esp32-server/main/manager-api/` |
| Web console | `../references/xiaozhi-esp32-server/main/manager-web/` |
| Mobile console | `../references/xiaozhi-esp32-server/main/manager-mobile/` |
| Browser test client | `../references/xiaozhi-esp32-server/main/digital-human/` |
| Deployment/integration docs | `../references/xiaozhi-esp32-server/docs/` |

## Cach doc

- Dung `architecture.md` de xac dinh subsystem nao can tham khao.
- Dung `realtime-ai-pipeline.md` khi lam audio/session/AI orchestration.
- Dung `protocols-and-apis.md` cung tai lieu firmware khi thay doi contract thiet bi.
- Dung `providers-and-configuration.md` khi them model/provider.
- Kiem tra source tai commit dang pin truoc khi trien khai wire format hoac endpoint.
