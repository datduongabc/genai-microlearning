# Đo retrieval và latency trên năm môn, 2026-11-04

Lần đo đầu tiên trên năm môn của đợt đánh giá: `advanced-programming`, `calculus 1`,
`machine-learning`, `operating-system`, `scientific-socialism`. Không so sánh trực tiếp với các
lần đo trên bốn môn cũ.

## Điều kiện đo

- Mã: commit `8f45f25` trở lên của `main` với các PR #57, #58, #59, #62, #63 và bản sửa của #64.
  #54 (judge) và #60 (phân tích đồng thuận) không nằm trong đường đo retrieval và sinh.
- Máy: WSL2, i7-12700H, RTX 3050 Ti Laptop 4 GB, Docker được cấp 7,57 GiB RAM.
- Stack Docker riêng, database riêng. Embedder Qwen3-Embedding-0.6B chạy GPU, reranker Jina v2 INT8
  chạy CPU với hạn mức 4 lõi.
- Dữ liệu thô: `nap_5mon.json`, `truy_van_5mon.json`, `thu_hang_5mon.json`, `ket_qua_5mon.json`,
  `profile_sinh.json`, `profile_bao_cao.json`.

## Retrieval

250 câu hỏi, 50 câu mỗi môn, do `groq-pro` đặt từ chunk mẫu (seed của script). Mỗi câu có đúng một
chunk đúng, nên recall@k ở đây chính là Hit Rate@K.

| Cấu hình | Recall@1 | Recall@3 | Recall@5 | Recall@8 | Recall@10 | MRR@10 | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Dense | 0,800 | 0,960 | 0,984 | 0,992 | 1,000 | 0,879 | 0,909 |
| Hybrid | 0,812 | 0,952 | 0,976 | 0,988 | 0,988 | 0,885 | 0,911 |
| Hybrid + rerank | 0,856 | 0,980 | 0,984 | 0,988 | 0,996 | 0,918 | 0,938 |

Theo từng môn, cấu hình hybrid + rerank:

| Môn | Recall@1 | Recall@8 | MRR@10 | nDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| advanced-programming | 0,800 | 1,000 | 0,893 | 0,921 |
| calculus 1 | 0,760 | 0,980 | 0,857 | 0,892 |
| machine-learning | 0,920 | 1,000 | 0,960 | 0,970 |
| operating-system | 0,860 | 0,980 | 0,920 | 0,936 |
| scientific-socialism | 0,940 | 0,980 | 0,959 | 0,969 |

Kiểm định McNemar trên recall@1, gộp năm môn:

| So sánh | Chỉ cấu hình sau đúng | Chỉ cấu hình trước đúng | p |
| --- | ---: | ---: | ---: |
| Hybrid + rerank so với hybrid | 27 | 16 | 0,126 |
| Hybrid + rerank so với dense | 31 | 17 | 0,060 |
| Hybrid so với dense | 18 | 15 | 0,728 |

Không so sánh nào đạt p < 0,05, nên số liệu này chưa đủ để kết luận rerank hay BM25 cải thiện
recall@1. Ở `calculus 1`, hybrid còn thấp hơn dense (0,64 so với 0,70).

Giới hạn của phép đo:

- Câu hỏi do LLM đặt từ chính chunk đúng và cùng ngôn ngữ với tài liệu, nên dễ hơn câu hỏi thật của
  sinh viên. Recall@10 gần 1,0 phản ánh điều này.
- Chưa đo câu hỏi tiếng Việt trên tài liệu tiếng Anh, là trường hợp đã thấy lỗi ở báo cáo
  2026-10-14.
- Mỗi câu chỉ có một chunk được coi là đúng, nên một chunk khác cũng trả lời được vẫn bị tính sai.

## Nạp tài liệu

937 chunk từ 23 tệp. Tổng 375 giây đọc và 28 giây nhúng, chạy tuần tự từng tệp.

