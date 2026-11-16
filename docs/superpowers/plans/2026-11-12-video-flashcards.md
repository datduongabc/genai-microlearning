# Video giọng AI và flashcard: kế hoạch triển khai

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Giảng viên sinh, duyệt và giao video thuyết minh giọng AI (slide kiểu NotebookLM) và bộ flashcard từ một tài liệu với một trọng tâm; sinh viên xem chúng trên trang `/learn` mở từ Moodle qua LTI.

**Architecture:** Hai bảng mới (`flashcard_sets`, `videos`) thuộc môn học, có RLS cho chủ môn học và cho thành viên (chỉ bản đã giao). Sinh, vẽ hình và dựng là job nền trên hàng đợi Postgres hiện có. Giọng và ảnh đi qua gateway như mọi model khác; slide là HTML có KaTeX chụp bằng Chromium headless trong worker, ghép với giọng bằng ffmpeg.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Postgres RLS, LiteLLM gateway, OpenRouter, Playwright (Chromium) trong image worker, ffmpeg, React + Vite + vitest, Terraform.

**Spec:** `docs/superpowers/specs/2026-11-12-video-flashcards-design.md`. Code dựng thử đã chạy được, dùng làm tham khảo (không chép nguyên): `make_script.py` và `render.py` trong `/tmp/claude-1000/-home-asus-projects-do-an/c872dd36-d02f-4f02-9fad-baac1b4dade9/scratchpad/video/`.

## Global Constraints

- Mọi model gọi qua gateway với một khóa OpenRouter; mỗi alias một nhà cung cấp, `allow_fallbacks: false`, `data_collection: deny`. Khóa không bao giờ ra khỏi container gateway.
- Model giọng: `heygen/heygen-voice-1-turbo` (mặc định, giọng `nadine`, trả `pcm` 44,1 kHz), `google/gemini-3.8-flash-lite-tts` và `google/gemini-3.8-flash-tts` (giọng `Kore`, chỉ `pcm` 24 kHz). Không dùng bản `:free`. Đọc tần số từ header `content-type` (`audio/pcm;rate=...`).
- Model ảnh: `google/gemini-nano-banana-2.1` (mặc định; `POST /chat/completions` với `modalities: ["image","text"]`) và `qwen/qwen-image-2.1-turbo` (chỉ `POST /images`). Tỉ lệ 4:3 cho hình trong slide.
- Model nào không chạy được với `data_collection: deny` thì dừng và báo người dùng, không tự bỏ hay thay.
- Gọi model trả phí chỉ trong các bước ghi rõ "trả phí"; trước mỗi bước đó hỏi người dùng (`working-rules`: không tự tốn tiền). Test tự động không gọi model thật.
- Video: nhắm khoảng 5 phút (4 đến 6 phút), 8 đến 10 cảnh, chặn cứng 8 phút; ước độ dài 15 ký tự lời thoại mỗi giây.
- Tối đa 5 cảnh loại `illustration` mỗi video; các loại cảnh: `title`, `illustration`, `formula`, `code`, `table`, `summary`.
- Flashcard: tối đa 15 thẻ mỗi bộ, không có hình.
- Ngôn ngữ đầu ra `vi` hoặc `en`, như mô-đun; trọng tâm tối đa 1000 ký tự, như `custom_instructions`.
- Câu do model viết phải có trích nguồn; câu giảng viên viết được lưu dù không trích nguồn, kèm cờ cảnh báo.
- Sinh viên chỉ thấy bản đã giao (`published_at IS NOT NULL`) của môn mình là thành viên.
- Ghi kết quả job qua `hold()` (`app/jobs/queue.py`); không giữ transaction trong lúc gọi model.
- Chữ trong mã, commit và tài liệu: không dùng dấu gạch dài; comment giải thích lý do; commit theo conventional commits, không thêm co-author của agent.
- Mỗi PR kèm bằng chứng: kết quả test và ảnh chụp giao diện thật.

## Review Focus

1. Giảng viên sửa hoặc dựng lại một video đã giao: sinh viên vẫn xem bản cũ cho tới khi bản mới dựng xong (Task 10).
2. Model ảnh trả lời không có ảnh, hoặc 404 vì không còn nhà cung cấp đạt `data_collection: deny`: cảnh giữ mô tả hình, job báo lỗi rõ, cảnh khác không mất (Task 8).
3. Lời thoại còn ký hiệu LaTeX hoặc dấu gạch dưới (`\frac`, `P_1`) đi vào TTS hoặc vào nhãn hình: bị chuẩn hóa hoặc bị đánh dấu trước khi dựng (Task 7).
4. Tài liệu bị xóa khi đã có video hoặc bộ thẻ: bản ghi bị xóa theo và tệp ảnh, video trong kho cũng bị xóa (Task 2).
5. Sinh viên khác môn, hoặc cùng môn nhưng bản chưa giao, đoán được id video: API trả 404 và không cấp link tệp (Task 6, Task 12).

