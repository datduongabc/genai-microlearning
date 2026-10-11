# Thiết kế lại kiến trúc hệ thống cho Giai đoạn 2

Đề tài HK261-DAGD1-033: Xây dựng hệ thống AI tạo sinh tự động tạo các mô-đun Microlearning từ tài liệu môn học trên LMS.
Ngày: 2026-09-23.
Trạng thái: đã thống nhất, chờ lập kế hoạch thực thi.

## 1. Bối cảnh và mục tiêu

Hệ thống đã chạy được end-to-end ở Giai đoạn 1: đọc tài liệu, chia chunk, nhúng, truy xuất, sinh 7 thành phần của một module, và xuất bản sang Moodle.
Giai đoạn 2 (Luận văn) có trọng tâm khác hẳn: tích hợp LMS, cho giảng viên chỉnh sửa nội dung AI, xây dựng rubric đánh giá, so sánh ba nhóm Human vs LLM-no-RAG vs RAG+LLM, và phân tích hallucination.

Vấn đề cần giải quyết trong lần thiết kế lại này là hệ thống đã phình ra so với phạm vi đề tài, trong khi phần đóng góp nghiên cứu của Giai đoạn 2 gần như chưa có gì.
Cụ thể, phần hạ tầng và bảo mật đang chiếm nhiều công sức hơn phần sinh nội dung, còn bộ chấm 9 tiêu chí thì chỉ được gọi bởi chính test của nó.

Ngoài ra, giữa ngày 2026-09-20 và 2026-09-23, thư mục `.git` của dự án đã biến mất cùng một số thành phần quan trọng.
Việc thiết kế lại lần này vì vậy gắn liền với việc khởi tạo lại kho mã và thiết lập quy trình làm việc chặt hơn.

### Ràng buộc đã chốt

Ứng dụng này chỉ dành cho giảng viên.
Học viên không đăng nhập vào đây; họ học các module trên Moodle sau khi giảng viên đã xuất bản.
Hệ quả là Moodle là con đường duy nhất tới người học, nên luồng xuất bản là mạch chính của hệ thống chứ không phải tính năng phụ.

Chi phí gọi mô hình hiện do hệ thống chịu bằng khóa tập trung.
Về lâu dài, giảng viên sẽ tự thêm khóa API của họ và tự trả tiền.

## 2. Hiện trạng đo được

Số liệu dưới đây lấy từ chính mã nguồn và môi trường chạy thật ngày 2026-09-23.

| Hạng mục | Số đo |
| --- | --- |
| Mã nguồn backend | 6.444 dòng |
| Mã kiểm thử backend | 8.465 dòng, 68 file |
| Mã auth và bảo mật | 650 dòng mã, 1.572 dòng test |
| Tính năng chat | 214 dòng, 2 bảng cơ sở dữ liệu, 0 giao diện |
| Thư mục `moodle/` | 61.556 file, 1,42 GB |
| Thư mục `documents/` | 46 file, 151 MB |
| `backend/.venv/` | 41.146 file, 1,13 GB |
| `frontend/node_modules/` | 3.191 file, 116 MB |
| `frontend/src/api.ts` | 392 dòng trong một file |

### Những thứ đang hỏng

Bộ chấm 9 tiêu chí ở `app/generation/evaluation.py` không được nối vào đâu cả, chỉ có test của chính nó gọi tới.

Plugin Moodle `local_lms_bridge` không còn trong kho mã, trong khi backend vẫn gọi `local_lms_bridge_create_book` và `local_lms_bridge_create_quiz`.
Chức năng xuất bản vì vậy hỏng ở mức mã nguồn.

Thư mục `backend/alembic/` không còn, trong khi quy trình CI có bước `alembic upgrade head`.
CI sẽ đỏ ngay ở bước này.

Tệp `devops/docker-compose.yml` vẫn mount `./moodle-src-523` nhưng thư mục đã được đổi tên thành `moodle/`.

