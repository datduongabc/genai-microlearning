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

(điền sau khi chạy)

## Kết quả

(điền sau khi chạy)
