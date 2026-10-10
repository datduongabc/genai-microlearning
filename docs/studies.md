# R18: thiết lập nghiên cứu

Phần đầu của R18 có ba bảng `studies`, `study_items`, `ratings` và một router
`/studies`. Mọi endpoint hiện tại chỉ dành cho admin đang hoạt động; người chưa
đăng nhập nhận 401, giảng viên nhận 404. RLS cũng chặn truy cập ba bảng từ tài
khoản không phải admin. UI chấm mù, ghi điểm và trang kết quả thuộc các phần sau.

Chạy migration `alembic upgrade head` để thêm revision `0018_study_model`.

| API | Nội dung |
| --- | --- |
| `POST /studies` | JSON `{"name":"Nghiên cứu 1","rubric_version":"v1"}`; trả 201, nghiên cứu ở trạng thái `draft` |
| `GET /studies` | Danh sách nghiên cứu |
| `GET /studies/{id}` | Nghiên cứu cùng toàn bộ đơn vị, gồm nhóm dành cho admin |
| `POST /studies/{id}/items` | JSON có `module_ids` và/hoặc `human_packets`; trả 201 với toàn bộ đơn vị sau khi gán lại mã |

`module_ids` là danh sách UUID của module RAG đã có do admin hiện tại sở hữu.
Cả module RAG và bản đối chứng phải thuộc môn học của admin đó; nguồn thuộc
người khác nhận cùng lỗi 404 như module không tồn tại. Mỗi UUID nạp một cặp:
`rag_llm` trỏ tới module thật và `llm_no_rag` chỉ giữ gói JSON tự chứa. Bản đối
chứng phải đã sinh xong bằng nhánh `use_retrieval=False`, với `paired_module_id`
và các điều kiện sinh khớp module RAG (alias, self-check, ngôn ngữ, chỉ dẫn).
Nếu chưa có bản đối chứng phù hợp, API trả 409; hãy sinh bản đối chứng trước
bằng API sinh module hoặc `scripts/export_study_pair.py`. Nạp vào nghiên cứu
không gọi bước sinh LLM.

Gói được dựng bằng `module_packet` và `reference_chunks` hiện có trong
`app/generation/study_packets.py`; hai nhóm dùng chung bộ tài liệu đối chiếu.
Phạm vi lớn có thể cần lượt nhúng/truy hồi để chọn đoạn đối chiếu, như script
xuất cặp hiện tại. Quyền đọc module, tài liệu, đoạn nguồn và job giữ nguyên
RLS theo chủ môn học; admin không được đọc nội dung của giảng viên khác.

`human_packets` là danh sách JSON đúng schema `ModuleContentInput` trong
`app/generation/evaluation.py`, gồm `anon_id`, `topic`, `bloom_level`,
`learning_objective`, `sub_objectives`, `content_markdown`, `slides`, `examples`,
`quiz`, `practice`, `source_chunks`. Ít nhất một câu quiz là bắt buộc. Admin
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
nạp thêm nếu nghiên cứu đã rời `draft` hoặc đã có bất kỳ lượt chấm nào.

Bảng `ratings` chưa có API ghi trong phần này. Nó khớp rubric đang dùng trong
`ModuleEvaluation`: tám trường điểm 1–5 kèm lý giải (`accuracy`, `completeness`,
`consistency`, `hallucination_score`, `slide_quality`, `quiz_quality`,
`bloom_fidelity`, `query_alignment`), trong đó điểm hallucination kèm danh sách
`hallucination_findings`, cộng ghi chú tổng thể `overall_notes` thành chín tiêu
chí. Rubric giữ nguyên theo `evaluation.py` và `docs/rubric.md`; không thêm
cột điểm số thứ chín. `rater_kind` là `human` kèm `user_id`, hoặc
`llm_judge` không có `user_id`. Mỗi người và LLM judge có tối đa một lượt chấm
trên một đơn vị. Bảng này độc lập với `evaluations` của pipeline.

Gói là snapshot: sửa module nguồn không sửa đơn vị nghiên cứu. Khóa ngoại
`RESTRICT` bảo toàn module nguồn RAG và tài khoản người chấm đang được tham
chiếu; muốn xóa nguồn phải xử lý nghiên cứu liên quan trước.

Các script R8 `export_study_pair.py`, `run_judge.py`, `analyze_study.py` và định
dạng JSON của chúng giữ nguyên. Nhập trực tiếp cặp JSON xuất sẵn chưa nằm
trong API này.

Generation conditions and control pairing are retained on modules independently of job cleanup; surviving generation jobs backfill legacy modules during migration. Study references block module, document, and course deletion with HTTP 409 before upload removal.