Vai trò `UserRole.student` tồn tại trong mô hình dữ liệu nhưng không được dùng ở bất kỳ đâu; mọi lần đăng ký đều gán cứng `teacher`.

Không còn `.gitignore` ở thư mục gốc, và `devops/` chưa bao giờ có tệp này.
Tệp `devops/.env` chứa khóa thật đang hoàn toàn không được che.

### Kết quả đo reranker

Đo trên 237 chunk thật và 20 câu hỏi tự viết, sau khi đã chuyển sang embedding đa ngữ.

| Nhóm câu hỏi | Trước rerank (hybrid) | Sau rerank |
| --- | --- | --- |
| Tiếng Việt, 14 câu | R@1 0,93 / MRR 0,964 | R@1 0,86 / MRR 0,907 |
| Tiếng Anh, 6 câu | R@1 0,33 / MRR 0,575 | R@1 0,50 / MRR 0,653 |
| Tổng, 20 câu | R@1 0,75 / R@5 1,00 | R@1 0,75 / R@5 0,95 |

Mô hình `bge-reranker-base` chủ yếu được huấn luyện trên tiếng Anh và tiếng Trung.
Khi embedding còn là mô hình tiếng Anh, reranker cứu được nhiều lỗi thứ tự nên có ích.
Sau khi embedding đã đa ngữ, nó chỉ còn làm xáo trộn kết quả tiếng Việt.
Mẫu 20 câu là quá nhỏ để kết luận, nên cần đo lại trên tập lớn hơn trước khi quyết định.

## 3. Quyết định giữ, sửa, xóa

### Giữ nguyên

Pipeline xử lý tài liệu: đọc PDF, PPTX, DOCX, HTML, audio và video, chia chunk theo định dạng, nhúng qua gateway. Mọi tài liệu dùng phạm vi bài giảng thông thường; không phát hiện mục lục hoặc sinh theo chương/mục.
Đây là Nhiệm vụ 2 của đề tài.

Truy xuất: dense cộng BM25 cộng RRF với k bằng 60.
Số phận của bước rerank chờ kết quả đo lại.

Sinh 7 thành phần: Planner, Learning Objective, Content, Slides, Example, Quiz, Practice, Explanation, kèm Corrective RAG trước bước Content.
Đây là Nhiệm vụ 3 và là lõi của hệ thống.

Hàng đợi công việc trong Postgres, vì một lần sinh module mất nhiều phút và cần chạy nền.

Tích hợp Moodle và trang giám sát.
Trang giám sát tuy không nằm trong đề tài nhưng có giao diện, demo được, và kể được câu chuyện kỹ thuật về job chạy nền và xử lý lỗi.

Phần lõi xác thực: đăng ký, đăng nhập, refresh token dùng một lần, băm mật khẩu, lọc quyền theo chủ sở hữu khóa học.
Giữ cả giới hạn tốc độ, nhật ký kiểm toán, và Row Level Security ba vai trò Postgres.
Lý do giữ RLS: nó đang chạy tốt, gỡ ra có rủi ro tạo lỗ rò dữ liệu giữa các khóa học, và phòng thủ nhiều lớp cho RAG đa người dùng là một điểm kỹ thuật đáng trình bày khi bảo vệ.

### Sửa

Tính năng chat đổi mục tiêu.
Trước đây nó được viết như hỏi đáp cho người học, nhưng người học không đăng nhập ứng dụng này.
Nó trở thành trợ lý tra cứu cho giảng viên, dùng để tìm xem tài liệu nào nói về chủ đề nào trước khi chọn phạm vi sinh module.
Phần backend giữ nguyên, cần thêm giao diện.

Việc chọn mô hình ngôn ngữ chuyển từ biến môi trường toàn cục sang lựa chọn theo từng lần sinh.
Chi tiết ở mục 5.

Pipeline sinh cần sửa các lỗi đã đo được, mô tả ở mục 7 nhóm E.

### Xóa

