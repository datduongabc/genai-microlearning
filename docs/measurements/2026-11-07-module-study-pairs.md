# Cặp module RAG và không RAG cho nghiên cứu chấm, 2026-11-07

Sinh 10 cặp module (20 gói) để chấm theo [rubric](../rubric.md): mỗi cặp gồm một module sinh có RAG
(`rag_llm`) và một module sinh không RAG trên cùng Learning Objective (`llm_no_rag`). Chưa chấm điểm.

## Điều kiện sinh

- **Mã:** `main` tại `9c5d61f` (sau #89). Sinh bằng `backend/scripts/generate_study_pairs.py`.
  Script này sinh module RAG rồi gọi `export_study_pair.py` để sinh bản không RAG và xuất cặp.
- **Dữ liệu:** DB đo ngày 2026-11-04, dump ra ở chế độ chỉ đọc rồi nạp vào DB tạm.
  - Chọn 2 tài liệu mỗi môn, tổng 10 tài liệu. Đây là các tài liệu của các khóa `profile:*` còn
    `raw_text`; các khóa đo retrieval chỉ giữ chunk.
  - `Chapter2.pptx` (scientific-socialism) không còn `raw_text`, nên được đọc lại từ tệp gốc bằng
    `read_pptx` trong image backend (có tesseract). Đọc lại `Chapter1.pptx` theo cùng cách cho kết quả
    trùng từng ký tự với bản đã lưu (12.488 ký tự), nên cách đọc lại khớp với lần nạp gốc.
  - Cache planner của 10 tài liệu này được xóa trước khi sinh. Cache cũ là của lần sinh bằng
    `claude-fast`, nên nếu giữ lại thì planner sẽ không phải của model đang so sánh.
- **Model:** `gemini-fast` ở chế độ thí nghiệm (tắt fallback), có vòng tự kiểm.
  - Cả 286 lượt gọi của 20 gói đều do `gemini-3.5-flash-lite` (gói miễn phí) phục vụ.
  - Mỗi module RAG cần 14–19 lượt gọi, mỗi bản không RAG 12–16 lượt.
  - Một module sinh hết trung bình 49,5 s, lâu nhất 90 s, chưa tính thời gian chờ 429.
- **Tài liệu đối chiếu:** theo [rubric mục 7](../rubric.md#7-r9-claim-không-có-căn-cứ-và-độ-chính-xác-trích-dẫn).
  Hai gói của một cặp dùng chung một bộ đoạn nguồn, 8 đến 54 đoạn mỗi cặp. Hai bài giảng
  machine-learning dài hơn giới hạn của bộ đối chiếu nên được lấy 8 đoạn bằng truy hồi.

| Môn | Tài liệu | Mã cặp (module RAG) |
| --- | --- | --- |
| advanced-programming | Chap1_Encapsulation.pptx | `e9ad9d2d` |
| advanced-programming | Chap2_Inheritance.pptx | `0f54d1ca` |
| calculus 1 | 1. Giới hạn dãy số.pdf | `40a27fe8` |
| calculus 1 | 2. Giới hạn và liên tục hàm một biến.pdf | `23600a2e` |
| machine-learning | Week02.mp4 | `cfd30918` |
| machine-learning | Week03.mp4 | `e3d92415` |
| operating-system | Chapter 2_Operating System Structures.pdf | `192b56da` |
| operating-system | Chapter 3_Processes.pdf | `ac16fbbd` |
| scientific-socialism | Chapter1.pptx | `1afc963f` |
| scientific-socialism | Chapter2.pptx | `f1f4dfdf` |

Mức Bloom khai báo: 8 cặp `understand`, 1 cặp `apply`, 1 cặp `analyze`. Mỗi gói có 3 câu quiz,
1 bài luyện tập, 2 ví dụ và 3 đến 6 slide.

## Dữ liệu

- **`module-study/packets/`:** 20 tệp StudyFile.
  - Khóa `packet` là phần đưa cho người chấm và mô hình chấm. Nó không chứa nhóm, tên môn, tên tệp
    hay tên model.
  - Lớp bọc ngoài ghi mã cặp, nhóm và model thực sự đã phục vụ từng lượt gọi. Không đưa lớp này cho
    người chấm.
- **Bộ tài liệu cho người chấm:** dựng từ thư mục này bằng `backend/scripts/build_rater_kit.py`.
  - Mỗi gói thành một trang HTML, đặt tên theo đúng mã `M-xxxxxxxx` mà `run_judge.py` gán.
  - Kèm `rubric.html` (rubric mục 2 đến 5), `ratings.csv` mẫu và `README.txt` ghi thứ tự chấm đã
    xáo.
  - Tệp khóa (mã ẩn danh, cặp, nhóm) được ghi ra ngoài thư mục bộ chấm.

## Việc tiếp theo

- **Mô hình chấm:** `run_judge.py` trên `packets/`.
  - Với gói do Gemini sinh, pool chấm là `claude-pro` (`claude-sonnet-5-5`) và `openai-pro`
    (`gpt-6.1-sol`). Cả hai đều trả phí.
  - Chấm 20 gói × 2 model là khoảng 40 lượt gọi. Mỗi gói dài 13–42 nghìn ký tự, trung bình 26 nghìn.
- **Người chấm:** cần ít nhất hai người chấm 20 gói để tính α và κ.
  - Trước khi chấm thật, mỗi người chấm thử hai gói không thuộc mẫu ([rubric mục 5](../rubric.md#5-hướng-dẫn-cho-người-chấm)).
  - Có thể lấy hai gói thử từ các module `claude-fast` sinh ngày 2026-11-04.
