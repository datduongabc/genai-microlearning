# Hệ thống AI tạo sinh mô-đun Microlearning từ tài liệu môn học

Đề tài HK261-DAGD1-033.
Sinh viên: Dương Nguyễn Tấn Đạt, ngành Khoa học Máy tính.

Hệ thống nhận tài liệu môn học của giảng viên (PDF, PowerPoint, HTML, video), truy xuất bằng RAG, rồi sinh mô-đun Microlearning gồm bảy thành phần: Learning Objective, Content, Slides, Example, Practice, Quiz, Explanation.
Mô-đun sau đó được xuất bản sang Moodle cho học viên học.

Ứng dụng này chỉ dành cho giảng viên.
Học viên không đăng nhập vào đây mà học trên Moodle.

Trong trang xem trước, giảng viên có thể yêu cầu AI sửa một thành phần bằng ngôn ngữ tự nhiên, ví dụ “Rút gọn slide 3” hoặc “Làm câu quiz 2 khó hơn”. AI bám các đoạn nguồn của mô-đun và ngôn ngữ đầu ra đã chọn; yêu cầu chưa rõ sẽ được hỏi lại. Bản gốc AI được giữ trong `ai_original`, và số lượt chỉnh sửa đã áp dụng được hiển thị trên trang.
API: `POST /modules/{module_id}/chat-edit` nhận `{message, session_id?}`, trả `{session_id, reply, applied, edit_count}`. Lịch sử lưu trong bảng chat hiện có, tách khỏi hỏi-đáp môn học bằng `chat_sessions.module_id`; `chat_messages.applied_edit` đánh dấu lượt đã áp dụng. Chạy `alembic upgrade head` trước khi dùng.

## Tài liệu

- Đề tài: `GenerativeAI.md`
- Thiết kế kiến trúc Giai đoạn 2: `docs/specs/2026-09-23-kien-truc-gd2-design.md`
- Kế hoạch thực thi: `docs/plans/`
- Đánh giá R8 và metric claim/trích dẫn R9: [docs/rubric.md](docs/rubric.md), gồm xuất cặp gói, chấm riêng Content, phân tích kết quả đã lưu và điều kiện chạy miễn phí.

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

## Phát triển

```bash
cd backend
uv run pytest
uv run ruff check .
uv run alembic upgrade head
```

```bash
cd frontend
npm run test
npm run build
```

Đây cũng đúng là những lệnh mà CI chạy, xem `.github/workflows/ci.yml`.
CI còn có job `moodle-plugin` chạy php lint và PHPUnit cho `moodle-plugin/local_lms_bridge` trên một site Moodle 5.2 dựng sẵn trong job.

## Quy trình đóng góp

Nhánh `main` luôn phải xanh và không nhận commit trực tiếp.
Mỗi tính năng làm trên một nhánh riêng theo tiền tố `feat/`, `fix/`, `refactor/` hoặc `chore/`.
Khi xong thì mở merge request, CI xanh mới được gộp, và gộp theo kiểu squash để mỗi tính năng tương ứng một commit trên `main`.

Một tính năng được coi là hoàn chỉnh khi: test được viết trước phần cài đặt, `ruff check` sạch, toàn bộ `pytest` xanh chứ không riêng test của tính năng đó, có migration kèm theo nếu đổi schema, và tính năng người dùng nhìn thấy được thì đã kiểm thử thật bằng trình duyệt.
