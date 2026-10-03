# Hệ thống AI tạo sinh mô-đun Microlearning từ tài liệu môn học

Đề tài HK261-DAGD1-033.
Sinh viên: Dương Nguyễn Tấn Đạt, ngành Khoa học Máy tính.

Hệ thống nhận tài liệu môn học của giảng viên (PDF, PowerPoint, HTML, video), truy xuất bằng RAG, rồi sinh mô-đun Microlearning gồm bảy thành phần: Learning Objective, Content, Slides, Example, Practice, Quiz, Explanation.
Mô-đun sau đó được xuất bản sang Moodle cho học viên học.

Ứng dụng này dành cho giảng viên, cộng một vai trò admin để quản lý tài khoản giảng viên.
Học viên không đăng nhập vào đây mà học trên Moodle.

Trong trang xem trước, giảng viên có thể yêu cầu AI sửa một thành phần bằng ngôn ngữ tự nhiên, ví dụ “Rút gọn slide 3” hoặc “Làm câu quiz 2 khó hơn”. AI bám các đoạn nguồn của mô-đun và ngôn ngữ đầu ra đã chọn; yêu cầu chưa rõ sẽ được hỏi lại. Bản gốc AI được giữ trong `ai_original`, và số lượt chỉnh sửa đã áp dụng được hiển thị trên trang. Mô-đun đối chứng (không RAG) không hỗ trợ chỉnh sửa bằng chat.
API: `POST /modules/{module_id}/chat-edit` nhận `{message, session_id?}`, trả `{session_id, reply, applied, edit_count}`. Lịch sử lưu trong bảng chat hiện có, tách khỏi hỏi-đáp môn học bằng `chat_sessions.module_id`; `chat_messages.applied_edit` đánh dấu lượt đã áp dụng. Chạy `alembic upgrade head` trước khi dùng.

Sinh lại cho cùng một tài liệu hay mục luôn tạo mô-đun mới, không ghi đè mô-đun đã có; chỉ một lượt đang chạy mới chặn lượt sau. Giảng viên xóa một mô-đun đã sinh bằng `DELETE /modules/{module_id}` (nút “Xóa mô-đun” trên danh sách, có hộp xác nhận): endpoint xóa luôn bản đối chứng không RAG đi cặp và mọi dòng phụ thuộc (câu hỏi, đánh giá, lịch sử chat-edit, job), trả 409 khi bản đối chứng của nó đang được sinh, và 404 với mô-đun của người khác. Ứng dụng không lưu mô-đun nào đã đẩy lên Moodle, nên Book/Quiz đã tạo trên Moodle được giữ nguyên.

Không có đăng ký công khai: tài khoản giảng viên do admin tạo trên trang Quản trị (`/admin`), nơi admin cũng khóa hoặc mở khóa tài khoản (khóa không xóa dữ liệu nào và có hiệu lực ngay, kể cả với token đang dùng), xóa tài khoản giảng viên chưa từng dùng (`DELETE /admin/users/{user_id}`: chỉ được khi giảng viên không có môn học nào và chưa liên kết hay tạo môn học Moodle nào qua ứng dụng, nếu không thì trả 409 và nút Xóa bị vô hiệu kèm lý do, nên giảng viên đã có dữ liệu chỉ khóa được; tài khoản Moodle đã nối không bị xóa), đặt lại mật khẩu, xem trạng thái liên kết Moodle của từng giảng viên và xem lỗi pipeline của mọi giảng viên để bấm "Thử lại" cho một hay nhiều job. Admin là vai trò riêng, không có môn học, và chỉ thấy job lỗi cùng tên tệp và tên môn học đi kèm, không thấy nội dung tài liệu, mô-đun hay chat. Admin đầu tiên chỉ tạo được bằng lệnh trên máy chủ: `docker exec -it -e PYTHONPATH=. do-an-backend uv run python scripts/create_admin.py admin@school.edu.vn --name "Admin"` (mật khẩu được hỏi tại chỗ; email của admin cũng phải là email trường như mọi tài khoản). Giảng viên tự xem lỗi tài liệu và lỗi sinh mô-đun của mình ngay trên trang môn học, lọc danh sách tài liệu theo trạng thái và bấm "Thử lại" (`POST /jobs/{job_id}/retry`) để job cũ chạy lại từ bước đã dừng, không tải lên lại và không tạo thêm mô-đun.

Chỉ email của trường được chấp nhận cho tài khoản: tên miền dạng `x.edu` hoặc `x.edu.vn` (nhãn `edu` ở cuối, hoặc đứng trước một nhãn quốc gia hai chữ cái). Quy tắc này được kiểm ở ứng dụng khi admin tạo giảng viên, khi tạo admin đầu tiên bằng lệnh và khi tạo tài khoản Moodle; màn hình đăng nhập không kiểm, để tài khoản cũ vẫn đăng nhập được.

