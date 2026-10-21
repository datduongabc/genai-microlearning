# Rubric đánh giá module Microlearning

Rubric này dùng chung cho mô hình chấm (`judge_module` trong `backend/app/generation/evaluation.py`) và cho người chấm, để so được độ đồng thuận giữa hai bên. Mỗi module được chấm trên **9 tiêu chí**: 8 tiêu chí cho điểm từ 1 đến 5 và ghi chú tổng quan `overall_notes`; `hallucination_findings` là danh sách phát hiện đi kèm điểm hallucination.

## 1. Thiết kế nghiên cứu

- **Hai nhóm ghép cặp.** Với mỗi chủ đề có một module sinh bằng RAG (nhóm `rag_llm`) và một module sinh không RAG trên cùng Learning Objective, cùng model sinh (nhóm `llm_no_rag`). Để không lẫn khác biệt giữa các model vào so sánh RAG, tuân theo [chế độ thí nghiệm và kiểm tra model thực sự đã sinh](gateway-routing.md#experiments).
- **Cùng tài liệu đối chiếu.** Hai module của một cặp được chấm trên cùng một bộ đoạn nguồn; quy tắc phạm vi và độ phủ ở [mục 7](#7-r9-claim-không-có-căn-cứ-và-độ-chính-xác-trích-dẫn). Không lấy bộ đối chiếu từ những chunk mà bản RAG tự trích dẫn.
- **Chấm mù.** Người chấm và mô hình chấm chỉ thấy mã ẩn danh (dạng `M-3fa9c2d1`), không thấy nhóm, tên môn, tên tệp hay tên model.
- **Hai mô hình chấm khác hãng với model sinh và khác hãng nhau.** Ưu tiên `claude-pro` (Anthropic), `openai-pro` (GPT-6.1 Sol, OpenAI), `gemini-fast` (Google); bỏ hãng thực sự đã sinh gói theo guard chung `app/llm/judge.py`. Gói Claude dùng OpenAI + Gemini; gói OpenAI dùng Claude + Gemini; gói Gemini dùng Claude + OpenAI. `gemini-fast` trỏ tới `gemini-3.5-flash-lite`, model miễn phí đã xác minh trên [bảng giá Google](https://ai.google.dev/gemini-api/docs/pricing); `gemini-3.5-flash` của alias `gemini-flash` chưa có trong danh sách chính thức nên không dùng làm judge.
- **Hết quota (429) hoặc vượt ngân sách.** Hai gói của một cặp luôn dùng cùng hai alias chấm; alias dự phòng cho một gói cũng chấm gói kia. Thử hãng còn đủ điều kiện theo thứ tự pool, sau các alias ưu tiên là `groq-llama` (Meta), `groq-qwen` (Alibaba), `groq-minimax` (MiniMax), model ID trong [gateway](../devops/gateway/config.yaml) đã đối chiếu [danh sách Groq](https://console.groq.com/docs/models) ngày 2026-10-17. Llama/MiniMax có thể cần quyền Enterprise; Qwen/MiniMax là preview, khả dụng tùy tài khoản. Không dùng GPT-OSS trong pool. Mỗi lượt ghim model, tắt fallback gateway và lưu alias/model thực sự chấm. Nếu hết ứng viên thì giữ lượt còn thiếu để chạy tiếp, không coi một lượt là đủ hai; không xóa điểm đã lưu.

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

Phân tích thống kê dùng danh sách này dưới dạng nhị phân: module **có ít nhất một** hallucination hay không.

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

Mức Bloom khai báo có khớp với mức **thực tế** mà nội dung, quiz và bài luyện tập đòi hỏi không. Đối chiếu theo bảng neo ở mục 4. Trang chấm mù hiện mức Bloom khai báo ngay cạnh tiêu chí này ("Mức Bloom đặt ra"), không hiện trong gói.

- **5**: khớp hoàn toàn mức khai báo.
- **3**: lệch một mức ở một phần (ví dụ khai báo Apply nhưng một nửa số câu chỉ đòi Remember).
- **1**: lệch rõ rệt (ví dụ khai báo Remember nhưng quiz đòi Analyze, hoặc khai báo Apply nhưng toàn bộ dừng ở Remember).

### 3.8. Bám chủ đề gốc (`query_alignment`)

Module có thực sự trả lời đúng **chủ đề gốc** được giao không (trường "Chủ đề"). Khác với `bloom_fidelity`, vốn chỉ đối chiếu với Learning Objective do chính module đặt ra. Một module có thể chính xác, nhất quán, không hallucination mà vẫn lạc chủ đề: khi đó tiêu chí này phải thấp dù các tiêu chí khác cao.

- **5**: toàn bộ module xoay quanh đúng chủ đề gốc.
- **3**: có phần đáng kể nói về chủ đề lân cận.
- **1**: module nói về chủ đề khác.

### 3.9. Ghi chú tổng quan (`overall_notes`)

Nhận xét tổng thể về ưu điểm và hạn chế của module, không cho thêm điểm số.

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
3. **Đọc hết module** (chủ đề, Learning Objective và các mục tiêu con, nội dung, slide, ví dụ, từng câu quiz cùng lời giải, bài luyện tập) rồi mới cho điểm. Mức Bloom khai báo chỉ hiện cạnh tiêu chí `bloom_fidelity`; dùng nó khi chấm `bloom_fidelity` và độ khó quiz.
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

Chạy trong thư mục `backend/`. R8 cần hai lượt thành công mỗi gói (có thể thêm lượt thử khi hết quota); R9 gọi một lượt tách claim khi Content không rỗng, một lượt kiểm mỗi claim khi có nguồn hợp lệ, rồi một lượt kiểm mỗi trích dẫn đã giải được chunk có nội dung. Hiện phát triển và validation chỉ dùng kiểm thử xác định hoặc route miễn phí được xác minh, đủ ngữ cảnh và quota; API trả phí để sau khi hoàn thành toàn bộ dự án. Bước xuất có thể sinh bản không RAG nếu chưa có sẵn, nên cũng cần route miễn phí. Kết quả và giới hạn của các lượt live đã chạy nằm trong [báo cáo smoke R9](measurements/2026-09-28-r9-live-smoke.md).

```bash
# 1. Xuất cặp gói của từng module RAG đã sinh (một lần cho mỗi module).
PYTHONPATH=. uv run python scripts/export_study_pair.py <module_id> --out study/packets

# 2. Chấm nghiên cứu bằng pool ở mục 1 (có model trả phí). Kiểm thử dùng mock.
#    Hai model khác hãng với model sinh và nhau. Bị ngắt thì chạy lại
#    đúng lệnh đó: chỉ những gói còn thiếu được chấm tiếp.
PYTHONPATH=. uv run python scripts/run_judge.py study/packets --out study/rubric.jsonl

# 3. R9: tách claim độc lập từ Content và chấm các trích dẫn được máy chủ giải.
PYTHONPATH=. uv run python scripts/run_claim_judge.py study/packets --judge <verified-free-alias> --out study/claims.jsonl

# 4. Phân tích cùng R8, kèm điểm người chấm nếu có.
PYTHONPATH=. uv run python scripts/analyze_study.py study/rubric.jsonl \
    --claims study/claims.jsonl --human study/human.csv --out study/report.json
```

Mã ẩn danh của mỗi gói (cột `anon_id` trong tệp `.jsonl`) cố định theo tên tệp gói, nên người nghiên cứu dùng chính mã đó khi đưa gói cho người chấm.

`analyze_study.py` báo cáo:

- **Điểm trung bình** mỗi tiêu chí của từng nhóm, theo từng mô hình chấm.
- **Wilcoxon signed-rank hai phía** trên các cặp (rag_llm trừ llm_no_rag) cho từng tiêu chí, kèm p đã hiệu chỉnh Holm cho 8 tiêu chí. Dùng Wilcoxon vì điểm là thang thứ bậc và mẫu nhỏ, không giả định phân phối chuẩn.
- **McNemar chính xác** cho tỉ lệ module có ít nhất một hallucination giữa hai nhóm, trên các cặp.
- **Spearman và kappa trọng số bậc hai** (thang 1-5) cho từng tiêu chí, giữa hai mô hình chấm và giữa mỗi mô hình chấm với người.

## 7. R9: claim không có căn cứ và độ chính xác trích dẫn

R9 là bước chấm riêng, không thay đổi điểm hay danh sách hallucination của R8. Chỉ tách **mọi khẳng định thực tế nguyên tử trong Content**, kể cả câu không có trích dẫn; không chấm Slides, Example, Quiz hay Practice. Mỗi claim lưu văn bản, khoảng ký tự Python `start` (gồm), `end` (không gồm) trong Content, quyết định hỗ trợ và lý do. Câu ghép có thể tách thành nhiều claim dùng chung khoảng ký tự. Tiêu đề không khẳng định, câu nối và chỉ dẫn học tập không thuộc mẫu số.

- `unsupported_rate = unsupported_claims / total_claims`: mẫu số là toàn bộ claim đã tách, không phải số câu hay số trích dẫn. Diễn đạt lại trung thành hoặc suy ra trực tiếp từ bộ đối chiếu được tính có hỗ trợ. **Không được nguồn hỗ trợ không chứng minh claim sai ngoài đời.** Đây là ước lượng từ mô hình chấm; bỏ sót khi tách claim hoặc phán đoán hỗ trợ sai vẫn là giới hạn cần kiểm chứng bằng người.
- Chỉ công bố tỉ lệ cho bộ đối chiếu `complete`, đủ định danh và tổng số chunk khớp, mọi chunk có nội dung. Với `partial` hoặc `unknown`, vẫn lưu quyết định đối với nguồn đã cung cấp nhưng `unsupported_rate` là `null`, không coi đó là đánh giá đầy đủ. Không có nguồn hoặc có chunk rỗng: quyết định claim là `null`, không được đếm thành không có căn cứ. Không có claim: mẫu số 0 và tỉ lệ `null`.
- Bộ đối chiếu lấy toàn bộ chunk của tài liệu theo thứ tự nếu tài liệu không rỗng và nằm trong giới hạn `REFERENCE_MAX_CHUNKS` và `REFERENCE_MAX_CHARACTERS` do [`small_reference` trong `study_packets.py`](../backend/app/generation/study_packets.py) quy định; độ phủ là `complete`. Tài liệu vượt giới hạn dùng truy hồi độc lập với cả hai bản theo câu truy vấn đặt Learning Objective; độ phủ là `unknown`, nên không công bố tỉ lệ claim không có căn cứ.
- `citation_accuracy = correct_citations / citation_count`: chỉ nhóm RAG; mẫu số là số marker inline được máy chủ giải từ Content hiện tại (hoặc số mục claim–locator của module cũ), độc lập với claim nguyên tử đã tách. Model chỉ viết mã `[S1]`, `[S2]` trong Markdown; máy chủ giữ `source_map` của chính lượt sinh, gồm `chunk_id` và locator cho từng **chunk vật lý** được đưa vào prompt, kể cả các chunk lân cận thêm bởi parent context. Không gộp cửa sổ dưới ID của một chunk con. Mã ngoài map và marker sai dạng (vd `[S1,S2]`, `[s1]`) bị từ chối trước khi lưu, kể cả khi tắt self-check: lý do từ chối được gửi lại cho model qua một lượt sinh lại, và chỉ khi lượt đó vẫn lỗi thì job mới thất bại.
- Marker theo sát câu vừa kết thúc hoặc fragment của bullet trên cùng dòng; các marker liền nhau `[S1][S3]` trích cùng một câu. Máy chủ tính claim và khoảng ký tự từ Markdown đã lưu, không lấy claim do model chép lại. Không coi marker cuối đoạn là bảo chứng cho các câu không gắn mã trước đó. Khi sửa tay hoặc bằng chat, danh sách được suy ra lại; client không được thay source map. Chat dùng map mới của prompt chỉnh sửa và đổi mã cũ sang mã hiện tại trước khi gửi model.
- Khi xuất, mỗi mã được giải thành nội dung của đúng chunk trong tài liệu và môn. Với nhiều mã của một câu, judge kiểm tra **từng mã riêng trên đúng chunk của nó**: một marker chỉ đúng khi chính chunk đó hỗ trợ câu, nên một nguồn đồng trích dẫn không liên quan bị tính sai. Không tìm thêm chunk lân cận để cứu một citation. Chunk đã mất hoặc locator cũ không tìm thấy/khớp nhiều chunk tính sai; module cũ vẫn đọc được theo quy tắc locator trước đây. Gói mù thay ký tự marker bằng khoảng trắng cùng độ dài, để không lộ nhóm RAG và giữ nguyên offsets của Content; các nguồn và citations vẫn ở metadata nghiên cứu ngoài gói mù.
- Thiếu `claim_citations` ở gói cũ: số trích dẫn và tỉ lệ là `null`. Danh sách đã lưu nhưng rỗng: số trích dẫn 0, tỉ lệ `null`. Nhóm không RAG không chấm trích dẫn. Độ phủ bộ đối chiếu không quyết định độ đúng của trích dẫn: trích dẫn đã giải được vẫn kiểm trên đúng chunk của nó, kể cả khi chunk đó nằm ngoài bộ đối chiếu.

Tệp R9 JSONL lưu mỗi gói/mô hình một dòng: mã ẩn danh, mã cặp, nhóm, alias và model chấm thật, SHA-256 của gói, cùng `claim_evaluation` đầy đủ (claim, trích dẫn, độ phủ, tử số, mẫu số, tỉ lệ). Metadata nhóm không đưa vào prompt chấm. Hai nhóm cùng cặp phải có nguồn và metadata đối chiếu giống nhau; cùng quy tắc từ chối model chấm cùng hãng như R8. Gói cũ chưa có metadata vẫn đọc được nhưng độ phủ mặc định `unknown`; xuất lại để có đánh giá đầy đủ.

Chạy lại cùng lệnh/tệp chỉ chấm gói còn thiếu; dòng cuối ghi dở được bỏ và chấm lại. Nếu gói, metadata hoặc model thật đằng sau alias chấm thay đổi, runner từ chối dùng kết quả cũ: dùng tệp kết quả mới cho lượt đánh giá mới. `--claims` đưa kết quả vào phần `claims` của báo cáo, tách theo alias chấm và nhóm, cộng tử số/mẫu số của các gói đủ điều kiện (không lấy trung bình tỉ lệ từng gói), công bố số gói được dùng/bị loại. Trình phân tích từ chối cặp `(judge, anon_id)` trùng lặp và cùng alias `judge` ánh xạ tới các `judge_model` khác nhau trong toàn bộ tệp R9 đầu vào, kể cả khi các gói khác nhau; phân tích riêng các lượt dùng model khác nhau. Trích dẫn cộng trên gói có dữ liệu trích dẫn. Các kiểm định và độ đồng thuận R8 vẫn dùng kết quả rubric, chưa áp dụng cho R9.

Kiểm chứng miễn phí bằng fake judge: 10 claim với 2 claim không có trong nguồn cho `unsupported_rate = 0,2`; claim được nguồn khác hỗ trợ nhưng trích sai chunk cho `citation_accuracy = 0`.

```bash
PYTHONPATH=. uv run pytest -q tests/integration/test_claim_evaluation.py tests/integration/test_run_judge.py \
    tests/unit/test_analyze_study.py tests/unit/test_evaluation.py
```
