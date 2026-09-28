# R9 live smoke trên Docker, 2026-09-28

Chạy mã của nhánh R9 trong `/tmp/r9-live` của container `do-an-backend`, kết nối stack Docker đang chạy. Không thay image backend hoặc volume. Module RAG đã có: `811cb907-ab7f-471d-986c-1c8fcb095f5a` (HEURISTIC FUNCTION). Job đối chứng không RAG `77f0e322-ea8e-4459-a449-ece3d9149110` sinh bằng `gemini-fast` qua hàng đợi chuẩn, tạo module `fccde642-8c88-45a4-831d-accc7c3d3889`. Probe một token của `gemini-fast` và `groq-fast` đều thành công. Hai gói xuất ra cùng bộ đối chiếu `complete` gồm 13/13 chunk của toàn tài liệu, vì module không có `section_id` và tài liệu nằm trong giới hạn 64 chunk, 120.000 ký tự.

Lệnh chính trong container (với `DATABASE_URL` nhận từ `APP_WORKER_DATABASE_URL` chỉ cho bước xuất):

```sh
PYTHONPATH=/tmp/r9-live /app/.venv/bin/python /tmp/r9-live/scripts/export_study_pair.py 811cb907-ab7f-471d-986c-1c8fcb095f5a --out /tmp/r9-live/packets
PYTHONPATH=/tmp/r9-live /app/.venv/bin/python /tmp/r9-live/scripts/run_claim_judge.py /tmp/r9-live/packets --judge groq-fast --out /tmp/r9-live/claims-groq-fast-v2.jsonl
```

Phân tích `scripts.analyze_study.analyze_claims(["/tmp/r9-live/claims-groq-fast-v2.jsonl"])`:

| Nhóm | Claim | Không được bộ đối chiếu hỗ trợ | Tỉ lệ | Trích dẫn đúng / đã lưu | Độ chính xác trích dẫn |
| --- | ---: | ---: | ---: | ---: | ---: |
| RAG+LLM | 10 | 0 | 0,0000 | 2 / 7 | 0,2857 |
| LLM không RAG | 14 | 2 | 0,1429 | không áp dụng | không áp dụng |

Model chấm thực: `groq/openai/gpt-oss-20b` qua alias `groq-fast`, khác hãng với model sinh `gemini-fast`. Đây là **một cặp smoke test**, không phải ước lượng quần thể hay bằng chứng rằng hai claim sai ngoài đời; “không được hỗ trợ” chỉ áp dụng cho bộ đối chiếu này. Trích dẫn được kiểm theo đúng chunk đã lưu, không lấy chunk khác thay thế.

## Chẩn đoán Groq

Trước sửa, cả `groq-fast` (`groq/openai/gpt-oss-20b`) và `groq-pro` (`groq/openai/gpt-oss-120b`) trả `json_validate_failed` ở lệnh `complete_structured` đầu tiên để **tách claim**, trước mọi lệnh kiểm hỗ trợ. Pydantic schema của `ClaimExtraction` dài 499 byte. Backend gửi `response_format.type=json_schema` và `strict=true` tới gateway; thử trực tiếp `json_object` với toàn Content và một schema phẳng strict cũng lỗi tương tự, trong khi JSON rất ngắn thành công. Một lệnh không ép JSON với toàn Content trên `groq-pro` dùng hết 3.072 output token cho reasoning, `finish_reason=length`, nội dung trả về rỗng. Cho R9 truyền `reasoning_effort=low` tới alias Groq giải quyết trường hợp này và cho phép chạy xong bằng `groq-fast`.

`groq-pro` vẫn không đáng tin cho gói này: trong một lượt đã trả 0 claim dù Content RAG dài 1.535 ký tự và chứa các khẳng định; lượt khác trả lỗi tạo JSON. Không dùng kết quả 0 claim đó làm tỉ lệ RAG. Cần kiểm lại model chấm 120B trước đánh giá chính thức. Các alias OpenAI không dùng được trong lượt này vì quota của khóa thử nghiệm; không gọi API trả phí.