---

### Task 1: Gọi giọng và ảnh qua gateway

**Files:**
- Modify: `devops/gateway/config.yaml` (thêm 5 alias)
- Create: `backend/app/llm/media.py`
- Create: `backend/scripts/check_media_models.py`
- Test: `backend/tests/unit/test_media_client.py`

**Interfaces:**
- Produces:
  - Alias: `voice-heygen`, `voice-gemini-lite`, `voice-gemini-flash`, `image-nano-banana`, `image-qwen`.
  - `VOICES: dict[str, VoiceModel]` và `IMAGE_MODELS: dict[str, ImageModel]` (tên hiển thị, giọng mặc định, giá tham khảo), `DEFAULT_VOICE = "voice-heygen"`, `DEFAULT_IMAGE_MODEL = "image-nano-banana"`.
  - `synthesize_speech(alias: str, text: str) -> Speech` với `Speech(pcm: bytes, sample_rate: int, served_model: str, cost: float | None)`.
  - `generate_image(alias: str, prompt: str, aspect_ratio: str = "4:3") -> GeneratedImage` với `GeneratedImage(data: bytes, mime: str, served_model: str, cost: float | None)`.
  - `MediaError(Exception)` cho trả lời không có âm thanh hoặc ảnh (lỗi tạm, job thử lại); lỗi 4xx từ nhà cung cấp là `ValueError` (job dừng, không thử lại).

- [ ] **Step 1: Viết test hỏng** trong `test_media_client.py`, thay HTTP bằng `httpx.MockTransport`:
  - `test_speech_reads_the_sample_rate_from_the_content_type`: trả lời `audio/pcm;rate=44100;channels=1` thì `Speech.sample_rate == 44100`; `rate=24000` thì 24000.
  - `test_speech_request_names_the_alias_and_the_alias_voice`: thân request có `model == "voice-heygen"`, `voice == "nadine"`, `response_format == "pcm"`.
  - `test_image_from_chat_completions_is_decoded`: thân trả lời `choices[0].message.images[0].image_url.url = "data:image/jpeg;base64,..."` cho ra đúng byte, `mime == "image/jpeg"`, `cost` lấy từ `usage.cost`.
  - `test_image_from_images_endpoint_is_decoded` cho `image-qwen` (trả `data[0].b64_json`).
  - `test_a_reply_without_an_image_is_a_media_error`.
  - `test_a_provider_refusal_is_not_retried`: 404 thì ném `ValueError` có thông điệp của nhà cung cấp.
- [ ] **Step 2: Chạy** `cd backend && uv run pytest tests/unit/test_media_client.py -q`. Kỳ vọng: FAIL, không import được `app.llm.media`.
- [ ] **Step 3: Thêm alias vào gateway.** Thử trước cách gốc của LiteLLM (`mode: audio_speech` và `mode: image_generation` với `openrouter/...`, `extra_body.provider: {order: [<tag>], allow_fallbacks: false, data_collection: deny}`; tag: `heygen`, `google-ai-studio`, `alibaba`). Nếu LiteLLM 1.100.1 không chuyển được tới `/audio/speech` hoặc `/images` của OpenRouter, dùng `pass_through_endpoints` của LiteLLM để chuyển `/openrouter/*` tới `https://openrouter.ai/api/v1` có gắn khóa. Khi đó bảng alias, tag nhà cung cấp và `data_collection: deny` nằm trong `media.py` và được test. Ghi trong comment của config vì sao chọn cách đó.
- [ ] **Step 4: Viết `app/llm/media.py`** theo Interfaces, gọi `settings.llm_gateway_url` bằng `settings.llm_gateway_key` như `app/llm/gateway.py`.
- [ ] **Step 5: Chạy lại test.** Kỳ vọng: PASS.
- [ ] **Step 6: Viết `scripts/check_media_models.py`.** Script gọi mỗi alias một lần qua gateway đang chạy (một câu ngắn; một hình nhỏ), in model phục vụ, tần số hoặc định dạng, chi phí, và thoát mã khác 0 nếu alias nào lỗi.
- [ ] **Step 7 (trả phí, khoảng $0,07, hỏi người dùng trước):** `docker compose -f devops/docker-compose.yml up -d --build gateway` rồi `docker exec do-an-worker /app/.venv/bin/python /app/scripts/check_media_models.py`. Kỳ vọng: 5 dòng OK. Alias nào báo không còn nhà cung cấp đạt `data_collection: deny` thì dừng và báo người dùng.
- [ ] **Step 8: Commit** `feat(llm): voice and image models through the gateway`.

### Task 2: Bảng, RLS, kho tệp và loại job

