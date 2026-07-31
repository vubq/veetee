# NOTICE

## Live2D

Giao diện 02 (`monolith` / Companion) trong demo này dùng Hiyori Momose làm nhân vật
tham chiếu. Hiyori là sample data thuộc sở hữu và bản quyền của Live2D Inc.

> This content uses sample data owned and copyrighted by Live2D Inc.
> The sample data are utilized in accordance with terms and conditions set by
> Live2D Inc. This content itself is created at the author's sole discretion.

Giấy phép của Live2D tách làm ba tầng, không dùng chung một điều khoản:

| Thành phần | Điều khoản |
|---|---|
| Cubism Components / SDK code | Live2D Open Software License |
| Cubism Core (`live2dcubismcore.min.js`) | Live2D Proprietary Software License |
| Sample model (Haru, **Hiyori**, Mao, Mark, Natori, Ren, Rice, Wanko) | Free Material License Agreement + Terms of Use for Live2D Cubism Sample Data |

Ràng buộc phải tuân thủ khi dùng Hiyori:

- Đồng ý cả **Free Material License Agreement** và **Terms of Use for Live2D Cubism
  Sample Data** — hai văn bản riêng, không văn bản nào thay thế văn bản kia.
- Điều kiện riêng của Hiyori: **không được thay đổi thiết kế nhân vật dưới bất kỳ hình
  thức nào**, và tôn trọng chủ đề mochi của nhân vật.
- **Giới hạn thương mại:** chỉ General User và Small-Scale Enterprise User mới được dùng
  cho mục đích thương mại. Live2D định nghĩa "Business" là pháp nhân có tổng doanh thu
  năm tài chính gần nhất **trên 10.000.000 JPY**; bên vượt ngưỡng này chỉ được dùng sample
  model cho mục đích nội bộ hoặc giám sát (Internal / Supervision Purpose).

**Trạng thái hiện tại của dự án:** dùng cá nhân, nằm trong phạm vi General User của Free
Material License. Ngưỡng doanh thu ở trên không áp dụng.

Ghi lại ngưỡng đó ở đây chỉ để dùng về sau: nếu có ngày Veetee phân phối UI Pack ra người
dùng cuối ở quy mô vượt ngưỡng, phải thay nhân vật. Việc thay là rẻ — đường ống xuất clip
và định dạng VTCLIP1 không phụ thuộc nhân vật nào, chỉ cần đổi nguồn frame, không đụng
firmware.

Repository này không đóng gói lại model, texture hay Cubism Core:

- `tools/capture-hiyori.html` tải model từ repository chính thức
  `Live2D/CubismWebSamples` (`Samples/Resources/Hiyori/`) và tải Cubism Core từ URL
  hosting chính thức của Live2D. Live2D khuyến cáo không dựa vào direct link tới Cubism
  Core cho production; nếu cần chạy lặp lại hoặc offline thì tải Cubism SDK for Web về.
- Clip đã xuất (`assets/hiyori/*.vclip`) là dữ liệu dẫn xuất, được `.gitignore`
  và phải được tạo lại tại chỗ.

Nguồn đã kiểm chứng:

- <https://github.com/Live2D/CubismWebSamples> — repo chính thức, model nằm ở `Samples/Resources/`.
- <https://www.live2d.com/eula/live2d-free-material-license-agreement_en.html>
- <https://www.live2d.com/eula/live2d-sample-model-terms_en.html>
- <https://www.live2d.com/en/learn/sample/momose-hiyori-video/> — trang nhân vật Hiyori.
- <https://help.live2d.com/en/other/other_16/> — giải thích ngưỡng doanh thu thương mại.

## Font

Demo dùng font hệ thống. Không đóng gói và không tải font từ Internet.
Firmware sẽ dùng `.vfont` riêng trong UI Pack; không dùng chung file font web
của Manager Web (xem `docs/22-veetee-interface-language.md` §7).
