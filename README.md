# Hệ thống AI tạo sinh mô-đun Microlearning từ tài liệu môn học

Đề tài HK261-DAGD1-033.
Sinh viên: Dương Nguyễn Tấn Đạt, ngành Khoa học Máy tính.

Hệ thống nhận tài liệu môn học của giảng viên (PDF, PowerPoint, HTML, video), truy xuất bằng RAG, rồi sinh mô-đun Microlearning gồm bảy thành phần: Learning Objective, Content, Slides, Example, Practice, Quiz, Explanation.
Mô-đun sau đó được xuất bản sang Moodle cho học viên học.

Mọi tài liệu được xử lý như bài giảng thông thường: không chọn loại giáo trình, không phát hiện mục lục và không sinh theo chương/mục. API sinh mô-đun trả 422 nếu request còn gửi `section_ids`; hãy bỏ trường này để sinh cho cả tài liệu. Đợt đánh giá chỉ dùng bốn thư mục trong `docs/documents`: `dsa`, `operating-system`, `program-fundamentals`, `web-programming`.

Model trả phí cho đợt đánh giá được chọn tường minh và ghim bằng `LLM_EXPERIMENT_MODE=true`; xem lựa chọn model, embedding và chính sách fallback tại [gateway routing](docs/gateway-routing.md).

Khi bộ đọc nhận diện tài liệu hỏng hoặc sai định dạng, job báo lỗi ngay ở lần xử lý đầu và không tự thử lại; PDF có mật khẩu cần bỏ mật khẩu rồi tải lại. Lỗi tạm thời khi gọi gateway vẫn được tự thử lại với thời gian chờ tăng dần.

Quiz và Practice dùng chung gate chất lượng: kiểm key duy nhất, từng phương án nhiễu, trùng ý, dấu hiệu lộ đáp án, Bloom của sub-objective và bằng chứng nguồn. Chỉ câu trượt được sinh lại một lần trong vòng tự kiểm; nếu vẫn trượt, giữ câu và lý do trong mô-đun để xem xét, đồng thời job sinh báo `failed` thay vì thành công. Tắt `self_check` chỉ bỏ kiểm bằng model; kiểm key, trùng phương án và bẫy độ dài/từ tuyệt đối vẫn chạy. Gate mở rộng dùng lại lượt kiểm hiện có: mô-đun bốn câu vẫn thường cần 15 lượt model (10 khi tắt tự kiểm), tối đa 30 theo các giới hạn sửa hiện có, gồm một lần cân bằng độ dài cả bộ.

Ứng dụng mở cho mọi người dùng; admin quản lý tài khoản và theo dõi lỗi. Quyền làm giáo viên trong Moodle được quản lý theo từng môn học trên Moodle.

Trong trang xem trước, giảng viên có thể yêu cầu AI sửa một thành phần bằng ngôn ngữ tự nhiên, ví dụ “Rút gọn slide 3” hoặc “Làm câu quiz 2 khó hơn”. AI bám các đoạn nguồn của mô-đun và ngôn ngữ đầu ra đã chọn; yêu cầu chưa rõ sẽ được hỏi lại. Bản gốc AI được giữ trong `ai_original`, và số lượt chỉnh sửa đã áp dụng được hiển thị trên trang. Mô-đun đối chứng (không RAG) không hỗ trợ chỉnh sửa bằng chat.
API: `POST /modules/{module_id}/chat-edit` nhận `{message, session_id?}`, trả `{session_id, reply, applied, edit_count}`. Lịch sử lưu trong bảng chat hiện có, tách khỏi hỏi-đáp môn học bằng `chat_sessions.module_id`; `chat_messages.applied_edit` đánh dấu lượt đã áp dụng. Chạy `alembic upgrade head` trước khi dùng.

