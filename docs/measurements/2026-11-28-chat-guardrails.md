# Chat: quyền theo tài liệu, che dữ liệu, phân luồng câu hỏi và phản hồi, 2026-11-28

## Vì sao làm

Một bản nhận xét hệ thống (đối chiếu với sách *AI Engineering*, các chương về dữ liệu RAG, guardrail, độ trễ và quan sát) chỉ ra bốn nhóm thiếu sót.
Đối chiếu với code, cả bốn đều đúng:

1. Sinh viên của một môn hỏi chat được trên mọi tài liệu của môn, và trích dẫn gửi kèm nguyên văn đoạn gốc về trình duyệt, kể cả đáp án nếu giảng viên lỡ tải lên.
2. Bộ đọc HTML bỏ bảng, danh sách, khối code và tiêu đề từ h4; bộ đọc DOCX dồn mọi bảng xuống cuối tài liệu và mất vị trí bảng khi cắt đoạn.
3. Chat không có ngưỡng điểm, nên câu chào và câu ngoài môn vẫn gọi model; câu hỏi nối tiếp chờ một lượt model viết lại câu hỏi.
4. Chat không ghi lại thời gian từng bước, điểm rerank, câu viết lại hay số token, và người dùng không có cách báo câu trả lời sai.

## Đã làm

- Mỗi tài liệu có lựa chọn "sinh viên được hỏi chat". Tài liệu có sẵn được bật, tài liệu mới mặc định tắt. Database chặn (migration 0057): sinh viên không đọc được đoạn nào của tài liệu đóng, nên chat của họ không tìm thấy nó.
- Flashcard và video đã xuất bản từ tài liệu đóng vẫn hiện cho sinh viên, kèm tên tài liệu và vị trí nguồn, nhưng không kèm nội dung đoạn (migration 0059).
- Khoá API, mật khẩu, email và số điện thoại bị thay bằng nhãn ngay lúc đọc tài liệu, trước khi lưu và trước khi gửi đi nhúng; câu hỏi chat và ghi chú phản hồi cũng vậy.
- Một dòng trong tài liệu giả dấu kết thúc vùng dữ liệu của prompt bị vô hiệu.
- Tải lên một tệp giống hệt tệp đã có trong môn bị từ chối, kèm tên tệp trùng; tệp mới trùng tên tệp cũ được nhắc để xoá bản cũ.
- HTML giữ bảng (theo dạng "tiêu đề cột: giá trị"), danh sách, khối code và mọi cấp tiêu đề; bảng DOCX nằm đúng chỗ, dưới tiêu đề mục của nó, và trích dẫn ghi được "bảng N, hàng M".
- Chat phân luồng: câu chào hoặc cảm ơn được trả lời cố định; câu hỏi đầu tiên đã có câu trả lời trên cùng bộ tài liệu được trả lại ngay từ bộ nhớ đệm; đoạn tốt nhất dưới ngưỡng thì trả lời "tài liệu không đề cập" mà không gọi model.
- Prompt đặt lịch sử hội thoại trước các đoạn nguồn để phần đầu prompt giống nhau giữa các lượt; tám đoạn được xếp tốt nhất ở hai đầu, yếu nhất ở giữa.
- Câu trả lời đọc lại tám từ liên tiếp trở lên của hướng dẫn hệ thống bị thay bằng lời từ chối.
- Mỗi câu trả lời lưu một bản ghi vết; giao diện có ba nút "Hữu ích", "Chưa đúng", "Sai trích dẫn"; giảng viên xem phản hồi không kèm tên người hỏi; `scripts/chat_feedback_cases.py` gom ca lỗi thành bộ kiểm thử hồi quy.

## Che dữ liệu trên tài liệu thật

Chạy bộ che trên toàn bộ 2.313 đoạn trong database ở máy (năm môn, cả các bản cắt cũ):

- 21 đoạn đổi, tất cả là email: email liên hệ của giảng viên Giải tích, email của giảng viên Hệ điều hành và một email ví dụ trong code Java.
- Không có số điện thoại, khoá hay mật khẩu nào bị nhận nhầm.
- Không có trường hợp che nhầm số liệu, công thức hay mã nguồn.

## Viết lại câu hỏi nối tiếp

Đo từ máy ở Việt Nam qua gateway và OpenRouter, câu "Nó có mấy tính chất?" sau một lượt hỏi về heap, mỗi cấu hình ba lần:

