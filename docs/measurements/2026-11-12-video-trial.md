# Video và flashcard, ba lần chạy thật, 2026-11-13 đến 2026-11-16

Ba lần chạy đầu cuối trên stack compose, mỗi lần có trả tiền model thật.
Mỗi lần đi qua giao diện như một giảng viên: sinh video, vẽ hình, dựng, giao, sinh flashcard.
Sau mỗi lần, các lỗi tìm thấy được sửa rồi chạy lại, nên bảng dưới là ba phiên bản liên tiếp của cùng một chức năng, không phải ba lần đo cùng một mã.

## Điều kiện đo

- Tài liệu: `Chapter 5_CPU Scheduling.pdf`, trọng tâm "FCFS và Round Robin", tiếng Việt.
- Model mặc định của hộp sinh: lời thoại `gemini-fast`, kiểm chứng `claude-fast`, giọng `voice-heygen` (HeyGen Voice 1 Turbo), hình `image-nano-banana` (Gemini Nano Banana 2.1).
  `served_models` của mỗi video cho thấy mọi alias được đúng model dự định phục vụ.
- Mã: lần 1 tại `e826e40` (2026-11-13), lần 2 tại `77043a5` và lần 3 tại `d8b5dfc` (cùng ngày 2026-11-16), nhánh `feat/video-flashcards`.
- Trình duyệt: Chromium headless của Playwright, ảnh chụp ở 1280 px và 390 px.
  MP4 được phát và kiểm bằng `ffprobe` (H.264 1280x720 24 fps, AAC 44.1 kHz mono).
- Mọi thao tác của giảng viên đi qua giao diện.
  API và database chỉ được đọc.
- Chi phí lấy từ trường `cost` của video và của bộ thẻ trong database, khớp với số hiện trên thanh của trình sửa.
  Giọng được tính theo ước lượng (`estimated: true`).
- Mỗi lần chạy gọi đúng một lần sinh lời thoại, một lần vẽ, một lần dựng, một lần sinh thẻ, không thử lại.
  Riêng lần 1 có thêm một lần vẽ lại.

### Phần sinh viên không đi qua LTI thật

Phần sinh viên được kiểm bằng **tài khoản thành viên**, không phải một launch LTI thật.
`devops/lti-e2e/run.sh` dựng Moodle và backend riêng trên Postgres tmpfs, nên không thấy video đã giao trong database dev.
Thay vào đó, một tài khoản có hình dạng giống sinh viên LTI được tạo: `can_author = false`, mật khẩu không dùng được, và một dòng `course_members` với `source = 'lti'`.
Token của nó được tạo bằng `create_access_token`, đặt vào `localStorage.auth` như `AuthContext` làm.
Cách này kiểm trang `/learn/{course}`, trang xem video và trang lật thẻ, cùng quyền đọc của thành viên (RLS).
Nó không kiểm chữ ký, nonce hay danh sách lớp của một launch thật.
Moodle cũng không phân giải được trong môi trường này, nên `/api/moodle/account` trả 502 ở mọi trang giảng viên.

## Kết quả theo lần chạy

Mục tiêu của prompt: video 4 đến 6 phút, 3 đến 5 hình.

| | Lần 1 | Lần 2 | Lần 3 |
| --- | ---: | ---: | ---: |
| Độ dài thật | 138,25 s (2:18) | 249,58 s (4:10) | 270,21 s (4:30) |
| Ước tính của trình sửa | 137,9 s | 264 s | 290,3 s |
| Số cảnh | 8 | 9 | 9 |
| Cảnh có hình | 1 | 2 | 4 |
| Hình đã vẽ | 2 (1 vẽ, 1 vẽ lại) | 2 | 4 |
| Ký tự lời thoại | 2.069 | 3.960 | 4.354 |
| Câu mỗi cảnh | 2 đến 3 | 5 đến 6 | 5 |
| Chunk được trích (khác nhau) | 4 | 9 | 9 trên 16 |
| Tệp MP4 | 3.080.966 byte | 5.460.073 byte | 6.118.748 byte |
| Cảnh bị cờ cần xem lại | 3 | 4 (5 cờ) | 7 (9 cờ) |
| Thời gian job: lời thoại / vẽ / dựng | 45 s / - / 1 phút | 1 phút 45 s / 12 s / 33 s | 1 phút 20 s / 9 s / 27 s |

Chi phí của video, theo `cost` (USD):

