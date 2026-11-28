# Câu hỏi tiếng Việt trên tài liệu tiếng Anh, Gemini Embedding, 2026-11-28

## Vì sao đo

Lần đo câu hỏi tiếng Việt trên tài liệu tiếng Anh gần nhất (2026-11-05) dùng embedder Qwen3 và cách cắt đoạn cũ.
Từ 22/11 hệ thống nhúng bằng Gemini Embedding, và Gemini đã yếu hơn ở môn tiếng Việt nhiều công thức, nên cần biết trường hợp chéo ngôn ngữ còn giữ được hay không.

## Cách đo

- Câu hỏi: 137 câu tiếng Anh của bộ benchmark 250 câu (`chunking/truy_van.json`), thuộc ba môn có tài liệu tiếng Anh: Lập trình nâng cao 41, Học máy 50, Hệ điều hành 46.
- Mỗi câu được GPT-6 Luna dịch sang tiếng Việt theo cách sinh viên hỏi, giữ nguyên tên hàm, mã và công thức (`crosslingual-gemini/truy_van.json`, có kèm câu gốc).
- Tìm kiếm và chấm đúng như `scripts/benchmark_chunking.py danh-gia`, trên database `benchmark_gemini` (cách cắt hiện tại), cùng đoạn đúng của câu gốc.
- So từng cặp với kết quả của chính câu tiếng Anh đó trong `chunking-gemini/thu_hang.json`.
- Không lượt nào mất bước nhúng hay rơi về thứ tự tìm kiếm vì Jev lỗi.

## Kết quả

| Cấu hình | Câu hỏi | Recall@1 | Recall@8 | MRR@10 | nDCG@10 |
| --- | --- | ---: | ---: | ---: | ---: |
| Dense | tiếng Anh | 0,803 | 0,993 | 0,873 | 0,902 |
| Dense | tiếng Việt | 0,701 | 0,964 | 0,801 | 0,844 |
| Hybrid | tiếng Anh | 0,854 | 0,985 | 0,912 | 0,932 |
| Hybrid | tiếng Việt | 0,672 | 0,920 | 0,761 | 0,803 |
| **Hybrid + Jev** | tiếng Anh | 0,847 | 0,993 | 0,913 | 0,933 |
| **Hybrid + Jev** | **tiếng Việt** | **0,854** | **0,993** | **0,912** | **0,932** |

Chênh lệch theo cặp, khoảng tin cậy 95% bootstrap:

- MRR@10, tiếng Việt có Jev trừ tiếng Anh có Jev: -0,001 (-0,021 đến +0,019).
- Recall@1, tiếng Việt có Jev trừ tiếng Anh có Jev: +0,007 (-0,029 đến +0,044).
- MRR@10, tiếng Việt có Jev trừ tiếng Việt chỉ hybrid: +0,151 (+0,091 đến +0,213).

Theo môn, MRR@10 của câu tiếng Việt có Jev so với câu tiếng Anh có Jev: Lập trình nâng cao 0,939 so với 0,947, Học máy 0,869 so với 0,854, Hệ điều hành 0,934 so với 0,946.

## Kết luận

- Khi hỏi bằng tiếng Việt, tìm kiếm từ khoá gần như không giúp được, vì tài liệu tiếng Anh không chứa từ của câu hỏi: hybrid còn tệ hơn dense.
- Jev bù lại toàn bộ: câu tiếng Việt có Jev ngang câu tiếng Anh có Jev, khoảng tin cậy hẹp quanh 0.
- Lần đo 05/11 với Qwen3 cho câu tiếng Việt MRR@10 0,918 với Jev trên một bộ câu khác (126 câu), nên mức này giữ được sau khi đổi sang Gemini.

## Chi phí và giới hạn

- Dịch 137 câu bằng Luna: dưới $0,01. Jev: 4.114 cặp (câu hỏi, đoạn) mới, khoảng $0,05 credit TypeSafe. Nhúng 137 câu hỏi: không đáng kể.
- Câu tiếng Việt do model dịch, không phải sinh viên viết; một câu có thêm "Thầy/cô cho em hỏi", giống cách sinh viên hỏi thật.
- Mỗi câu có đúng một đoạn đúng, viết từ chính đoạn đó, như bộ benchmark gốc.
- `danh-gia` cũng chấm cách cắt cũ và dừng ở bước tổng hợp vì hai môn tiếng Việt không có câu nào trong bộ này; bảng trên tính từ `thu_hang.json` (cách cắt mới), lưu ở `crosslingual-gemini/ket_qua.json`.
