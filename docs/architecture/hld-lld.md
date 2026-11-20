# HLD và LLD

Trạng thái: cập nhật 2026-11-20, khớp với main sau PR #185.
Thay bộ ảnh PNG ngày 05-06/11 trong `hld-lld/`, vốn thiếu flashcard, video, SCORM, trang học viên, OpenRouter, đồng giảng viên và hồ sơ production.
Hình viết bằng Mermaid để GitHub tự vẽ và sửa được bằng tay; script vẽ ảnh cũ đã mất.
Số đo nằm trong report (mục Results), tài liệu này chỉ mô tả thiết kế.

## 1. HLD

```mermaid
flowchart LR
  subgraph Clients
    L[Giảng viên<br/>SPA React]
    S[Sinh viên<br/>/learn qua LTI]
    A[Quản trị viên]
    M[Moodle 5.2<br/>LTI 1.3 + plugin bridge]
  end
  CF[CloudFront] --> FE[S3 frontend]
  CF --> ALB[ALB] --> API
  L & S & A --> CF
  M -- LTI launch --> CF
  subgraph ECS[ECS Fargate]
    API[api<br/>REST, SSE chat, LTI]
    W[worker<br/>job queue]
    GW[llm-gateway<br/>LiteLLM, min 2 task]
    EMB[embedder<br/>TEI Qwen3-0.6B]
    RR[reranker Jina<br/>chỉ khi chọn jina]
  end
  API & W --> DB[(RDS PostgreSQL 16<br/>pgvector, RLS, queue)]
  API --> VK[(Valkey<br/>rate limit)]
  API & W --> S3[(S3<br/>uploads/, media/, study-files/)]
  API & W --> GW --> OR[OpenRouter<br/>chat, judge, TTS, ảnh, Whisper]
  GW --> EMB
  API & W -. mặc định .-> JEV[Jev rerank API]
  API & W -. tùy chọn .-> RR
  W -- Book, Quiz, NRPS --> M
```

Kiểu kiến trúc: modular monolith.
Một image backend chạy thành hai service với tín hiệu scale khác nhau: `api` theo request và CPU, `worker` theo số job đang chờ (`JobsReady`).
Mọi service đều stateless: dữ liệu, vector và hàng đợi ở Postgres, file ở S3, bộ đếm rate limit ở Valkey (mất cũng không sao).
Mọi lệnh gọi model đi qua gateway, chỉ gateway giữ khóa OpenRouter; reranker là ngoại lệ, gọi thẳng từ backend.

### Hai hồ sơ triển khai

| | Demo (`terraform.tfvars`, mặc định) | Production (`infra/environments/production.tfvars`) |
| --- | --- | --- |
| Mục tiêu | Chạy khi cần đo hoặc demo, vừa $200 credit | Khoảng 30.000 người dùng, 10-20% online cùng lúc |
| api | 1-4 task | 2-12 task, 1 vCPU, 2 GB |
| worker | 1-8 task, 4 job mỗi task | 2-12 task, 1 vCPU, 3 GB, 4 job mỗi task |
| gateway | 2-4 task, scale theo CPU | 2-6 task, 1 vCPU |
| embedder | 1-2 task, 4 vCPU, 8 GB | 1-3 task |
| RDS | `db.t4g.medium`, single-AZ, backup 1 ngày | `db.m7g.large`, Multi-AZ, chống xóa, backup 14 ngày |
| Kết nối DB khi tải đầy | 349/407 | 653/897 (Terraform check chặn nếu vượt) |

Hồ sơ production chưa được apply và chưa load test; nó được `terraform test` kiểm tra trên AWS giả lập trong CI.
Quá 12 api và 12 worker, bước tiếp theo là PgBouncer ở transaction mode (`scalable-design.md`, mục 7).

## 2. LLD theo module

### 2.1 API và phân quyền

```mermaid
flowchart TB
  R[Request] --> MW[Concurrency limit<br/>≤ pool DB]
  MW --> AUTH[JWT access token<br/>refresh token trong DB]
  AUTH --> DEP{Kiểm tra trong code}
  DEP -->|tác giả: owner hoặc teacher| AUTHOR[documents, modules,<br/>flashcards, videos, jobs]
  DEP -->|chỉ owner| OWN[đổi tên, xóa môn,<br/>liên kết Moodle]
  DEP -->|member learner| LEARN["/learn: đọc, chat riêng"]
  DEP -->|admin| ADM[tài khoản, job lỗi,<br/>nghiên cứu, erase_user]
  AUTHOR & OWN & LEARN & ADM --> RLS[(RLS trong Postgres<br/>app.current_user_id)]
```