Sinh lại cho cùng một tài liệu luôn tạo mô-đun mới, không ghi đè mô-đun đã có; chỉ một lượt đang chạy mới chặn lượt sau. Giảng viên xóa một mô-đun đã sinh bằng `DELETE /modules/{module_id}` (nút “Xóa mô-đun” trên danh sách, có hộp xác nhận): khi được phép, endpoint xóa luôn bản đối chứng không RAG đi cặp và mọi dòng phụ thuộc (câu hỏi, đánh giá, lịch sử chat-edit, job), trả 409 khi bản đối chứng của nó đang được sinh, và 404 với mô-đun của người khác. Nguồn đang được nghiên cứu tham chiếu có [ràng buộc xóa riêng](docs/studies.md#bao-toan-nguon). Ứng dụng không lưu mô-đun nào đã đẩy lên Moodle, nên Book/Quiz đã tạo trên Moodle được giữ nguyên.



Mọi người có thể đăng ký bằng email bất kỳ tại `/register` và đăng nhập bằng email, mật khẩu cục bộ. `POST /auth/register` và admin tạo tài khoản dùng chung `app/auth/accounts.py`: kiểm email hợp lệ, chuẩn hóa email, chính sách mật khẩu, tên và email trùng. Đăng ký và đăng nhập giới hạn 10 lần/phút; nhật ký không ghi mật khẩu hay token. Không có vai trò teacher/student trong ứng dụng: `users.is_admin` chỉ phân biệt admin. Admin vẫn được tạo bằng `scripts/create_admin.py` trên máy chủ, không qua API công khai.

Admin tạo tài khoản người dùng mà không tự liên kết Moodle; có thể đặt lại mật khẩu, vô hiệu hóa/kích hoạt lại, theo dõi và thử lại job lỗi. Vô hiệu hóa chặn đăng nhập và token hiện có, thu hồi phiên; dữ liệu, liên kết Moodle và email đăng ký được giữ nguyên. Kích hoạt lại khôi phục quyền đăng nhập với mật khẩu cũ. Admin không thể vô hiệu hóa tài khoản admin. Liên kết "Quên mật khẩu?" trên trang đăng nhập hướng dẫn liên hệ quản trị viên để đặt lại mật khẩu.

Mật khẩu ứng dụng và Moodle là riêng biệt. Ứng dụng không kiểm mật khẩu Moodle và không tạo người dùng Moodle. Nhập email trong “Tài khoản Moodle” để gửi yêu cầu. Email hợp lệ luôn nhận phản hồi 202 cùng thông báo chung và URL có mã yêu cầu ngẫu nhiên, kể cả khi không có tài khoản Moodle phù hợp; yêu cầu vẫn chờ rồi hết hạn sau một giờ. Nếu có tài khoản phù hợp, đăng nhập đúng tài khoản Moodle và xác nhận trên trang/thông báo Moodle (hết hạn sau một giờ); chỉ sau xác nhận ứng dụng mới lưu liên kết. Mỗi tài khoản Moodle chỉ liên kết với một tài khoản ứng dụng; đăng ký/tạo tài khoản ở đây luôn để liên kết trống. Không tự cấp vai trò giáo viên từ email hay từ đăng nhập. Có thể chọn “Hủy liên kết Moodle…” trong menu tài khoản ở thanh bên (có hộp xác nhận), hoặc nhờ admin hủy liên kết trên trang quản lý người dùng. Thao tác chỉ xóa liên kết tài khoản trong ứng dụng, giữ nguyên liên kết khóa học và mọi nội dung trên Moodle; cần liên kết lại tài khoản trước khi đẩy nội dung lên Moodle.

Admin Moodle tạo khóa học và phân quyền trong Moodle. Ứng dụng chỉ liệt kê, liên kết và đẩy nội dung vào khóa học mà tài khoản Moodle đã liên kết hiện có quyền `moodle/course:manageactivities`; kiểm tra lại trước mỗi thao tác. Không tạo khóa học hay tự ghi danh từ ứng dụng. Nhiều khóa học ứng dụng có thể liên kết cùng một khóa học Moodle được phép. Nút đẩy nội dung ẩn khi kiểm tra quyền thất bại.

Nếu Moodle trả về nội dung không phải JSON (ví dụ trang lỗi HTML), ứng dụng báo lỗi Moodle có kiểm soát để người dùng thử lại sau.

Tài khoản có ít nhất một khóa học ứng dụng liên kết với khóa học Moodle mà họ hiện có quyền quản lý hoạt động được sinh mô-đun đầy đủ. Các tài khoản khác được sinh tối đa 3 mô-đun mỗi ngày UTC, cấu hình bằng `NON_TEACHER_DAILY_GENERATION_LIMIT` (số nguyên dương). Mỗi mô-đun tính một lượt, kể cả bản đối chứng; retry job không tính lại. Không kiểm tra được Moodle thì áp dụng hạn mức; admin ứng dụng cũng tuân theo quy tắc này. Hạn mức lưu theo tài khoản, không đặt lại khi xóa khóa học.


## Tài liệu

- Đề tài: `GenerativeAI.md`
- Thiết kế kiến trúc Giai đoạn 2: `docs/specs/2026-09-23-kien-truc-gd2-design.md`
- Kế hoạch thực thi: `docs/plans/`
- Đánh giá R8 và metric claim/trích dẫn R9: [docs/rubric.md](docs/rubric.md), gồm xuất cặp gói, chấm riêng Content, phân tích kết quả đã lưu và điều kiện chạy miễn phí.
- Thiết lập nghiên cứu R18 qua API admin và chấm mù theo lời mời: [docs/studies.md](docs/studies.md).

## Kiến trúc

| Thành phần | Công nghệ |
| --- | --- |
| Backend | FastAPI, SQLAlchemy, Alembic |
| Cơ sở dữ liệu | PostgreSQL 16 với pgvector, Row Level Security |
| Hàng đợi công việc | Bảng trong chính Postgres, không dùng Redis hay Celery |
| Truy xuất | Dense cộng BM25 cộng Reciprocal Rank Fusion |
| Cổng mô hình | LiteLLM Proxy, giữ toàn bộ khóa nhà cung cấp |
| Frontend | React, Vite, TypeScript |
| LMS | Moodle 5.2 cùng plugin `local_lms_bridge` tự viết |

Backend không bao giờ thấy khóa của nhà cung cấp mô hình.
Nó chỉ biết địa chỉ gateway, một khóa nội bộ, và tên alias của mô hình.
Chính sách fallback, cân bằng tải, ghim model cho thí nghiệm và model thực sự đã sinh: [gateway routing](docs/gateway-routing.md).


## Yêu cầu

Docker Desktop, và mã nguồn Moodle 5.2 tải riêng (xem bước 2).

## Chạy hệ thống

1. Sao chép biến môi trường rồi điền giá trị thật:

```bash
cp devops/.env.example devops/.env
```

2. Tải mã nguồn Moodle và giải nén vào thư mục `moodle/` ở gốc kho mã.
   Thư mục này không nằm trong kho vì nặng 1,42 GB và là sản phẩm của bên thứ ba.
   Sau khi giải nén, tệp `moodle/public/version.php` phải tồn tại.

   Phiên bản phải **bằng hoặc mới hơn** phiên bản đã ghi trong cơ sở dữ liệu, hiện là
   `5.2.3+ (Build: 20260916)`, tức `$version = 2026042003.01`.
   Moodle từ chối khởi động với mã nguồn cũ hơn database và báo
   "The code you are using is older than the version recorded in the database".
   Kiểm tra phiên bản database bằng:

   ```bash
   docker compose exec db psql -U "$DATABASE_USER" -d microlearning \
     -c "select value from mdl_config where name = 'release'"
   ```

   Nếu dựng mới hoàn toàn (chưa có dữ liệu Moodle) thì bản 5.2 bất kỳ đều dùng được.

3. Dựng toàn bộ stack:

```bash
docker compose up -d
```

- Giao diện: http://localhost:5173
- API: http://localhost:8000
- Moodle: http://localhost:8081

Nếu mọi thao tác với Moodle báo "Moodle từ chối yêu cầu do cấu hình quyền truy cập chưa đúng" (nhật ký backend: `Access control exception`, kể cả với `core_webservice_get_site_info`), lần cài Moodle đầu đã bị ngắt trước khi bật vai trò (`rolesactive=0`). Dựng lại image để script `finish-interrupted-install.php` sửa (chạy mỗi lần container khởi động, không làm gì trên site đã ổn): `docker compose up -d --build moodle`; nhật ký Moodle sẽ in `roles activated`.

## Phát triển

```bash
cd backend
uv run pytest tests/unit tests/integration tests/e2e
uv run ruff check .
uv run alembic upgrade head
```

```bash
cd frontend
npm run test -- src/test/unit src/test/integration
npm run build
```

Đây cũng đúng là những lệnh mà CI chạy, xem `.github/workflows/ci.yml`.
`uv run pytest` và `npm run test` cũng tự tìm toàn bộ các tầng trên.

- Backend `tests/unit`: thuật toán, cấu hình và xử lý dữ liệu cục bộ; không cần DB hay dịch vụ mạng.
- Backend `tests/integration`: HTTP app, DB và các ranh giới với gateway, Moodle, OCR hoặc FFmpeg dùng hàm giả.
- Backend `tests/e2e`: pipeline nhập tài liệu chạy đầy đủ tới dữ liệu được lưu trong DB, với embedding ngoại tuyến. Test sinh module dùng LLM thật vẫn mang marker `live_llm`, mặc định không chạy trong CI; chỉ chạy riêng bằng `uv run pytest tests/e2e -m live_llm` khi đã cấu hình DB và gateway thử nghiệm.
- Frontend `src/test/unit`: component và schema; `src/test/integration`: luồng trang, auth và API. Fixture jsdom chung ở `src/test/setup.ts`.

Có thể chạy riêng một tầng bằng đường dẫn, ví dụ `uv run pytest tests/unit` hoặc `npm run test -- src/test/unit`. Các tầng backend cần DB phải dùng PostgreSQL/pgvector thử nghiệm đã chạy migration, với `DATABASE_URL`, `APP_RUNTIME_DATABASE_URL` và `APP_WORKER_DATABASE_URL` trỏ tới DB đó (như CI), vì test migration tạo và xoá DB tạm. Fixture dùng chung nằm ở `backend/tests/conftest.py`; fixture DB dùng chung cho self-check/ngôn ngữ nằm ở `backend/tests/integration/conftest.py`.
CI còn có job `moodle-plugin` chạy php lint và PHPUnit cho `moodle-plugin/local_lms_bridge` trên một site Moodle 5.2 dựng sẵn trong job.

## Quy trình đóng góp

Nhánh `main` luôn phải xanh và không nhận commit trực tiếp.
Mỗi tính năng làm trên một nhánh riêng theo tiền tố `feat/`, `fix/`, `refactor/` hoặc `chore/`.
Khi xong thì mở merge request, CI xanh mới được gộp, và gộp theo kiểu squash để mỗi tính năng tương ứng một commit trên `main`.

Một tính năng được coi là hoàn chỉnh khi: test được viết trước phần cài đặt, `ruff check` sạch, toàn bộ `pytest` xanh chứ không riêng test của tính năng đó, có migration kèm theo nếu đổi schema, và tính năng người dùng nhìn thấy được thì đã kiểm thử thật bằng trình duyệt.
