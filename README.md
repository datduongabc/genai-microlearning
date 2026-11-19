# GENAILMS: sinh mô-đun Microlearning từ tài liệu môn học

Đề tài HK261-DAGD1-033, Đồ án chuyên ngành, Khoa Khoa học và Kỹ thuật Máy tính, Trường Đại học Bách khoa – ĐHQG-HCM.
Sinh viên: Dương Nguyễn Tấn Đạt (2352222). Giảng viên hướng dẫn: TS. Nguyễn Quang Hùng, PGS. TS. Thoại Nam.

GENAILMS là công cụ cho giảng viên:

1. Tải tài liệu môn học lên: PDF, PPTX, DOCX, HTML, âm thanh hoặc video bài giảng.
2. Sinh một mô-đun Microlearning gồm bảy phần: Learning Objective, Content, Slides, Example, Practice, Quiz, Explanation.
3. Mỗi ý quan trọng trong bài có trích dẫn tới đúng đoạn nguồn (trang, slide hoặc mốc thời gian).
4. Giảng viên đối chiếu nguồn và sửa bài, bằng tay hoặc qua chat với AI; bản gốc AI luôn được giữ lại.
5. Bài được xuất bản lên Moodle thành một Book và hai quiz.

Giảng viên và sinh viên mở công cụ từ Moodle qua LTI 1.3. Sinh viên chỉ được hỏi đáp trên tài liệu của khóa học mình.

Báo cáo cuối kỳ: [`final-report/report.pdf`](final-report/report.pdf). Đề bài: [`GenerativeAI.md`](GenerativeAI.md).

## Tính năng chính

| Nhóm | Chức năng |
| --- | --- |
| Tài liệu | Upload ba bước, có thể gửi thẳng lên S3 bằng presigned POST. Đọc PDF có sửa công thức, OCR trang và chữ trong hình. Đọc slide, ghi chú và SmartArt của PPTX. Phiên âm audio/video bằng Whisper, giữ mốc thời gian. Chia đoạn theo cấu trúc nguồn. |
| Truy xuất | Dense (Qwen3-Embedding chạy tại chỗ) cộng BM25, gộp bằng Reciprocal Rank Fusion, rerank bằng Jev (mặc định) hoặc Jina tự host. Số đoạn chọn theo adaptive-k. |
| Sinh mô-đun | Planner chọn khái niệm, sinh LO và sub-objective, kiểm tra độ liên quan rồi viết lại truy vấn khi thiếu nguồn, viết Content kèm `[S#]`, sau đó Slides và Example. Câu hỏi do model tự kiểm, có bẫy cú pháp và luật cân bằng độ dài; khi trượt thì giữ bản ít lỗi nhất. |
| Biên tập | Mở đoạn nguồn của từng trích dẫn, sửa từng thành phần hoặc từng câu hỏi, sửa qua chat, xuất slide ra PPTX. |
| Moodle | Liên kết tài khoản, xác nhận ngay trong Moodle. Kiểm quyền giảng viên theo khóa học. Tạo Book, Practice (phản hồi ngay) và Quiz (phản hồi sau khi nộp), kèm điều kiện hoàn thành để khóa học tính được và cấp chứng chỉ. |
| LTI 1.3 | Giảng viên và sinh viên vào thẳng từ hoạt động External tool. Danh sách lớp được đồng bộ qua NRPS, sinh viên rời lớp bị gỡ quyền sau khoảng một giờ. |
| Hỏi đáp | Chat theo khóa học, câu trả lời stream (SSE), có trích dẫn và lưu lịch sử. Sinh viên được 30 tin mỗi ngày. |
| Nghiên cứu | Cặp module RAG và không RAG, gói chấm mù với thứ tự xáo riêng cho từng người chấm, chấm bằng model judge, phân tích độ đồng thuận. |
| Quản trị | Quản lý tài khoản; xem và thử lại job lỗi mà không đọc được nội dung của giảng viên. |

