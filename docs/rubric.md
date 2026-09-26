# Rubric đánh giá module Microlearning

Rubric này dùng chung cho mô hình chấm (`judge_module` trong `backend/app/generation/evaluation.py`) và cho người chấm, để so được độ đồng thuận giữa hai bên. Mỗi module được chấm trên **9 tiêu chí**: 8 tiêu chí cho điểm từ 1 đến 5 và một danh sách các câu hallucination.

## 1. Thiết kế nghiên cứu

- **Hai nhóm ghép cặp.** Với mỗi chủ đề có một module sinh bằng RAG (nhóm `rag_llm`) và một module sinh không RAG trên cùng Learning Objective, cùng model sinh (nhóm `llm_no_rag`). Khác biệt điểm trong một cặp vì vậy chỉ còn là do RAG.
- **Cùng tài liệu đối chiếu.** Hai module của một cặp được chấm trên cùng một bộ đoạn nguồn, lấy bằng một lượt truy hồi độc lập với cả hai bản.
- **Chấm mù.** Người chấm và mô hình chấm chỉ thấy mã ẩn danh (dạng `M-3fa9c2d1`), không thấy nhóm, tên môn, tên tệp hay tên model.
- **Hai mô hình chấm khác hãng với model sinh.** Mô hình chấm chính là Claude Sonnet 5 (alias `claude-pro`), mô hình chấm thứ hai là gpt-oss-120b trên Groq (alias `groq-pro`). Model sinh là Gemini, nên cả hai mô hình chấm đều khác hãng: mô hình chấm thường ưu ái văn của chính họ nhà mình. `scripts/run_judge.py` từ chối chạy khi mô hình chấm cùng hãng với model đã sinh gói.

## 2. Thang điểm chung

| Điểm | Ý nghĩa chung |
|---|---|
| 5 | Đạt hoàn toàn, không tìm thấy lỗi nào đáng kể |
| 4 | Đạt, có lỗi nhỏ không ảnh hưởng tới việc học |
| 3 | Đạt một phần, có lỗi người học sẽ nhận ra hoặc bị ảnh hưởng |
| 2 | Phần lớn chưa đạt, lỗi nghiêm trọng ở nhiều chỗ |
| 1 | Không đạt, hoặc sai lệch làm module mất giá trị |

Điểm 2 và 4 là mức trung gian khi module nằm giữa hai mức neo mô tả ở mục 3.

## 3. Chín tiêu chí

Tên trong ngoặc là tên cột trong tệp điểm và trong kết quả của mô hình chấm.

### 3.1. Độ chính xác (`accuracy`)

Nội dung, câu quiz và lời giải thích có đúng với **tài liệu nguồn** được cung cấp không. Chỉ đối chiếu với tài liệu nguồn, không dựa vào hiểu biết riêng của người chấm về chủ đề.

- **5**: mọi thông tin khớp với nguồn.
- **3**: có một vài chi tiết sai lệch nhưng ý chính vẫn đúng.
- **1**: có thông tin sai ở ý chính, hoặc đáp án đúng của quiz thực ra là sai theo nguồn.

### 3.2. Độ đầy đủ (`completeness`)

So với **toàn bộ** tài liệu nguồn, không chỉ với phạm vi hẹp mà Learning Objective tự chọn. Trong nhận xét phải liệt kê cụ thể ý hoặc khái niệm lớn nào trong nguồn mà nội dung không nhắc tới. Ý bị bỏ sót chỉ bị trừ điểm khi nó **cần thiết** để đạt Learning Objective; ý thuộc chủ đề khác thì ghi nhận nhưng không trừ.

- **5**: đủ mọi ý cần thiết cho Learning Objective.
- **3**: thiếu một ý cần thiết, hoặc nhiều ý chỉ được nhắc lướt.
- **1**: thiếu phần lớn các ý cần thiết, người học không đạt được Learning Objective chỉ với module này.

### 3.3. Tính nhất quán (`consistency`)

Mỗi câu quiz có hỏi đúng điều đã dạy trong nội dung không; lời giải thích của mỗi câu có khớp với đáp án đúng của chính câu đó không; các ví dụ có mâu thuẫn với nội dung không.

- **5**: mọi câu hỏi, lời giải và ví dụ khớp với nội dung và với nhau.
- **3**: một câu hỏi về điều nội dung chưa dạy, hoặc một lời giải lệch đáp án.
- **1**: nhiều câu hỏi, lời giải hoặc ví dụ mâu thuẫn với nội dung.

### 3.4. Hallucination (`hallucination_score` và danh sách hallucination)

