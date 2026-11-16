# Video giọng AI và flashcard cho học trước ở nhà

Ngày: 2026-11-12.
Trạng thái: đã duyệt 2026-11-12, sau một video dựng thử (xem mục cuối).

## Ý định

Thầy hướng dẫn áp dụng lớp học đảo ngược.
Ở nhà, sinh viên xem video ngắn, đọc bài và làm quiz; lên lớp chỉ làm bài tập và quiz trên lớp.
Mô-đun microlearning hiện có (7 phần, đưa sang Moodle bằng Book, Quiz hoặc gói SCORM) là phần đọc và quiz ở nhà.
Tài liệu này thêm hai thứ cho phần học trước ở nhà: video thuyết minh bằng giọng AI, có slide minh họa kiểu NotebookLM, và bộ flashcard ôn lý thuyết.
Cả hai bám sát tài liệu môn học như phần còn lại của hệ thống: không thêm kiến thức ngoài tài liệu, có trích nguồn.

## Người dùng quyết định

- Video và flashcard là **mục tự tạo riêng**, giống NotebookLM, không phải phần thứ 8, thứ 9 của mô-đun. Mô-đun giữ nguyên 7 phần.
- Mỗi video hoặc bộ flashcard được **sinh độc lập từ một tài liệu**, không sinh từ mô-đun, kể cả khi đã có mô-đun cùng chủ đề.
- Lúc sinh có **hộp tùy chỉnh** giống hộp sinh mô-đun: giảng viên gõ trọng tâm (ví dụ "tập trung vào array" trong chương 1 gồm array và linked list). Một tài liệu có thể có nhiều video và nhiều bộ flashcard, mỗi cái một trọng tâm.
- Video **dài tối đa 8 phút**, có **giọng đọc AI**.
- Hình của video làm **như NotebookLM**: slide do hệ thống dựng, có hình minh họa do **model sinh ảnh** vẽ. Không dùng ảnh chụp trang tài liệu.
- Model ảnh: **Gemini Nano Banana 2.1** và **Qwen Image 2.1 Turbo**, qua OpenRouter, đã thử trên một slide mẫu (khoảng $0,037 và $0,018 một ảnh).
- Model giọng: **HeyGen Voice 1 Turbo**, **Gemini 3.8 Flash-Lite TTS**, **Gemini 3.8 Flash TTS**, qua OpenRouter, đã nghe thử với tiếng Việt và tiếng Anh. Mặc định là HeyGen vì rẻ nhất (khoảng $0,03 một video 8 phút, so với $0,07 và $0,11).
- Giảng viên chọn model giọng (và model ảnh) trong hộp tùy chỉnh, như bộ chọn model lúc sinh mô-đun.
- Không dùng model TTS mã nguồn mở tự chạy.
- Giảng viên **duyệt và sửa lời thoại** trước khi video được dựng.
- Flashcard dùng để **ôn phần lý thuyết**: mặt trước là một khái niệm hoặc câu hỏi ngắn, mặt sau là định nghĩa hoặc câu trả lời ngắn có trích nguồn. Giảng viên duyệt và sửa được bộ thẻ.
- Sinh viên xem video và lật flashcard **trên trang của hệ thống, mở từ Moodle qua LTI**.

## Mục tiêu

1. Giảng viên sinh được lời thoại video từ một tài liệu với một trọng tâm, sửa lời thoại và nội dung slide, rồi dựng thành video MP4 không quá 8 phút.
2. Giảng viên sinh được một bộ flashcard từ một tài liệu với một trọng tâm, rồi sửa, thêm hoặc xóa thẻ.
3. Giảng viên quyết định video và bộ thẻ nào sinh viên được thấy.
4. Sinh viên mở công cụ từ khóa học Moodle, thấy các video và bộ thẻ đã giao của môn đó, xem video và lật thẻ.
5. Mỗi câu thuyết minh, mỗi ý trên slide và mỗi mặt sau của thẻ do model viết đều trỏ được về trang, slide hoặc mốc thời gian của tài liệu.

## Không làm (non-goals)

- Không thêm video hay flashcard vào mô-đun, gói SCORM hoặc Moodle Book. *(lựa chọn của mình)*
- Không dùng ảnh chụp trang tài liệu, nên không cài LibreOffice và không giữ tệp gốc sau ingestion.
- Không có nhân vật ảo hay hoạt hình chuyển động: mỗi cảnh là một slide tĩnh kèm giọng đọc.
- Không có trình sửa video theo dòng thời gian: giảng viên sửa lời, slide và hình của từng cảnh, rồi dựng lại.
- Không có lịch ôn ngắt quãng (spaced repetition) hay chấm điểm flashcard. *(lựa chọn của mình)*
- Không có trang mô-đun cho sinh viên trên hệ thống: mô-đun vẫn đến tay sinh viên qua Moodle như hiện tại. *(lựa chọn của mình)*
- Không tự chạy model TTS hay model ảnh.