## Kiến trúc

| Thành phần | Công nghệ |
| --- | --- |
| Backend (`api`, `worker`) | Python 3.14, FastAPI, SQLAlchemy 2, Alembic. Cùng một image, hai tiến trình. |
| Dữ liệu | PostgreSQL 16 với pgvector: dữ liệu, vector, hàng đợi job và Row Level Security. |
| Bộ đếm rate limit | Redis 7 khi chạy local, Valkey trong thiết kế AWS. Mất dữ liệu ở đây không mất việc. |
| Cổng mô hình | LiteLLM. Giữ một khóa duy nhất của OpenRouter; mọi model (GPT-6 Luna mặc định, Gemini, Claude, DeepSeek, Whisper) đều đi qua OpenRouter. |
| Embedding | Text Embeddings Inference với Qwen3-Embedding-0.6B, chạy GPU hoặc CPU. |
| Rerank | Jev (TypeSafe), hoặc Jina reranker v2 INT8 tự host (chỉ được dùng phi thương mại). |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS. |
| LMS | Moodle 5.2 với plugin `local_lms_bridge`, kèm LTI 1.3. |
| Hạ tầng | Docker Compose và Caddy khi chạy local. Terraform cho AWS (ECS Fargate, CloudFront), đã chạy trên AWS từ ngày 2026-11-11, xem [infra/README.md](infra/README.md). |

Các service đều không giữ trạng thái, nên thêm task là scale được.
Backend không bao giờ cầm khóa nhà cung cấp: nó chỉ biết địa chỉ gateway, một khóa nội bộ và tên alias.
Chính sách định tuyến, fallback và ghim model khi đo xem ở [docs/gateway-routing.md](docs/gateway-routing.md).

## Cấu trúc kho mã

| Đường dẫn | Nội dung |
| --- | --- |
| `backend/app` | API và worker, chia theo chức năng: `api`, `auth`, `ingestion`, `retrieval`, `generation`, `chat`, `jobs`, `llm`, `lti`, `moodle`, `study`, `models`. |
| `backend/alembic` | Migration, gồm cả role và policy RLS. |
| `backend/tests` | Test unit, integration và e2e. |
| `backend/scripts` | Script vận hành (tạo admin, đăng ký nền tảng LTI, embed lại) và các script đo. |
| `frontend/src` | Trang, component, API client và test. |
| `moodle-plugin/local_lms_bridge` | Plugin Moodle và test PHPUnit. |
| `devops` | Compose, cấu hình gateway, image embedder/reranker, Caddy, load test, các kịch bản e2e trình duyệt và LTI. |
| `infra` | Terraform cho AWS và script triển khai. |
| `docs` | Báo cáo đo kèm dữ liệu thô, rubric, giao thức nghiên cứu, ghi chú thiết kế. |
| `final-report` | Báo cáo cuối kỳ (chỉ lưu PDF trong git). |

## Yêu cầu

- Docker và Docker Compose. Muốn embed bằng GPU thì cần thêm NVIDIA Container Toolkit.
- Mã nguồn Moodle 5.2, tải riêng (xem bước 2 bên dưới).
- Khóa API trong `devops/.env`:
  - `OPENROUTER_API_KEY` cho mọi model: GPT-6 Luna (mặc định), Gemini, Claude, DeepSeek và Whisper (phiên âm).
  - `TYPESAFE_API_KEY` cho Jev. Muốn rerank ngay trên máy, không gửi đoạn tài liệu cho dịch vụ rerank bên ngoài, thì đặt `RERANKER_PROVIDER=jina`.

## Chạy hệ thống

1. Tạo file môi trường, điền khóa, rồi đặt mật khẩu ngẫu nhiên cho các role database và admin Moodle:

   ```bash
   cp devops/.env.example devops/.env
   python devops/scripts/rotate_secrets.py
   ```

