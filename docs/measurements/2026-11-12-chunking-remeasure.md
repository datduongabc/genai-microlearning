# Đo lại retrieval sau bản sửa nhận tiêu đề PDF, 2026-11-12

Trước PR #105, mọi dòng PDF bắt đầu bằng số đều được coi là tiêu đề mục.
Số trang (`4 / 33`), dòng code và mục liệt kê vì vậy cắt chunk và thành nhãn trích dẫn.
Số retrieval trong báo cáo (Hit Rate@1 0,924 với Jev) được đo trên cách cắt đó.
Bộ câu hỏi cũ (`truy_van_5mon.json`) không dùng lại được: nó chỉ lưu id ngẫu nhiên của chunk đúng, mà database của lần đo đó đã không còn.
Lần này đo lại trên bộ câu hỏi mới, và đo cả hai cách cắt trên cùng bộ câu hỏi để tách riêng tác động của bản sửa.

## Điều kiện đo

- Mã: `main` tại `ba6e081`, script `backend/scripts/benchmark_chunking.py` chạy trong image backend (có tesseract), database riêng `benchmark_chunking`.
- Embedder Qwen3-Embedding-0.6B chạy GPU qua gateway, Jev `jev-1.14.0`, 30 ứng viên hybrid mỗi câu.
- Mỗi PDF được đọc một lần cho mỗi quy tắc, nhưng lời gọi đọc lại công thức (`gemini-fast`) được cache theo ảnh trang, nên model đọc mỗi trang đúng một lần và hai cách cắt chỉ khác nhau ở quy tắc tiêu đề. Script dừng hẳn nếu một lời gọi lỗi, để không có cách cắt nào giữ bản chữ vỡ.
- Machine Learning là video và audio, bản sửa không động tới. Transcript của lần đo 2026-11-08 được chép từ database chính thay vì trả tiền Whisper lần nữa.
- Câu hỏi: `groq-pro` (gpt-oss-120b) đặt 250 câu, 50 câu mỗi môn, mỗi câu từ một chunk của cách cắt mới, chọn bằng seed `20261112` theo thứ tự (môn, tệp, chỉ số chunk), nên chạy lại chọn đúng các chunk đó. Không câu nào được dùng để chọn tham số.
- Chi phí: khoảng $0,09 OpenRouter (câu hỏi và đọc lại công thức) và khoảng $0,11 Jev (8.488 lời gọi; chunk giống nhau giữa hai cách cắt chỉ chấm một lần).

## Cắt tài liệu

Chỉ Calculus 1 đổi: 292 chunk theo quy tắc cũ, 275 theo quy tắc mới.
Bốn môn còn lại cho chunk giống hệt nhau ở cả hai cách cắt, nên 215 trên 250 câu hỏi có chunk cũ trùng nguyên văn với chunk mới.

| Tệp Calculus 1 | Chunk cũ | Chunk mới | Giống hệt |
| --- | ---: | ---: | ---: |
| 1. Giới hạn dãy số | 55 | 50 | 22 |
| 2. Giới hạn và liên tục hàm một biến | 40 | 38 | 11 |
| 3. Đạo hàm và vi phân | 77 | 75 | 31 |
| 4. Tích phân xác định | 21 | 19 | 10 |
| 5. Tích phân bất định | 50 | 47 | 10 |
| 6. Tích phân suy rộng | 49 | 46 | 16 |

## Retrieval trên cách cắt hiện tại

| Cấu hình | R@1 | R@3 | R@5 | R@8 | R@10 | MRR@10 | nDCG@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Dense | 0,792 | 0,932 | 0,976 | 0,980 | 0,992 | 0,868 | 0,899 |
| Hybrid | 0,828 | 0,964 | 0,976 | 0,988 | 0,992 | 0,894 | 0,918 |
| Hybrid + Jev | 0,884 | 0,984 | 0,988 | 0,992 | 0,992 | 0,932 | 0,947 |

Hybrid + Jev theo từng môn:

