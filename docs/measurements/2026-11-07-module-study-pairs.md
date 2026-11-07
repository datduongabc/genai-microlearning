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

## Nhận định không có căn cứ (R9) bằng `groq-pro`

Lệnh: `run_claim_judge.py --judge groq-pro`. Bước này tách phần Content thành từng nhận định, rồi kiểm
từng nhận định với bộ đoạn đối chiếu chung của cặp. Ở bản RAG, nó còn kiểm từng trích dẫn đã giải
được về chunk.

- **Model chấm:** cả 20 gói đều do `openai/gpt-oss-120b` chấm, khác hãng với model sinh.
- **Số lượt gọi:** khoảng 370. Groq giới hạn 250 nghìn token mỗi phút, nên lệnh được chạy lại sau
  mỗi lần bị chặn. Các gói đã chấm được giữ nguyên.
- **Kết quả thô:** `module-study/claims-groq-pro.jsonl`.

**Gói không được tính** theo [rubric mục 7](../rubric.md#7-r9-claim-không-có-căn-cứ-và-độ-chính-xác-trích-dẫn):

- Hai cặp machine-learning (`cfd30918`, `e3d92415`) không được tính. Bộ đối chiếu của chúng được lấy
  bằng truy hồi, nên độ phủ là `unknown`: một nhận định không tìm thấy trong 8 đoạn chưa chắc đã
  không có trong bài giảng.
- Bản không RAG của cặp `40a27fe8` (Giới hạn dãy số) không tách ra được nhận định nào, dù Content
  dài 1.808 ký tự và chứa nhiều công thức LaTeX. Đây là chỗ bước tách nhận định không xử lý được,
  không có nghĩa là module không có nhận định. Cặp này cũng bị loại.

Còn lại 7 cặp có độ phủ đầy đủ:

| | rag_llm | llm_no_rag |
| --- | ---: | ---: |
| Nhận định | 120 | 101 |
| Không có căn cứ | 0 | 17 (16,8%) |
| Module có ít nhất 1 nhận định không có căn cứ | 0 / 7 | 6 / 7 |
| Trích dẫn đúng chunk (cả 10 gói RAG) | 148 / 166 (89,2%) | — |

- **Kiểm định theo cặp.** Tỉ lệ nhận định không có căn cứ của bản không RAG cao hơn bản RAG ở 6/7
  cặp và bằng nhau ở cặp còn lại.
  - Wilcoxon signed-rank cho p = 0,031.
  - McNemar chính xác trên "có ít nhất một nhận định không có căn cứ" cho 6 cặp lệch về không RAG,
    0 cặp lệch về RAG, p = 0,031.
- **Mẫu còn nhỏ (n = 7) và chỉ có một model chấm.** Đây là bằng chứng sơ bộ, chưa phải kết luận.
- **"Không có căn cứ" không có nghĩa là sai.** Đọc các nhận định bị đánh dấu thì phần lớn là kiến thức
  chung và thường đúng, nhưng không có trong tài liệu nguồn. Ví dụ "GUI cung cấp cửa sổ, biểu tượng và
  menu", trong khi slide chỉ nói tới biểu tượng, chuột và bàn phím. Một số lượt đánh giá của judge khá
  khắt khe (như phần Text của tiến trình chứa mã máy).
  - Điều R9 đo là module có bám sát tài liệu của giảng viên hay không, và RAG giữ được điều đó.
- **Ba gói RAG có trích dẫn chính xác thấp hơn:** `1afc963f` 9/13, `cfd30918` 19/26 và `f1f4dfdf`
  13/20. Ở các gói này, nhận định đúng với nguồn nhưng trỏ sang một chunk lân cận.

## Việc tiếp theo

- **Mô hình chấm:** `run_judge.py` trên `packets/`.
  - Với gói do Gemini sinh, pool chấm là `claude-pro` (`claude-sonnet-5-5`) và `openai-pro`
    (`gpt-6.1-sol`). Cả hai đều trả phí.
  - Chấm 20 gói × 2 model là khoảng 40 lượt gọi. Mỗi gói dài 13–42 nghìn ký tự, trung bình 26 nghìn.
- **Người chấm:** cần ít nhất hai người chấm 20 gói để tính α và κ.
  - Trước khi chấm thật, mỗi người chấm thử hai gói không thuộc mẫu ([rubric mục 5](../rubric.md#5-hướng-dẫn-cho-người-chấm)).
  - Có thể lấy hai gói thử từ các module `claude-fast` sinh ngày 2026-11-04.