2. Giải nén mã nguồn Moodle vào thư mục `moodle/` ở gốc kho mã, sao cho có tệp `moodle/public/version.php`.
   Thư mục này không nằm trong git vì nặng khoảng 1,4 GB và là mã của bên thứ ba.
   Nếu database Moodle đã có dữ liệu thì mã nguồn phải bằng hoặc mới hơn phiên bản đã ghi (hiện là 5.2.3+, `2026042003.01`).
   Nếu cũ hơn, Moodle báo "The code you are using is older than the version recorded in the database".

3. Khởi động stack:

   ```bash
   ./devops/scripts/compose.sh up -d
   ```

   Script tự dùng GPU cho embedder khi có, không có thì dùng CPU. Muốn ép CPU thì đặt `EMBEDDER_DEVICE=cpu`.
   Migration chạy một lần trong service `migrate` trước khi backend khởi động.

4. Tin chứng chỉ cục bộ của Caddy một lần, rồi tạo admin đầu tiên:

   ```bash
   docker exec do-an-proxy caddy trust
   docker exec -it -e PYTHONPATH=. do-an-backend uv run python scripts/create_admin.py admin@example.com --name "Admin"
   ```

5. Truy cập:
   - Ứng dụng: https://localhost:8443
   - Moodle: http://localhost:8081
   - API (chỉ trên máy): http://localhost:8000

6. Muốn mở công cụ từ Moodle thì:
   - Đăng ký nền tảng bằng `backend/scripts/register_lti_platform.py`, với các giá trị Moodle hiển thị cho công cụ.
   - Thêm một hoạt động External tool vào khóa học.
   - Giảng viên phải mở công cụ một lần trước, sinh viên mới vào được.

Muốn chạy nhiều API và worker trên một máy:

```bash
./devops/scripts/compose.sh -f devops/docker-compose.scale.yml up -d --scale backend=2 --scale worker=2
```

Chạy trên nhiều máy thì cần `UPLOAD_STORE=s3`. Cách đưa ra địa chỉ công khai xem ở [devops/README.md](devops/README.md).

Nếu mọi thao tác Moodle báo lỗi quyền truy cập (nhật ký backend ghi `Access control exception`), nghĩa là lần cài Moodle đầu đã bị ngắt giữa chừng.
Dựng lại image để script tự sửa: `./devops/scripts/compose.sh up -d --build moodle`.

## Cấu hình thường dùng

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `LLM_MODEL` | `openai-fast` | Alias sinh mặc định (GPT-6 Luna), chọn theo `docs/measurements/2026-11-16-four-chat-models.md` và `2026-11-16-four-models-rest.md`. |
| `LLM_EXPERIMENT_MODE` | `false` | Ghim model và tắt fallback khi đo. |
| `RERANKER_PROVIDER` | `jev` | `jev` hoặc `jina`. |
| `WORKER_POOL_SIZE` | `4` | Số job một worker chạy cùng lúc. |
| `QUESTION_CONCURRENCY` | `5` | Số câu hỏi sinh song song trong một mô-đun. |
| `NON_TEACHER_DAILY_GENERATION_LIMIT` | `3` | Số mô-đun mỗi ngày của tài khoản chưa liên kết Moodle. |
| `LEARNER_DAILY_CHAT_LIMIT` | `30` | Số tin chat mỗi ngày của sinh viên. |
| `UPLOAD_STORE` | `local` | `local` (gửi qua API) hoặc `s3` (presigned POST). |

Danh sách đầy đủ có trong `devops/.env.example` và `backend/app/config.py`.

## Phát triển và kiểm thử

```bash
cd backend
uv run alembic upgrade head
uv run ruff check .
uv run pytest tests/unit tests/integration tests/e2e
```

```bash
cd frontend
npm run lint
npm run test -- src/test/unit src/test/integration
npm run build
```