| Model viết lại | Token đầu ra | Chữ đầu tiên |
| --- | ---: | ---: |
| Gemini 3.5 Flash Lite, không giới hạn suy nghĩ | 189-275 | 4,0-5,0 s |
| GPT-6 Luna (model trả lời) | 90-92 | 3,1-4,1 s |
| Gemini 3.5 Flash Lite, suy nghĩ "minimal" (đã chọn) | 6 | 0,95-2,1 s |

Phần lớn thời gian chờ là đường truyền và hàng chờ của nhà cung cấp, không phải model: cùng cấu hình "minimal" có lần mất 3,2 s.
Mục tiêu dưới 2 s cho chữ đầu tiên của cả câu trả lời cần đo lại trên AWS (Singapore), cùng lúc với chat.
Chi phí các lần thử: dưới $0,001.

## Ngưỡng điểm cho câu hỏi ngoài môn

`backend/scripts/calibrate_chat_threshold.py` tìm mỗi câu hỏi đúng như chat tìm (dense cộng BM25, 30 ứng viên, Gemini Embedding) rồi chấm 30 ứng viên bằng Jev `jev-1.14.0`; con số so với ngưỡng là điểm của đoạn tốt nhất.

- Câu hỏi của môn: 250 câu của bộ benchmark (`chunking/truy_van.json`), hỏi trong đúng môn của nó, trên database `benchmark_gemini` (cách cắt hiện tại).
- Câu ngoài môn: 40 câu (`chat-threshold/off_topic.json`), 20 câu đời thường và 20 câu gần với học thuật nhưng không thuộc năm môn, mỗi câu hỏi trong cả năm môn: 200 lượt.
- Không lượt nào mất bước nhúng hay rơi về thứ tự tìm kiếm vì Jev lỗi.

| Ngưỡng | Câu của môn còn được trả lời | Lượt ngoài môn bị chặn |
| ---: | ---: | ---: |
| 0,02 | 250/250 | 58,0% |
| 0,03 | 250/250 | 90,5% |
| **0,05 (đã chọn)** | **250/250** | **98,5%** |
| 0,08 | 250/250 | 99,0% |
| 0,10 | 250/250 | 99,5% |
| 0,15 | 249/250 | 99,5% |
| 0,20 | 247/250 | 100% |

- Điểm của câu đúng môn: trung vị 0,97, thấp nhất 0,12, 0,16 và 0,19 (đều ở CNXH khoa học).
- Điểm của lượt ngoài môn: trung vị 0,01.
- Ba lượt lọt qua 0,05: "Chiến dịch Điện Biên Phủ diễn ra năm nào?" trong CNXH khoa học (0,17) và "Kể cho tôi một câu chuyện cười" trong Lập trình nâng cao (0,09) và Học máy (0,06). Chúng vẫn đi tới model như trước, và model vẫn có quy tắc trả lời "không đề cập".
- Chọn 0,05 thay vì 0,10: cả hai giữ đủ 250 câu, nhưng 0,10 chỉ cách câu thấp nhất 0,02, trong khi câu hỏi thật của sinh viên thường mơ hồ hơn câu benchmark (mỗi câu viết từ đúng một đoạn).
- Chi phí: khoảng 6.200 cặp (câu hỏi, đoạn) mới gửi Jev, khoảng $0,08; các cặp của 250 câu benchmark lấy từ cache của lần đo 22/11. Điểm từng lượt ở `chat-threshold/scores.json`, bảng ở `chat-threshold/summary.json`.

## Kiểm thử

- Máy chủ: 2.392 kiểm thử đạt (unit, integration với PostgreSQL thật, e2e), một kiểm thử gọi model thật được bỏ qua như thường lệ.
- Giao diện: 364 kiểm thử đạt.
- Trên trình duyệt thật (môi trường `devops/browser-e2e`): ô "Sinh viên được hỏi chat", ba nút phản hồi, biểu mẫu "Chưa đúng" và danh sách phản hồi cho giảng viên.

## Cần đo lại

- Chat Ragas và thử prompt injection: prompt chat đã đổi thứ tự (lịch sử trước nguồn, đoạn tốt nhất ở hai đầu).
- Thời gian chữ đầu tiên của chat nhiều lượt trên AWS.