Mỗi request chạy bằng role `app_runtime`, đặt `app.current_user_id` cho phiên; RLS lọc lại ở tầng DB nên một lỗi kiểm tra trong code không lộ dữ liệu tenant khác.
`app_authored_course_ids()` (migration 0049) trả các môn mình sở hữu cộng các môn mình là `teacher`; mọi policy của documents, chunks, modules, jobs, flashcard_sets, videos dùng nó.
Không có quyền thì trả 404, không phải 403, để một id không tự xác nhận là có tồn tại; riêng đồng giảng viên chạm vào việc chỉ owner được làm thì nhận 403 có lý do.
Thao tác admin xuyên tenant đi qua hàm `SECURITY DEFINER` có kiểm tra `current_user_is_admin()`.
`erase_user` (0048) xóa email, tên, mật khẩu, liên kết Moodle, chat, membership, phiên đăng nhập và tên trong danh sách người chấm; giữ dòng user để lượt chấm còn khóa ngoại; từ chối khi người đó còn sở hữu môn học.

### 2.2 LTI 1.3 và người trong môn

```mermaid
sequenceDiagram
  participant M as Moodle
  participant API as api
  participant DB as Postgres
  M->>API: /lti/login (OIDC)
  API->>DB: lưu state, nonce (10 phút)
  API-->>M: redirect tới auth Moodle
  M->>API: /lti/launch id_token
  API->>API: kiểm chữ ký JWKS, iss, aud, nonce, deployment
  alt Instructor, môn chưa mở
    API->>DB: tạo course, lti_context, chủ sở hữu
  else Instructor, môn đã có chủ khác
    API->>DB: course_members role=teacher
  else Learner
    API->>DB: course_members role=learner
  end
  API-->>M: redirect về SPA, code trong fragment
  Note over API: SPA đổi code lấy token, course_role owner hoặc member
```

Vai trò do Moodle ký, nên sinh viên được Moodle chuyển thành instructor (trợ giảng) sẽ thành `teacher`.
Danh sách lớp đồng bộ qua NRPS trong job của worker.

### 2.3 Hàng đợi job

```mermaid
stateDiagram-v2
  [*] --> queued: enqueue cùng transaction với dữ liệu
  queued --> running: FOR UPDATE SKIP LOCKED
  running --> done
  running --> queued: lỗi tạm thời, backoff 30-240 s
  running --> queued: mất 5 heartbeat (sweeper) hoặc task dừng
  running --> failed: hết 5 lần hoặc lỗi vĩnh viễn
  failed --> queued: Thử lại (giảng viên hoặc admin)
```

Bảy loại job: `document_pipeline`, `module_generation`, `flashcard_generation`, `video_script`, `video_images`, `video_render`, `study_judge`.
`handle_job_error` đánh dấu job và đối tượng của nó (document, set, video) thất bại trong một transaction.
Worker xin ECS scale-in protection khi đang có job; khi bị dừng, nó trả job về hàng đợi sau 100 s mà không tính một lần thử.

### 2.4 Nạp tài liệu

```mermaid
flowchart LR
  U[Trình duyệt] -->|B1: tạo document| API
  U -->|B2: presigned POST| S3[(S3 uploads/)]
  U -->|B3: báo xong| API --> Q[job document_pipeline]
  Q --> X[extract<br/>PDF: PyMuPDF + OCR + đọc lại công thức<br/>PPTX: slide, bảng, notes<br/>audio/video: Whisper 600 s]
  X --> C[chunk theo cấu trúc<br/>340 token, có locator]
  C --> E[embed qua gateway<br/>embed-default]
  E --> DB[(chunks + vector)]
  E --> D[xóa file gốc]
```

### 2.5 Truy hồi

```mermaid
flowchart LR
  Q[Câu hỏi] --> DENSE[pgvector cosine]
  Q --> BM[BM25<br/>cache theo scope]
  DENSE & BM --> RRF[RRF k=60]
  RRF --> RR{Jev hoặc Jina}
  RR -->|lỗi| RRF2[giữ thứ tự hybrid]
  RR --> K[adaptive-k, sàn 8<br/>hoặc k cố định]
  K --> N[+ 2 chunk lân cận<br/>cho Content]
```

Phạm vi: module trong một tài liệu, chat trong cả môn.

### 2.6 Sinh mô-đun

```mermaid
flowchart TB
  P[planner: 8 khái niệm<br/>cache theo tài liệu] --> LO[LO + Bloom + sub-objectives]
  LO --> RET[truy hồi + relevance check<br/>viết lại query 1 lần]
  RET --> CT["Content có [S#]"]
  CT --> SC{tự kiểm}
  SC -->|trượt| CT
  SC --> SL[Slides, Example]
  SC --> QZ[Practice, Quiz<br/>5 câu song song]
  QZ --> SV{kiểm trên server<br/>miễn phí}
  SV -->|trượt, ≤ 2 lần| QZ
  SV --> QC{tự kiểm của model}
  QC --> EXP[Explanation gom từ câu hỏi]
  EXP --> MOD[(module + evaluations)]
```