Liệt kê **từng** câu hoặc ý trong nội dung, quiz hay lời giải thích **không có căn cứ** trong tài liệu nguồn, kèm vị trí (nội dung, quiz, lời giải thích) và lý do. Phải kiểm từng câu, kể cả khi module trông sạch. Một ý diễn đạt lại hoặc suy ra trực tiếp từ nguồn không phải hallucination; một con số, tên riêng, định nghĩa hay khẳng định mà nguồn không có thì là hallucination, kể cả khi nó đúng ngoài đời.

- **5**: không có câu nào không có căn cứ; danh sách rỗng.
- **4**: một câu không có căn cứ, ở chi tiết phụ.
- **3**: hai đến ba câu không có căn cứ, hoặc một câu ở ý chính.
- **2**: nhiều câu không có căn cứ, rải rác khắp module.
- **1**: phần lớn nội dung không có căn cứ trong nguồn.

Tiêu chí thứ chín là chính danh sách này. Phân tích thống kê dùng nó dưới dạng nhị phân: module **có ít nhất một** hallucination hay không.

### 3.5. Chất lượng slide (`slide_quality`)

Các gạch đầu dòng trên slide có tóm tắt đúng nội dung, rõ ràng, độ dài hợp lý không.

- **5**: tóm tắt đúng và đủ ý chính, mỗi gạch đầu dòng ngắn gọn.
- **3**: đúng nhưng thiếu ý chính, hoặc gạch đầu dòng dài như đoạn văn.
- **1**: slide sai nội dung, rời rạc, hoặc chép nguyên văn cả đoạn.

### 3.6. Chất lượng quiz (`quiz_quality`)

Các câu quiz có đúng độ khó theo mức Bloom khai báo không; phương án nhiễu có hợp lý, không sai lộ liễu không; cả bộ câu hỏi có để lộ đáp án không (ví dụ đáp án đúng luôn là phương án dài nhất); các câu có kiểm những ý khác nhau của bài hay lặp lại một ý.

- **5**: độ khó đúng mức, phương án nhiễu đều hợp lý, không lộ đáp án, mỗi câu kiểm một ý khác nhau.
- **3**: có một vài phương án nhiễu sai lộ liễu, hoặc hai câu kiểm cùng một ý.
- **1**: đáp án đoán được mà không cần học, hoặc câu hỏi không kiểm được gì của bài.

### 3.7. Độ khớp mức Bloom (`bloom_fidelity`)

Mức Bloom khai báo có khớp với mức **thực tế** mà nội dung, quiz và bài luyện tập đòi hỏi không. Đối chiếu theo bảng neo ở mục 4.

- **5**: khớp hoàn toàn mức khai báo.
- **3**: lệch một mức ở một phần (ví dụ khai báo Apply nhưng một nửa số câu chỉ đòi Remember).
- **1**: lệch rõ rệt (ví dụ khai báo Remember nhưng quiz đòi Analyze, hoặc khai báo Apply nhưng toàn bộ dừng ở Remember).

### 3.8. Bám chủ đề gốc (`query_alignment`)

Module có thực sự trả lời đúng **chủ đề gốc** được giao không (trường "Chủ đề"). Khác với `bloom_fidelity`, vốn chỉ đối chiếu với Learning Objective do chính module đặt ra. Một module có thể chính xác, nhất quán, không hallucination mà vẫn lạc chủ đề: khi đó tiêu chí này phải thấp dù các tiêu chí khác cao.

- **5**: toàn bộ module xoay quanh đúng chủ đề gốc.
- **3**: có phần đáng kể nói về chủ đề lân cận.
- **1**: module nói về chủ đề khác.

## 4. Bảng neo Bloom

Cùng bảng mà pipeline sinh dùng (`BLOOM_VERB_TABLE` và `BLOOM_LEVEL_ANCHORS` trong `backend/app/generation/common.py`).

| Mức | Động từ điển hình | Hành vi mà câu hỏi hay nội dung đòi hỏi |
|---|---|---|
| remember (Nhớ) | liệt kê, định nghĩa, gọi tên, nhận biết | Chỉ yêu cầu nhớ lại sự kiện/định nghĩa/thuật ngữ đã nêu trong nguồn, KHÔNG đòi hỏi giải thích lý do hay áp dụng. |
| understand (Hiểu) | giải thích, mô tả, phân biệt, tóm tắt | Yêu cầu diễn giải lại bằng lời riêng hoặc nêu quan hệ giữa các khái niệm đã học, không chỉ lặp lại nguyên văn nguồn. |
| apply (Áp dụng) | viết, thực thi, sử dụng, minh họa | Yêu cầu vận dụng khái niệm vào 1 tình huống cụ thể CHƯA xuất hiện trong nguồn. |
| analyze (Phân tích) | phân tích, so sánh, phân loại, chỉ ra mối liên hệ | Yêu cầu tách nhỏ vấn đề, chỉ ra mối quan hệ/khác biệt giữa các thành phần (không chỉ mô tả từng phần riêng lẻ). |
| evaluate (Đánh giá) | đánh giá, phê bình, lựa chọn phương án | Yêu cầu đưa ra phán đoán có tiêu chí rõ ràng, cân nhắc ưu-nhược điểm giữa các phương án. |
| create (Sáng tạo) | thiết kế, xây dựng, phát triển, kết hợp | Yêu cầu kết hợp các phần đã học thành 1 giải pháp/sản phẩm mới, không dừng ở tái hiện. |