**Files:**
- Create: `backend/alembic/versions/0032_videos_flashcards.py`
- Create: `backend/app/models/media.py` (export trong `app/models/__init__.py`)
- Modify: `backend/app/models/models.py` (`JobType`), `backend/app/storage.py`, `backend/app/config.py`, `backend/app/api/documents.py` (xóa tệp khi xóa tài liệu), `infra/terraform/iam.tf`
- Test: `backend/tests/integration/test_media_rls.py`, `backend/tests/integration/test_media_storage.py`

**Interfaces:**
- Produces:
  - `FlashcardSet`: `id`, `course_id`, `document_id` (FK tổ hợp như `Module`, `ondelete=CASCADE`), `focus: str | None`, `output_language`, `model_alias`, `status: MediaStatus`, `cards: list[dict]` JSONB (`front`, `back`, `citations: list[str]` id chunk, `author: "model"|"lecturer"`, `flag: str | None`), `published_at`, `created_at`, `updated_at`.
  - `Video`: như trên, cộng thêm `voice_alias`, `image_alias`, `scenes: list[dict]` JSONB (Task 7 định nghĩa), `draft_version: int`, `rendered_version: int | None`, `video_key: str | None`, `duration_seconds: float | None`, `cost: dict` JSONB (`script`, `images`, `voice`), `served_models: list` JSONB, `error: str | None`.
  - `MediaStatus` enum: `generating`, `draft`, `drawing`, `rendering`, `ready`, `failed`.
  - `JobType` thêm `flashcard_generation`, `video_script`, `video_images`, `video_render`.
  - `media_store() -> UploadStore`: S3 tiền tố `settings.media_s3_prefix = "media/"`, local `uploads/media`. Có thêm `presigned_get(key, filename) -> str`, như `study_file_store`.
  - `delete_media_for_document(db, document_id) -> None`: xóa tệp ảnh và video của mọi `Video` thuộc tài liệu.

- [ ] **Step 1: Viết test hỏng** `test_media_rls.py` theo mẫu `test_cross_tenant_access.py`:
  - `test_owner_reads_and_writes_their_course_videos_and_sets`.
  - `test_another_lecturer_sees_nothing`.
  - `test_a_member_sees_only_published_items_of_their_course`: `published_at` NULL thì không thấy, có giá trị thì thấy; thành viên không `UPDATE` được.
  - `test_a_member_of_another_course_sees_nothing`.
  - `test_deleting_the_document_deletes_its_videos_and_sets`.
  - `test_media_storage.py::test_deleting_a_document_removes_its_media_files`: tạo tệp giả dưới `media_store()`, xóa tài liệu qua API, tệp không còn. Đây là Review Focus 4.
- [ ] **Step 2: Chạy** `uv run pytest tests/integration/test_media_rls.py tests/integration/test_media_storage.py -q`. Kỳ vọng: FAIL.
- [ ] **Step 3: Viết migration 0032.**
  - Các giá trị `ALTER TYPE jobtype ADD VALUE` chạy trong `autocommit_block` như `0020_study_judge.py`.
  - Policy chủ môn học theo mẫu `0018`; policy thành viên `SELECT` với `course_id IN (SELECT app_member_course_ids()) AND published_at IS NOT NULL`, như `0027`.
  - Policy `worker_processes_every_tenant` cho `app_worker`; quyền `SELECT, INSERT, UPDATE` cho worker.
  - `downgrade` xóa bảng và policy; giá trị enum ở lại, như 0020.
- [ ] **Step 4: Viết model, `media_store`, `delete_media_for_document`.** Gọi hàm xóa trong luồng xóa tài liệu ở `api/documents.py`, cạnh chỗ xóa tệp tải lên. Thêm `media/*` vào hai statement S3 của `iam.tf` cạnh `study-files/*`.
- [ ] **Step 5: Chạy** `uv run alembic upgrade head`, test trên, rồi `cd infra/terraform && terraform test`. Kỳ vọng: PASS.
- [ ] **Step 6: Commit** `feat: tables, row level security and storage for videos and flashcards`.

### Task 3: Sinh bộ flashcard

**Files:**
- Create: `backend/app/generation/flashcards.py`, `backend/app/generation/fact_check.py`
- Modify: `backend/app/jobs/queue.py` (dispatch `flashcard_generation` trong `execute_job_step`)
- Test: `backend/tests/unit/test_flashcards.py`, `backend/tests/unit/test_fact_check.py`

**Interfaces:**
- Consumes: `retrieve_for_generation` (`app/retrieval/search.py`), `ChunkScope`, `complete_structured`, `using_model`, `hold`, `FlashcardSet`.
- Produces:
  - `MAX_CARDS = 15`.
  - `generate_flashcards(db, flashcard_set_id: uuid.UUID) -> None`.
  - `run_flashcard_step(db, job: Job) -> None`.
  - `check_against_sources(statements: list[tuple[str, list[str]]], generator_alias: str) -> list[str | None]` trong `fact_check.py`: mỗi phần tử là lý do nghi sai, hoặc `None` khi đạt. Dùng judge thuộc họ khác với model sinh, với chỉ dẫn `_SUPPORT_INSTRUCTION` của `claim_evaluation.py`. Video dùng lại hàm này ở Task 7.

