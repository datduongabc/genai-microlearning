# Rubric đánh giá module Microlearning

Đây là **rubric v3** (`RUBRIC_VERSION` trong `backend/app/generation/evaluation.py`).
Rubric dùng chung cho mô hình chấm (`judge_module`) và cho người chấm, để so được độ đồng thuận giữa hai bên.
Mỗi module được chấm trên **9 tiêu chí cho điểm từ 1 đến 5**, kèm ghi chú tổng quan `overall_notes`.
Hai danh sách đi kèm điểm: `hallucination_findings` (khẳng định không có căn cứ) và `quiz_flaws` (lỗi viết câu hỏi của từng câu).
Mỗi tiêu chí được neo vào một nguồn đã công bố, liệt kê ở [mục 9](#9-căn-cứ-khoa-học); các nguồn đã được đọc toàn văn trừ khi ghi khác.
Một nghiên cứu ghi rubric lúc tạo (`studies.rubric_version`, do máy chủ đặt) và chỉ nhận đánh giá mô-đun mới theo rubric hiện hành; nghiên cứu cũ vẫn xem và xuất được kết quả theo tiêu chí của nó.

## 1. Thiết kế nghiên cứu

- **Hai nhóm ghép cặp.** Với mỗi chủ đề có một module sinh bằng RAG (nhóm `rag_llm`) và một module sinh không RAG trên cùng Learning Objective, cùng model sinh (nhóm `llm_no_rag`). Để không lẫn khác biệt giữa các model vào so sánh RAG, tuân theo [chế độ thí nghiệm và kiểm tra model thực sự đã sinh](gateway-routing.md#experiments).
- **Cùng tài liệu đối chiếu.** Hai module của một cặp được chấm trên cùng một bộ đoạn nguồn; quy tắc phạm vi và độ phủ ở [mục 7](#7-r9-claim-không-có-căn-cứ-và-độ-chính-xác-trích-dẫn). Không lấy bộ đối chiếu từ những chunk mà bản RAG tự trích dẫn.
- **Chấm mù.** Người chấm và mô hình chấm chỉ thấy mã ẩn danh (dạng `M-3fa9c2d1`), không thấy nhóm, tên môn, tên tệp hay tên model.
- **Hai mô hình chấm khác hãng với model sinh và khác hãng nhau.** Pool theo thứ tự ưu tiên: `claude-fast` (Claude Haiku 5.5, Anthropic), `openai-fast` (GPT-6 Luna, OpenAI), `gemini-flash` (Gemini 3.8 Flash, Google), `deepseek-flash` (DeepSeek V4.1 Flash, DeepSeek); bỏ hãng thực sự đã sinh gói theo guard chung `app/llm/judge.py`.
  Gói Claude dùng OpenAI + Gemini; gói OpenAI dùng Claude + Gemini; gói Gemini và gói DeepSeek dùng Claude + OpenAI.
  Các lượt chấm đã lưu trước 2026-11-16 dùng pool cũ (`claude-pro`, `openai-pro`, `gemini-fast` và các alias Groq); kết quả của chúng giữ nguyên trong `docs/measurements`.
- **Ứng viên hết quota hoặc không dùng được.** 429, gói vượt `max_input_tokens`, `NotFoundError`, `PermissionDeniedError`, lỗi API 403 hoặc vượt cửa sổ ngữ cảnh thì thử ứng viên kế tiếp.
  Các lỗi khác giữ cơ chế retry/pending của từng runner.
  Hai gói của một cặp cần hoàn tất cùng hai alias chấm; alias dự phòng cho một gói cũng chấm gói kia.
  Thử hãng còn đủ điều kiện theo thứ tự pool; mỗi gói có ba ứng viên khác hãng sinh.
  Mỗi lượt ghim model, tắt fallback gateway và lưu alias/model thực sự chấm.
  Nếu hết ứng viên thì giữ lượt còn thiếu để chạy tiếp, không coi một lượt là đủ hai; không xóa điểm đã lưu.

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
Mô hình chấm được dặn thêm: độ dài của module không phải chất lượng, vì mô hình chấm có xu hướng ưu ái câu trả lời dài (verbosity bias, Zheng et al. 2023).

### 3.1. Độ chính xác (`accuracy`)

Nội dung, ví dụ, câu quiz, bài luyện tập và lời giải thích có đúng với **tài liệu nguồn** được cung cấp không, và mức chi tiết có phù hợp không.
Chỉ đối chiếu với tài liệu nguồn, không dựa vào hiểu biết riêng của người chấm về chủ đề.
Căn cứ: mục "Content quality" của LORI (độ xác thực, chính xác, mức chi tiết phù hợp; Leacock & Nesbit 2007) và faithfulness của RAGAS (câu trả lời phải dựa trên ngữ cảnh được cung cấp; Es et al. 2024).

- **5**: mọi thông tin khớp với nguồn.
- **3**: có một vài chi tiết sai lệch nhưng ý chính vẫn đúng.
- **1**: có thông tin sai ở ý chính, hoặc đáp án đúng của quiz thực ra là sai theo nguồn.

### 3.2. Độ đầy đủ (`completeness`)

So với **toàn bộ** tài liệu nguồn, không chỉ với phạm vi hẹp mà Learning Objective tự chọn. Trong nhận xét phải liệt kê cụ thể ý hoặc khái niệm lớn nào trong nguồn mà nội dung không nhắc tới. Ý bị bỏ sót chỉ bị trừ điểm khi nó **cần thiết** để đạt Learning Objective; ý thuộc chủ đề khác thì ghi nhận nhưng không trừ.

- **5**: đủ mọi ý cần thiết cho Learning Objective.
- **3**: thiếu một ý cần thiết, hoặc nhiều ý chỉ được nhắc lướt.
- **1**: thiếu phần lớn các ý cần thiết, người học không đạt được Learning Objective chỉ với module này.

Căn cứ: mục "Central" trong rubric chấm câu hỏi do LLM sinh của Scaria et al. (2024): điều được hỏi có quan trọng với chủ đề của ngữ cảnh không.

### 3.3. Tính nhất quán (`consistency`)

Theo constructive alignment (Biggs 2014): động từ của Learning Objective (ví dụ "phân biệt", "tính", "giải thích") phải là việc nội dung dạy, ví dụ minh hoạ và câu hỏi đòi hỏi.
Biggs mô tả động từ trong mục tiêu là mối nối giữa mục tiêu, hoạt động học và bài kiểm tra.
Cụ thể: mỗi câu quiz và bài luyện tập có hỏi đúng điều đã dạy trong nội dung không; lời giải thích của mỗi câu có khớp với đáp án đúng của chính câu đó không; các ví dụ có mâu thuẫn với nội dung không.

- **5**: mọi câu hỏi, lời giải và ví dụ khớp với nội dung và với nhau.
- **3**: một câu hỏi về điều nội dung chưa dạy, hoặc một lời giải lệch đáp án.
- **1**: nhiều câu hỏi, lời giải hoặc ví dụ mâu thuẫn với nội dung.

### 3.4. Hallucination (`hallucination_score` và danh sách hallucination)

Tách nội dung thành từng ý nhỏ (atomic claim) và kiểm từng ý có căn cứ trong tài liệu nguồn không, theo cách của FActScore (Min et al. 2023).
FActScore đo độ chính xác chứ không đo độ bao phủ, nên ý bị thiếu thuộc `completeness`, không thuộc tiêu chí này.
Liệt kê **từng** ý trong nội dung, slide, ví dụ, quiz, bài luyện tập hay lời giải thích **không có căn cứ** trong tài liệu nguồn, kèm vị trí (một trong sáu phần đó) và lý do.
Bối cảnh giả định của một ví dụ (tên người, tình huống) không phải khẳng định; chỉ khẳng định về kiến thức của bài mới tính. Mục tiêu học tập không thuộc danh sách: nó là đích đến chứ không phải khẳng định, và đã được `bloom_fidelity`, `query_alignment` kiểm. Phải kiểm từng câu, kể cả khi module trông sạch. Một ý diễn đạt lại hoặc suy ra trực tiếp từ nguồn không phải hallucination; một con số, tên riêng, định nghĩa hay khẳng định mà nguồn không có thì là hallucination, kể cả khi nó đúng ngoài đời.

- **5**: không có câu nào không có căn cứ; danh sách rỗng.
- **4**: một câu không có căn cứ, ở chi tiết phụ.
- **3**: hai đến ba câu không có căn cứ, hoặc một câu ở ý chính.
- **2**: nhiều câu không có căn cứ, rải rác khắp module.
- **1**: phần lớn nội dung không có căn cứ trong nguồn.

Phân tích thống kê dùng danh sách này dưới dạng nhị phân: module **có ít nhất một** hallucination hay không.

### 3.5. Chất lượng slide (`slide_quality`)

Theo ba nguyên tắc thiết kế đa phương tiện của Mayer (2023), mỗi nguyên tắc có bằng chứng thực nghiệm:

- **Coherence**: bỏ chi tiết thừa không phục vụ mục tiêu (đúng ở 18/19 thí nghiệm, hệ số ảnh hưởng trung vị 0,86).
- **Signaling**: tiêu đề và gạch đầu dòng làm nổi ý chính và cấu trúc (26/28 thí nghiệm, 0,70).
- **Segmenting**: chia bài thành phần nhỏ, mỗi slide một ý (7/7 thí nghiệm, 0,67).

Gạch đầu dòng còn phải tóm tắt đúng nội dung.

- **5**: đạt cả ba nguyên tắc và tóm tắt đúng.
- **3**: vi phạm rõ một nguyên tắc (ví dụ gạch đầu dòng dài như đoạn văn), hoặc có gạch đầu dòng tóm tắt sai.
- **1**: slide sai nội dung, rời rạc, hoặc chép nguyên văn cả đoạn.

### 3.6. Chất lượng quiz (`quiz_quality`)

Điểm này **không do người chấm chọn**: nó được tính từ danh sách lỗi viết câu hỏi (`quiz_flaws`).
Người chấm (hoặc mô hình chấm) đánh dấu, cho **từng** câu quiz và cho bài luyện tập, những lỗi trong 19 lỗi viết câu hỏi (item-writing flaws) của Tarrant et al. (2006), theo đúng cách diễn đạt trong bảng 1 của Moore et al. (2024).
Câu quiz đánh số 1..n theo thứ tự, bài luyện tập là câu n+1; mỗi câu phải có đúng một mục, kể cả khi không có lỗi nào.

| Khoá | Câu hỏi mắc lỗi nếu |
|---|---|
| `longest_option_correct` | đáp án đúng dài và chi tiết hơn các phương án khác |
| `ambiguous_information` | câu hỏi hoặc phương án viết mơ hồ, khó hiểu |
| `implausible_distractors` | có phương án nhiễu vô lý |
| `true_or_false` | các phương án là chuỗi mệnh đề đúng/sai |
| `absolute_terms` | dùng từ tuyệt đối (luôn luôn, không bao giờ, tất cả) |
| `complex_k_type` | phương án là tổ hợp các phương án khác |
| `negatively_worded` | câu hỏi dùng dạng phủ định |
| `convergence_cues` | đáp án đúng chung nhiều thành phần nhất với các phương án khác |
| `lost_sequence` | phương án không xếp theo thứ tự số hoặc thời gian |
| `unfocused_stem` | phải đọc phương án mới hiểu và trả lời được câu hỏi |
| `none_of_the_above` | có phương án "không có đáp án nào đúng" |
| `word_repeats` | một từ của câu hỏi chỉ lặp lại ở đáp án đúng |
| `more_than_one_correct` | có hơn một đáp án đúng |
| `logical_cues` | câu hỏi và đáp án có gợi ý giúp đoán ra đáp án |
| `all_of_the_above` | có phương án "tất cả đều đúng" |
| `fill_in_the_blank` | câu hỏi bỏ trống chữ ở giữa để phương án điền vào |
| `vague_terms` | phương án dùng từ mơ hồ (thường, đôi khi) |
| `grammatical_cues` | không phải mọi phương án đều khớp ngữ pháp với câu hỏi |
| `gratuitous_information` | câu hỏi có thông tin thừa không cần để trả lời |

Theo Tarrant et al. (2006), như Moore et al. (2024) nhắc lại, câu có **tối đa một lỗi** là dùng được, từ hai lỗi trở lên là không dùng được.
Với p là tỉ lệ câu dùng được, điểm = 1 + làm tròn(4p): mọi câu dùng được cho 5, không câu nào dùng được cho 1.
Ví dụ với 6 câu: 6, 5, 4, 3, 2, 1, 0 câu dùng được lần lượt cho 5, 4, 4, 3, 2, 2, 1.
Nhận xét của tiêu chí vẫn bắt buộc, để nói lỗi chính.
Độ khó theo mức Bloom chấm ở `bloom_fidelity`, không ở đây.

Lý do chấm bằng danh sách có/không thay vì một điểm tổng thể: với cùng một cặp module Tree, hai mô hình chấm theo rubric v1 cho quiz 5 và 3.
Chấm theo danh sách các câu hỏi có/không làm các mô hình chấm đồng thuận hơn so với thang Likert (CheckEval, Lee et al. 2025, bản arXiv; đo trên tóm tắt văn bản và hội thoại, giữa các mô hình với nhau).
Moore et al. (2023) cũng ghi nhận GPT-4 chỉ bắt được 79% số lỗi mà người chấm tìm ra, nên lỗi của mô hình chấm cần được đối chiếu với người.

### 3.7. Độ khớp mức Bloom (`bloom_fidelity`)

Mức Bloom khai báo có khớp với mức **thực tế** mà nội dung, quiz và bài luyện tập đòi hỏi không. Đối chiếu theo bảng neo ở mục 4, dựa trên Bloom bản sửa đổi (Krathwohl 2002). Trang chấm mù hiện mức Bloom khai báo ngay cạnh tiêu chí này ("Mức Bloom đặt ra"), không hiện trong gói.

- **5**: khớp hoàn toàn mức khai báo.
- **3**: lệch một mức ở một phần (ví dụ khai báo Apply nhưng một nửa số câu chỉ đòi Remember).
- **1**: lệch rõ rệt (ví dụ khai báo Remember nhưng quiz đòi Analyze, hoặc khai báo Apply nhưng toàn bộ dừng ở Remember).

### 3.8. Bám chủ đề gốc (`query_alignment`)

Module có thực sự trả lời đúng **chủ đề gốc** được giao không (trường "Chủ đề"), và có tập trung vào **một** mục tiêu chính không.
Khác với `bloom_fidelity`, vốn chỉ đối chiếu với Learning Objective do chính module đặt ra.
Một module có thể chính xác, nhất quán, không hallucination mà vẫn lạc chủ đề: khi đó tiêu chí này phải thấp dù các tiêu chí khác cao.
Tính "micro" theo tổng quan hệ thống của Monib et al. (2025): microlearning là nội dung ngắn, hướng hành động, tập trung vào một mục tiêu cụ thể; bài này cũng nhắc lại (trích McNeill & Fitch) rằng quá nhiều mục tiêu hay quá nhiều nội dung gây quá tải.

- **5**: toàn bộ module xoay quanh đúng chủ đề gốc, một mục tiêu rõ.
- **3**: đúng chủ đề nhưng dàn trải, hoặc có phần đáng kể nói về chủ đề lân cận.
- **1**: module nói về chủ đề khác.

### 3.9. Ví dụ và lời giải (`examples_feedback`)

Hai phần dạy bằng phản hồi của module:

- **Ví dụ** có đúng, có trình bày từng bước hoặc bước con để người học theo được cách làm, và có giúp hiểu khái niệm không (worked examples; Atkinson et al. 2000).
- **Lời giải** của mỗi câu có nói vì sao đáp án đúng là đúng và vì sao phương án nhiễu sai, cụ thể và ngắn gọn, thay vì chỉ xác nhận đúng/sai không (elaborated feedback; Shute 2008, bảng 2: phản hồi nên nêu "cái gì, thế nào, vì sao" và chia thành phần vừa sức).

- **5**: ví dụ có bước rõ ràng và mọi lời giải nêu lý do.
- **3**: ví dụ chỉ nêu kết quả, hoặc nhiều lời giải chỉ xác nhận đáp án.
- **1**: ví dụ sai, hoặc không có lời giải dùng được.

### 3.10. Ghi chú tổng quan (`overall_notes`)

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
   Riêng `quiz_quality`: đánh dấu lỗi của từng câu, điểm tự tính (mục 3.6). Trong ứng dụng, mỗi câu hiện kèm đề bài và danh sách 19 lỗi để đánh dấu.
5. **Ghi lại hallucination** trong cột `hallucination_notes`: mỗi câu không có căn cứ một dòng, kèm vị trí. Cột này không vào thống kê điểm nhưng giúp đối chiếu với danh sách của mô hình chấm.
6. **Hiệu chuẩn trước khi chấm thật.** Chấm thử hai gói không nằm trong mẫu, rồi đối chiếu với người nghiên cứu để thống nhất cách hiểu các mức neo. Không sửa điểm của gói thật sau khi đã xem điểm của mô hình chấm.
7. **Mỗi người chấm một tệp CSV riêng**, mã hoá UTF-8, dòng đầu là tên cột:

```csv
anon_id,accuracy,completeness,consistency,hallucination_score,slide_quality,quiz_quality,bloom_fidelity,query_alignment,examples_feedback,hallucination_notes
M-3fa9c2d1,4,3,5,4,4,3,5,5,4,"nội dung: câu về năm 1998 không có trong nguồn"
```

Để trống một ô khi không chấm được tiêu chí đó; ô trống không được tính. Mỗi mã ẩn danh chỉ xuất hiện một lần trong tệp.
Chấm ngoài ứng dụng bằng CSV thì tự tính `quiz_quality` theo công thức ở mục 3.6; tệp CSV không mang danh sách lỗi từng câu.

## 6. Quy trình chạy và phân tích

Chạy trong thư mục `backend/`. R8 cần hai lượt thành công mỗi gói (có thể thêm lượt thử khi hết quota); R9 gọi một lượt tách claim khi Content không rỗng, một lượt kiểm mỗi claim khi có nguồn hợp lệ, rồi một lượt kiểm mỗi trích dẫn đã giải được chunk có nội dung. Hiện phát triển và validation chỉ dùng kiểm thử xác định hoặc route miễn phí được xác minh, đủ ngữ cảnh và quota; API trả phí để sau khi hoàn thành toàn bộ dự án. Bước xuất có thể sinh bản không RAG nếu chưa có sẵn, nên cũng cần route miễn phí. Kết quả lượt chạy R9 trên 10 cặp module nằm trong [module-study](measurements/2026-11-07-module-study-pairs.md).

```bash
# 1. Xuất cặp gói của từng module RAG đã sinh (một lần cho mỗi module).
PYTHONPATH=. uv run python scripts/export_study_pair.py <module_id> --out study/packets

# 2. Chấm nghiên cứu bằng pool ở mục 1 (có model trả phí). Kiểm thử dùng mock.
#    Hai model khác hãng với model sinh và nhau. Bị ngắt thì chạy lại
#    đúng lệnh đó: chỉ những gói còn thiếu được chấm tiếp.
PYTHONPATH=. uv run python scripts/run_judge.py study/packets --out study/rubric.jsonl
#    --judges a,b giới hạn pool (guard khác hãng vẫn áp dụng), ví dụ khi hãng ưu tiên chưa có khóa;
#    ghi vào tệp riêng để lượt chính thức vẫn chọn đúng hai alias ưu tiên.

# 3. R9: tách claim độc lập từ Content và chấm các trích dẫn được máy chủ giải.
PYTHONPATH=. uv run python scripts/run_claim_judge.py study/packets --judge <verified-free-alias> --out study/claims.jsonl

# 4. Xuất nghiên cứu chấm mù trong ứng dụng: mỗi người chấm một CSV ẩn danh rater-N.csv, và các
#    lượt chấm của mô hình chấm trong ứng dụng vào judge.jsonl, cùng mã ẩn danh M01, M02, ...
#    Thư mục đích không được có sẵn rater-*.csv hay judge.jsonl.
PYTHONPATH=. uv run python scripts/export_human_ratings.py <study_id> --out study/inapp

# 5. Phân tích judge.jsonl vừa xuất cùng các CSV người chấm, lặp --human cho mỗi người chấm.
PYTHONPATH=. uv run python scripts/analyze_study.py study/inapp/judge.jsonl \
    --claims study/claims.jsonl --human study/inapp/rater-1.csv --human study/inapp/rater-2.csv \
    --out study/report.json
```

Có hai hệ mã ẩn danh, không được trộn. Nghiên cứu trong ứng dụng đánh mã M01, M02, ... cho các gói; `export_human_ratings.py` ghi đúng mã đó cho cả người chấm lẫn mô hình chấm. `run_judge.py` (bước 2) đánh mã `M-` cộng 8 ký tự hex, cố định theo tên tệp gói; chỉ ghép tệp `.jsonl` của nó với CSV của người chấm đã chấm chính các tệp gói đó. Đưa một tệp người chấm không có mã nào trùng với tệp của mô hình chấm thì `analyze_study.py` dừng với lỗi, thay vì báo độ đồng thuận rỗng.

`analyze_study.py` báo cáo:

- **Điểm trung bình** mỗi tiêu chí của từng nhóm, theo từng mô hình chấm.
- **Wilcoxon signed-rank hai phía** trên các cặp (rag_llm trừ llm_no_rag) cho từng tiêu chí, kèm p đã hiệu chỉnh Holm cho 9 tiêu chí. Dùng Wilcoxon vì điểm là thang thứ bậc và mẫu nhỏ, không giả định phân phối chuẩn.
- **McNemar chính xác** cho tỉ lệ module có ít nhất một hallucination giữa hai nhóm, trên các cặp.
- **Spearman, kappa trọng số bậc hai và AC2 của Gwet trọng số bậc hai** (thang 1-5) cho từng tiêu chí, giữa hai mô hình chấm, giữa mỗi mô hình chấm với từng người, và giữa từng cặp người chấm.
  AC2 được báo kèm kappa vì khi điểm dồn về một đầu thang, kappa có thể thấp dù hai bên gần như đồng ý ("kappa paradox"), còn AC2 ít đổi theo phân bố điểm hơn (Quarfoot & Levine 2016).
  Ví dụ đã kiểm trong test: 10 gói, 8 gói hai bên cùng cho 5 và 2 gói lệch một mức cho AC2 = 0,985 nhưng kappa = -0,111.
- **Độ tin cậy của cả nhóm người chấm** (khi có từ hai tệp `--human`), cho từng tiêu chí:
  - **Krippendorff's α thứ bậc** (kèm α khoảng) trên mọi điểm đã cho. Đây là chỉ số chính: nó đúng với thang thứ bậc 1-5 và chịu được việc mỗi người không chấm đủ mọi gói.
  - **Fleiss' κ** trên những gói mà ai cũng chấm. Fleiss coi thang là danh nghĩa (lệch 1 điểm bị tính như lệch 4 điểm), nên thường thấp hơn α thứ bậc trên cùng dữ liệu; báo kèm vì đây là chỉ số quen thuộc.
  - **Nhóm người cộng một mô hình chấm** (`humans+<judge>`): α khi thêm mô hình chấm vào như một người chấm nữa. α không giảm nghĩa là mô hình chấm đồng thuận với người ngang người với người.
  - Mốc đọc α thường dùng (Krippendorff 2004): α ≥ 0,800 tin cậy được; 0,667 ≤ α < 0,800 chỉ dùng cho kết luận thận trọng.
  - Cài đặt được kiểm bằng ví dụ đã công bố: dữ liệu của Krippendorff (2011) cho α danh nghĩa 0,743, thứ bậc 0,815, khoảng 0,849; ví dụ Fleiss 10 đối tượng × 14 người cho κ = 0,210.

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
PYTHONPATH=. uv run pytest -q tests/unit/test_claim_evaluation.py tests/unit/test_run_judge.py \
    tests/unit/test_analyze_study.py tests/unit/test_evaluation.py
```

## 8. Thay đổi

- **2026-11-11: phủ đủ các phần của module.**
  Mỗi phần được sinh ở một bước riêng (`_generate_module` trong `backend/app/generation/module_generation_pipeline.py`), và ví dụ đọc đoạn nguồn mà không có bước tự kiểm, nên phần nào cũng có thể chứa khẳng định không có căn cứ.
  Trước đó, độ chính xác và hallucination chỉ kiểm nội dung, quiz và lời giải thích, còn tính nhất quán và chất lượng quiz không nói rõ có gồm bài luyện tập.
  Từ ngày này, độ chính xác kiểm thêm ví dụ và bài luyện tập; hallucination kiểm thêm slide, ví dụ và bài luyện tập; tính nhất quán và chất lượng quiz gồm cả bài luyện tập.
  Lời nhắc của mô hình chấm và form chấm trong ứng dụng đổi cùng lúc.
  Lượt chấm thử `module-study/judge-groq-qwen.jsonl` dùng rubric cũ, nên không gộp với các lượt chấm chính thức.
- **2026-11-22: rubric v3.**
  Mỗi tiêu chí được neo vào một nguồn đã công bố (mục 9), các nguồn được đọc toàn văn trước khi dẫn.
  `quiz_quality` chuyển từ một điểm tổng thể sang danh sách 19 lỗi viết câu hỏi cho từng câu, điểm tự tính; thêm tiêu chí thứ chín `examples_feedback`; `query_alignment` thêm tính tập trung vào một mục tiêu; `slide_quality` theo ba nguyên tắc của Mayer; hallucination kiểm theo từng ý nhỏ và không tính bối cảnh giả định của ví dụ.
  Phân tích báo thêm AC2 của Gwet.
  Nghiên cứu tạo trước v3 (rubric `v1` và `docs/rubric.md 2026-11-07`) giữ tám tiêu chí cũ: xem và xuất được, nhưng không nhận đánh giá mô-đun hay lượt chấm mới.

## 9. Căn cứ khoa học

Các nguồn dưới đây đã được đọc toàn văn, trừ hai nguồn ghi "chỉ xác minh trang tóm tắt".

- Atkinson, R. K., Derry, S. J., Renkl, A., & Wortham, D. (2000). Learning from examples: Instructional principles from the worked examples research. *Review of Educational Research, 70*(2), 181-214.
- Biggs, J. (2014). Constructive alignment in university teaching. *HERDSA Review of Higher Education, 1*, 5-22.
- Biggs, J. (1996). Enhancing teaching through constructive alignment. *Higher Education, 32*, 347-364. Chỉ xác minh trang tóm tắt; nội dung dùng ở đây lấy từ Biggs (2014).
- Es, S., James, J., Espinosa-Anke, L., & Schockaert, S. (2024). RAGAs: Automated evaluation of retrieval augmented generation. *Proceedings of EACL 2024: System Demonstrations*, 150-158.
- Fleiss, J. L. (1971). Measuring nominal scale agreement among many raters. *Psychological Bulletin, 76*(5), 378-382.
- Haladyna, T. M., Downing, S. M., & Rodriguez, M. C. (2002). A review of multiple-choice item-writing guidelines for classroom assessment. *Applied Measurement in Education, 15*(3), 309-333. Chỉ xác minh trang tóm tắt (31 nguyên tắc, tổng hợp từ 27 giáo trình); bộ lỗi dùng ở mục 3.6 lấy từ Tarrant et al. (2006) qua Moore et al. (2024).
- Krippendorff, K. (2004). *Content analysis: An introduction to its methodology* (2nd ed.). Sage.
- Krippendorff, K. (2011). Computing Krippendorff's alpha-reliability. Annenberg School for Communication, University of Pennsylvania.
- Krathwohl, D. R. (2002). A revision of Bloom's taxonomy: An overview. *Theory Into Practice, 41*(4), 212-218.
- Leacock, T. L., & Nesbit, J. C. (2007). A framework for evaluating the quality of multimedia learning resources. *Educational Technology & Society, 10*(2), 44-59.
- Lee, Y., Kim, J., Kim, J., Cho, H., Kang, J., Kang, P., & Kim, N. (2025). CheckEval: A reliable LLM-as-a-Judge framework for evaluating text generation using checklists. arXiv:2403.18771v3.
- Mayer, R. E. (2023). Research-based principles for designing multimedia instruction. Trong C. E. Overson, C. M. Hakala, L. L. Kordonowy, & V. A. Benassi (Eds.), *In their own words*. Division 2, American Psychological Association.
- Min, S., Krishna, K., Lyu, X., Lewis, M., Yih, W., Koh, P. W., Iyyer, M., Zettlemoyer, L., & Hajishirzi, H. (2023). FActScore: Fine-grained atomic evaluation of factual precision in long form text generation. *Proceedings of EMNLP 2023*, 12076-12100.
- Monib, W. K., Qazi, A., & Apong, R. A. (2025). Microlearning beyond boundaries: A systematic review and a novel framework for improving learning outcomes. *Heliyon, 11*(2), e41413.
- Moore, S., Nguyen, H. A., Chen, T., & Stamper, J. (2023). Assessing the quality of multiple-choice questions using GPT-4 and rule-based methods. Trong *Responsive and Sustainable Educational Futures (EC-TEL 2023)*, 229-245. Springer.
- Moore, S., Costello, E., Nguyen, H. A., & Stamper, J. (2024). An automatic question usability evaluation toolkit. *Artificial Intelligence in Education (AIED 2024)*. arXiv:2405.20529.
- Quarfoot, D., & Levine, R. A. (2016). How robust are multirater interrater reliability indices to changes in frequency distribution? *The American Statistician, 70*(4), 373-384.
- Scaria, N., Chenna, S. D., & Subramani, D. (2024). How good are modern LLMs in generating relevant and high-quality questions at different Bloom's skill levels for Indian high school social science curriculum? *Proceedings of the 19th Workshop on Innovative Use of NLP for Building Educational Applications (BEA 2024)*, 1-10.
- Shute, V. J. (2008). Focus on formative feedback. *Review of Educational Research, 78*(1), 153-189.
- Tarrant, M., Knierim, A., Hayes, S. K., & Ware, J. (2006). The frequency of item writing flaws in multiple-choice questions used in high stakes nursing assessments. *Nurse Education Today, 26*(8), 662-671. Dẫn qua Moore et al. (2024), chưa đọc bản gốc.
- Zheng, L., Chiang, W.-L., Sheng, Y., et al. (2023). Judging LLM-as-a-judge with MT-Bench and Chatbot Arena. *NeurIPS 2023 Datasets and Benchmarks Track*.