| | Lần 1 | Lần 2 | Lần 3 |
| --- | ---: | ---: | ---: |
| Lời thoại (10 lời gọi) | không ghi | 0,019845 | 0,019954 |
| Hình | 0,076407 (2) | 0,073733 (2) | 0,146900 (4) |
| Giọng | 0,008690 | 0,016632 | 0,018287 |
| Tổng video | 0,0851, thiếu lời thoại | 0,110210 | 0,185140 |

Bộ flashcard của mỗi lần:

| | Lần 1 | Lần 2 | Lần 3 |
| --- | ---: | ---: | ---: |
| Số thẻ | 10 | 7 | 8 |
| FCFS / Round Robin | 0 / hầu hết | 2 / 5 | 4 / 4 |
| Thẻ ví dụ có tính toán | 0 | 0 | 2 (FCFS) |
| Cờ cần xem lại | 0 | 0 | 0 |
| Chi phí | không ghi | 0,004711 | 0,005503 |

Tổng chi phí: lần 2 là $0,1149, lần 3 là $0,1906 (video $0,1851 cộng thẻ $0,0055).
Lần 3 vượt mức $0,15 đã duyệt khoảng 23%, do 4 hình thay vì 2.
Lần 1 không có tổng đầy đủ vì chi phí lời thoại và thẻ chưa được ghi.
Hình chiếm gần 80% chi phí video, nên số hình quyết định giá: một video 3 đến 5 hình tốn khoảng $0,11 đến $0,18 chỉ riêng hình.

Ảnh chụp:

- Trình sửa với video đã dựng xong (lần 3, 1280 px): [11-editor-player-1280.png](2026-11-12-video-trial/11-editor-player-1280.png).
- Sinh viên xem video (lần 3, 390 px): [21-learn-video-390.png](2026-11-12-video-trial/21-learn-video-390.png).
- Mặt sau một thẻ với nguồn (lần 3, 390 px): [26-learn-card6-back-390.png](2026-11-12-video-trial/26-learn-card6-back-390.png).
- Hình sai hàng của ví dụ Round Robin (lần 3, 1280 px), xem lỗi còn lại: [08-scene8-1280.png](2026-11-12-video-trial/08-scene8-1280.png).

## Đổi gì giữa các lần, và vì sao

Từ lần 1 sang lần 2:

- **Trang khóa học vỡ bố cục** (lần 1: tên tài liệu một chữ mỗi dòng ở 1280 px, cuộn ngang ở 390 px, vì thêm hai nút "Sinh flashcard" và "Sinh video").
  Bố cục được sửa, tên tài liệu rộng 378 px.
- **Video quá ngắn** (2:18 so với đích 4 đến 6 phút, mỗi cảnh chỉ 2 đến 3 câu).
  Prompt lời thoại đổi sang truy xuất theo từng chủ đề (16 chunk) và yêu cầu 5 đến 7 câu mỗi cảnh.
  Bản nháp tự dài 3.960 ký tự.
- **Lời kiểm chứng bằng tiếng Anh**, kèm một cờ sai ("thời điểm số không" bị hiểu là "không theo thứ tự").
  Lời kiểm chứng chuyển sang tiếng Việt và cờ sai không còn.
- **Chi phí lời thoại và thẻ không được ghi.**
  Mọi lời gọi LLM nay được tính giá và lưu vào `cost` (migration 0035 thêm cột cho bộ thẻ).
- **Chữ trên slide bằng tiếng Anh** và lặp lại bảng hay công thức.
  Slide nay dùng tiếng Việt và không lặp.
- **Thông báo job xong hiện xa nút vừa bấm.** Thông báo nay hiện ngay dưới nút đó.
- **Nguồn lặp hai lần dưới mỗi thẻ.** Nay chỉ hiện một lần.
- **Thẻ lệch chủ đề** (không có thẻ FCFS).
  Prompt thẻ đổi để chia đều theo chủ đề.

Từ lần 2 sang lần 3:

- **Chỉ 2 hình.** Prompt yêu cầu 3 đến 5 hình, và mọi ví dụ có tính toán của bài (hai thứ tự FCFS, Round Robin q = 4)
  đều được vẽ.
- **Dài 4:10, sát cận dưới 4 phút.** Bước bổ sung chạy "bất cứ khi nào dưới 4 phút hoặc có cảnh ngắn"
  (`b1eda21`).
  Bản nháp lần 3 dài 4.354 ký tự, mọi cảnh từ 444 ký tự trở lên, nên bước này không cần chạy.
