# R18: thiết lập nghiên cứu

Phần đầu của R18 có ba bảng `studies`, `study_items`, `ratings` và một router
`/studies`. Mọi endpoint hiện tại chỉ dành cho admin đang hoạt động; người chưa
đăng nhập nhận 401, giảng viên nhận 404. RLS cũng chặn truy cập ba bảng từ tài
khoản không phải admin. UI chấm mù, ghi điểm và trang kết quả thuộc các phần sau.

Chạy migration `alembic upgrade head` để thêm revision `0018_study_model` và
`0019_study_judge` (LLM judge, xem [bên dưới](#llm-judge)).

| API | Nội dung |
| --- | --- |
| `POST /studies` | JSON `{"name":"Nghiên cứu 1","rubric_version":"v1"}`; trả 201, nghiên cứu ở trạng thái `draft` |
| `GET /studies` | Danh sách nghiên cứu |
| `GET /studies/{id}` | Nghiên cứu cùng toàn bộ đơn vị, gồm nhóm dành cho admin |
| `POST /studies/{id}/items` | JSON có `module_ids` và/hoặc `human_packets`; trả 201 với toàn bộ đơn vị sau khi gán lại mã |
| `POST /studies/{id}/judge` | Không có body; trả 202 với `job_id` của job chấm nền |
| `GET /studies/{id}/judge/{job_id}` | Tiến độ và kết quả từng đơn vị của job chấm |

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
nạp thêm nếu nghiên cứu đã rời `draft` hoặc đã có lượt chấm của người. Lượt
chấm của LLM judge không khóa việc nạp: có thể nạp gói người soạn sau khi đã
chạy judge, rồi chạy lại judge để chấm cả đơn vị mới.

Các trường chấm của bảng `ratings` khớp `ModuleEvaluation` và
[rubric dùng chung](rubric.md), không thêm cột điểm số thứ chín. `rater_kind`
là `human` kèm `user_id`, hoặc `llm_judge` không có `user_id` nhưng có
`judge_alias` và `judge_model` ghi lại model đã chấm. Mỗi người và LLM judge có
tối đa một lượt chấm trên một đơn vị. Bảng này độc lập với `evaluations` của
pipeline.

<a id="llm-judge"></a>

## LLM judge

`POST /studies/{id}/judge` xếp một job `study_judge` vào hàng đợi nền hiện có
và trả về ngay; request không chờ LLM. Job chấm lần lượt mọi đơn vị của nghiên
cứu bằng `judge_module` trong `app/generation/evaluation.py`, trên chính gói
tự chứa mà người chấm nhìn thấy, rồi ghi một hàng `llm_judge` vào `ratings` cho
mỗi đơn vị: tám điểm kèm lý do, `hallucination_findings` và `overall_notes`.
Rubric và prompt không đổi.

- **Model chấm.** Cố định là `groq-pro` (gpt-oss-120b trên Groq); request
  không chọn được model khác. Nếu gateway không có alias này, hoặc nó cùng
  hãng với model đã sinh bất kỳ đơn vị nào, request bị từ chối 422, dùng chung
  phép kiểm hãng với `scripts/run_judge.py` (`app/llm/judge.py`). Gateway
  không trả lời được thì nhận 503. Hãng của đơn vị lấy từ
  `study_items.generation_models` (model thực sự phục vụ), nếu thiếu thì từ
  alias `generator_model`, nếu cũng thiếu (đơn vị nạp trước revision 0019) thì
  coi như alias mặc định của cấu hình; hai cột này nằm ngoài gói nên không lộ
  cho người chấm. Gói người soạn không có model sinh nên không bị kiểm.
- **Ghim model.** Lượt chấm chạy ở chế độ thí nghiệm (`disable_fallbacks`).
  `ratings.judge_model` là model gateway báo đã phục vụ; nếu nó khác model
  của alias lúc xếp job thì đơn vị bị ghi lỗi và không lưu điểm.
- **Chạy lại.** Mỗi lần gọi là một job mới chấm mọi đơn vị hiện có. Chấm lại
  thay hàng `llm_judge` cũ của đơn vị chứ không thêm hàng. Khi một job chấm
  còn `queued`/`running`, lần gọi thứ hai và việc nạp thêm đơn vị nhận 409.
- **Lỗi từng đơn vị.** Lỗi tạm thời (hết quota, mạng) được thử lại có back-off
  trên đúng đơn vị đó; hết lượt thử hoặc lỗi không thử lại được (JSON sai
  rubric, gói quá lớn) được ghi vào `results` với `status: failed` và job chấm
  tiếp đơn vị sau. Job kết thúc `done` kể cả khi có đơn vị lỗi; đọc `results`.
- **Giới hạn Groq.** Bậc miễn phí cho 8.000 token/phút, nên gateway khai
  `max_input_tokens` 3.952 cho `groq-pro` và dành 2.048 token đầu ra. Job đọc
  đúng giới hạn đó từ gateway lúc xếp job và đếm token của prompt như
  gateway đếm; vượt thì đơn vị nhận lỗi `Packet too large ...` ngay, không
  gọi Groq, không thử lại, và gói không bao giờ bị cắt bớt. Nếu gateway vẫn
  từ chối vì vượt cửa sổ ngữ cảnh thì đơn vị cũng bị ghi lỗi mà không thử lại.
  Mọi job chấm dùng chung một lượt gọi Groq mỗi 61 giây, nên một nghiên cứu
  N đơn vị mất khoảng N phút.

`GET /studies/{id}/judge/{job_id}` trả `status`, `judge_alias`, `judge_model`,
`total`, `completed`, `results` (mỗi phần tử có `item_id`, `status`, và
`judge_model` hoặc `error`) và `last_error` của lượt thử gần nhất. Job đã xong
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
dạng JSON của chúng giữ nguyên; `run_judge.py` vẫn ghi JSONL và không ghi vào
`ratings`, vì tệp cặp của nó không gắn với đơn vị nghiên cứu nào. Nhập trực
tiếp cặp JSON xuất sẵn chưa nằm trong API này.
