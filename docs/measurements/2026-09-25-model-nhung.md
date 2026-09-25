# Chọn model nhúng: Gemini hay model chạy tại chỗ

Ngày đo: 2026-09-25.

## Vì sao đo

Gói miễn phí của Gemini chỉ nhúng 1.000 văn bản mỗi ngày, tính theo từng chunk.
Kho `docs/documents` có 11 môn, khoảng 100 tài liệu và 6 video, tức vài nghìn chunk, nên nhúng hết mất khoảng 3 ngày.
Lần nào đổi tầng đọc hay cách cắt chunk cũng phải nhúng lại từ đầu, lại thêm 3 ngày.
Câu hỏi: có model nào chạy tại chỗ, miễn phí, truy hồi không kém Gemini không.

## Cách đo

- Tập chunk: đúng các chunk đang có trong database benchmark (`benchmark_rerank`), cộng chunk của 9 tệp đầu môn chemistry1 đọc bằng tầng đọc hiện hành.
  Mọi model nhúng cùng một văn bản, nên khác biệt đo được là của riêng model.
- Câu hỏi: 250 câu cũ của phép đo rerank (`docs/measurements/truy_van.json`), 50 câu mỗi môn, do LLM đặt từ một chunk mà không thấy ví dụ mẫu nào.
- Đáp án đúng: chính chunk sinh ra câu hỏi nếu nó còn trong database (advanced-programming, dsa, operating-system).
  Với calculus1 và chemistry1, chunk cũ đã đổi sau khi sửa tầng đọc, nên đáp án là mọi chunk mới cùng tệp có chung trang với chunk cũ.
- Chỉ đo dense (cosine), vì BM25 và rerank không đổi theo model.
- Gemini dùng vector đã lưu sẵn cho chunk, chỉ nhúng câu hỏi, đi qua gateway như ứng dụng thật (768 chiều).
- Script: `so_sanh_nhung.py` trong thư mục nháp của phiên làm việc.

## Kết quả

Ba môn tiếng Anh, 150 câu, cùng tập chunk:

| Model | R@1 | R@5 | R@10 | MRR |
|---|---|---|---|---|
| Gemini embedding-001 | 0.673 | 0.947 | 0.987 | 0.787 |
| Qwen3-Embedding-0.6B | 0.693 | 0.933 | 0.973 | 0.800 |
| Qwen3-Embedding-0.6B, câu hỏi kèm hướng dẫn | 0.713 | 0.947 | 0.987 | 0.818 |
| bge-m3 | 0.633 | 0.880 | 0.933 | 0.751 |

Hai môn tiếng Việt, 100 câu, MRR:

| Model | calculus1 | chemistry1 |
|---|---|---|
| Gemini embedding-001 | (chờ hạn mức) | (chờ hạn mức) |
| Qwen3-Embedding-0.6B | 0.727 | 0.901 |
| Qwen3-Embedding-0.6B, câu hỏi kèm hướng dẫn | 0.704 | 0.882 |
| bge-m3 | 0.724 | 0.839 |

Cắt vector Qwen3 từ 1024 xuống 768 chiều làm MRR gộp 5 môn giảm từ 0.807 xuống 0.794, R@5 không đổi (0.928).

## Chi phí chạy

- Bản ONNX trên CPU (Text Embeddings Inference 1.9.4): 1,1 giây mỗi chunk, 0,3 giây mỗi câu hỏi, khoảng 2,9 GB RAM với lô 1.024 token.
- Bản gốc trên GPU RTX 3050 Ti: khoảng 0,06 giây mỗi chunk.
- Hai bản cho ra cùng một vector (cosine 1,0000 trên 64 chunk), nên đổi qua lại không phải nhúng lại.
- Bộ chạy candle trên CPU chậm tới mức không dùng được: hơn 2 phút cho 16 chunk.

## Quyết định

Dùng Qwen3-Embedding-0.6B bản ONNX chạy trên CPU, không kèm hướng dẫn truy vấn, cắt về 768 chiều.
Hướng dẫn truy vấn tiếng Anh giúp ba môn tiếng Anh nhưng làm hai môn tiếng Việt kém đi, gộp lại thì ngang nhau, mà người dùng chính là giảng viên Việt Nam.
Giữ 768 chiều để không phải đổi cột và chỉ mục của database, đổi lại mất khoảng 0,01 MRR.
bge-m3 kém rõ ở dsa (MRR 0.601) nên bị loại.