- **Bấm "Sinh" rồi vài giây không thấy gì** (POST mất 6 đến 8 giây).
  Một dòng "Đang gửi yêu cầu..." hiện ngay và nút sinh bị khóa.
- **Lời chào đầu video luôn bị cờ.** Cảnh 1 không còn bị cờ.
- **Tên tệp lặp trên mỗi dòng nguồn** trong danh sách cảnh của sinh viên.
  Tên tệp nay hiện một lần dưới trình phát.
- **Cùng một video hiện 4:10, 4:09 và 4,2 phút.** Nay là 4:30 ở danh sách, trình phát và trình sửa ("4,5 phút").
- **Ô trả lời của thẻ bị cắt ở 390 px.** Ô nay tự giãn theo nội dung.
- **Thẻ**: "trưng thu" thành "trưng dụng", câu hỏi và đáp án khớp nhau, có hai thẻ ví dụ FCFS.

## Lỗi còn lại sau lần 3

Chưa có lỗi nặng.
Còn một lỗi vừa và các lỗi nhẹ sau:

- **Vừa:** hình Gantt của ví dụ Round Robin (q = 4)
  đặt 8 thanh vào 8 hàng so với ba nhãn P1, P2, P3.
  Mốc thời gian và nhãn thanh đúng, nhưng hàng sai.
  Hình này qua được bước kiểm hình ("Mọi hình đã khớp với mô tả"), vì mô tả không nói hình có mỗi tiến trình một hàng.
- Lời thoại cảnh 5 nói sai về hiệu ứng đoàn xe (nói về thứ tự P2, P3, P1, nơi nó đã hết).
  Bộ kiểm chứng đã cờ đúng, nhưng video đã giao không được sửa trong lần chạy này.
- Lời kiểm chứng vẫn có từ "Evidence" ở 4 cờ.
  Commit c77f56a đã dặn bộ kiểm chứng gọi là "đoạn nguồn"; bộ thẻ sinh sau đó không có cờ nào, nên chưa đo lại được trên video.
- Chữ trên slide diễn đạt công thức bằng lời ("n trừ một nhân q") thay vì giữ công thức.
- Cảnh 2 có một gạch đầu dòng lặp lại hàng của bảng ngay dưới nó.
- Danh sách của sinh viên có nhiều video và bộ thẻ trùng tên, chỉ phân biệt bằng độ dài hoặc số thẻ.
  Trang giảng viên có giờ tạo, trang sinh viên chưa có.
- Lời từ chối khi hết hạn mức ngày ghi "mô-đun" dù đang sinh thẻ hoặc video, và chỉ hiện sau khi người dùng điền xong hộp và chờ khoảng 6 giây.
  Commit c77f56a đã sửa chữ thành "video" hoặc "bộ flashcard"; lời từ chối vẫn chỉ hiện sau khi gửi.
- Bộ thẻ không có thẻ cho ví dụ Round Robin q = 4.
  Cả 4 thẻ Round Robin trích cùng một chunk.
- Một thẻ viết "P_1, P_2, P_3"
  (từ lớp chữ của PDF), thẻ khác viết "P1, P2, P3".
- Nhãn vị trí của thẻ 3 là "Scheduling Algorithm Optimization Criteria" vì chunk gộp hai slide (trang 9 đến 10), dù nội dung đúng.
- Mỗi yêu cầu sinh mất khoảng 6 giây vì hai lần tra Moodle thất bại.
  Đây là lỗi môi trường đo, không phải của nhánh.

Trừ hai mục có ghi commit sửa, những mục này đã được ghi lại và chưa sửa trong nhánh.

## Giới hạn của phép đo

- Một video và một bộ thẻ cho mỗi lần chạy, trên một tài liệu và một trọng tâm.
  Đây là chạy thử đầu cuối, không phải thống kê: độ dài và số hình của một lần sinh khác có thể khác.
- Chất lượng lời thoại, hình và thẻ được đánh giá bằng cách đọc và xem kết quả, không có người chấm độc lập.
- Lần 3 ban đầu bị từ chối sinh thẻ vì tài khoản giảng viên đã dùng hết 3 lượt sinh trong ngày.
  Bộ thẻ được chạy lại sau khi một lượt được giải phóng, trên mã mới hơn vài commit (worker khởi động lại lúc 01:58).
- Kiểm bộ chọn đoạn nguồn (picker) chỉ có ở lần 1.