- [ ] **Step 1: Viết test hỏng** (thay `complete_structured` bằng hàm giả):
  - `test_cards_are_capped_at_15`: model trả 20 thẻ thì lưu 15.
  - `test_a_card_without_a_resolvable_citation_is_dropped`: số `[S#]` không có trong đoạn đã truy xuất thì bỏ thẻ đó.
  - `test_cards_keep_chunk_ids_not_prompt_numbers`.
  - `test_fact_check_flags_what_the_judge_rejects`: judge trả `supported=False` cho thẻ 2 thì `cards[1]["flag"]` có lý do, thẻ khác `None`.
  - `test_the_judge_is_from_another_family_than_the_generator`: sinh bằng `gemini-fast` thì judge không phải alias `gemini-*`.
  - `test_a_lost_job_writes_nothing`: `hold` ném `LostJob` thì bản ghi vẫn `generating`.
- [ ] **Step 2: Chạy** `uv run pytest tests/unit/test_flashcards.py tests/unit/test_fact_check.py -q`. Kỳ vọng: FAIL.
- [ ] **Step 3: Viết code.**
  - Truy xuất theo `focus` (hoặc khái niệm đầu của planner khi không có trọng tâm) trong phạm vi tài liệu, `top_k=None` (Adaptive-k).
  - Prompt yêu cầu tối đa 15 thẻ ôn lý thuyết, mặt sau chỉ dùng đoạn nguồn, ghi `[S#]`.
  - Kiểm bằng `check_against_sources`, rồi `hold` và ghi `cards`, `status = draft`.
- [ ] **Step 4: Chạy lại test.** Kỳ vọng: PASS.
- [ ] **Step 5: Commit** `feat(generation): flashcard sets from a document and a focus`.

### Task 4: API flashcard cho giảng viên

**Files:**
- Create: `backend/app/api/flashcards.py` (đăng ký trong `app/main.py`)
- Modify: `backend/tests/integration/test_cross_tenant_access.py` (thêm mọi route mới vào danh sách tấn công)
- Test: `backend/tests/integration/test_flashcards_api.py`

**Interfaces:**
- Consumes: Task 2, Task 3, `reserve_generations`, `require_author`, `_usable_alias` (hoặc chuyển nó ra chỗ dùng chung).
- Produces:
  - `POST /flashcard-sets?document_id=` body `{focus?: str ≤1000, output_language: "vi"|"en", model_alias?: str}` trả `{id, job_id}`.
  - `GET /courses/{course_id}/flashcard-sets` trả danh sách tóm tắt.
  - `GET /flashcard-sets/{id}`.
  - `PUT /flashcard-sets/{id}/cards` body `{cards: [{front, back, citations: [chunk_id]}]}`: tối đa 15 thẻ; thẻ thay đổi được gắn `author="lecturer"` và bỏ `flag`; thẻ không có trích nguồn được gắn `flag="Chưa có nguồn"`.
  - `POST /flashcard-sets/{id}/publish`, `POST /flashcard-sets/{id}/unpublish`.
  - `DELETE /flashcard-sets/{id}`.

- [ ] **Step 1: Viết test hỏng:**
  - `test_generating_reserves_one_generation_and_queues_a_job`.
  - `test_a_16th_card_is_rejected_with_422`.
  - `test_an_edited_card_is_marked_as_the_lecturers`.
  - `test_publish_requires_a_draft_with_cards`.
  - `test_another_lecturer_gets_404_everywhere`.
  - `test_a_learner_cannot_call_author_routes` (403 theo `require_author`).
- [ ] **Step 2: Chạy.** Kỳ vọng: FAIL.
- [ ] **Step 3: Viết router** theo Interfaces, khóa hàng tài liệu `FOR UPDATE` như `POST /modules/generate`.
- [ ] **Step 4: Chạy** test trên và `tests/integration/test_cross_tenant_access.py`. Kỳ vọng: PASS.
- [ ] **Step 5: Commit** `feat(api): lecturer routes for flashcard sets`.

### Task 5: Giao diện flashcard cho giảng viên

**Files:**
- Modify: `frontend/src/components/GenerateDialog.tsx` (nhận thêm `kind: "module" | "flashcards" | "video"`, ô "Trọng tâm", và với video thêm chọn giọng và model ảnh), `frontend/src/pages/CourseDetailPage.tsx`
- Create: `frontend/src/api/flashcards.ts`, `frontend/src/pages/FlashcardEditorPage.tsx` (route `/flashcard-sets/:id`, `authorOnly`)
- Test: `frontend/src/test/integration/pages/FlashcardEditorPage.test.tsx`, cập nhật test của `GenerateDialog`

