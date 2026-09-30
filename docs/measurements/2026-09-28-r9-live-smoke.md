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

## Bổ sung 2026-09-30: mục nhỏ có `section_id`, chunk đầy đủ và trích dẫn đã lưu

Lượt trên chỉ chạy module không có mục. Lượt này kiểm đường còn lại: module thuộc một mục nhỏ (`section_id` khác rỗng) phải xuất đúng toàn bộ chunk của mục, không lấy chunk của mục khác, và các trích dẫn đã lưu phải khớp chunk đó. Tài nguyên tách riêng, không đụng dữ liệu cũ: một người dùng, môn học và tài liệu mới `data_structures.pdf` (PDF sách giáo khoa 4 trang, 2 bookmark, 2 chunk sau khi nhúng), tải lên qua API `POST /documents?kind=textbook`; ingest tạo 2 mục `Chapter 1: Hash Tables` (chunk 0) và `Chapter 2: Binary Search Trees` (chunk 1).

Module RAG `15553625-6f99-4643-8c1d-d65c727d825f` sinh cho mục Chapter 1 bằng `gemini-fast` qua `POST /modules/generate` (job `b7010f7c-6976-40cf-87cd-81ea4816618c`); bản đối chứng không RAG `5c9bf631-c388-463d-9fa9-e5947f398c42` qua `use_retrieval=false` + `paired_module_id` (job `f854b1cc-1c8d-4ee1-a46c-c7fc557a686d`). Xuất và chấm chạy mã nhánh R9 sao chép vào `/tmp/r9-live-sec` trong container `do-an-backend`, cùng lệnh như trên với `--judge groq-fast`.

Kết quả xuất: cả hai gói có `reference.coverage = complete`, `total_chunks = 1`, `chunk_ids` là đúng chunk 0 của mục (chunk 1 của Chapter 2 không vào bộ đối chiếu, dù cùng tài liệu). Gói RAG có 12 trích dẫn đã lưu, cả 12 `resolution = resolved` và cùng `chunk_id` với chunk của mục.

| Nhóm | Claim | Không được bộ đối chiếu hỗ trợ | Tỉ lệ | Trích dẫn đúng / đã lưu | Độ chính xác trích dẫn |
| --- | ---: | ---: | ---: | ---: | ---: |
| RAG+LLM | 14 | 0 | 0,0000 | 12 / 12 | 1,0000 |
| LLM không RAG | 13 | 5 | 0,3846 | không áp dụng | không áp dụng |

Model chấm: `groq/openai/gpt-oss-20b` (alias `groq-fast`), khác hãng với model sinh `gemini-fast`. Đây là **một cặp trên một mục một chunk và một tài liệu tự dựng**, không phải ước lượng quần thể. Nội dung không RAG dùng thêm kiến thức ngoài tài liệu nên bị đánh "không được hỗ trợ" là điều dự kiến, chứ không chứng minh claim sai; vài lý do của model chấm 20B còn đáng ngờ (ví dụ một claim về rehashing bị đánh không hỗ trợ dù chunk có nói), nên cần người kiểm mẫu trước khi dùng số này. Vì mục chỉ có một chunk, cả 12 trích dẫn cùng trỏ về chunk đó nên lượt này không thể lộ trích dẫn nhầm chunk; phần đó vẫn dựa vào kiểm thử xác định trong `tests/test_claim_evaluation.py`.

## Đối chiếu bổ sung 2026-09-30: ba trường hợp còn thiếu bằng chứng live

Kiểm lại trên mã worktree của bước Test, không sửa mã sản phẩm hay dữ liệu cũ. Kết nối PostgreSQL từ host không tới được mạng Docker; thay vào đó chạy exporter hiện tại trong tiến trình Python của container `do-an-backend`, nạp mã worktree qua stdin vào bộ nhớ. Đặt `DATABASE_URL` từ `APP_WORKER_DATABASE_URL` sẵn có và `PGOPTIONS=-c default_transaction_read_only=on`; xác nhận `SHOW transaction_read_only` là `on`. Chặn mọi lệnh completion trong bước xuất, nên không thể sinh bản đối chứng mới. Hai tệp JSON của exporter được thu trong bộ nhớ rồi ghi vào thư mục evidence trên host; không sao chép mã hoặc ghi tệp vào container.