Danh sách mật khẩu phổ biến và chính sách mật khẩu phức tạp, thay bằng kiểm tra độ dài tối thiểu.

Ba tệp test hạ tầng `test_production_mode.py`, `test_compose_topology.py`, `test_config_secrets.py`, khoảng 320 dòng.

Giá trị `UserRole.student` vì là mã chết và mâu thuẫn với ràng buộc chỉ giảng viên dùng ứng dụng.

Tệp `schema.sql` ở thư mục gốc, vì `alembic/` mới là nguồn sự thật duy nhất cho schema.

### Thêm mới

Phân hệ nghiên cứu Giai đoạn 2, mô tả ở mục 4.

Nhánh sinh không dùng RAG, để tạo nhóm đối chứng.

Việc chọn mô hình theo từng lần sinh, mô tả ở mục 5.

### Khôi phục

Các thành phần đã mất: `.gitignore` gốc, `.gitattributes`, `README.md`, `docker-compose.yml` gốc dạng include, `devops/.env.example`, `backend/alembic/`, `backend/scripts/`, và plugin `moodle-plugin/local_lms_bridge/`.

## 4. Phân hệ nghiên cứu Giai đoạn 2

Đây là phần mới lớn nhất và là đóng góp nghiên cứu của luận văn.
Vì hệ thống vốn đã phình, phân hệ này bị giới hạn cứng ở ba bảng, một router, và hai trang giao diện.
Mọi thống kê nặng nằm ở script đọc từ cơ sở dữ liệu, không nhét vào ứng dụng.

### Mô hình dữ liệu

Bảng `studies` mô tả một nghiên cứu: tên, phiên bản rubric, trạng thái và danh sách email người chấm được mời.

Bảng `study_items` mô tả một đơn vị đem chấm.
Mỗi hàng gồm nhóm thuộc một trong ba giá trị `human`, `llm_no_rag`, `rag_llm`, một mã ẩn danh dạng M01 hoặc M02, và một gói nội dung tự chứa dạng JSON.
Gói nội dung dùng đúng cấu trúc `ModuleContentInput` đã có sẵn trong `app/generation/evaluation.py`.
Quy tắc tham chiếu module và nạp cặp đã sinh nằm trong [hướng dẫn R18](../studies.md).
Thiết kế tự chứa này đã được tính từ trước trong `evaluation.py` và được giữ nguyên.

Bảng `ratings` lưu một lượt chấm: tham chiếu tới `study_items`, định danh người hoặc mô hình chấm theo [hướng dẫn R18](../studies.md), và kết quả theo [rubric dùng chung](../rubric.md).
Người chấm và mô hình chấm dùng chung một bảng, nhờ đó tính được độ đồng thuận giữa người và mô hình, vốn là một kết quả đáng giá cho chương đánh giá.

Bảng `ratings` tách hẳn khỏi bảng `evaluations` đã có.
Bảng `evaluations` chỉ lưu kết quả đúng hoặc sai của các bước kiểm tra tự động bên trong pipeline sinh, ví dụ `content_groundedness_pass`, gắn với một module cụ thể.
Bảng `ratings` lưu điểm rubric chín tiêu chí do người hoặc mô hình chấm, gắn với một đơn vị nghiên cứu ẩn danh có thể không phải module nào của hệ thống.
Hai loại dữ liệu này khác mục đích nên không gộp.

### Luồng hoạt động

Tạo một nghiên cứu, rồi nạp các đơn vị chấm từ ba nguồn.
Nhóm `rag_llm` lấy từ module có sẵn trong hệ thống.
Nhóm `llm_no_rag` sinh bằng nhánh bỏ qua truy xuất.
Nhóm `human` nhập thủ công.

Mỗi đơn vị được gán mã ẩn danh.
Khi chấm, giao diện chỉ hiện mã ẩn danh và nội dung, không hiện nhóm, và thứ tự được xáo ngẫu nhiên theo từng người chấm.

Mô hình chấm chạy trên cùng các gói nội dung đó và ghi vào cùng bảng `ratings`.

