# Đo lại tầng rerank với embedding Qwen3, 2026-09-26

## Vì sao đo lại

Hai lần đo trước (`2026-09-24-rerank.md`) không đủ để quyết giữ hay bỏ cross-encoder `BAAI/bge-reranker-base`:

- Mọi lần đo rerank dùng embedding Gemini, trong khi sản phẩm nay nhúng bằng Qwen3-Embedding-0.6B (`2026-09-25-model-nhung.md`).
  Reranker chấm lại ứng viên do nhánh dense đưa lên, nên đổi model nhúng là đổi đầu vào của nó.
- Hai môn tiếng Việt được đo **trước** bản sửa công thức (`f5b587b`), lúc 88% số dòng của `calculus1` còn là mảnh vỡ.
  Bằng chứng "rerank hại môn nhiều công thức" vì vậy là trên văn bản vỡ.

Trong khi đó reranker kéo torch và sentence-transformers vào ảnh backend (5,04 GB).

## Quy tắc quyết định (viết trước khi chạy)

Quy tắc này được chốt với sinh viên và ghi lại ở đây trước khi có bất kỳ con số nào.

**Giữ reranker và Adaptive-k, trừ khi phép đo cho thấy chúng làm truy hồi kém đi có ý nghĩa thống kê (p < 0,05)** trên gộp 5 môn, hoặc trên bất kỳ môn tiếng Việt nào (`calculus1`, `chemistry1`).
Dù giữ hay bỏ, reranker cũng ra khỏi ảnh backend.

Cụ thể hoá thành phép kiểm:

1. **Reranker.** So `hybrid+rerank` với `hybrid` bằng McNemar chính xác hai phía trên từng cặp câu, ở hai chỉ số:
   recall@1 (chunk đúng đứng đầu, như các lần đo trước) và recall@10 (chunk đúng có trong 10 chunk đầu, điều mà LO và Content quan tâm vì chúng lấy nhiều chunk).
   Kiểm trên ba nhóm: gộp 5 môn, `calculus1`, `chemistry1`.
   Bỏ reranker nếu ở bất kỳ nhóm nào, với bất kỳ chỉ số nào, `hybrid+rerank` **kém hơn** `hybrid` với p < 0,05.
   Ngược lại thì giữ, kể cả khi reranker không hơn có ý nghĩa.
2. **Adaptive-k.** So tập chunk mà Adaptive-k chọn cho LO và Content (`retrieve(top_k=None, min_k=8)`) với phương án thay thế nếu bỏ nó, là hybrid lấy cố định 8 chunk.
   Chỉ số: chunk đúng có nằm trong tập được chọn không; McNemar chính xác hai phía, trên ba nhóm như trên.
   Bỏ Adaptive-k nếu nó kém hơn với p < 0,05 ở bất kỳ nhóm nào.
   Adaptive-k cắt theo điểm của reranker, nên bỏ reranker thì Adaptive-k cũng đi theo.
3. Không hiệu chỉnh cho nhiều phép kiểm. Việc này làm quy tắc dễ ra "bỏ" hơn, tức nghiêng về phía thận trọng với reranker.

## Cách đo

Mọi thứ chạy tại chỗ, trong một stack Docker tạm riêng cho phép đo: pgvector, embedder Qwen3 (cùng ảnh, cùng commit model với `devops/docker-compose.yml`), gateway, và ảnh backend để có tesseract.
Script: `backend/scripts/benchmark_rerank.py`, ba pha `nap`, `sinh-truy-van`, `danh-gia`.

### Tài liệu và chunk

Cùng tập tệp với các lần trước (hằng `MON` trong script), đọc và cắt bằng tầng đọc hiện hành (sau `f5b587b`), nhúng bằng Qwen3-Embedding-0.6B, 768 chiều:

| môn | ngôn ngữ | tệp | chunk |
|---|---|---|---|
| `advanced-programming` | Anh | 11 pptx | 134 |
| `dsa` | Anh | 5 pdf | 224 |
| `operating-system` | Anh | 7 pdf | 202 |
| `calculus1` | Việt | 8 pdf | 277 |
| `chemistry1` | Việt | 9 pdf | 234 |
| tổng | | | 1.071 |

### Bộ câu hỏi phải sinh lại

Kế hoạch ban đầu là dùng lại 250 câu cũ (`truy_van.json`) và ánh xạ đáp án sang chunk mới theo trang, như `2026-09-25-model-nhung.md` đã làm.
Không làm được: `truy_van.json` chỉ lưu mã chunk, còn tệp và trang của chunk đó nằm trong database `benchmark_rerank`, và database ấy (container `do-an-test-db` cùng volume của nó) đã bị xoá khi dọn ổ đĩa ngày 2026-09-26.
Không còn nơi nào ghi mã chunk cũ ứng với trang nào, nên đáp án cũ không chấm lại được.
Gán đáp án bằng một model (lấy chunk mà một model khác cho là hợp nhất) thì bị loại, vì nhãn sẽ nghiêng về cấu hình nào giống model gán nhãn.

