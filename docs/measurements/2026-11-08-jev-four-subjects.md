# Bốn môn học với Jev và Gemini 3.5 Flash Lite, 2026-11-08

Đo tốc độ của cả luồng trên stack compose, sau khi Jev được đồng ý và thành reranker mặc định: tải tài liệu lên,
hỏi chat, sinh module. Script: `backend/scripts/measure_subjects.py`. Dữ liệu thô ở [jev-subjects/](jev-subjects/),
tóm tắt từng module ở `jev-subjects/summary.txt`.

## Cách đo

- **Stack:** `docker compose` trên một máy (WSL, 12 GB RAM), một `backend`, một `worker` (4 luồng job), embedder
  Qwen3-Embedding-0.6B trên CPU, `RERANKER_PROVIDER=jev` (`jev-1.14.0`).
- **Model:** `gemini-fast-paid`, tức `google/gemini-3.5-flash-lite` qua OpenRouter, cố định Google AI Studio, ở chế
  độ thí nghiệm (tắt fallback). Gói Gemini miễn phí đã hết hạn mức ngày, nên mọi bước đều dùng đường trả phí, kể cả
  bước đọc lại công thức (`FORMULA_VISION_MODEL`).
- **Môn:** Advanced Programming (5 PPTX), Calculus 1 (6 PDF), Operating Systems (5 PDF), Scientific Socialism (3 PPTX).
  Machine Learning chỉ có video và audio, cần phiên âm qua Groq (trả phí), nên chưa đo.
- **Mỗi môn:** một khóa học mới; tải từng tệp lên theo ba bước rồi chờ `embedded` (hệ thống chỉ xử lý một tài liệu
  mỗi khóa một lúc); hỏi 10 câu của [bộ đánh giá chat](2026-11-07-chat-ragas.md) thuộc môn đó, lần lượt; sinh hai
  module từ hai tệp đầu theo thứ tự tên, lần lượt.
- **Một lần chạy, một người dùng, không tải song song.** Đây là độ trễ của một người dùng, không phải năng lực chịu tải.
- Thời gian module tính từ lúc gửi yêu cầu đến khi job `done`, hỏi trạng thái mỗi 5 s, nên sai số tới 5 s. Thời gian
  từng giai đoạn lấy từ `modules.generation_timings`, đo chính xác.

## Kết quả

| Môn | Tệp | MB | Tải lên + xử lý, TB (max) | Chat TB / p95 | Trích dẫn TB | Module |
| --- | ---: | ---: | --- | --- | ---: | --- |
| Advanced Programming | 5 | 11,4 | 24,1 s (54,8 s) | 2,9 s / 7,7 s | 1,3 | 45,8 s, 25,9 s |
| Calculus 1 | 6 | 5,8 | 42,1 s (66,7 s) | 1,9 s / 2,2 s | 1,2 | 20,7 s, 30,7 s |
| Operating Systems | 5 | 15,7 | 25,5 s (32,3 s) | 1,6 s / 1,8 s | 1,1 | 30,8 s, 30,7 s |
| Scientific Socialism | 3 | 14,2 | 13,5 s (16,2 s) | 1,9 s / 2,2 s | 1,6 | 26,5 s, 30,8 s |
| **Tổng** | **19** | **47,1** | PDF 34,6 s, PPTX 20,1 s | **2,06 s / 2,35 s** (n=40) | | **TB 30,2 s** (n=8) |

- **Không lỗi nào:** 19/19 tệp `embedded`, 40/40 câu chat trả về 200 và có trích dẫn, 8/8 module `done`. Không câu
  nào bị từ chối theo cụm từ "không đề cập / không có thông tin". Chất lượng câu trả lời chưa được chấm ở lượt này.
- **Jev:** 65 lần rerank trong 8 module, trung bình 0,44 s mỗi lần (max 1,35 s), không lần nào `jev_fallback`.
  Container Jina không nhận request nào.
- **Model:** cả 138 lượt gọi do `google/gemini-3.5-flash-lite` phục vụ.
- **Token mỗi module:** trung bình 62.268 vào, 8.524 ra, khoảng 0,04 $ theo giá OpenRouter. Cả lượt đo (19 tệp,
  40 câu chat, 8 module, các lần đọc lại công thức, và một lượt đầu bị dừng) tốn 0,52 $.
- **Giai đoạn chậm nhất là sinh câu hỏi:** trung bình 11,7 s mỗi module. Module 45,8 s có một lượt sinh câu hỏi
  28,4 s; các giai đoạn còn lại đều 1–4 s, riêng Content của một module OS là 8,5 s.
- Câu chat đầu tiên của lượt chạy mất 11,9 s (khởi động), các câu sau 1,5–2,5 s.

## So với các lần đo trước

| Lần đo | Model | Rerank | Module | Rerank TB |
| --- | --- | --- | --- | --- |
| 2026-10, 5 module lần lượt (report, mục Generation latency) | Claude Haiku 5.5 | Jina, CPU | 368,9 s | 2,0 s |
| Hôm nay, 8 module lần lượt | Gemini 3.5 Flash Lite | Jev | 30,2 s | 0,44 s |

Hai lần khác nhau cả model lẫn reranker, và Haiku dùng khoảng 240.000 token mỗi module so với 62.000 ở đây, nên mức
giảm không thể quy hết cho Jev. Riêng phần rerank nhanh hơn khoảng 4,5 lần, khớp với đo trước
([2026-11-05](2026-11-05-real-llm-and-jev.md): p95 1,48 s so với 3,56 s).

## Hai lỗi cấu hình tìm ra trong lúc đo

- **Đọc lại công thức vẫn dùng gói miễn phí.** `FORMULA_VISION_MODEL` không được compose truyền vào, nên PDF của
  Calculus gọi `gemini-fast` đã hết hạn mức: mỗi trang nhận 429 rồi bỏ qua, gắn `formula_unverified`. Ở lượt đầu
  bị dừng, sáu PDF Calculus mất trung bình 71,0 s (max 115 s); sau khi compose truyền biến và đặt sang
  `gemini-fast-paid`, còn 42,1 s (max 66,7 s). Số trong bảng là của lượt sau.
- **Giới hạn 3 module/ngày cho tài khoản chưa liên kết Moodle** (`NON_TEACHER_DAILY_GENERATION_LIMIT`) chặn module thứ tư
  của lượt đầu. Đây là tính năng, không phải lỗi; lượt sau đặt giới hạn 50 cho tài khoản đo.

## Giới hạn

- Một lần chạy, một người dùng, một máy; chưa có số khi nhiều người dùng cùng lúc với cấu hình này.
- Chưa có Machine Learning (video, audio).
- Chất lượng module và câu trả lời chưa được chấm; tốc độ không nói gì về chất lượng.
- Đi qua OpenRouter thêm một chặng mạng; gọi thẳng Gemini có thể nhanh hơn một chút nhưng bị giới hạn 15 request/phút.

## Chạy lại

```bash
LLM_MODEL=gemini-fast-paid LLM_EXPERIMENT_MODE=true FORMULA_VISION_MODEL=gemini-fast-paid \
  NON_TEACHER_DAILY_GENERATION_LIMIT=50 docker compose up -d --force-recreate backend worker
cd backend && uv run python -u scripts/measure_subjects.py out.jsonl \
  "advanced-programming,calculus 1,operating-system,scientific-socialism"
```