Trang kết quả hiển thị điểm trung bình theo nhóm và độ đồng thuận giữa người và mô hình.
Kiểm định thống kê và khoảng tin cậy nằm ở script riêng.

### Chín tiêu chí rubric

Rubric đã được cài đặt trong `evaluation.py`; định nghĩa tiêu chí nằm trong [rubric dùng chung](../rubric.md).
Bộ tiêu chí này ánh xạ trực tiếp vào Nhiệm vụ 5 của đề tài.

## 5. Chọn mô hình và đường tới khóa API riêng

Hiện nay mô hình được chọn bằng biến môi trường toàn cục `llm_model_dev` và `llm_model_demo` cùng một cờ bật tắt, và mọi hàm sinh đều gọi `complete_structured` không có tham số mô hình.

Thay bằng một đối tượng cấu hình nhỏ truyền vào `run_module_generation` rồi xuống các bước, thay cho việc đọc biến toàn cục.
Cấu trúc của nó là `LLMConfig` gồm `model_alias` và `credential_ref`.

Trước mắt `credential_ref` luôn rỗng, nghĩa là dùng khóa tập trung của hệ thống, và giảng viên chỉ chọn mô hình từ danh sách alias đã cấu hình trong gateway.
Về sau, khi làm tính năng khóa API riêng, `credential_ref` trỏ tới khóa của giảng viên và gateway nhận khóa theo từng yêu cầu.
Thiết kế như vậy để việc thêm khóa riêng là thêm vào chứ không phải viết lại.

Khả năng chọn mô hình có giá trị nghiên cứu chứ không chỉ là tiện ích.
Kế hoạch Giai đoạn 2 có mục tối ưu prompt và mô hình, nên việc đổi mô hình trên cùng tài liệu và cùng prompt biến việc so sánh mô hình thành một chiều của nghiên cứu, chấm bằng cùng rubric.

Khi làm tính năng khóa API riêng sau này, các yêu cầu bắt buộc là: mã hóa khi lưu, không bao giờ trả về phía client ngoài bốn ký tự cuối, không ghi vào log hay nhật ký kiểm toán, và có chức năng xóa.

### Cách đã làm thật, lệch với đoạn trên (2026-09-24)

Lựa chọn mô hình không được truyền qua tham số của `run_module_generation` rồi xuống từng bước, mà đi qua một `ContextVar` trong `app/llm/gateway.py`, bọc một lần ở bước worker.
Lý do: một lượt sinh đi qua mười chỗ gọi LLM rải khắp generation và retrieval, truyền tay thì phải thêm tham số vào chữ ký của cả pipeline chỉ để chuyển tiếp.
Thiết kế này đã được duyệt trong phiên làm Nhóm C.

`ContextVar` hiện chỉ mang alias, chưa phải `LLMConfig`, vì `credential_ref` sẽ luôn rỗng cho tới khi làm khóa riêng và thêm một trường luôn rỗng bây giờ là đoán trước.
Mục tiêu của đoạn trên vẫn giữ nguyên: khi làm khóa riêng, chỉ đổi thứ `ContextVar` mang từ alias thành `LLMConfig`, gói gọn trong `gateway.py` và chỗ bọc ở worker, không đụng tới chữ ký của bước nào.

Hai thứ đã làm thêm so với đoạn trên: giảng viên có một mặc định riêng (`users.default_model_alias`), và mọi model được thử bằng một lệnh một token trước khi xếp hàng lượt sinh hay lưu làm mặc định, vì gateway liệt kê cả những model mà khóa nhà cung cấp đang trống.

## 6. Kiến trúc đích