## 5. Hướng dẫn cho người chấm

1. **Chấm mù.** Mỗi gói chỉ mang mã ẩn danh. Không đoán và không ghi nhận xét về việc module có thể do nguồn nào sinh ra. Các gói được đưa theo thứ tự đã xáo trộn; chấm từng gói độc lập, không so hai gói với nhau.
2. **Đọc tài liệu nguồn trước.** Mỗi gói kèm các đoạn tài liệu nguồn. `accuracy` và hallucination chỉ đối chiếu với các đoạn này, không với hiểu biết riêng của bạn.
3. **Đọc hết module** (chủ đề, Learning Objective và các mục tiêu con, nội dung, slide, ví dụ, từng câu quiz cùng lời giải, bài luyện tập) rồi mới cho điểm.
4. **Cho điểm nguyên từ 1 đến 5** theo các mức neo ở mục 3. Không chắc giữa hai mức thì chọn mức trung gian 2 hoặc 4.
5. **Ghi lại hallucination** trong cột `hallucination_notes`: mỗi câu không có căn cứ một dòng, kèm vị trí. Cột này không vào thống kê điểm nhưng giúp đối chiếu với danh sách của mô hình chấm.
6. **Hiệu chuẩn trước khi chấm thật.** Chấm thử hai gói không nằm trong mẫu, rồi đối chiếu với người nghiên cứu để thống nhất cách hiểu các mức neo. Không sửa điểm của gói thật sau khi đã xem điểm của mô hình chấm.
7. **Mỗi người chấm một tệp CSV riêng**, mã hoá UTF-8, dòng đầu là tên cột:

```csv
anon_id,accuracy,completeness,consistency,hallucination_score,slide_quality,quiz_quality,bloom_fidelity,query_alignment,hallucination_notes
M-3fa9c2d1,4,3,5,4,4,3,5,5,"nội dung: câu về năm 1998 không có trong nguồn"
```

Để trống một ô khi không chấm được tiêu chí đó; ô trống không được tính. Mỗi mã ẩn danh chỉ xuất hiện một lần trong tệp.

## 6. Quy trình chạy và phân tích

Chạy trong thư mục `backend/`. Mỗi lệnh chấm tốn một lượt gọi LLM trả tiền cho mỗi gói.

```bash
# 1. Xuất cặp gói của từng module RAG đã sinh (một lần cho mỗi module).
PYTHONPATH=. uv run python scripts/export_study_pair.py <module_id> --out study/packets

# 2. Chấm bằng hai mô hình chấm khác hãng với model sinh. Bị ngắt thì chạy lại
#    đúng lệnh đó: chỉ những gói còn thiếu được chấm tiếp.
PYTHONPATH=. uv run python scripts/run_judge.py study/packets --judge claude-pro --out study/claude-pro.jsonl
PYTHONPATH=. uv run python scripts/run_judge.py study/packets --judge groq-pro --out study/groq-pro.jsonl

# 3. Phân tích, kèm điểm của người chấm nếu có.
PYTHONPATH=. uv run python scripts/analyze_study.py study/claude-pro.jsonl study/groq-pro.jsonl \
    --human study/human.csv --out study/report.json
```

Mã ẩn danh của mỗi gói (cột `anon_id` trong tệp `.jsonl`) cố định theo tên tệp gói, nên người nghiên cứu dùng chính mã đó khi đưa gói cho người chấm.

`analyze_study.py` báo cáo:

- **Điểm trung bình** mỗi tiêu chí của từng nhóm, theo từng mô hình chấm.
- **Wilcoxon signed-rank hai phía** trên các cặp (rag_llm trừ llm_no_rag) cho từng tiêu chí, kèm p đã hiệu chỉnh Holm cho 8 tiêu chí. Dùng Wilcoxon vì điểm là thang thứ bậc và mẫu nhỏ, không giả định phân phối chuẩn.
- **McNemar chính xác** cho tỉ lệ module có ít nhất một hallucination giữa hai nhóm, trên các cặp.
- **Spearman và kappa trọng số bậc hai** (thang 1-5) cho từng tiêu chí, giữa hai mô hình chấm và giữa mỗi mô hình chấm với người.