- Test backend cần một PostgreSQL có pgvector đã chạy migration, với ba biến `DATABASE_URL`, `APP_RUNTIME_DATABASE_URL` và `APP_WORKER_DATABASE_URL` trỏ tới DB đó, như CI.
- Model, reranker, Moodle và nền tảng LTI đều được thay bằng hàm giả: không test nào gửi tài liệu ra ngoài.
- Test sinh mô-đun bằng model thật mang marker `live_llm` và không chạy trong CI.

| Bộ test | Số test |
| --- | ---: |
| Backend unit / integration / e2e | 779 / 1.360 / 2 |
| Frontend unit / integration | 104 / 340 |
| Plugin Moodle (PHPUnit) | 21 |
| Terraform (`terraform test`, provider giả lập) | 8 |

CI (`.github/workflows/ci.yml`) có năm job: `backend`, `frontend`, `images`, `infra` và `moodle-plugin`.
Job `images` build image backend và gateway từ đúng Dockerfile mà `deploy.sh` dùng, rồi quét bằng Trivy: lỗ hổng CRITICAL hoặc HIGH đã có bản vá làm pull request trượt.
Triển khai lên AWS vẫn chạy bằng `infra/scripts/deploy.sh` có người duyệt bản plan, vì một role CI đủ quyền apply toàn bộ hạ tầng rủi ro hơn lợi ích cho bản demo.

Load test và kiểm thử lỗi nằm trong `devops/loadtest`. Kịch bản trình duyệt thật và LTI thật nằm trong `devops/browser-e2e` và `devops/lti-e2e`.

## Kết quả đo chính

Đo trên năm môn: Lập trình nâng cao, Giải tích 1, Học máy, Hệ điều hành, Chủ nghĩa xã hội khoa học (23 tệp, 937 đoạn). Chi tiết ở chương Results của báo cáo và trong `docs/measurements/`.

| Chỉ số | Kết quả |
| --- | --- |
| Truy xuất (hybrid + Jina, 250 câu) | Hit Rate@1 0,856; MRR@10 0,918; nDCG@10 0,938 |
| Jev so với Jina (200 câu giữ lại) | MRR@10 0,952 so với 0,909; p95 rerank thấp hơn 2,4–6,3 lần |
| Chat (Ragas, 50 câu) | Faithfulness 0,990; answer relevancy 0,740 |
| Pilot 10 cặp module | RAG: 0/120 ý không có nguồn; không RAG: 17/101 |
| Năm môn với Jev và Gemini 3.5 Flash Lite | Upload 33,7 s/tệp, chat 2,1 s, mô-đun 28,9 s |
| Rubric hai judge, 10 mô-đun | Trung bình 3,76/5; quiz và Bloom yếu nhất (~2,9) |
| Prompt injection trong chat | 22/80 → 4/80 sau khi thêm luật chống chỉ thị nhúng |

## Giới hạn hiện tại

- Chất lượng quiz còn thấp: đáp án đúng thường là phương án dài nhất, câu hỏi chủ yếu ở mức nhớ.
- Chưa có người chấm thật; điểm rubric mới đến từ model judge trên ít mô-đun.
- Mọi số đo tải đều chạy trên một máy; bản triển khai AWS mới qua smoke test, chưa được đo tải.
- Compose vẫn phục vụ frontend bằng Vite dev server.
- Mỗi khóa học có một giảng viên; chưa có LTI Deep Linking và chưa trả điểm về Moodle.

Danh sách đầy đủ và kế hoạch phát triển nằm ở chương Conclusion của báo cáo.

## Tài liệu

- [docs/rubric.md](docs/rubric.md): rubric tám tiêu chí, metric claim và trích dẫn, cách xuất và phân tích.
- [docs/studies.md](docs/studies.md): thiết lập nghiên cứu và chấm mù.
- [docs/gateway-routing.md](docs/gateway-routing.md): alias model, nhà cung cấp, fallback và chế độ thí nghiệm.
- [devops/README.md](devops/README.md): vận hành stack, phân quyền credential, scale và công khai.
- [docs/measurements/](docs/measurements/): mỗi lần đo một báo cáo, kèm script và dữ liệu thô.
