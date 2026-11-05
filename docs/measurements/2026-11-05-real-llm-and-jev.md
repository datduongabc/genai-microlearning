# Đo tải với LLM thật và so sánh Jev với Jina, 2026-11-05

Kết luận chính: rerank bằng Jina trên CPU là nút thắt của cả sinh module và chat. Thay bằng Jev gỡ được nút thắt này
trên máy đo và giữ chất lượng truy hồi trên bộ đánh giá hiện có. Chưa có bằng chứng ổn định 30 phút, vì hết credit LLM.
Các số dùng mock LLM trong `2026-11-05-generation-throughput.md` không dùng làm công suất của worker.

## Điều kiện đo

- Máy: WSL2, i7-12700H, RTX 3050 Ti Laptop 4 GB, Docker được cấp 7,57 GiB RAM. Mọi dịch vụ chạy chung một máy.
- Model: `claude-fast` (Haiku) và `openai-fast` (Luna) qua LiteLLM, `LLM_EXPERIMENT_MODE=true`, không fallback. Cấu hình
  router giống production: `num_retries 1`, `allowed_fails 0`, `cooldown_time 60`.
- Tải: mỗi module sinh từ một document riêng, cùng một bài giảng (`operating-system/Chapter 3_Processes.pdf`, 33 chunk).
  `N worker × 4` là N worker, mỗi worker chạy 4 job cùng lúc.
- Rerank: Jina v2 INT8 trên CPU, hạn mức 4 lõi; hoặc Jev `jev-1.14.0` gọi riêng cho từng chunk.
- Dữ liệu thô: `load-test/real/` và `jev-rerank/`.

## Sinh module

| Cấu hình | Model | Rerank | Module | Xong | Modules/giờ | Lần chạy lại job | LLM TB | Rerank TB | Rerank p95 |
| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1×4 | Haiku | Jina | 4 | 4 | 74,9 | 0 | 11,1 s | 6,9 s | |
| 2×4 | Haiku | Jina | 8 | 7 | 28,0 | 17 | 11,2 s | 6,2 s | |
| 4×4 | Haiku | Jina | 16 | 11 | 29,8 | 32 | 36,9 s | 21,3 s | |
| 4×4, 2 reranker | Haiku | Jina | 16 | 9 | 23,6 | 43 | 35,0 s | 19,8 s | |
| 1×4 | Luna | Jina | 4 | 4 | 90,4 | 0 | 11,8 s | 5,1 s | 10,2 s |
| 2×4 | Luna | Jina | 8 | 8 | 107,7 | 0 | 11,7 s | 12,0 s | 23,8 s |
| 4×4 | Luna | Jina | 16 | 16 | 162,4 | 0 | 12,2 s | 32,4 s | 48,3 s |
| 8×4 | Luna | Jina | 32 | 32 | 182,5 | 0 | 12,5 s | 80,8 s | 123,5 s |
| 4×4, 2 reranker | Luna | Jina | 16 | 16 | 180,7 | 0 | 12,3 s | 28,3 s | 45,4 s |
| 4×4, 2 embedder | Luna | Jina | 16 | 16 | 138,9 | 0 | 13,4 s | 32,9 s | 44,8 s |
| 4×4, 2 gateway | Luna | Jina | 16 | 16 | 108,8 | 0 | 13,0 s | 55,0 s | |
| 4×4 | Luna | Jev | 16 | 16 | 319,3 | 0 | 13,5 s | 0,9 s | 1,25 s |
| 8×4, lần 1 | Luna | Jev | 32 | 32 | 637,7 | 0 | 13,5 s | 0,9 s | 1,38 s |
| 8×4, lần 2 | Luna | Jev | 32 | 32 | 336,0 | 0 | 22,2 s | 1,3 s | 2,74 s |
| 8×4, lần 3 | Luna | Jev | 32 | 32 | 539,1 | 0 | 14,1 s | 1,1 s | 1,83 s |

Đọc bảng:

