# R18: thiết lập nghiên cứu và chấm mù

R18 có ba bảng `studies`, `study_items`, `ratings` và một router `/studies`.
Phần thiết lập (tạo nghiên cứu, nạp đơn vị, mời người chấm) chỉ dành cho admin
đang hoạt động; người chưa đăng nhập nhận 401, giảng viên nhận 404. Phần
[chấm mù](#cham-mu) dành cho tài khoản được mời. [LLM judge](#llm-judge)
ghi điểm vào cùng bảng; trang kết quả thuộc phần sau.

Chạy migration `alembic upgrade head` để thêm revision `0018_study_model`,
`0019_blind_ratings`, `0020_study_judge` và `0021_remove_textbook_scope`.

Phạm vi và độ phủ gói đối chiếu theo [rubric R9](rubric.md#7-r9-claim-không-có-căn-cứ-và-độ-chính-xác-trích-dẫn). Migration `0021` giữ module,
job, thành phần, câu hỏi, liên kết cặp và đơn vị nghiên cứu, nhưng bỏ dữ liệu
chương/mục. Job theo mục đang queued/running được đánh dấu failed để không
tự chuyển thành lượt sinh toàn tài liệu; cần sinh lại cho cả tài liệu. Module cũ sinh theo mục không còn tái lập được phạm vi đối chiếu
cũ; các hàng hiện có chỉ là dữ liệu thử, chưa dùng cho đánh giá chính thức.
Downgrade dựng lại cấu trúc mục rỗng, không khôi phục dữ liệu mục đã bỏ.

Phạm vi môn đánh giá được ghi tại [README](../README.md). Script
`benchmark_rerank.py` chỉ nhận các môn trong phạm vi này và bỏ dữ liệu môn khác khỏi kết quả gộp mới; các tệp đo
lịch sử được giữ làm bằng chứng của những lần đo trước.

| API | Nội dung |
| --- | --- |
| `POST /studies` | JSON `{"name":"Nghiên cứu 1","rubric_version":"v1"}`; trả 201, nghiên cứu ở trạng thái `draft` |
| `GET /studies` | Danh sách nghiên cứu |
| `GET /studies/{id}` | Nghiên cứu cùng toàn bộ đơn vị, gồm nhóm dành cho admin |
| `POST /studies/{id}/items` | JSON có `module_ids` và/hoặc `human_packets`; trả 201 với toàn bộ đơn vị sau khi gán lại mã |
| `GET /studies/{id}/raters` | Danh sách email người chấm được mời: `{"emails": [...]}` |
| `POST /studies/{id}/judge` | Không có body; trả 202 với `job_id` của job chấm nền |
| `GET /studies/{id}/judge/{job_id}` | Tiến độ và kết quả từng đơn vị của job chấm |
| `PUT /studies/{id}/raters` | JSON `{"emails": [...]}` thay toàn bộ danh sách; danh sách rỗng thu hồi mọi lời mời |

`module_ids` là danh sách UUID của module RAG đã có do admin hiện tại sở hữu.
Cả module RAG và bản đối chứng phải thuộc môn học của admin đó; nguồn thuộc
người khác nhận cùng lỗi 404 như module không tồn tại. Mỗi UUID nạp một cặp:
`rag_llm` trỏ tới module thật và `llm_no_rag` chỉ giữ gói JSON tự chứa. Bản đối
chứng phải đã sinh xong bằng nhánh `use_retrieval=False`, với `paired_module_id`
và các điều kiện sinh khớp module RAG (alias, self-check, ngôn ngữ, chỉ dẫn).
Nếu chưa có bản đối chứng phù hợp, API trả 409; hãy sinh bản đối chứng trước
bằng API sinh module hoặc `scripts/export_study_pair.py`. Nạp vào nghiên cứu
không gọi bước sinh LLM.

Điều kiện sinh và liên kết cặp được lưu trên module, độc lập với việc dọn job.
Migration điền dữ liệu cho module cũ từ các job sinh còn tồn tại; module chưa
được điền vẫn dùng job làm nguồn dự phòng. Các trường điều kiện bị thiếu dùng
mặc định trong `normalize_generation_payload` của
`app/generation/module_generation_pipeline.py`; alias được chọn tường minh
phải khớp, còn alias bỏ trống vẫn biểu thị dùng cấu hình mặc định.

Gói được dựng bằng `module_packet` và `reference_chunks` hiện có trong
`app/generation/study_packets.py`; hai nhóm dùng chung bộ tài liệu đối chiếu.
Phạm vi lớn có thể cần lượt nhúng/truy hồi để chọn đoạn đối chiếu, như script
xuất cặp hiện tại. Quyền đọc module, tài liệu, đoạn nguồn và job giữ nguyên
RLS theo chủ môn học; admin không được đọc nội dung của giảng viên khác.

`human_packets` là danh sách JSON đúng schema `ModuleContentInput` trong
`app/generation/evaluation.py`, gồm `anon_id`, `topic`, `bloom_level`,
`learning_objective`, `sub_objectives`, `content_markdown`, `slides`, `examples`,
`quiz`, `practice`, `source_chunks`. Ít nhất một câu quiz là bắt buộc. Mỗi câu
quiz và practice phải có danh sách `choices` không rỗng và đáp án thỏa
`0 <= correct_index < len(choices)`; packet vi phạm bị từ chối với 422. Admin
chịu trách nhiệm soạn nội dung không tiết lộ nhóm, tên model hoặc tên nguồn.
`anon_id` nhập vào luôn bị thay bằng mã do nghiên cứu gán.

Ví dụ nạp một cặp RAG/đối chứng và một gói người soạn từ tệp JSON:

```sh
jq --arg mid "$module_id" '{module_ids: [$mid], human_packets: [.]}' human-packet.json > items.json
curl -H "Authorization: Bearer $access_token" -H 'Content-Type: application/json' \
  --data-binary @items.json "http://localhost:8000/studies/$study_id/items"
```

Mỗi lần nạp xáo lại mã `M01`, `M02`, ... của **toàn bộ** nghiên cứu draft, để
nhóm nạp sau không luôn nhận dãy mã lớn hơn. Mã trong `packet.anon_id` và
`study_items.anon_code` luôn khớp nhau. Việc nạp là một transaction và khóa
hàng nghiên cứu để các lần nạp đồng thời không trùng mã. Module RAG trùng,
packet không hợp lệ hoặc nguồn thiếu khiến cả lượt nạp bị từ chối. Không
nạp thêm nếu nghiên cứu đã rời `draft`, đã có lượt chấm của người hoặc đang
chạy judge. Lượt chấm judge không khóa việc nạp sau khi job đã xong; chạy lại
judge để chấm cả các đơn vị mới.

Các trường chấm của bảng `ratings` khớp `ModuleEvaluation` và
[rubric dùng chung](rubric.md), không thêm cột điểm số thứ chín. `rater_kind`
là `human` kèm `user_id`, hoặc `llm_judge` không có `user_id`. Mỗi người và
LLM judge có tối đa một lượt chấm trên một đơn vị; `judge_alias` và `judge_model`
lưu alias cùng model thực sự đã phục vụ. Bảng này độc lập với
`evaluations` của pipeline.

<a id="cham-mu"></a>

## Chấm mù

Admin mời người chấm theo email, trên tab **Nghiên cứu** của trang quản trị
hoặc bằng `PUT /studies/{id}/raters`. Mỗi email phải thuộc một tài khoản đang
hoạt động (giảng viên khác, chuyên gia), nếu không cả yêu cầu bị từ chối với
422. Email được chuẩn hóa về chữ thường, bỏ trùng và lưu ở cột
`studies.rater_emails`, không thêm bảng thứ tư. Tối đa 100 người chấm mỗi
nghiên cứu.

| API | Nội dung |
| --- | --- |
| `GET /studies/mine` | Các nghiên cứu mà tài khoản hiện tại được mời, kèm tiến độ riêng `rated`/`total` |
| `GET /studies/mine/{id}/items` | Nghiên cứu, các đơn vị theo thứ tự đã xáo cho người chấm này, và lượt chấm của chính họ trên từng đơn vị |
| `PUT /studies/mine/{id}/items/{item_id}/rating` | Tạo hoặc sửa lượt chấm của mình; thân là `ModuleEvaluation` |

Người chấm chỉ nhận `id`, `anon_code`, `packet` và lượt chấm của mình trên mỗi
đơn vị; thông tin nghiên cứu chỉ gồm tên, phiên bản rubric, trạng thái và ngày
tạo. Nhóm, `module_id`, danh sách người chấm và điểm của người khác không bao
giờ có trong các phản hồi này. Tài khoản không được mời, đã bị thu hồi lời mời
hoặc hỏi một nghiên cứu không tồn tại đều nhận cùng lỗi 404; tài khoản bị vô
hiệu hóa nhận 401.

Thứ tự đơn vị được xáo bằng hạt giống `study_id:user_id`, nên mỗi người chấm
có một thứ tự riêng và tải lại trang giữ nguyên thứ tự đó. Mã `M01`, `M02`, ...
không đổi theo người chấm.

Thân `PUT .../rating` được kiểm bằng `ModuleEvaluation` của
`app/generation/evaluation.py`: tám tiêu chí, mỗi tiêu chí có `score` nguyên từ
1 đến 5 và `justification`, cùng `hallucination_findings` (mỗi mục có `claim`,
`field`, `reason`) và `overall_notes`. `anon_id` phải bằng mã ẩn danh của đơn
vị, nếu không nhận 422. Gọi lại cùng đường dẫn sẽ sửa lượt chấm đã lưu. Lưu
lượt chấm không đổi trạng thái nghiên cứu; khi đã có lượt chấm của người thì không nạp
thêm đơn vị được nữa (409). Nghiên cứu `closed` từ chối lưu và sửa lượt chấm
với 409.

Trang **Chấm nghiên cứu** (`/studies`) liệt kê các nghiên cứu được mời cùng
tiến độ "Đã chấm n/tổng". Trang chấm của một đơn vị hiện mã ẩn danh và chủ đề
ở đầu trang; gói chấm chia thành các tab, mở sẵn tab đoạn nguồn: đoạn nguồn,
mục tiêu học tập, nội dung, slide, ví dụ, bài luyện tập, quiz và giải thích
đáp án, mỗi lần chỉ hiện một tab. Bên cạnh là biểu mẫu tám tiêu chí chấm điểm của rubric cùng danh sách khẳng định không có căn cứ;
mức Bloom khai báo chỉ hiện cạnh tiêu chí `bloom_fidelity`, không hiện trong gói. Nội dung đang
nhập không được lưu nháp: khi biểu mẫu có thay đổi chưa lưu, chuyển sang gói
khác, bấm một liên kết, đăng xuất, tải lại hoặc đóng trang đều hỏi xác nhận
trước. Nút Back/Forward của trình duyệt không hỏi xác nhận.

### Phân quyền ở database

RLS trên `studies` và `study_items` vẫn chỉ cho admin: RLS lọc theo hàng nên
không ẩn riêng được cột nhóm và `module_id`. Người chấm đọc qua hai view
`blind_studies` và `blind_study_items`, chỉ gồm các cột được phép và chỉ trả
hàng của nghiên cứu mà tài khoản đang hoạt động trong `app.current_user_id`
được mời. Trên `ratings`, người chấm chỉ đọc, thêm và sửa được hàng `human`
mang `user_id` của chính mình, trên đơn vị thuộc nghiên cứu họ được mời; không
có quyền xóa. Thu hồi lời mời giữ nguyên các lượt chấm đã lưu cho admin, nhưng
người bị thu hồi không còn đọc hay sửa được chúng.

Trigger trên `ratings` khóa hàng nghiên cứu (cùng khóa với việc nạp đơn vị),
từ chối ghi vào nghiên cứu `closed` và không cho đổi đơn vị hay người chấm của
một lượt chấm đã có.

<a id="llm-judge"></a>

## LLM judge

`POST /studies/{id}/judge` xếp một job `study_judge` vào hàng đợi nền hiện có
và trả về ngay; request không chờ LLM. Job chấm lần lượt mọi đơn vị của nghiên
cứu bằng `judge_module` trong `app/generation/evaluation.py`, trên chính gói
tự chứa mà người chấm nhìn thấy, rồi ghi hai hàng `llm_judge` thuộc hai hãng vào `ratings` cho
mỗi đơn vị: tám điểm kèm lý do, `hallucination_findings` và `overall_notes`.
Rubric và prompt không đổi.

- **Model chấm.** Pool, thứ tự ưu tiên và hãng ở [rubric mục 1](rubric.md#1-thiết-kế-nghiên-cứu). Mỗi đơn vị chọn hai hãng khác nhau và khác tất cả model thực sự sinh gói; dùng guard chung với CLI. Provenance lấy từ `generation_models`, rồi `generator_model`, rồi alias mặc định cho dữ liệu cũ. Gói người soạn không có hãng sinh. Gateway không trả lời nhận 503; thiếu hai hãng hợp lệ nhận 422 trước khi xếp job.
- **Ghim model.** Chạy thí nghiệm (`disable_fallbacks`); lưu `judge_alias`, `judge_model` và `judge_family`. Sai model được ghim thì không lưu điểm.
- **Chạy lại.** Migration 0022 đổi unique index thành `(study_item_id, judge_family)`. Giữ điểm đã hoàn tất, chỉ chấm phần thiếu; tối đa hai hãng hợp lệ trên mỗi đơn vị. Job đang chạy ngăn xếp trùng và nạp thêm đơn vị.
- **Hết quota.** 429 thử ứng viên hợp lệ kế tiếp. Nếu không đủ hai lượt, job `failed` giữ cursor tại đơn vị thiếu, `last_error` ghi mã và số lượt thiếu. `POST /studies/{id}/judge` tạo job mới tiếp tục từ điểm đã lưu. Các lỗi khác cũng giữ cursor khi hết retry; không ghi `done` cho một lượt thiếu.
- **Ngân sách.** Đếm toàn prompt theo `max_input_tokens` của alias; không chia hoặc cắt gói. Không có cam kết miễn phí cho toàn pool: không gọi API trả phí trong kiểm thử, mọi provider được mock.

Migration 0020 giữ trigger khóa nghiên cứu, cấm sửa danh tính lượt chấm và
cấm ghi khi `closed`. Chỉ vai trò `app_worker` được bỏ qua kiểm lời mời khi
đang ghi hàng `llm_judge`; ghi hàng `human` vẫn phải qua kiểm lời mời/admin.

`GET /studies/{id}/judge/{job_id}` trả `status`, `required_ratings_per_item`, `pending_item_ids`,
`total`, `completed`, `results` (mỗi phần tử có `item_id`, `status`, và
`judge_models` hoặc `error`) và `last_error` của lượt thử gần nhất. Job đã xong
bị dọn theo lịch dọn job chung; điểm vẫn nằm trong `ratings`.

```sh
curl -X POST -H "Authorization: Bearer $access_token" \
  "http://localhost:8000/studies/$study_id/judge"
curl -H "Authorization: Bearer $access_token" \
  "http://localhost:8000/studies/$study_id/judge/$job_id"
```

<a id="bao-toan-nguon"></a>

Gói là snapshot: sửa module nguồn không sửa đơn vị nghiên cứu. Khóa ngoại
`RESTRICT` bảo toàn module nguồn RAG và tài khoản người chấm đang được tham
chiếu; muốn xóa nguồn phải xử lý nghiên cứu liên quan trước. Xóa module,
tài liệu hoặc môn học chứa module đang được tham chiếu trả HTTP 409 trước
khi xóa tệp tải lên; xung đột khóa ngoại lúc commit cũng rollback và trả 409.

Các script R8 `export_study_pair.py`, `run_judge.py`, `analyze_study.py` và định
dạng JSON của chúng giữ nguyên. Nhập trực tiếp cặp JSON xuất sẵn chưa nằm
trong API này.

Khi nhập cặp AI vào nghiên cứu hoặc xuất bằng CLI, mỗi component đã sửa dùng
`ai_original` đã lưu, gồm cả các hàng câu hỏi và lời giải gốc. Component chưa có
snapshot dùng nội dung hiện tại. Các lần sửa trước khi có cơ chế giữ bản gốc
không thể khôi phục nếu chưa lưu snapshot.
