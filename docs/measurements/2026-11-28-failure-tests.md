# Thử lỗi trên một máy, sau khi nhúng và rerank chuyển ra dịch vụ ngoài, 2026-11-28

## Vì sao đo lại

Tám kịch bản ngày 06/11 (`2026-11-06-failure-tests.md`) chạy khi embedder Qwen3 và reranker Jina còn nằm trong hệ thống.
Nay câu hỏi được nhúng bằng Gemini Embedding và xếp hạng bằng Jev, cả hai qua mạng, và chat có thêm ngưỡng điểm, bộ nhớ đệm câu trả lời và phân luồng.

## Cách chạy

- `devops/loadtest/run_failures.sh` trên bộ máy thử tải: model là `mock_gateway.py` (trung vị 13,3 s mỗi lần gọi); Postgres, Redis, hai api và hai worker (mỗi worker 4 luồng) là thật.
- Dịch vụ nhúng ngoài được đóng vai bằng embedder trong máy (trả 768 chiều như Gemini), sau gateway giả; dừng nó là nhà cung cấp nhúng ngừng.
- Jev được đóng vai bằng một dịch vụ riêng (`jev`, cùng `mock_gateway.py`, đường `/v1/systemone`), trỏ tới bằng thiết lập mới `JEV_URL`; dừng nó là TypeSafe ngừng.
- Không gọi model trả tiền nào. Kết quả thô ở `load-test/failures-2026-11-28.json`.

## Tạo nội dung

Mỗi kịch bản xếp 8 mô-đun cùng lúc, gây lỗi giữa chừng và chờ mọi việc. Đạt khi 8 việc xong và mỗi tài liệu có đúng một mô-đun.

| Kịch bản | Lỗi | Xong | Mô-đun | Lần thử lại | Thời gian | Kết quả |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| baseline | không | 8/8 | 8 | 0 | 208 s | đạt |
| worker-sigterm | `docker stop` một worker | 8/8 | 8 | 0 | 249 s | đạt |
| worker-sigkill | `kill -9` một worker | 8/8 | 8 | 4 | 489 s | đạt |
| gateway-60s | gateway model ngừng 60 s | 8/8 | 8 | 16 | 336 s | đạt |
| jev-60s | Jev ngừng 60 s | 8/8 | 8 | 0 | 217 s | đạt, 28 lượt rerank dùng thứ tự tìm kiếm |
| embedder-60s | dịch vụ nhúng ngừng 60 s, từ giây thứ 5 | 8/8 | 8 | 9 | 312 s | đạt |
| database-restart | `docker compose restart db` | 8/8 | 8 | 0 | 183 s | đạt |
| api-sigkill | `kill -9` một api | 8/8 | 8 | 0 | 204 s | đạt |

- Jev ngừng không làm việc nào phải thử lại: rerank lỗi thì dùng thứ tự tìm kiếm, đúng thiết kế, và chất lượng giảm chứ việc không hỏng.
- Dịch vụ nhúng ngừng làm bước tìm kiếm thất bại, việc được trả về hàng đợi và thử lại sau 30 s, xong khi dịch vụ chạy lại.
- worker-sigkill: bốn việc của worker chết được giao lại sau khoảng 4 phút, như sau bản sửa ngày 06/11.
- database-restart lần này không có lần thử lại nào (ngày 06/11: 2); không có việc nào đang ghi đúng lúc database khởi động lại.

## Chat

Sáu tin nhắn, hai tin cùng lúc, trong khi một phụ thuộc bị dừng. API được khởi động lại và Redis được xoá trước mỗi kịch bản, để không câu nào lấy từ bộ nhớ đệm.

| Phụ thuộc bị dừng | Mã trả về | Cách trả lời |
| --- | --- | --- |
| Redis | 6 × 200 | bình thường; giới hạn lượt dùng rơi về từng process |
| Dịch vụ nhúng | 6 × 200 | tìm theo từ khoá; 3 câu có đoạn khớp được model trả lời, 3 câu không có từ nào khớp nhận "tài liệu không đề cập" |
| Jev | 6 × 200 | 6 câu do model trả lời từ thứ tự tìm kiếm, không áp ngưỡng điểm, không lưu vào bộ nhớ đệm |

## Lỗi tìm ra và đã sửa

**Chat trả lời "không đề cập" cho mọi câu khi Jev ngừng.**
Khi Jev không trả lời, `rerank_scored` dùng thứ tự tìm kiếm làm điểm (1/60, 1/61, ...), và ngưỡng 0,05 mới thêm coi các điểm đó là "không liên quan".
Lần chạy đầu: 3/3 câu chưa có trong bộ nhớ đệm bị chặn với điểm 0,0167; 3 câu còn lại lấy câu trả lời đã lưu từ kịch bản trước nên che mất lỗi.
Sửa: `rerank_scored` báo ra `fallback`, `rerank_scored_for_chat` khi đó trả về không có điểm, nên chat không áp ngưỡng; câu trả lời làm khi không có điểm không được lưu vào bộ nhớ đệm, để không sống quá lúc Jev ngừng.
Kiểm thử `test_while_jev_is_down_the_answer_comes_from_the_search_order_and_is_not_saved` trượt trước bản sửa và đạt sau.

**Bộ thử tự che lỗi.**
- Mỗi worker nhớ embedding của câu tìm kiếm, và 8 mô-đun cùng một bài giảng dùng chung câu tìm kiếm, nên các câu được nhúng trong vài giây đầu rồi dùng lại; dừng dịch vụ nhúng ở giây thứ 60 không chạm vào gì.
  Nay worker được khởi động lại trước mỗi kịch bản và dịch vụ nhúng bị dừng từ giây thứ 5.
- Câu trả lời chat lưu trong Redis tồn tại qua việc khởi động lại api; nay Redis được xoá trước mỗi kịch bản chat.
- Bài giảng mặc định `Chapter 3_Processes.pdf` không còn trong `docs/documents`; nay dùng `Chapter 4_Threads & Concurency.pdf`.
- Bộ thử tải còn dùng reranker Jina đã bỏ; nay có Jev đóng vai, và địa chỉ Jev là thiết lập `JEV_URL`.

## Giới hạn

- Một lần mỗi kịch bản, trên một máy, với model giả: đo tính đúng khi có lỗi, không đo thời gian.
- Dịch vụ ngoài "ngừng" bằng cách dừng container, nên lỗi là không kết nối được; một nhà cung cấp trả 429 hay 503 kéo dài, hoặc trả chậm, chưa được thử ở đây.
- Khi dịch vụ nhúng ngừng, chat chỉ còn tìm theo từ khoá; câu hỏi tiếng Việt trên tài liệu tiếng Anh hầu như không có từ khớp, nên sẽ nhận "không đề cập" cho tới khi dịch vụ chạy lại. Hành vi này có từ trước.