- Các dòng Jev 8×4 có 32 module vừa đủ 32 chỗ nên không có hàng đợi. Đây là tốc độ của một đợt, không phải công suất duy trì.
- Mỗi cấu hình Jina chạy một lần. Bốn lần 4×4 của Luna với Jina dao động từ 108,8 đến 180,7 modules/giờ, nên chưa kết luận được
  thêm reranker, embedder hay gateway có tác dụng gì. Trên bốn lần đó, dao động đi theo thời gian rerank (28–55 s), còn thời gian LLM gần
  như không đổi. Với Jev, lần chậm nhất lại đi cùng LLM chậm hơn. Cả hai là quan sát, chưa xác minh nguyên nhân.
- Haiku dùng khoảng 240.000 token mỗi module, Luna khoảng 115.000. Chưa rõ vì sao. Token của lần chạy thất bại không được tính.

Các lần không dùng làm số đo:

- `embedder-2-worker-4x4` của Haiku: 0/16, hết credit Anthropic.
- `worker-8x4-queue96` của Luna với Jev: hết credit OpenAI giữa chừng, nên 187,2 modules/giờ là sai. Trước khi hết credit, 58 module
  xong trong khoảng 5,5 phút, khoảng 630 modules/giờ, mỗi module khoảng 151 s, 0 lần chạy lại với các job xong.
- Haiku thiếu các nấc 2 gateway, chat và 8 worker vì hết credit.

## Chat

Luna, 1 worker, 1 reranker. Độ trễ tính đến khi nhận đủ câu trả lời vì chat chưa stream, nên chưa so được với mục tiêu TTFT.

| API | Kết nối | Tin nhắn | Jina: tin/phút | Jina: TB, p95 | Jina: 503 | Jev: tin/phút | Jev: TB, p95 | Jev: 503 |
| ---: | ---: | ---: | ---: | --- | ---: | ---: | --- | ---: |
| 1 | 25 | 50 | 22,4 | 53,0 s, 71,2 s | 0 | 151,6 | 8,7 s, 11,0 s | 0 |
| 2 | 50 | 100 | 22,5 | 97,7 s, 132,9 s | 5 | 280,6 | 9,2 s, 11,7 s | 0 |
| 4 | 100 | 200 | 22,5 | 164,9 s, 239,1 s | 60 | 462,3 | 10,8 s, 13,4 s | 0 |

Với Jina, reranker dùng khoảng 350% CPU còn API dùng 1–2%. Nguồn lỗi 503 chưa được xác định từ log, nhưng chúng biến mất khi hết nghẽn rerank.

## Chất lượng truy hồi

30 ứng viên từ hybrid search, cùng cho mọi phương án. Câu hỏi do LLM đặt từ một chunk, mỗi câu có một chunk đúng. 50 câu (cứ năm câu lấy một) chỉ để chọn cách
gọi Jev; gọi từng chunk tốt hơn gom một lần (MRR 0,942 so với 0,906). Câu tiếng Việt do Luna dịch từ 126 câu tiếng Anh, tài liệu vẫn tiếng Anh.

Mức chấp nhận chốt trước khi có kết quả 200 câu: so với Jina, Recall@8 giảm tối đa 0,01; MRR@10 giảm tối đa 0,02 và phải cao hơn không rerank.

| Bộ | Phương án | Recall@1 | Recall@8 | MRR@10 | p95 |
| --- | --- | ---: | ---: | ---: | ---: |
| 200 câu | Không rerank | 0,795 | 0,990 | 0,877 | |
| 200 câu | Jina | 0,845 | 0,985 | 0,909 | 3,56 s |
| 200 câu | Jev | 0,925 | 0,995 | 0,952 | 1,48 s |
| 126 câu tiếng Việt | Không rerank | 0,683 | 0,952 | 0,787 | |
| 126 câu tiếng Việt | Jina | 0,802 | 0,976 | 0,873 | 2,89 s |
| 126 câu tiếng Việt | Jev | 0,873 | 0,976 | 0,918 | 0,46 s |

