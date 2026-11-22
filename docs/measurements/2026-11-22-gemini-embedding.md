# Gemini Embedding qua OpenRouter thay Qwen3 tự host, 2026-11-22

## Vì sao đo

Embedder Qwen3-Embedding-0.6B chạy trên CPU cần một task Fargate 4 vCPU, 8 GB chạy suốt ngày.
Ở 2 vCPU, một PDF 1,2 MB giữ embedder ở 98% CPU hơn 8 phút và ECS thay task giữa chừng (`infra/README.md`).
Khi đo sinh nội dung đồng thời trên AWS, embedder lên tới 98,9% CPU lúc nạp tài liệu.
Lần so trước (`2026-09-25-model-nhung.md`) loại Gemini vì gói miễn phí chỉ nhúng 1.000 chunk mỗi ngày, không vì chất lượng.
Qua OpenRouter thì không có hạn mức đó, giá khoảng $0,15 cho một triệu token.
Câu hỏi: Gemini Embedding 001 có truy xuất kém Qwen3 trên bộ câu hỏi hiện tại không.

## Cách đo

- Cùng database của lần đo `2026-11-12-chunking-remeasure.md` (`benchmark_chunking`), chép sang `benchmark_gemini`, xoá vector và nhúng lại toàn bộ 1.857 chunk qua alias gateway mới, 768 chiều.
- Cùng 250 câu hỏi (`chunking/truy_van.json`), cùng Jev `jev-1.14.0`, 30 ứng viên hybrid.
- Script: `backend/scripts/benchmark_chunking.py danh-gia` với `EMBEDDING_MODEL` trỏ alias Gemini.
- Đáp án Jev được cache theo (câu hỏi, đoạn); 1.481 cặp mới (ứng viên mà Qwen3 không đưa vào top 30) nằm trong `chunking-gemini/jev_cache_added.jsonl`.
- Chi phí: dưới $0,10 cho nhúng, khoảng $0,02 cho Jev. Nhúng 1.857 chunk mất 106 s.

## Kết quả trên cách cắt hiện tại

| Cấu hình | Model nhúng | R@1 | R@3 | R@8 | MRR@10 | nDCG@10 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| Dense | Qwen3 | 0,792 | 0,932 | 0,980 | 0,868 | 0,899 |
| Dense | Gemini | 0,816 | 0,932 | 0,992 | 0,881 | 0,909 |
| Hybrid | Qwen3 | 0,828 | 0,964 | 0,988 | 0,894 | 0,918 |
| Hybrid | Gemini | 0,840 | 0,956 | 0,984 | 0,899 | 0,922 |
| Hybrid + Jev | Qwen3 | 0,884 | 0,984 | 0,992 | 0,932 | 0,947 |
| Hybrid + Jev | Gemini | 0,884 | 0,980 | 0,992 | 0,931 | 0,947 |

MRR@10 theo môn:

| Môn | Dense Qwen3 | Dense Gemini | Hybrid + Jev Qwen3 | Hybrid + Jev Gemini |
| --- | ---: | ---: | ---: | ---: |
| advanced-programming | 0,847 | 0,860 | 0,913 | 0,923 |
| calculus 1 | 0,924 | 0,843 | 0,990 | 0,990 |
| machine-learning | 0,925 | 0,914 | 0,863 | 0,854 |
| operating-system | 0,791 | 0,863 | 0,940 | 0,940 |
| scientific-socialism | 0,856 | 0,925 | 0,952 | 0,949 |

- Với cấu hình hệ thống thật dùng (hybrid + Jev), hai model cho cùng kết quả trong khoảng 0,004.
- Riêng dense, Gemini kém ở Giải tích (tiếng Việt, nhiều công thức) nhưng tốt hơn ở Hệ điều hành và CNXH khoa học; BM25 và Jev bù phần chênh.
- Gemini với hybrid + Jev so với dense ở hạng 1: đúng thêm 36, mất 19 (McNemar p = 0,030).
- Adaptive-k sàn 8 giữ được chunk đúng ở 0,992 số câu, trung bình 8,60 chunk.

## Quyết định

`embed-default` trỏ sang `openrouter/google/gemini-embedding-001`, ghim Google Vertex, không fallback, `data_collection: deny`.
Bỏ service embedder khỏi Terraform: không còn task 4 vCPU, 8 GB, image ECR 2,4 GB và secret của nó.
Qwen3 giữ ở compose sau profile `local-embedder` (alias `embed-qwen3`) cho nơi không cho gửi tài liệu ra ngoài.
Migration `0053_gemini_vectors` xoá vector cũ; chạy `scripts/reembed_chunks.py` để nhúng lại.
Đánh đổi: toàn bộ tài liệu được gửi tới Google để nhúng, trong khi trước đây chỉ các đoạn được truy xuất mới đi qua Jev và LLM.