## Thất bại dù vẫn chạy được (anti-goals)

- Lời thoại, chữ trên slide hoặc thẻ chứa kiến thức không có trong tài liệu.
- Chữ trên slide sai dấu hoặc méo (lỗi thường gặp của model ảnh với tiếng Việt), hoặc công thức sai.
- Hình minh họa vẽ sai khái niệm mà giảng viên không thấy hoặc không sửa được trước khi giao.
- Sinh viên thấy video hoặc bộ thẻ chưa được giảng viên duyệt và giao.
- Video dài quá 8 phút.
- Tài liệu môn học gửi tới nhà cung cấp lưu hoặc huấn luyện trên dữ liệu gửi lên.
- Giảng viên phải ngồi chờ trên trang trong lúc dựng video, hoặc một lần dựng hỏng làm mất lời thoại đã sửa.
- Một sinh viên xem được video hoặc thẻ của môn mình không thuộc.

## Ràng buộc từ hệ thống hiện có

- Mọi model gọi qua gateway LiteLLM với một khóa OpenRouter; mỗi alias khóa cứng một nhà cung cấp, `allow_fallbacks: false`, `data_collection: deny` (`devops/gateway/config.yaml`). Năm model giọng và ảnh phải theo đúng quy tắc này; bản `:free` của HeyGen không dùng. Nếu model nào không đạt `data_collection: deny`, người làm báo người dùng trước khi bỏ hoặc thay nó.
- Cách gọi đã thử được:
  - Giọng: `POST /api/v1/audio/speech`. Gemini TTS chỉ trả `pcm` 24 kHz; HeyGen trả `pcm` 44,1 kHz (đọc tần số từ header `content-type`) và bắt buộc có `voice` (đã thử `nadine`); Gemini đã thử `Kore`.
  - Ảnh: Nano Banana 2.1 qua `POST /api/v1/chat/completions` với `modalities: ["image", "text"]`; Qwen Image 2.1 Turbo chỉ qua `POST /api/v1/images`. Cả hai nhận tỉ lệ 16:9 và trả chi phí trong `usage.cost`.
- ffmpeg đã có trong image backend và worker.
- Job nền dùng hàng đợi Postgres hiện có (`app/jobs/queue.py`): thêm `JobType` cần migration; ghi kết quả phải qua `hold()` để worker đã mất job không ghi đè.
- Sinh viên vào qua LTI là `CourseMember` với `can_author = false`; trang `/learn/{course}` hiện chỉ có chat. RLS hiện cho thành viên đọc `documents` và `chunks` nhưng không đọc `modules` (`alembic/versions/0027_course_members.py`). Bảng mới cần chính sách RLS cho chủ môn học (đọc, ghi) và cho thành viên (chỉ đọc bản đã giao).
- Hạn mức sinh của người dùng chưa liên kết Moodle (`generation/limits.py`, 3 lần một ngày) áp dụng cho mọi lần gọi model sinh nội dung.
- Ngôn ngữ đầu ra là `vi` hoặc `en`, như mô-đun.
- Lời thoại và slide chỉ dựa trên phần chữ đã lưu (chunk), nên dùng được với mọi tài liệu đã xử lý, kể cả 5 môn hiện có, PPTX và bài ghi âm.

## Cách làm đã chốt

### Lời thoại theo cảnh *(lựa chọn của mình)*

Lời thoại là một danh sách cảnh; mỗi cảnh là một slide.
Mỗi cảnh có một loại: **minh họa** (khái niệm cần hình), **công thức, code hoặc bảng** (hệ thống tự trình bày), hoặc **mở đầu, tóm tắt** (chỉ có chữ).
Mỗi cảnh có: tiêu đề slide, 2 đến 4 ý chính (có thể có công thức, đoạn code ngắn hoặc bảng nhỏ lấy từ tài liệu), một mô tả hình minh họa cho model ảnh, vài câu thuyết minh, và trích nguồn tới chunk.
Lời thoại được sinh bằng truy xuất theo trọng tâm như mô-đun (hybrid, rerank, Adaptive-k), với model sinh nội dung đang chọn.
Khi sinh, model được yêu cầu nhắm khoảng 5 phút (4 đến 6 phút), tức khoảng 8 đến 10 cảnh; mức chặn cứng vẫn là 8 phút.