| Môn | R@1 | R@8 | MRR@10 | nDCG@10 |
| --- | ---: | ---: | ---: | ---: |
| advanced-programming | 0,840 | 1,000 | 0,913 | 0,936 |
| calculus 1 | 0,980 | 1,000 | 0,990 | 0,993 |
| machine-learning | 0,780 | 0,980 | 0,863 | 0,893 |
| operating-system | 0,880 | 1,000 | 0,940 | 0,956 |
| scientific-socialism | 0,940 | 0,980 | 0,952 | 0,959 |

McNemar chính xác trên recall@1:

| So sánh | Chỉ cấu hình sau đúng | Chỉ cấu hình trước đúng | p |
| --- | ---: | ---: | ---: |
| Hybrid + Jev so với hybrid | 36 | 22 | 0,087 |
| Hybrid + Jev so với dense | 43 | 20 | 0,005 |
| Hybrid so với dense | 19 | 10 | 0,136 |

Adaptive-k với sàn 8 giữ được chunk đúng ở 0,992 số câu, trung bình 8,52 chunk.

Số này không so trực tiếp được với lần đo cũ (Hit Rate@1 0,924), vì bộ câu hỏi khác.
Machine Learning giờ là môn khó nhất; lý do có thể là bài giảng nói lặp ý nhiều hơn slide, nên nhiều chunk cùng trả lời được câu hỏi, nhưng chưa kiểm.

## Quy tắc cũ và mới trên cùng bộ câu hỏi

Với cách cắt cũ, câu hỏi không chỉ ra chunk đúng, nên chấm hai cách:

- **Chặt:** chunk cũ giống hệt chunk mới nếu có; nếu không, chunk cũ gần nhất theo Dice trên tập từ.
- **Nới:** thêm mọi chunk cũ có ít nhất một nửa số từ nằm trong chunk mới, vì một đoạn bị quy tắc cũ cắt đôi thì nửa nào cũng có thể trả lời được.

Với 35 câu Calculus 1 không có chunk giống hệt, Dice với chunk cũ gần nhất từ 0,667 trở lên, trung vị cả môn 0,992.

| Hybrid + Jev | R@1 | MRR@10 | Calculus 1 R@1 | Calculus 1 MRR@10 |
| --- | ---: | ---: | ---: | ---: |
| Quy tắc cũ, chặt | 0,860 | 0,912 | 0,860 | 0,891 |
| Quy tắc cũ, nới | 0,876 | 0,926 | 0,940 | 0,963 |
| Quy tắc mới | 0,884 | 0,932 | 0,980 | 0,990 |

McNemar trên recall@1, quy tắc mới so với cũ: chặt 7 câu chỉ mới đúng và 1 câu chỉ cũ đúng (p = 0,070); nới 3 và 1 (p = 0,625).
Trên cả năm môn, bản sửa không tạo khác biệt có ý nghĩa thống kê; ở Calculus 1, recall@1 tăng từ 0,86 hoặc 0,94 (tùy cách chấm) lên 0,98.
Tác động chính của bản sửa là thứ người dùng thấy: chunk và trích dẫn không còn mang số trang làm nhãn mục.

## Giới hạn

- Câu hỏi do LLM đặt từ chính chunk đúng, cùng ngôn ngữ với tài liệu, nên dễ hơn câu hỏi thật của sinh viên.
- Câu hỏi lấy từ chunk của cách cắt mới, nên cách chấm chặt hơi thiên về cách cắt mới; cách chấm nới bù lại phần đó.
- Bộ câu hỏi xuyên ngôn ngữ (126 câu) và Ragas trên 50 câu chat vẫn là số đo trên cách cắt cũ, chưa đo lại.

## Dữ liệu thô

Trong `docs/measurements/chunking/`: `nap.json` (số chunk mỗi tệp), `truy_van.json` (câu hỏi và chunk đúng), `thu_hang.json` (thứ hạng từng câu), `ket_qua.json` (chỉ số và kiểm định), `jev_cache.jsonl` (chỉ hash và điểm số).
`vision_cache.jsonl` chứa nguyên văn các trang tài liệu nên không đưa lên git; thiếu nó thì chạy lại pha `nap` sẽ gọi lại model đọc công thức.

```bash
python scripts/benchmark_chunking.py nap
python scripts/benchmark_chunking.py sinh-truy-van
python scripts/benchmark_chunking.py danh-gia
```