Giảng viên được nối với tài khoản Moodle bằng email (so sánh không phân biệt hoa thường) và việc nối do chính giảng viên chọn, không có gì được tạo tự động khi admin tạo tài khoản. Liên kết được lưu lại (`users.moodle_user_id`, migration 0008) và chỉ được đặt khi giảng viên bấm "Có": Moodle có sẵn tài khoản cùng email cũng chưa tính là đã nối. Liên kết đã lưu được kiểm lại với Moodle mỗi lần ứng dụng hỏi trạng thái (`GET /moodle/account`) và khi ghi danh thất bại: nếu tài khoản Moodle đó đã bị xóa thì liên kết bị xóa, giảng viên nhận thông báo cần liên kết lại (422) và mục "Tài khoản Moodle" chuyển sang "Chưa liên kết"; hộp thoại không hiện lại. Giảng viên chưa nối (kể cả khi Moodle đã có email của họ) sẽ thấy hộp thoại khuyên nên nối khi vào ứng dụng lần đầu, với "Có (khuyến nghị)" và "Không". Hộp thoại chỉ hiện một lần cho mỗi giảng viên: câu trả lời "Có" hay "Không" (hoặc tự liên kết) được lưu cùng tài khoản (`users.moodle_prompted_at`, migration 0009; `POST /moodle/account/prompt/dismiss` cho "Không"), không lưu trong trình duyệt, nên đăng nhập lại cũng không hỏi nữa. "Có" nối với tài khoản Moodle đã có cùng email (không bao giờ tạo trùng), còn chưa có thì tạo mới bằng `core_user_create_users` (tên đăng nhập là email, mật khẩu ngẫu nhiên mà giảng viên tự đặt lại bằng "quên mật khẩu" của Moodle); "Không" đóng hộp thoại. Trang môn học (và phần đẩy lên Moodle của trang mô-đun) luôn có mục "Tài khoản Moodle" cho biết đã liên kết hay chưa, với nút "Liên kết tài khoản Moodle" dùng được bất cứ lúc nào (`GET /moodle/account`, `POST /moodle/account/link`). Phải nối tài khoản trước khi liên kết một môn học với Moodle, tạo course Moodle hay đẩy mô-đun lên: khi liên kết, giảng viên chọn course có sẵn trong ô thả xuống liệt kê mọi course đã tạo trên Moodle (`GET /courses/moodle/courses`, trừ course đã gắn với môn khác) hoặc tạo course mới. Tài khoản Moodle đã nối của chính giảng viên (không phải chủ của token web service) được ghi danh làm giáo viên (editingteacher) khi tạo course mới, khi liên kết course có sẵn, và lại trước mỗi lần đẩy lên Moodle (ghi danh lại người đã là giáo viên không thay đổi gì). Hai tài khoản Moodle cùng email thì không nối được: hộp thoại và trang môn học báo rõ để nhờ admin Moodle xử lý trùng email. Trạng thái Moodle trên trang admin vẫn dựa vào việc có tài khoản Moodle cùng email hay không; các lần tra cứu chạy song song và cả danh sách chờ tối đa 10 giây, lần nào chưa xong thì hiện "Không kiểm tra được Moodle". Token web service cần quyền `moodle/user:create`, và hai hàm `core_user_get_users` (tìm theo email không phân biệt hoa thường) và `core_user_create_users` vừa được thêm vào service `lms_bridge`, nên sau khi cập nhật hãy để Moodle nâng cấp plugin (số phiên bản đã tăng) trước khi dùng.

Về phía Moodle, cài đặt site `allowemailaddresses` (Quản trị site → Plugins → Xác thực → Email, hoặc `php admin/cli/cfg.php --name=allowemailaddresses --set=".edu .edu.vn"`) chỉ chặn email ngoài trường khi tự đăng ký hoặc đổi email trong hồ sơ; nó không chặn tài khoản tạo qua web service, nên quy tắc edu của ứng dụng mới là chốt chặn cho tài khoản do ứng dụng tạo. Ứng dụng không tự đổi cài đặt này trên Moodle thật; chỉ bản Moodle thử riêng cho kiểm thử được đặt như vậy.

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

Nếu mọi thao tác với Moodle báo "Moodle từ chối yêu cầu do cấu hình quyền truy cập chưa đúng" (nhật ký backend: `Access control exception`, kể cả với `core_webservice_get_site_info`), lần cài Moodle đầu đã bị ngắt trước khi bật vai trò (`rolesactive=0`). Dựng lại image để script `finish-interrupted-install.php` sửa (chạy mỗi lần container khởi động, không làm gì trên site đã ổn): `docker compose up -d --build moodle`; nhật ký Moodle sẽ in `roles activated`.

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
