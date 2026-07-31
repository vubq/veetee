# assets/hiyori

Thư mục đích cho clip nhân vật đã đóng gói. Nội dung sinh ra ở đây **không commit**:
đó là dữ liệu dẫn xuất từ sample data của Live2D và phụ thuộc điều kiện sử dụng
trong `../../NOTICE.md`.

Cần có:

```text
manifest.json      # đổi tên từ manifest.example.json hoặc lấy từ zip của tool capture
boot.vclip
idle.vclip
listening.vclip
thinking.vclip
speaking.vclip
closing.vclip
mouth.vclip        # tùy chọn, 4 mức miệng cho lip-sync
```

Hai cách tạo:

```bash
# 1. Trực tiếp từ Live2D trong trình duyệt
#    mở tools/capture-hiyori.html, bấm "Xuất clip", giải nén zip vào thư mục này

# 2. Từ PNG sequence xuất bằng Cubism Editor
node ../../tools/pack-clip.mjs pack ./png/idle ./idle.vclip --fps=12
node ../../tools/pack-clip.mjs inspect ./idle.vclip
```

Ngân sách: tổng mọi `.vclip` phải nằm dưới **2 MiB** của một UI slot. Demo hiển thị
phần trăm đã dùng ở ô *Clip đã nạp*.