Chênh lệch Jev trừ Jina, khoảng tin cậy 95% bootstrap theo cặp:

| Bộ | Recall@8 | MRR@10 |
| --- | --- | --- |
| 200 câu | +0,010 (-0,010 đến +0,030) | +0,044 (+0,007 đến +0,080) |
| 126 câu tiếng Việt | 0,000 (-0,024 đến +0,024) | +0,046 (-0,008 đến +0,099) |

Jev đạt mức chấp nhận trên cả hai bộ. Với câu tiếng Việt, khoảng tin cậy của Recall@8 rộng hơn mức cho phép nên chưa chứng minh được tương đương.

**Jina không lặp lại y hệt.** Chạy lại với cùng câu hỏi và ứng viên, Jina đổi thứ hạng ở 12/250 câu và 10/126 câu. Lần chạy lại cho:

| Bộ | Phương án | Recall@1 | Recall@8 | MRR@10 | Adaptive sàn 8: chứa chunk đúng | Adaptive sàn 8: số chunk TB |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 250 câu | Không rerank | 0,812 | 0,988 | 0,885 | 0,988 | 8,00 |
| 250 câu | Jina | 0,880 | 0,992 | 0,929 | 0,996 | 8,45 |
| 250 câu | Jev | 0,924 | 0,992 | 0,950 | 0,992 | 8,53 |
| 126 câu tiếng Việt | Không rerank | 0,683 | 0,952 | 0,787 | 0,952 | 8,00 |
| 126 câu tiếng Việt | Jina | 0,786 | 0,960 | 0,866 | 0,968 | 8,55 |
| 126 câu tiếng Việt | Jev | 0,873 | 0,976 | 0,918 | 0,984 | 8,83 |

Các số của Jev ở lần này lấy từ cache, nên độ lặp lại của Jev chưa được đo. Trên 250 câu, mức MRR hơn Jina còn +0,021 thay vì +0,044 của riêng 200 câu.
Adaptive-k với điểm của Jev cho kết quả tương đương Jina, không cần đổi ngưỡng.

## Độ lặp lại và giới hạn của Jev

Chạy lại 250 câu không dùng cache: 6/250 câu đổi hạng, lệch tối đa 2 hạng. Recall@1, Recall@8 giữ nguyên, MRR@10 0,950 và 0,950.
Trung bình 0,29 s, p95 0,38 s. Cùng cách so sánh, Jina đổi hạng ở 12/250 câu.

Gọi trực tiếp bằng văn bản tự viết dài khoảng 1.400 ký tự, mỗi mức 20 s, từ một tiến trình trên máy đo (`jev-rerank/stress.json`):

| Đồng thời | Lệnh gọi/giây | Token/giây | p50 | p95 | Bị từ chối |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 8 | 34,7 | 24.409 | 0,22 s | 0,26 s | 0 |
| 16 | 70,0 | 49.299 | 0,22 s | 0,27 s | 0 |
| 32 | 140,6 | 99.008 | 0,22 s | 0,27 s | 0 |
| 64 | 269,3 | 189.556 | 0,22 s | 0,30 s | 0 |
| 128 | 70,4 | 49.596 | 0,89 s | 5,57 s | 0 |
| 256 | 61,1 | 42.979 | 3,70 s | 7,77 s | 0 |
| 512 | 60,7 | 42.755 | 6,87 s | 17,48 s | 0 |

Giữ 64 đồng thời thêm 45 s: 12.484 lệnh gọi, khoảng 278 lệnh gọi/giây ổn định, p95 khoảng 0,27 s, không lỗi (`jev-rerank/stress-hold.json`).
Vậy mức sụt từ 128 không phải hạn mức bị dùng hết. Chưa xác định được nó đến từ phía Jev hay từ máy đo, vì chỉ có một máy gọi và không có lỗi 429 nào.