**Interfaces:**
- Consumes: các route của Task 4.
- Produces: `generateFlashcards(documentId, {focus, outputLanguage, modelAlias})`, `getFlashcardSet(id)`, `saveFlashcards(id, cards)`, `publishFlashcardSet(id, published: boolean)`.

- [ ] **Step 1: Viết test hỏng:**
  - `shows a warning on a flagged card and on a card without a source`.
  - `saves edited cards and stops at 15` (nút thêm bị tắt ở thẻ thứ 15).
  - `publishes only after saving`.
  - Ở trang môn học: `lists flashcard sets under their document with their status`.
- [ ] **Step 2: Chạy** `cd frontend && npx vitest run`. Kỳ vọng: FAIL.
- [ ] **Step 3: Viết code.** Mỗi tài liệu trên trang môn học có thêm nút "Sinh flashcard", mở `GenerateDialog` với `kind="flashcards"`. Trình sửa dùng thành phần và token của hệ thiết kế hiện có, không thêm màu tự đặt.
- [ ] **Step 4: Chạy** `npx vitest run && npm run lint && npx tsc -b`. Kỳ vọng: PASS, sạch.
- [ ] **Step 5: Kiểm trên trình duyệt** với stack local: sinh một bộ (gọi model thật `gemini-fast`, vài xu, hỏi trước), sửa, giao. Chụp ảnh trình sửa ở 1280 px và 390 px.
- [ ] **Step 6: Commit** `feat(frontend): generate, edit and publish flashcard sets`.

### Task 6: Trang học của sinh viên và flashcard

**Files:**
- Create: `backend/app/api/learn.py` (đăng ký trong `main.py`)
- Modify: `frontend/src/pages/LearnerChatPage.tsx` (thành trang học: danh sách và chat), `frontend/src/App.tsx`
- Create: `frontend/src/pages/LearnFlashcardsPage.tsx` (route `/learn/:courseId/flashcards/:setId`), `frontend/src/api/learn.ts`
- Test: `backend/tests/integration/test_learn_api.py`, `frontend/src/test/integration/pages/LearnFlashcardsPage.test.tsx`

**Interfaces:**
- Consumes: `require_course_reader` (`app/api/deps.py`), Task 2.
- Produces:
  - `GET /learn/courses/{course_id}/items` trả `{flashcard_sets: [...], videos: [...]}`, chỉ bản đã giao, nhóm theo tài liệu.
  - `GET /learn/flashcard-sets/{id}` trả thẻ (`front`, `back`, nhãn nguồn), không kèm `flag`.

- [ ] **Step 1: Viết test hỏng:**
  - `test_a_member_lists_only_published_items`.
  - `test_an_unpublished_set_is_404_for_a_member` (Review Focus 5).
  - `test_a_member_of_another_course_gets_404`.
  - `test_the_learner_view_has_no_review_flags`.
  - Frontend: `flips a card and moves to the next`, `shows the source label on the back`, `works at 390 px wide` (không cuộn ngang).
- [ ] **Step 2: Chạy.** Kỳ vọng: FAIL.
- [ ] **Step 3: Viết code.** Chat môn học vẫn ở trang, đặt dưới danh sách. Thẻ lật bằng bấm hoặc phím cách, có nút trước và sau.
- [ ] **Step 4: Chạy** backend và frontend test, lint, tsc. Kỳ vọng: PASS.
- [ ] **Step 5: Kiểm thật qua LTI:** chạy `devops/lti-e2e/run.sh`, mở công cụ bằng tài khoản sinh viên, thấy bộ đã giao và lật được thẻ. Chụp ảnh ở 390 px.
- [ ] **Step 6: Commit** `feat: learners see and study published flashcard sets`.

### Task 7: Lời thoại video theo cảnh

**Files:**
- Create: `backend/app/generation/video_script.py`
- Modify: `backend/app/jobs/queue.py` (dispatch `video_script`)
- Test: `backend/tests/unit/test_video_script.py`

**Interfaces:**
- Consumes: Task 2, `check_against_sources` (Task 3), retrieval như Task 3.
- Produces:
  - Pydantic `Scene`:
    - `kind: Literal["title","illustration","formula","code","table","summary"]`, `title`;
    - `bullets: list[str]` (2 đến 4);
    - `formula: str | None` (LaTeX), `code: str | None`, `table: list[list[str]] | None`;
    - `image_prompt: str | None`, `narration`;
    - `citations: list[str]` (id chunk), `author: "model"|"lecturer"`;
    - `flags: list[str]`;
    - `image_key: str | None`, `image_prompt_used: str | None`, `voice_key: str | None`, `voice_text_used: str | None`.
  - Hằng số: `TARGET_SECONDS = 300`, `MAX_SECONDS = 480`, `CHARS_PER_SECOND = 15`, `MAX_ILLUSTRATIONS = 5`, `MIN_SCENES = 8`, `MAX_SCENES = 10`.
  - `estimated_seconds(scenes) -> float`.
  - `generate_video_script(db, video_id) -> None`.
  - `normalize_narration(text: str) -> str`.
  - `prepare_for_render(scenes) -> list[str]`: trả các vấn đề chặn việc dựng.