```
┌─ Frontend (React) ─────────────────────────────────────────┐
│  Courses │ CourseDetail │ ModulePreview (+ giảng viên sửa) │
│  Monitoring │ [MỚI] BlindRating │ [MỚI] StudyResults       │
│  [MỚI] Chat tra cứu cho giảng viên                         │
└────────────────────────────┬───────────────────────────────┘
                             │ REST
┌─ Backend (FastAPI) ────────┴───────────────────────────────┐
│ api/    auth · courses · documents · modules · moodle      │
│         jobs · chat │ [MỚI] studies                        │
├────────────────────────────────────────────────────────────┤
│ ingestion/   readers → chunker → embeddings                │
│ retrieval/   dense + BM25 + RRF ( + rerank: chờ đo lại )   │
│ generation/  planner → LO → content → slides → example     │
│                     → quiz/practice → explanation          │
│              [MỚI] nhánh sinh không RAG                    │
│ study/       [MỚI] packet · rating · llm_judge             │
│ chat/        trợ lý tra cứu cho giảng viên                 │
│ moodle/      client xuất bản                               │
│ core/        config · db · llm gateway · job queue         │
├────────────────────────────────────────────────────────────┤
│ Postgres + pgvector, RLS ba vai trò                        │
│   giữ:  users courses documents chunks                     │
│         modules module_components questions evaluations    │
│         jobs refresh_tokens moodle_course_claims chat_*    │
│   MỚI:  studies · study_items · ratings                    │
└───────────┬───────────────────────────────┬────────────────┘
            │                               │
     LiteLLM gateway                 Moodle + local_lms_bridge
     (khóa API tập trung)            (phải viết lại)
```

## 7. Cấu trúc thư mục đích

```
do-an/
├── .gitignore  .gitattributes  README.md
├── docker-compose.yml                    (file include, khôi phục)
├── .github/workflows/ci.yml
│
├── docs/
│   ├── de-tai.md                         (GenerativeAI.md dời vào)
│   ├── specs/                            (tài liệu thiết kế)
│   └── references/                       (3 tài liệu nghiên cứu)
│
├── backend/
│   ├── app/
│   │   ├── api/          router HTTP, mỏng, tập trung một chỗ
│   │   ├── core/         config · db · llm · job_queue · middleware · logging
│   │   ├── auth/  ingestion/  retrieval/  generation/  models/
│   │   ├── study/        phân hệ nghiên cứu
│   │   ├── chat/         tách từ services/chat.py
│   │   └── moodle/       tách từ services/moodle_client.py
│   ├── alembic/  scripts/                (khôi phục)
│   ├── docker/           Dockerfile · entrypoint · pgvector.sql
│   └── tests/
│
├── frontend/src/
│   ├── api/              client cộng một tệp mỗi miền, tách từ api.ts
│   └── pages/  components/  auth/  lib/
│
├── devops/
│   ├── docker-compose.yml  .env.example
│   └── caddy/  gateway/  moodle/  scripts/
│
└── moodle-plugin/local_lms_bridge/       (khôi phục)
```

### Những thứ ra khỏi kho mã

Thư mục `moodle/` là sản phẩm bên thứ ba nặng 1,42 GB và không phải mã của dự án.
Nó được gitignore, README ghi bước tải về và giải nén, và đường mount trong compose được sửa lại cho đúng tên thư mục.

Thư mục `documents/` nặng 151 MB gồm tài liệu môn học dùng để thử nghiệm.
Nó được gitignore.
Nếu kiểm thử tích hợp cần dữ liệu thật thì giữ vài tệp nhỏ trong `backend/tests/integration/fixtures/`.

Ngoài ra `.venv/`, `node_modules/`, `dist/`, `uploads/`, và `__pycache__/` đều được gitignore.

### Lý do tách `services/`

Thư mục `services/` hiện chứa bốn thứ khác loại: `chat.py` là một tính năng, `moodle_client.py` là tích hợp bên ngoài, còn `llm.py`, `job_queue.py`, `maintenance.py` là hạ tầng dùng chung.
Sau khi tách thành `chat/`, `moodle/` và `core/`, mỗi thư mục có đúng một lý do tồn tại.
Vì lịch sử git đã mất nên việc dời tệp lúc này không làm mất thông tin gì.