## Chat qua SSE

Jev thật, LLM giả với từ đầu tiên sau 1,5 s và phần trả lời 6 s. Bảng này chỉ đo phần của hệ thống, chưa phải TTFT trên model thật.

| API | Tin đến/giây | Tin xử lý/phút | Từ đầu tiên, p95 |
| ---: | ---: | ---: | ---: |
| 1 | 1 | 52,0 | 2,20 s |
| 1 | 2 | 102,8 | 2,04 s |
| 1 | 4 | 204,3 | 2,14 s |
| 1 | 8 | 305,5 | 15,74 s |
| 1 | 12 | 245,5 | 58,12 s |
| 2 | 8 | 407,4 | 2,56 s |
| 4 | 8 | 407,8 | 2,23 s |
| 4 | 16 | 251,8 | 97,9 s |

Riêng các đợt mở cùng lúc 25 đến 400 stream, đo trước khi sửa giới hạn gọi Jev, không có lỗi nhưng 400 stream phải chờ từ đầu tiên đến p95 118 s.

## Kiểm soát tải LLM với 429 thật

Groq `gpt-oss-120b`, 32 module, 4 worker × 4, dữ liệu thô `load-test/real/real-groq-pro-l{0,1}-r1.json`.

| Cấu hình | Xong | Lỗi | Modules/giờ | Thời gian | Giá trị `job_attempts` | Bị 429 và chờ lại | Rerank quay về hybrid |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| L0, lần 1 | 1 | 31 | 17,6 | 204,6 s | 159 | 0 | 0/9 |
| L1, lần 1 | 30 | 2 | 241,2 | 447,8 s | 4 | 141 | 165/246 |

- Không đạt kế hoạch 3 lần cho mỗi cấu hình: TypeSafe trả 402 hết credit trong lần L1, và tác giả quyết định không chi thêm. L2 chưa đo.
- L0 và L1 không cùng điều kiện rerank. Đây là bằng chứng một lần chạy, đủ để bỏ L0 nhưng không phải công suất ổn định.
- Hai module L1 lỗi vì model viết sai mã trích dẫn, ví dụ `[ S2 ]`, không phải vì tải.
- L1 dùng khoảng 407.000 token/phút trong khi header báo hạn mức 250.000. Chưa rõ nguyên nhân, nên các trần tính theo token/phút chưa được kiểm chứng.
- Khi Jev trả 402, không job nào lỗi vì rerank, nhưng hệ thống không có cảnh báo khi chất lượng truy hồi giảm.

## Chi phí Jev

Theo giá trong tài liệu TypeSafe, $0,042 cho 1 triệu token đầu vào; điều khoản hợp đồng mô tả theo credit nên cần đối chiếu trong console.
Mỗi retrieval khoảng 16.200 token, khoảng $0,0007. Một module khoảng 8 retrieval, khoảng $0,005.

## Giới hạn của lần đo

- Chỉ đo trên một máy, với một bài giảng và một loại tải. Chưa đo trên AWS.
- Chưa chạy ổn định 30 phút, chưa chạm giới hạn quota LLM hay rate limit Jev.
- Bộ câu hỏi do LLM đặt và dịch, không phải câu hỏi thật của sinh viên. Câu hỏi cho Jev cũng hỏi "đoạn này có đủ thông tin để trả lời không", gần với cách tạo nhãn, nên có thể có lợi cho Jev.
- Chưa so với Jina trên GPU. Chưa dùng judge chấm module sinh từ ngữ cảnh do Jev chọn.
- Jev chạy ở máy chủ Mỹ. TypeSafe nói không dùng đầu vào để huấn luyện, nhưng hợp đồng cho phép tạo telemetry từ dữ liệu và không nêu thời hạn lưu. Jev cần nhà trường chấp thuận trước khi dùng với tài liệu thật.
- Jina INT8 có giấy phép CC-BY-NC-4.0, chỉ phù hợp cho nghiên cứu phi thương mại.