- [ ] **Step 1: Viết test hỏng:**
  - `test_more_than_five_illustrations_become_text_scenes`: cảnh thứ 6 trở đi đổi thành `summary` và bỏ `image_prompt`.
  - `test_a_short_script_is_topped_up_once`: tổng dưới 0,8 × `TARGET_SECONDS` × `CHARS_PER_SECOND` thì gọi model thêm đúng một lần cho các cảnh ngắn nhất.
  - `test_a_script_over_eight_minutes_is_refused_before_render`: `prepare_for_render` báo lỗi kèm số phút ước tính.
  - `test_latex_and_underscores_do_not_reach_the_voice`: `normalize_narration("P_1 chạy $\\frac{a}{b}$")` không còn `_`, `$` hay `\` (Review Focus 3).
  - `test_image_prompt_labels_are_written_as_shown`: `P_1` trong `image_prompt` thành `P1`.
  - `test_formula_words_use_text`: `Average\ waiting\ time` thành `\text{Average waiting time}`.
  - `test_fact_check_flags_a_wrong_bullet`: judge từ chối một ý thì `flags` của cảnh có lý do, không chặn việc dựng.
  - `test_uncited_model_sentences_are_dropped`.
- [ ] **Step 2: Chạy.** Kỳ vọng: FAIL.
- [ ] **Step 3: Viết code.** Prompt lấy từ bản dựng thử, gồm các quy tắc của spec:
  - mô tả hình ghi đủ số liệu và nhãn đúng dạng hiển thị;
  - `preemption` là "trưng dụng";
  - lời thoại đọc ký hiệu thành chữ;
  - 420 đến 550 ký tự mỗi cảnh.

  Ghi `scenes`, `status = draft`, `draft_version = 1`, và chi phí vào `cost.script`.
- [ ] **Step 4: Chạy lại test.** Kỳ vọng: PASS.
- [ ] **Step 5: Commit** `feat(generation): scene by scene video scripts with checks`.

### Task 8: Slide và hình minh họa

**Files:**
- Create: `backend/app/video/slides.py`, `backend/app/video/slide.html` (mẫu), `backend/app/video/illustrations.py`
- Modify: `backend/Dockerfile` (Chromium cho Playwright và font Be Vietnam Pro; KaTeX đóng gói vào image, không lấy từ CDN lúc chạy), `backend/pyproject.toml` (`playwright`, `pillow`), `backend/app/jobs/queue.py` (dispatch `video_images`), `infra/terraform` (bộ nhớ worker nếu đo thấy cần)
- Test: `backend/tests/unit/test_slides.py`, `backend/tests/unit/test_illustrations.py`

**Interfaces:**
- Consumes: Task 1 `generate_image`, Task 2 `media_store`, Task 7 `Scene`.
- Produces:
  - `render_slide(scene: Scene, image: bytes | None, source_label: str, size=(1280, 720)) -> bytes` (PNG).
  - `trim_whitespace(image: bytes) -> bytes`.
  - `draw_illustrations(db, video_id, scene_indexes: list[int] | None = None) -> None`: chỉ vẽ cảnh `illustration` có `image_prompt` khác `image_prompt_used`, hoặc các cảnh được chỉ định.

- [ ] **Step 1: Viết test hỏng:**
  - `test_vietnamese_title_and_formula_render`: PNG đúng 1280×720, và trang HTML sinh ra có KaTeX đã render (không còn chuỗi `\frac`).
  - `test_subscripts_render_as_sub`: `P_1` thành `P<sub>1</sub>`.
  - `test_a_scene_without_an_image_uses_the_text_layout`.
  - `test_trim_keeps_a_margin_around_the_drawing`.
  - `test_only_changed_prompts_are_redrawn`: hai cảnh, một cảnh đổi prompt thì `generate_image` được gọi một lần.
  - `test_a_reply_without_an_image_keeps_the_prompt_and_fails_the_job_clearly` (Review Focus 2): `MediaError` thì cảnh giữ `image_prompt`, `image_key` không đổi, `video.error` có tên cảnh.
  - `test_no_network_is_needed_to_render` (chặn mạng của trang Chromium khi render).
- [ ] **Step 2: Chạy.** Kỳ vọng: FAIL.
- [ ] **Step 3: Viết code.**
  - Mẫu HTML lấy từ bản dựng thử: thanh màu bên trái, chữ bên trái, hình bên phải, dòng nguồn ở chân.
  - Có `<!doctype html>`, gọi `katex.render` trực tiếp; đợi `document.fonts.ready` rồi mới chụp.
  - Hình lưu dưới `media/videos/{video_id}/img-{index}-{hash}.jpg`.
- [ ] **Step 4: Chạy lại test**, rồi build image worker và chạy test trong container. Kỳ vọng: PASS. Ghi kích thước image trước và sau.
- [ ] **Step 5: Commit** `feat(video): slides with printed text and model illustrations`.

### Task 9: Dựng video

**Files:**
- Create: `backend/app/video/render.py`
- Modify: `backend/app/jobs/queue.py` (dispatch `video_render`)
- Test: `backend/tests/unit/test_video_render.py`

**Interfaces:**
- Consumes: Task 1 `synthesize_speech`, Task 8 `render_slide`, Task 7 `prepare_for_render`, `estimated_seconds`.
- Produces:
  - `render_video(db, video_id) -> None`: giọng mỗi cảnh (dùng lại `voice_key` khi `voice_text_used` bằng lời hiện tại), slide, rồi MP4 H.264 + AAC 720p, 24 fps, đệm 0,6 s cuối mỗi cảnh.
  - Ghi `video_key` mới, `rendered_version = draft_version`, `duration_seconds`, `cost.voice`, `served_models`, `status = ready`.

- [ ] **Step 1: Viết test hỏng** (giọng giả là PCM im lặng, ffmpeg thật):
  - `test_a_video_has_one_segment_per_scene_and_the_measured_duration`.
  - `test_unchanged_scenes_reuse_their_voice`: dựng lại sau khi sửa một cảnh thì `synthesize_speech` được gọi một lần.
  - `test_a_measured_duration_over_eight_minutes_fails_and_keeps_the_script`.
  - `test_the_previous_video_stays_until_the_new_one_is_stored` (Review Focus 1): khi lỗi giữa chừng, `video_key` cũ vẫn còn và tệp của nó chưa bị xóa.
  - `test_24_and_44_1_khz_voices_mix_in_one_video`.
- [ ] **Step 2: Chạy.** Kỳ vọng: FAIL.
- [ ] **Step 3: Viết code.** ffmpeg từ image (có sẵn); chỉ xóa tệp video cũ sau khi đã lưu tệp mới và commit `video_key` mới.
- [ ] **Step 4: Chạy lại test.** Kỳ vọng: PASS.
- [ ] **Step 5: Commit** `feat(video): render narrated videos from approved scenes`.

### Task 10: API video cho giảng viên

**Files:**
- Create: `backend/app/api/videos.py` (đăng ký trong `main.py`)
- Modify: `backend/app/config.py` (`video_redraws_per_day = 20`, `video_renders_per_day = 10`), `backend/tests/integration/test_cross_tenant_access.py`
- Test: `backend/tests/integration/test_videos_api.py`

**Interfaces:**
- Consumes: Task 2, 7, 8, 9, `reserve_generations`.
- Produces:
  - `POST /videos?document_id=` body `{focus?, output_language, model_alias?, voice_alias?, image_alias?}`.
  - `GET /courses/{course_id}/videos`, `GET /videos/{id}`, `PUT /videos/{id}/scenes` (tăng `draft_version`; cảnh sửa được gắn `author="lecturer"`; câu không có trích nguồn được gắn cờ "Chưa có nguồn").
  - `POST /videos/{id}/illustrations` body `{scene_indexes?: [int]}`, `POST /videos/{id}/render`.
  - `POST /videos/{id}/publish` (chỉ khi `rendered_version` không rỗng), `POST /videos/{id}/unpublish`, `DELETE /videos/{id}`.
  - `GET /videos/{id}/file` trả link có hạn tới bản đã dựng.
  - `GET /videos/{id}/scenes/{index}/preview.png`: slide của cảnh theo bản nháp hiện tại, render bằng `render_slide` (Task 8), để giảng viên xem trước khi dựng.

- [ ] **Step 1: Viết test hỏng:**
  - `test_generating_reserves_one_generation`.
  - `test_redraws_and_renders_have_a_daily_limit` (429 với thông điệp tiếng Việt).
  - `test_render_is_refused_over_eight_minutes` (422 kèm số phút).
  - `test_editing_a_published_video_keeps_the_rendered_one_for_learners`: sau `PUT /scenes`, `rendered_version` và `video_key` không đổi.
  - `test_publish_needs_a_rendered_video`.
  - `test_scene_preview_is_a_1280_by_720_png_for_the_owner_only`.
  - `test_another_lecturer_gets_404_everywhere`.
- [ ] **Step 2: Chạy.** Kỳ vọng: FAIL.
- [ ] **Step 3: Viết router.** Không cho dựng khi đang có job `video_images` hoặc `video_render` của cùng video (409).
- [ ] **Step 4: Chạy** test trên và test truy cập chéo. Kỳ vọng: PASS.
- [ ] **Step 5: Commit** `feat(api): lecturer routes for videos`.

### Task 11: Giao diện video cho giảng viên

**Files:**
- Create: `frontend/src/api/videos.ts`, `frontend/src/pages/VideoEditorPage.tsx` (route `/videos/:id`, `authorOnly`)
- Modify: `frontend/src/pages/CourseDetailPage.tsx`, `frontend/src/components/GenerateDialog.tsx` (chọn giọng và model ảnh, mặc định HeyGen và Nano Banana, hiện giá tham khảo)
- Test: `frontend/src/test/integration/pages/VideoEditorPage.test.tsx`

**Interfaces:**
- Consumes: Task 10.

- [ ] **Step 1: Viết test hỏng:**
  - `shows each scene as its slide with narration below`.
  - `shows the estimated length and blocks render over 8 minutes`.
  - `marks flagged scenes and uncited lecturer sentences`.
  - `redraws one scene after its prompt is edited`.
  - `shows the rendered video while a new draft is being edited`.
  - `polls a running job and shows its error`.
- [ ] **Step 2: Chạy.** Kỳ vọng: FAIL.
- [ ] **Step 3: Viết code.** Luồng trên trang: lời thoại và slide chữ, rồi "Vẽ hình", rồi xem hình, rồi "Dựng video", rồi xem, rồi "Giao cho sinh viên". Ảnh xem trước của slide lấy từ `GET /videos/{id}/scenes/{index}/preview.png` (Task 10).
- [ ] **Step 4: Chạy** `npx vitest run && npm run lint && npx tsc -b`. Kỳ vọng: PASS.
- [ ] **Step 5: Commit** `feat(frontend): video editor for lecturers`.

### Task 12: Sinh viên xem video

**Files:**
- Modify: `backend/app/api/learn.py` (thêm `GET /learn/videos/{id}` và link tệp), `frontend/src/pages/LearnerChatPage.tsx`
- Create: `frontend/src/pages/LearnVideoPage.tsx` (route `/learn/:courseId/videos/:videoId`)
- Test: `backend/tests/integration/test_learn_api.py`, `frontend/src/test/integration/pages/LearnVideoPage.test.tsx`

- [ ] **Step 1: Viết test hỏng:**
  - `test_a_member_gets_a_short_lived_link_to_the_rendered_video`.
  - `test_an_unpublished_or_foreign_video_is_404_and_gives_no_link` (Review Focus 5).
  - Frontend: `plays the video and lists the sources of its scenes`, `works at 390 px wide`.
- [ ] **Step 2: Chạy.** Kỳ vọng: FAIL.
- [ ] **Step 3: Viết code.** Dùng thẻ `<video controls>` với link có hạn. Bên dưới có danh sách cảnh: tiêu đề, mốc thời gian và nguồn; bấm vào một cảnh thì video nhảy tới cảnh đó.
- [ ] **Step 4: Chạy test, lint, tsc.** Kỳ vọng: PASS.
- [ ] **Step 5: Commit** `feat: learners watch published videos`.

### Task 13: Chạy thật từ đầu đến cuối và tài liệu

**Files:**
- Modify: `infra/README.md` (kho `media/`, Chromium trong worker), `docs/integration/lti.md` (trang học của sinh viên), `docs/measurements/2026-11-12-video-trial.md` (mới)

- [ ] **Step 1 (trả phí, khoảng $0,25, hỏi người dùng trước): chạy thật trên stack local.**
  1. Sinh một video từ "Chapter 5_CPU Scheduling.pdf" (trọng tâm FCFS và Round Robin, tiếng Việt).
  2. Sửa một câu, vẽ lại một hình, dựng, giao.
  3. Mở bằng tài khoản sinh viên qua `devops/lti-e2e/run.sh`.
  4. Làm tương tự với một bộ flashcard.

  Chụp ảnh từng bước ở 1280 px và 390 px; ghi độ dài, số cảnh, số hình, chi phí thật theo `cost`.
- [ ] **Step 2: Chạy toàn bộ test:**
  - `cd backend && uv run ruff check . && uv run pytest tests/unit tests/integration tests/e2e -q`
  - `cd frontend && npx vitest run && npm run lint && npx tsc -b`
  - `cd infra/terraform && terraform test`

  Kỳ vọng: tất cả qua.
- [ ] **Step 3: Viết tài liệu** theo các file trên, mỗi câu một dòng, không dùng dấu gạch dài.
- [ ] **Step 4: Commit** `docs: videos and flashcards, measured on a real run`, rồi mở PR kèm số test và ảnh chụp làm bằng chứng.