Thư mục `api/` giữ tập trung thay vì đưa router vào từng tính năng, để nhìn một chỗ là thấy toàn bộ bề mặt HTTP.

## 8. Danh sách tính năng theo thứ tự

Mỗi dòng là một nhánh và một merge request.

### Tình trạng tới 2026-09-24

Nhóm A và B đã gộp vào `main`.
Nhóm C đã xong `feat/model-selection`, chưa gộp; `feat/no-rag-generation` chưa làm.
Nhóm E đang đo `rerank-benchmark` (tên nhánh thật là `feat/rerank-benchmark`).
Nhóm D và F chưa bắt đầu.

Các nhánh sửa lỗi phát sinh ngoài danh sách dưới đây, đều tách từ `main`: `fix/ci-stale-paths` (đã gộp), `chore/ci-action-versions`, `fix/env-duplicate-guard`, `chore/container-names`, `fix/rotate-secrets-env-names`, `fix/migrate-secret-key-noise`, `fix/backend-reload-watcher`, `fix/mobile-layout`.

Database dev đang ở revision `0003` của `feat/model-selection`, nên cho tới khi nhánh đó vào `main` thì `migrate` thất bại trên mọi nhánh tách từ `main`, và backend chờ `migrate` sẽ không khởi động qua `docker compose up`.
Gộp `feat/model-selection` trước để gỡ chuyện này.

### Nhóm A, nền móng

Nhóm này đang chặn mọi việc khác.

| Nhánh | Việc | Lý do ưu tiên |
| --- | --- | --- |
| `chore/git-init` | Tạo `.gitignore` và `.gitattributes` gốc, khởi tạo kho, kiểm tra không lọt khóa, commit nền | Chặn rò khóa API; chưa có git thì chưa có merge request nào |
| `fix/restore-alembic` | Sinh một migration khởi tạo duy nhất từ các mô hình SQLAlchemy hiện có, đối chiếu từng bảng và từng cột với schema thật trong volume `do-an_db_data`, rồi chèn bản ghi phiên bản tương ứng để dữ liệu đang có không bị migrate lại từ đầu | CI chạy `alembic upgrade head` nên đang đỏ |
| `fix/restore-moodle-plugin` | Viết lại `local_lms_bridge` gồm `create_book` và `create_quiz`, sửa đường mount Moodle trong compose | Moodle là đường duy nhất tới người học |

### Nhóm B, dọn phình

| Nhánh | Việc |
| --- | --- |
| `refactor/trim-auth` | Bỏ danh sách mật khẩu phổ biến và chính sách phức tạp, thay bằng độ dài tối thiểu; bỏ vai trò `student`; bỏ ba tệp test hạ tầng |
| `refactor/folder-structure` | Tách `services/` thành `core/`, `chat/`, `moodle/`; gom `backend/docker/`; tách `frontend/src/api.ts`; dời tài liệu vào `docs/` |

### Nhóm C, nền cho nghiên cứu

| Nhánh | Việc |
| --- | --- |
| `feat/model-selection` | Luồn `LLMConfig` qua pipeline thay biến toàn cục, API liệt kê mô hình, ô chọn mô hình khi sinh module |
| `feat/no-rag-generation` | Nhánh sinh bỏ qua truy xuất, tạo nhóm đối chứng `llm_no_rag` |

### Nhóm D, phân hệ nghiên cứu

| Nhánh | Việc |
| --- | --- |
| `feat/study-model` | Ba bảng `studies`, `study_items`, `ratings`, kèm migration và API tạo nghiên cứu, nạp gói ba nhóm |
| `feat/blind-rating-ui` | Trang chấm mù chín tiêu chí, xáo thứ tự theo từng người chấm |
| `feat/llm-judge` | Nối `evaluation.py` vào phân hệ nghiên cứu, ghi điểm vào bảng `ratings` |
| `feat/study-results` | Trang kết quả theo nhóm và độ đồng thuận người với mô hình, cùng script thống kê |