Vì vậy bộ câu hỏi được sinh lại từ chunk mới, bằng đúng pha `sinh-truy-van` cũ không đổi gì: cùng lời nhắc không có ví dụ mẫu, cùng hạt giống 20260923, 50 chunk mỗi môn chọn ngẫu nhiên trong các chunk từ 300 ký tự, cùng model `groq-pro` (gpt-oss-120b, gói miễn phí của Groq, không tốn đồng nào và không đụng hạn mức Gemini).
Được thêm một điều: đáp án đúng là chính chunk sinh ra câu hỏi, không phải xấp xỉ theo trang.
Bộ mới: `truy_van_qwen3.json`; thứ hạng từng câu: `thu_hang_qwen3.json`; bảng số: `ket_qua_qwen3.json`.
Ba tệp cũ giữ nguyên làm lịch sử.
Con số ở đây không so thẳng được với các lần đo trước, vì chunk, câu hỏi và model nhúng đều khác.

### Cấu hình

| cấu hình | đường đi |
|---|---|
| `dense` | chỉ cosine similarity qua pgvector |
| `hybrid` | dense + BM25, trộn bằng RRF (k=60), 30 ứng viên mỗi nhánh |
| `hybrid+rerank` | `retrieve()` của sản phẩm: như trên, rồi cross-encoder chấm lại 30 ứng viên |
| Adaptive-k | `retrieve(top_k=None, min_k=8)`, đúng lời gọi của LO và Content, so với 8 chunk đầu của `hybrid` |

Reranker trong lần đo là `bge-reranker-base` nạp bằng sentence-transformers ngay trong tiến trình, tức đúng đường chạy của sản phẩm lúc đo.

## Kết quả

250 câu, 50 mỗi môn.

| cấu hình | recall@1 | recall@3 | recall@5 | recall@10 | MRR@10 | nDCG@10 |
|---|---|---|---|---|---|---|
| dense | 0,700 | 0,876 | **0,944** | **0,980** | 0,801 | 0,845 |
| hybrid | 0,708 | 0,880 | 0,924 | 0,964 | 0,801 | 0,841 |
| hybrid+rerank | **0,784** | **0,880** | 0,920 | 0,964 | **0,842** | **0,871** |

Recall@1 và recall@10 theo môn:

| môn | dense | hybrid | hybrid+rerank |
|---|---|---|---|
| `advanced-programming` | 0,80 / 1,00 | 0,82 / 1,00 | **0,92** / 1,00 |
| `dsa` | 0,54 / 0,96 | 0,50 / 0,90 | **0,58** / 0,92 |
| `operating-system` | 0,76 / 1,00 | 0,80 / 1,00 | **0,90** / 1,00 |
| `calculus1` | 0,66 / 0,94 | 0,66 / 0,94 | 0,64 / 0,92 |
| `chemistry1` | 0,74 / 1,00 | 0,76 / 0,98 | **0,88** / 0,98 |

### Kiểm định theo cặp

McNemar chính xác hai phía; hai cột giữa đếm số câu chỉ một bên đúng.

| so sánh | nhóm | chỉ bên sau đúng | chỉ bên trước đúng | p |
|---|---|---|---|---|
| rerank so với hybrid, recall@1 | gộp 5 môn | 35 | 16 | **0,011** |
| | `calculus1` | 6 | 7 | 1,000 |
| | `chemistry1` | 7 | 1 | 0,070 |
| | `advanced-programming` | 6 | 1 | 0,125 |
| | `dsa` | 9 | 5 | 0,424 |
| | `operating-system` | 7 | 2 | 0,180 |
| rerank so với hybrid, recall@10 | gộp 5 môn | 3 | 3 | 1,000 |
| | `calculus1` | 1 | 2 | 1,000 |
| | `chemistry1` | 0 | 0 | 1,000 |
| Adaptive-k so với hybrid lấy 8 | gộp 5 môn | 6 | 6 | 1,000 |
| | `calculus1` | 3 | 3 | 1,000 |
| | `chemistry1` | 1 | 1 | 1,000 |
| hybrid so với dense, recall@1 | gộp 5 môn | 17 | 15 | 0,860 |
| hybrid so với dense, recall@10 | gộp 5 môn | 2 | 6 | 0,289 |
| rerank so với dense, recall@1 | gộp 5 môn | 41 | 20 | **0,010** |

Adaptive-k chọn trung bình 8,7 chunk (trung vị 8, nhiều nhất 27); cả nó lẫn hybrid lấy 8 đều chứa chunk đúng ở 237 trên 250 câu.
Bảng đầy đủ theo từng môn nằm trong `ket_qua_qwen3.json`.

## Áp quy tắc

- Reranker: không nhóm nào, ở chỉ số nào, `hybrid+rerank` kém hơn `hybrid` với p < 0,05. **Giữ.**
- Adaptive-k: không nhóm nào kém hơn hybrid lấy cố định 8 chunk với p < 0,05. **Giữ.**

## Nhận xét