Xuất lại module `15553625-6f99-4643-8c1d-d65c727d825f` thành công, dùng lại bản đối chứng đã lưu. Truy vấn độc lập xác nhận tài liệu có hai chunk, mục được chọn chỉ có một chunk, và cả hai gói chứa đúng nội dung cùng ID `12c989dd-5e77-4e70-815a-f1b7548f59c3`, `coverage=complete`, `total_chunks=1`. Cả 12 claim/locator xuất ra khớp `claim_citations` đã lưu; mỗi locator khớp duy nhất một chunk trong tài liệu, ID và nội dung trích dẫn khớp chunk của mục. Chunk mục khác không lọt vào bộ đối chiếu.

Sau khi xác nhận gateway vẫn ánh xạ `groq-fast` sang `groq/openai/gpt-oss-20b`, probe một output token trả HTTP 200. Dùng đúng route Groq miễn phí đã được chủ tài khoản cho phép, không gọi alias trả phí hoặc thay đổi billing. Hai gói kiểm soát dùng metadata/gói xuất làm mẫu, thay Content và bộ đối chiếu bằng dữ liệu giả nhỏ, rồi chạy CLI `scripts/run_claim_judge.py` thật với `--judge groq-fast`. Gói vẫn ghi `generator_model=gemini-fast` để giữ kiểm tra khác hãng; Content kiểm soát do người kiểm thử tạo, không phải Gemini sinh. Không giả lập extraction hoặc support verdict.

| Gói kiểm soát | Kết quả live |
| --- | --- |
| Mười câu đơn về kho lưu trữ Zora, tám câu có trong bộ đối chiếu; hai câu thêm nói có đài quan sát trên mái và sở hữu tàu ngầm | Tách đúng 10 claim; hai claim thêm không được hỗ trợ; `unsupported_rate=0.2` |
| Claim kho Zora mở năm 2001 có trong chunk thứ nhất, nhưng trích dẫn trỏ chunk thứ hai chỉ nói bảo tàng Tavi trưng bày đồ gốm | Claim được bộ đối chiếu hỗ trợ (`unsupported_rate=0.0`); trích dẫn sai (`correct_citations=0`, `citation_count=1`, `citation_accuracy=0.0`) |

Hai gói hoàn tất trong một lượt, 14 completion qua đúng `groq-fast`, ngoài probe một token. Không thử lại để ép số 0,2, không ghi đè verdict. Đây là smoke test dữ liệu giả bằng một model; kết quả stochastic không bảo đảm mọi lần chạy hay chất lượng chấm nội dung giáo trình. Hai kiểm thử xác định `test_two_invented_claims_in_ten_give_point_two` và `test_wrong_chunk_is_incorrect_despite_support_elsewhere` vẫn giữ nguyên và đều pass; không chạy toàn bộ suite, linter hoặc formatter.

Bằng chứng tái lập của lượt gate này nằm tại `/home/asus/.no-mistakes/evidence/01M3S6YGMSE8CC3F14EZGJH7JE/r9-reconcile/`: `verify.py` (driver, truyền credential trực tiếp không in ra), `remote_export.py` (export read-only trong bộ nhớ), `export/*.json`, `controlled/*.json`, `live-claims.jsonl` (claim/span/verdict đầy đủ), và `verification.json` (kết quả đối chiếu). Chạy driver từ `backend` bằng `PYTHONPATH=. PYTHONDONTWRITEBYTECODE=1 uv run python /home/asus/.no-mistakes/evidence/01M3S6YGMSE8CC3F14EZGJH7JE/r9-reconcile/verify.py`. Chạy lại với output cũ sẽ resume và không chấm lại hai gói; muốn đo một lượt stochastic mới phải dùng đường dẫn evidence/output mới. Evidence là tệp cục bộ của lượt gate, không được commit vào repository.