### Nhóm E, chất lượng nội dung sinh

| Nhánh | Việc |
| --- | --- |
| `chore/rerank-benchmark` | Đo lại reranker trên tập lớn hơn rồi quyết định giữ, bỏ, hay đổi sang mô hình đa ngữ |
| `fix/generation-defects` | Practice phải khác Quiz; cân bằng độ dài phương án; không lưu im lặng khi bước kiểm tra trượt; dọn nhãn nội bộ trong trường `reasoning` |
| `feat/module-per-concept` | Mỗi khái niệm của Planner thành một module, giảng viên tích chọn khái niệm. Đây là góp ý của giảng viên hướng dẫn. Tùy chọn, làm nếu còn thời gian |

### Nhóm F, phần còn lại của Giai đoạn 2

| Nhánh | Việc |
| --- | --- |
| `feat/teacher-chat` | Giao diện chat tra cứu tài liệu cho giảng viên |
| `feat/teacher-editing` | Hoàn thiện việc giảng viên chỉnh sửa nội dung AI, hiện mới có sửa tiêu đề và nội dung thành phần |
| `feat/byok` | Giảng viên tự thêm khóa API. Làm sau cùng, theo các yêu cầu bảo mật ở mục 5 |

## 9. Quy trình git

Nhánh `main` luôn phải xanh và không nhận commit trực tiếp.
Mỗi tính năng làm trên một nhánh riêng theo tiền tố `feat/`, `fix/`, `refactor/`, hoặc `chore/`.
Khi xong thì mở merge request, CI xanh mới được gộp, và gộp theo kiểu squash để mỗi tính năng tương ứng một commit trên `main`.

### Định nghĩa một tính năng là hoàn chỉnh

Kiểm thử được viết trước, theo hợp đồng của tính năng chứ không theo phần cài đặt đã viết.

Lệnh `ruff check` sạch và toàn bộ `pytest` xanh, không chỉ riêng test của tính năng đó.

Có migration kèm theo nếu thay đổi schema.

Tính năng người dùng nhìn thấy được thì phải kiểm thử thật bằng trình duyệt, không chỉ test đơn vị.

Thông điệp commit theo Conventional Commits, và mô tả merge request ghi rõ làm gì, vì sao, và đã kiểm thử thế nào.

## 10. Rủi ro và ràng buộc

Khóa API trong `devops/.env` hiện không được `.gitignore` nào che.
Nếu khởi tạo kho và thêm tất cả tệp thì commit đầu tiên sẽ khoảng 106.000 tệp, 2,8 GB, kèm khóa thật.
Việc đầu tiên của `chore/git-init` vì vậy là tạo `.gitignore` rồi kiểm tra `git status` trước khi commit.

Hạn mức gói miễn phí của Gemini là 1.000 yêu cầu nhúng mỗi ngày tính theo từng chunk, và 20 yêu cầu sinh mỗi ngày với các mô hình thế hệ mới.
Hạn mức này đủ cho phát triển hằng ngày nhưng không đủ để nạp một tài liệu lớn hoặc chạy nhiều nhánh thí nghiệm trong cùng một ngày.
Nếu cần chạy nghiên cứu quy mô lớn thì phải bật thanh toán.

Số đo reranker hiện chỉ dựa trên 20 câu hỏi, nên quyết định giữ hay bỏ phải chờ lần đo lại.

## 11. Ngoài phạm vi

Những việc sau được ghi nhận là hướng phát triển nhưng không làm trong đợt này.

Học viên đăng nhập vào ứng dụng này; họ chỉ học trên Moodle.

Plugin chat trong Moodle cho người học.

RAPTOR, tức cây tóm tắt phân cấp cho truy xuất đa tầng.

Siêu dữ liệu phân quyền theo vai trò ở mức chunk.

Kiểm định thống kê nặng nằm trong ứng dụng; phần này để ở script.

Kéo dữ liệu làm bài của học viên từ Moodle về để phân tích kết quả học tập.
