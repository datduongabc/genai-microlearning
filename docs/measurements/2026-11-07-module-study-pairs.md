# Cặp module RAG và không RAG cho nghiên cứu chấm, 2026-11-07

Sinh 10 cặp module (20 gói) để chấm theo [rubric](../rubric.md): mỗi cặp gồm một module sinh có RAG
(`rag_llm`) và một module sinh không RAG trên cùng Learning Objective (`llm_no_rag`). Mới có một lượt chấm sơ bộ bằng một mô hình chấm, chưa phải kết quả của nghiên cứu.

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

## Chấm sơ bộ bằng `groq-qwen`

Thiết kế chấm cần hai hãng khác Google (`claude-pro` và `openai-pro`), nhưng hai khóa này chưa nạp
tiền. Trong khi chờ, 20 gói được chấm bằng một mô hình chấm duy nhất:
`run_judge.py --judges groq-qwen`. Tùy chọn `--judges` giới hạn pool chấm và vẫn giữ guard khác hãng.

- **Model chấm:** cả 20 lượt đều do `qwen/qwen3.8-27b` (Alibaba) phục vụ.
  - Có một lượt Groq trả "Failed to generate JSON". Chạy lại lệnh thì lượt đó được chấm.
- **Các model Groq khác trong pool không dùng được:** `groq-llama` và `groq-minimax` báo tài khoản
  không có quyền truy cập. Tài khoản chỉ có Qwen và GPT-OSS, mà GPT-OSS không thuộc pool.
- **Kết quả thô:** `module-study/judge-groq-qwen.jsonl`. Tệp này tách riêng khỏi lượt chấm chính
  thức, để lượt sau vẫn chọn đúng `claude-pro` và `openai-pro`.

Trung bình trên 10 cặp, tính bằng `analyze_study.py`:

| Tiêu chí | rag_llm | llm_no_rag | RAG hơn / kém / bằng |
| --- | ---: | ---: | --- |
| accuracy | 5,0 | 5,0 | 0 / 0 / 10 |
| completeness | 4,5 | 4,2 | 3 / 0 / 7 |
| consistency | 5,0 | 5,0 | 0 / 0 / 10 |
| hallucination_score | 5,0 | 5,0 | 0 / 0 / 10 |
| slide_quality | 4,7 | 5,0 | 0 / 3 / 7 |
| quiz_quality | 4,2 | 4,2 | 2 / 2 / 6 |
| bloom_fidelity | 4,7 | 4,7 | 1 / 1 / 8 |
| query_alignment | 4,6 | 4,9 | 0 / 2 / 8 |

- **Không tiêu chí nào khác biệt có ý nghĩa.** Mọi p Holm đều bằng 1,0 (Wilcoxon). McNemar trên tỉ
  lệ module có hallucination cho p = 1,0.
- **Không phân biệt được hai nhóm.** Judge này cho điểm trần ở accuracy, consistency và
  hallucination, và không ghi hallucination nào, kể cả ở bản không RAG. Bản không RAG được viết không
  có nguồn, nên khó tin là mọi nhận định của nó đều có trong đoạn đối chiếu.
  - Như vậy `qwen3.8-27b` chấm quá dễ, không dùng được làm bằng chứng cho câu hỏi RAG và không RAG.
  - Đây chính là điều lượt chấm chính thức bằng hai model mạnh, cùng người chấm, phải kiểm.
- **Việc kiểm hallucination theo từng claim (R9, `run_claim_judge.py`) chưa chạy.** Bước này kiểm
  từng nhận định với nguồn, nên ít phụ thuộc vào độ dễ dãi của mô hình chấm hơn.

## Việc tiếp theo

- **Mô hình chấm:** `run_judge.py` trên `packets/`.
  - Với gói do Gemini sinh, pool chấm là `claude-pro` (`claude-sonnet-5-5`) và `openai-pro`
    (`gpt-6.1-sol`). Cả hai đều trả phí.
  - Chấm 20 gói × 2 model là khoảng 40 lượt gọi. Mỗi gói dài 13–42 nghìn ký tự, trung bình 26 nghìn.
- **Người chấm:** cần ít nhất hai người chấm 20 gói để tính α và κ.
  - Trước khi chấm thật, mỗi người chấm thử hai gói không thuộc mẫu ([rubric mục 5](../rubric.md#5-hướng-dẫn-cho-người-chấm)).
  - Có thể lấy hai gói thử từ các module `claude-fast` sinh ngày 2026-11-04.