Mỗi mô-đun khoảng 14-25 lệnh gọi model.
Điều kiện no-RAG của nghiên cứu chạy cùng các bước, bỏ khối nguồn; mô-đun đối chứng không xuất bản được.
Giới hạn còn lại: job không có checkpoint giữa các bước, nên chạy lại sau khi bị dừng sẽ gọi lại model đã trả tiền.

### 2.7 Flashcard và video

```mermaid
flowchart TB
  F[focus + tài liệu] --> FP[focus_passages]
  FP --> FG[flashcard_generation<br/>model sinh thẻ]
  FG --> FC[fact check bằng model<br/>khác họ]
  FC --> FS[(flashcard_set<br/>thẻ bị nghi được gắn cờ, không xóa)]
  FP --> VS["video_script<br/>6-9 cảnh, câu nào cũng phải có [S#]"]
  VS --> VC[fact check khác họ]
  VC --> VE[giảng viên sửa kịch bản]
  VE --> VI[video_images<br/>≤ 5 ảnh minh họa, chỉ vẽ lại cảnh đổi]
  VE --> VR[video_render]
  VI --> VR
  VR --> TTS[TTS mỗi cảnh<br/>giữ giọng nếu lời không đổi]
  VR --> SLD[slide HTML + KaTeX<br/>Chromium chụp PNG, không mạng]
  TTS & SLD --> FF[ffmpeg ghép MP4 + phụ đề<br/>trần 8 phút]
  FF --> S3[(S3 media/)]
```

Ảnh, giọng và chữ tách nhau: model ảnh chỉ vẽ hình, chữ trên slide do hệ thống in từ kịch bản nên luôn đúng chính tả và khớp nguồn.
Ảnh và giọng đã trả tiền được giữ khi một cảnh khác lỗi; job lỗi nêu tên cảnh lỗi.
Trình duyệt hỏi lại slide đã có sẽ nhận 304 theo ETag, không render lại.

### 2.8 Chat

```mermaid
sequenceDiagram
  participant B as Trình duyệt
  participant API as api
  participant DB as Postgres
  participant GW as gateway
  B->>API: POST tin nhắn
  API->>DB: lưu câu hỏi, commit, đóng session
  API->>GW: viết lại câu hỏi nối tiếp
  API->>API: truy hồi (≤ 8 song song), rerank (≤ 2 lần)
  API->>GW: sinh câu trả lời (stream)
  API-->>B: SSE session, delta..., done
  API->>DB: lưu câu trả lời + nguồn từ marker S
```

Không giữ kết nối DB khi chờ model; `chat_max_streams` trả 503 trước khi task quá tải.
Sinh viên có trần chat mỗi ngày; giảng viên không đọc chat của sinh viên.

### 2.9 Xuất bản

```mermaid
flowchart LR
  MOD[(module)] --> LES[lesson.py<br/>Markdown → HTML, lọc bằng nh3]
  LES --> BOOK[Moodle Book + Quiz<br/>qua plugin bridge, cần liên kết Moodle]
  LES --> SCORM[gói SCORM 1.2<br/>một SCO, chấm điểm trong trang]
  SCORM --> ANY[LMS bất kỳ]
```

### 2.10 Nghiên cứu chấm điểm

```mermaid
flowchart LR
  ADM[Admin] --> ST[study]
  ST --> PAIR[cặp RAG / no-RAG<br/>mã ẩn danh, trộn lại]
  ST --> MED[gói flashcard / video<br/>chụp lúc thêm, video được sao chép<br/>sang study-files/video-copies/]
  ST --> REF[tài liệu gốc cho người chấm]
  PAIR & MED --> RAT[người chấm: chấm mù theo rubric]
  PAIR & MED --> JUD[study_judge: model chấm]
  RAT & JUD --> RES[kết quả, xuất CSV]
```

Gói là ảnh chụp tại lúc thêm, nên giảng viên sửa, dựng lại hay xóa bản gốc không đổi thứ đã được chấm.

## 3. Còn thiếu so với kiến trúc đầy đủ

- Job sinh mô-đun chưa có checkpoint giữa các bước.
- Render video dùng chung worker (1 vCPU, 3 GB) với các job khác; tách một worker render riêng khi lượng video tăng.
- Chưa có PgBouncer; cần khi vượt 12 api và 12 worker.
- Hồ sơ production chưa được apply và chưa load test.
- Chưa có giao diện nhập mô-đun mẫu do giảng viên viết (nhóm `human` đã có ở backend).