### Slide: chữ do hệ thống in, hình do model ảnh vẽ *(lựa chọn của mình)*

Chỉ cảnh loại minh họa gọi model ảnh, tối đa 5 cảnh mỗi video *(lựa chọn của mình, người dùng đã đồng ý)*; cảnh công thức, code, bảng, mở đầu và tóm tắt dùng mẫu trình bày, không tốn tiền và không vẽ sai.
Model ảnh chỉ vẽ phần hình minh họa, theo mô tả của cảnh, với yêu cầu không vẽ chữ và chừa chỗ cho chữ.
Tiêu đề, ý chính, công thức và dòng trích nguồn do hệ thống in lên slide theo một mẫu trình bày cố định, nên chữ luôn đúng dấu và khớp với nguồn.
Lý do: trong lần thử, model ảnh rẻ còn vẽ sai thao tác (chèn thành xóa hoặc đổi chỗ) và một model viết chữ méo; tiếng Việt có dấu là điểm yếu chung của model ảnh.
Mặc định dùng Nano Banana 2.1, vì nó là model duy nhất vẽ đúng khái niệm trong lần thử *(lựa chọn của mình)*; Qwen Image 2.1 Turbo là lựa chọn rẻ hơn khoảng một nửa.
Trong trình sửa, giảng viên xem từng slide, vẽ lại hình của một cảnh (có thể sửa mô tả hình trước khi vẽ lại), hoặc bỏ hình để cảnh chỉ còn chữ.
Hình được vẽ sau khi giảng viên duyệt lời thoại và slide chữ, nên lời thoại bị bỏ hoặc sinh lại không tốn tiền hình; giảng viên xem hình trước khi dựng và trước khi giao.

### Kiểm tự động trước khi giảng viên duyệt

Một model thuộc họ khác với model viết lời thoại đối chiếu từng câu thuyết minh, từng ý trên slide và từng mặt sau của thẻ với đoạn nguồn được trích, và đánh dấu những chỗ nghi sai hoặc không có trong nguồn để giảng viên xem trước, giống cách câu hỏi quiz được kiểm.
Lý do: lần dựng thử có hai lỗi kiến thức thật mà trích nguồn không chặn được, vì câu vẫn trỏ đúng đoạn nhưng nói sai ý ("một phần chín" thay vì "một phần n", và một ý trên slide bị ngược nghĩa).
Chỗ bị đánh dấu không chặn việc dựng; giảng viên sửa hoặc bỏ đánh dấu.

### Quy tắc cho mô tả hình và độ dài

- Mô tả hình phải ghi đủ và đúng mọi số liệu và nhãn có trong hình (thời gian chạy, mốc trên biểu đồ Gantt, tên tiến trình), viết nhãn ở dạng sẽ hiện ra ("P1", không phải "P_1"); hình chỉ vẽ đúng khi mô tả đủ chi tiết.
- Chữ trong công thức dùng `\text{}` để không in nghiêng như biến.
- Sau khi sinh, hệ thống đếm ký tự lời thoại; thiếu nhiều so với khoảng 5 phút thì yêu cầu model viết thêm cho các cảnh ngắn, vì lần thử model viết khoảng 4 phút dù được nhắm 5 phút.

### Trích nguồn khi giảng viên sửa *(lựa chọn của mình)*

Câu và ý do model viết phải có trích nguồn; câu không trích được bị bỏ trước khi lưu, như phần nội dung của mô-đun.
Giảng viên là tác giả và chịu trách nhiệm nội dung mình viết, nên câu giảng viên sửa hoặc thêm được lưu và dựng dù không có trích nguồn.
Cảnh hoặc thẻ có câu không trích nguồn hiện cảnh báo trong trình sửa, và giảng viên gắn được trích nguồn bằng cách chọn đoạn của tài liệu.
Trích nguồn được lưu theo từng câu thuyết minh, từng ý trên slide và theo mặt sau của thẻ.

### Giới hạn 8 phút *(lựa chọn của mình)*

Trước khi dựng, hệ thống ước độ dài từ số ký tự lời thoại (đo được khoảng 15 ký tự mỗi giây với cả ba giọng ở cả hai ngôn ngữ) và chặn khi quá 8 phút, kèm số phút ước tính.
Sau khi tạo giọng, độ dài thật được đo lại; quá 8 phút thì job thất bại với lý do rõ ràng và lời thoại vẫn giữ nguyên để sửa.