| Môn | Tệp | Chunk | Đọc trung bình/tệp (s) | Nhúng/chunk (ms) |
| --- | ---: | ---: | ---: | ---: |
| advanced-programming | 5 | 74 | 1,9 | 33 |
| calculus 1 | 6 | 292 | 46,2 | 35 |
| machine-learning | 4 | 349 | 9,4 | 21 |
| operating-system | 5 | 147 | 8,5 | 37 |
| scientific-socialism | 3 | 75 | 2,8 | 31 |

`calculus 1` chậm nhất vì các trang có công thức vỡ được đọc lại qua vision trên gói Gemini miễn
phí, nên thời gian này gồm cả chờ hạn mức. Thời gian của `machine-learning` phụ thuộc mạng và độ trễ
của nhà cung cấp Whisper.

## Sinh module

Model `claude-fast` (Claude Haiku 5.5) ở chế độ thí nghiệm. Toàn bộ 228 bản ghi model là
`claude-haiku-5-5`, không có fallback. Chín module đều thành công. Hai lượt dùng tài liệu khác nhau
nhưng đều trải qua nhiều môn.

| Chỉ số | 1 lượt một lúc (n = 5) | 4 lượt đồng thời (n = 4) |
| --- | ---: | ---: |
| Thời gian một module, trung bình (s) | 368,9 | 315,1 |
| Thời gian một module, lớn nhất (s) | 429,3 | 345,9 |
| Module mỗi giờ | 9,76 | 41,63 |
| Một lệnh gọi LLM, trung bình (s) | 13,5 | 11,1 |
| Một lệnh gọi LLM, p95 (s) | 19,7 | 17,0 |
| Một lần rerank, trung bình (s) | 2,0 | 4,7 |
| Một lần rerank, p95 (s) | 3,6 | 9,3 |
| Prompt token mỗi module, trung bình | 207.810 | 188.795 |
| Completion token mỗi module, trung bình | 70.444 | 58.459 |

Thời gian trung bình theo giai đoạn ở lượt tuần tự:

| Giai đoạn | Giây | Lệnh gọi LLM |
| --- | ---: | ---: |
| Câu hỏi (Quiz và Practice) | 275,6 | 17,2 |
| Content | 50,4 | 3,6 |
| Truy xuất cho Content | 11,8 | 1,0 |
| Example | 8,3 | 1,0 |
| Learning Objective | 7,2 | 1,0 |
| Planner | 7,1 | 1,0 |
| Slides | 5,8 | 1,0 |
| Truy xuất khác | 2,6 | 0 |

Giai đoạn câu hỏi chiếm khoảng 75% thời gian sinh. Trong một lần truy xuất, nhúng câu hỏi trung bình
0,09 s, BM25 0,006 s, tìm vector 0,01 s và RRF không đáng kể, còn rerank mất 2,0 s.

Tranh chấp tài nguyên: reranker đạt đỉnh 399% CPU, tức chạm hạn mức 4 lõi, và thời gian rerank trung
bình tăng khoảng 2,3 lần khi có 4 lượt đồng thời. Đỉnh bộ nhớ: reranker 1,0 GiB, embedder 565 MiB
RAM, gateway 387 MiB, runner 388 MiB, Postgres 65 MiB. GPU dùng tối đa 1.283 MiB VRAM.

TTFT không đo được: mọi lệnh gọi sinh là structured output không stream, nên thời gian trên là của
cả lệnh gọi.

Giới hạn của phép đo: mẫu nhỏ, lượt tuần tự và đồng thời dùng tài liệu khác nhau, và số liệu chỉ phản
ánh một máy cùng hạn mức tài khoản tại thời điểm đo. Chưa đo tải từ 8 lượt đồng thời trở lên và chưa
đo chat. `raw_text` của chín tài liệu được đọc lại riêng cho bước Planner, nên hai tệp video được
phiên âm thêm một lần ngoài bước nạp.