**Lần đầu reranker hơn có ý nghĩa thống kê.**
Trên 250 câu, nó đưa chunk đúng lên đầu ở 35 câu mà hybrid không làm được, và chỉ đánh rơi 16 (p = 0,011).
Các lần đo trước chỉ đạt ý nghĩa ở một môn (`operating-system`) và gộp lại thì không (p = 0,087).
Mức lợi đến từ vị trí đầu: ở recall@10 thì reranker và hybrid ngang nhau (3 và 3), tức reranker sắp lại thứ tự trong cùng một tập chứ không kéo thêm chunk mới vào.

**Nghi vấn "rerank hại tiếng Việt" không còn.**
Trên văn bản đã sửa công thức, `calculus1` về trung tính (6 và 7, p = 1,0), trong khi trước đây trên văn bản vỡ nó nghiêng rõ về hại (3 và 10, p = 0,092).
`chemistry1` vẫn được lợi (7 và 1, p = 0,070).
Điều này khớp với giải thích của lần đo trước: cái làm hại reranker là công thức vỡ, không phải tiếng Việt.

**Hybrid vẫn chưa hơn dense.**
Ở vị trí đầu hai bên ngang nhau (17 và 15); ở độ sâu 10 thì dense còn giữ được nhiều hơn một chút (6 và 2, p = 0,29), đúng như lần đo trước đã thấy ở `dsa`.
Không có số nào chống lưng cho BM25 trong phép đo này; nó được giữ vì rẻ và bắt được thuật ngữ đúng nguyên văn, không phải vì phép đo.

**Adaptive-k không thắng mà cũng không thua.**
Với đúng một chunk đúng mỗi câu, phép đo này chỉ hỏi được "chunk đúng có nằm trong tập được chọn không", và câu trả lời như nhau ở 237 câu.
Cái Adaptive-k nhắm tới là lấy thêm ngữ cảnh khi phân bố điểm cho thấy còn nhiều chunk liên quan, việc mà một nhãn một chunk không đo được.
Nó được giữ theo quy tắc, không phải vì đã chứng minh được lợi ích; cái giá là trung bình thêm 0,7 chunk mỗi lần gọi.

## Chuyển reranker sang container riêng

Theo quy tắc, reranker ra khỏi ảnh backend dù giữ hay bỏ.
Nó nay chạy ở service `reranker` của `devops/docker-compose.yml`: Text Embeddings Inference 1.9.4 bản CPU, bản ONNX có sẵn trong kho `BAAI/bge-reranker-base` (commit `2cfc18c`), backend gọi `POST /rerank` qua HTTP (`app/retrieval/reranker.py`).

Đổi đường chạy thì phải chắc điểm không đổi, vì Adaptive-k cắt theo điểm.
So hai bản trên đúng 30 ứng viên hybrid của 100 câu (20 câu đầu mỗi môn):

- chênh lệch điểm lớn nhất 6 × 10⁻⁶ (cả hai cùng qua sigmoid, thang 0-1);
- thứ hạng chunk đúng giống nhau ở cả 100 câu;
- thứ tự 30 ứng viên giống hệt ở 99 câu; câu còn lại chỉ đổi chỗ hai chunk có điểm gần như bằng nhau;
- Adaptive-k chọn cùng số chunk ở 99 câu; câu còn lại (`calculus1`) là 27 so với 20, do hai khoảng cách điểm gần bằng nhau đổi thứ tự, chunk đúng vẫn đứng đầu.

Chi phí chạy trên máy đo:

| | trước | sau |
|---|---|---|
| ảnh backend | 5,04 GB | 1,72 GB |
| phụ thuộc bị gỡ | | torch, sentence-transformers, transformers và 15 gói kéo theo |
| thời gian chấm 30 ứng viên | | khoảng 5,5 giây (giới hạn 4 nhân CPU) |
| RAM của service reranker | | khoảng 2,2 GB (giới hạn 4 nhân CPU) |

ONNX Runtime mở một luồng cho mỗi nhân CPU nó thấy và RAM tăng theo: không giới hạn thì trên máy 20 nhân nó giữ khoảng 6 GB, còn 8 nhân cũng không nhanh hơn 4 nhân.
Vì vậy service đặt `cpus: ${RERANKER_CPUS:-4}`; máy ít hơn 4 nhân đặt `RERANKER_CPUS` trong `.env`.

## Giới hạn

- Nhãn đúng do LLM sinh, mỗi câu chỉ một chunk đúng, nên các chỉ số là cận dưới.
- Trong 250 câu có 3 câu vẫn nhắc tới "đoạn trích", "sơ đồ" hay "văn bản" dù lời nhắc cấm, và 2 câu tiếng Việt trong môn tiếng Anh `advanced-programming`; nhiễu này rơi đều lên mọi cấu hình.
- 50 câu mỗi môn chỉ đủ thấy chênh lệch lớn; kết luận theo từng môn nên đọc cùng số gộp.
- Quy tắc không hiệu chỉnh cho nhiều phép kiểm; không phép kiểm nào gần ngưỡng theo chiều "kém hơn", nên kết luận không phụ thuộc vào điều này.