### Dựng video *(lựa chọn của mình)*

Có ba job nền: sinh lời thoại và slide chữ, vẽ hình cho các cảnh minh họa, và dựng (giọng và video).
Job dựng tạo giọng cho từng cảnh (một lời gọi TTS mỗi cảnh, nên một cảnh lỗi chỉ cần gọi lại cảnh đó), ghép slide và giọng bằng ffmpeg thành MP4 H.264 + AAC 720p, rồi lưu vào kho tệp bền.
Hình và giọng của cảnh không đổi được dùng lại khi dựng lại, nên sửa một cảnh không tốn tiền vẽ lại các cảnh khác.
Lời thoại đã sửa được lưu trước khi dựng, nên dựng hỏng không làm mất bản sửa; dựng lại sau khi sửa tạo bản video mới thay bản cũ.
Ảnh và video lưu trong S3 trên AWS và trong thư mục tệp trên compose, phát cho trình duyệt bằng link có hạn như tệp tham chiếu của nghiên cứu (`study_file_store`).

### Flashcard

Một bộ thẻ sinh bằng truy xuất theo trọng tâm, tối đa 15 thẻ (người dùng quyết định).
Mỗi thẻ có mặt trước, mặt sau và trích nguồn; mặt sau chỉ dùng đoạn được truy xuất, như phần nội dung của mô-đun.
Giảng viên sửa, thêm, xóa và đổi thứ tự thẻ.
Flashcard không có hình.

### Giao cho sinh viên *(lựa chọn của mình)*

Mỗi video và mỗi bộ thẻ có trạng thái "đã giao cho sinh viên", mặc định là chưa.
Video chỉ giao được khi đã dựng xong.
Sinh viên chỉ thấy những thứ đã giao, của môn mình là thành viên; RLS chặn phần còn lại.
Sửa lời thoại của một video đã giao thì sinh viên vẫn xem bản đã dựng cho tới khi dựng lại xong.

### Trang của sinh viên

Trang `/learn/{course}` thêm danh sách video và bộ thẻ đã giao của môn học, theo tài liệu, rồi tới trang xem video và trang lật thẻ.
Chat môn học vẫn ở đó.
Trang chạy được trên điện thoại.

### Hạn mức và chi phí *(lựa chọn của mình)*

Một video 5 phút (khoảng 4.500 ký tự, 9 cảnh, tối đa 5 hình) ước khoảng $0,22 với Nano Banana và $0,12 với Qwen, giọng HeyGen; giọng Gemini Flash cộng khoảng $0,05.
Sinh video và sinh bộ thẻ mỗi lần tính một lượt trong hạn mức sinh hiện có.
Vẽ lại hình và dựng lại có giới hạn số lần mỗi ngày cho mỗi người, vì mỗi lần đều tốn tiền.
Model thật sự phục vụ mỗi ảnh và mỗi đoạn giọng, cùng chi phí OpenRouter trả về, được ghi lại như `generation_models` của mô-đun.

## Để người làm quyết

- Lược đồ bảng và tên cột, miễn RLS đạt yêu cầu trên.
- Đi giọng và ảnh qua LiteLLM hay gọi thẳng OpenRouter, miễn vẫn một khóa, một nhà cung cấp mỗi alias và `data_collection: deny`.
- Prompt, schema đầu ra có cấu trúc, và cách kiểm trích nguồn.
- Mẫu trình bày slide và cách dựng nó thành ảnh (ví dụ HTML có KaTeX chụp bằng trình duyệt headless, hoặc vẽ bằng Pillow), tham số ffmpeg.
- Bố cục và chữ trên giao diện, theo hệ thiết kế hiện có.
- Cách kiểm thử, gồm một lần chạy thật: sinh, sửa, vẽ lại một hình, dựng một video ngắn, giao, rồi mở bằng tài khoản sinh viên từ Moodle qua LTI.

## Video dựng thử (2026-11-12)

Code tạm trong thư mục nháp, không đưa vào hệ thống.
Tài liệu "Chapter 5_CPU Scheduling.pdf", trọng tâm FCFS và Round Robin, tiếng Việt: 9 cảnh, 4 hình Nano Banana, giọng HeyGen `nadine`, slide HTML có KaTeX chụp bằng trình duyệt headless, ghép bằng ffmpeg.
Kết quả: khoảng 4 phút, 5,4 MB, khoảng $0,17 (thêm khoảng $0,04 cho một hình vẽ lại); người dùng xem và duyệt.

