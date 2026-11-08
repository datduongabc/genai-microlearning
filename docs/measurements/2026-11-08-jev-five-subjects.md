# Năm môn học với Jev và Gemini 3.5 Flash Lite, 2026-11-08

Đo tốc độ và chất lượng của cả luồng trên stack compose, sau khi Jev được đồng ý và thành reranker mặc định: tải tài
liệu lên, hỏi chat, sinh module, rồi chấm câu trả lời (Ragas) và module (rubric). Dữ liệu thô ở
[jev-subjects/](jev-subjects/).

| Tệp | Nội dung |
| --- | --- |
| `advanced-programming.jsonl`, `calculus-os-socialism.jsonl`, `machine-learning.jsonl` | Từng sự kiện tải lên, chat, sinh module (`backend/scripts/measure_subjects.py`) |
| `summary.txt` | Từng module: số lượt gọi, model phục vụ, rerank, token, thời gian từng giai đoạn |
| `answers-gemini-fast-paid-jev.jsonl`, `ragas-groq-pro-jev.jsonl` | 50 câu chat có ngữ cảnh (`scripts/chat_eval.py`) và điểm Ragas |
| `rubric-claude-pro-openai-pro.jsonl` | Điểm rubric của 10 module, hai judge (`scripts/judge_modules.py`) |
| `questions-*.json` | Bộ câu hỏi chat-eval trỏ vào các khóa học của lượt đo này |

## Cách đo

- **Stack:** `docker compose` trên một máy (WSL, 12 GB RAM), một `backend`, một `worker` (4 luồng job), embedder
  Qwen3-Embedding-0.6B trên CPU, `RERANKER_PROVIDER=jev` (`jev-1.14.0`).
- **Model sinh:** `gemini-fast-paid`, tức `google/gemini-3.5-flash-lite` qua OpenRouter, cố định Google AI Studio, ở
  chế độ thí nghiệm (tắt fallback). Gói Gemini miễn phí đã hết hạn mức ngày, nên mọi bước đều dùng đường trả phí, kể
  cả bước đọc lại công thức (`FORMULA_VISION_MODEL`). Phiên âm: Groq `whisper-large-v3-turbo`.
- **Môn:** Advanced Programming (5 PPTX), Calculus 1 (6 PDF), Operating Systems (5 PDF), Scientific Socialism
  (3 PPTX), Machine Learning (2 MP4, 2 MP3, 5,3 giờ).
- **Mỗi môn:** một khóa học mới; tải từng tệp lên theo ba bước rồi chờ `embedded` (hệ thống chỉ xử lý một tài liệu
  mỗi khóa một lúc); hỏi 10 câu của [bộ đánh giá chat](2026-11-07-chat-ragas.md) thuộc môn đó, lần lượt; sinh hai
  module từ hai tệp đầu theo thứ tự tên, lần lượt.
- **Một lần chạy, một người dùng, không tải song song.** Đây là độ trễ của một người dùng, không phải năng lực chịu tải.
- Thời gian module tính từ lúc gửi yêu cầu đến khi job `done`, hỏi trạng thái mỗi 5 s, nên sai số tới 5 s. Thời gian
  từng giai đoạn lấy từ `modules.generation_timings`, đo chính xác.

## Tốc độ

| Môn | Tệp | MB | Tải lên + xử lý, TB (max) | Chat TB / p95 | Trích dẫn TB | Module |
| --- | ---: | ---: | --- | --- | ---: | --- |
| Advanced Programming | 5 | 11,4 | 24,1 s (54,8 s) | 2,9 s / 7,7 s | 1,3 | 45,8 s, 25,9 s |
| Calculus 1 | 6 | 5,8 | 42,1 s (66,7 s) | 1,9 s / 2,2 s | 1,2 | 20,7 s, 30,7 s |
| Operating Systems | 5 | 15,7 | 25,5 s (32,3 s) | 1,6 s / 1,8 s | 1,1 | 30,8 s, 30,7 s |
| Scientific Socialism | 3 | 14,2 | 13,5 s (16,2 s) | 1,9 s / 2,2 s | 1,6 | 26,5 s, 30,8 s |
| Machine Learning | 4 | 1.165,3 | 58,4 s (74,4 s) | 2,0 s / 2,9 s | 2,0 | 20,8 s, 25,8 s |
| **Tổng** | **23** | **1.212** | **33,7 s** | **2,05 s / 2,52 s** (n=50) | | **TB 28,9 s** (n=10) |

- **Không lỗi nào:** 23/23 tệp `embedded`, 50/50 câu chat trả về 200 và có trích dẫn, 10/10 module `done`.
- **Video và audio:** Week02.mp4 (401 MB) 74,4 s, trong đó tải lên 11,3 s; Week03.mp4 (617 MB) 69,1 s, tải lên 20,7 s;
  hai MP3 (83 và 64 MB) 45,0 s mỗi tệp. Phiên âm 5,3 giờ qua Groq khoảng 0,21 $.
- **Jev:** 81 lần rerank trong 10 module, trung bình 0,44 s (max 1,35 s), không lần nào `jev_fallback`. Container
  Jina không nhận request nào.
- **Model:** cả 170 lượt gọi sinh module do `google/gemini-3.5-flash-lite` phục vụ.
- **Token mỗi module:** trung bình 60.102 vào, 8.219 ra, khoảng 0,04 $ theo giá OpenRouter.
- **Giai đoạn chậm nhất là sinh câu hỏi:** 5–28 s mỗi module; các giai đoạn còn lại 1–4 s, riêng Content của một
  module OS là 8,5 s.
- Câu chat đầu tiên của lượt chạy mất 11,9 s (khởi động), các câu sau 1,5–3,2 s.

## Chất lượng câu trả lời chat (Ragas)

Cùng 50 câu với [lượt 2026-11-07](2026-11-07-chat-ragas.md), hỏi lại trên các khóa học của lượt này đúng như
`POST /courses/{id}/chat` (30 ứng viên, rerank lấy 8, một lượt hỏi). Judge `groq-pro` (`openai/gpt-oss-120b` trên
Groq qua OpenRouter), khác hãng với model sinh, Ragas 0.2.15, embedding `embed-default`. Faithfulness tính trên các
câu không bị từ chối, như lượt trước.

| Lượt | Rerank | Từ chối | Faithfulness (95%) | Answer relevancy (95%) | Chunk nguồn trong 8 ngữ cảnh |
| --- | --- | ---: | --- | --- | ---: |
| 2026-11-07 | Jina | 4/50 | 0,967 (0,916–1,000) | 0,725 (0,648–0,788) | 50/50 |
| 2026-11-08 | Jev | 3/50 | 0,990 (0,973–1,000) | 0,740 (0,672–0,797) | 48/50 |

| Môn | Từ chối | Faithfulness | Answer relevancy |
| --- | ---: | --- | --- |
| Advanced Programming | 0 | 1,000 | 0,799 (0,702–0,882) |
| Calculus 1 | 0 | 1,000 | 0,802 (0,774–0,826) |
| Operating Systems | 2 | 1,000 | 0,685 (0,437–0,882) |
| Scientific Socialism | 0 | 0,986 (0,957–1,000) | 0,687 (0,612–0,753) |
| Machine Learning | 1 | 0,963 (0,889–1,000) | 0,728 (0,549–0,857) |

- Trên 40 câu của bốn môn văn bản, chênh lệch relevancy Jev trừ Jina là +0,005 (bootstrap theo cặp, −0,054 đến
  +0,062): **không phân biệt được**. Faithfulness cao hơn một chút nhưng khoảng tin cậy chồng nhau.
- Hai lượt không chỉ khác reranker: prompt chat đã có thêm quy tắc chống prompt injection
  ([2026-11-07-prompt-injection](2026-11-07-prompt-injection.md)), và các tài liệu được nạp lại nên chunk mới. "Chunk
  nguồn trong ngữ cảnh" ở lượt này so bằng nội dung chunk, vì id chunk đã khác; hai câu không khớp có thể do ranh giới
  chunk chứ không chắc là truy hồi sai.

## Chất lượng module (rubric)

Mười module, mỗi module thành gói như người chấm của nghiên cứu nhận (`app/generation/study_packets.py`: các thành phần
của module và các chunk tham chiếu), chấm một lần bởi mỗi judge theo 8 tiêu chí điểm 1–5 của
[rubric](../rubric.md), cố định model, tắt fallback. Judge: `claude-pro` (Claude Sonnet 5.5) và `openai-pro` (GPT-6.1
Sol), đều khác hãng với Gemini; model thực sự phục vụ được ghi và đúng ở cả 20 lượt. Tốn khoảng 0,08 $ mỗi module
cho hai judge.

| Tiêu chí | Claude | GPT | Chung | Tỉ lệ ≥ 4 |
| --- | ---: | ---: | ---: | ---: |
| query_alignment | 4,9 | 4,9 | 4,90 | 100% |
| accuracy | 4,3 | 4,1 | 4,20 | 95% |
| slide_quality | 4,0 | 3,8 | 3,90 | 90% |
| consistency | 3,7 | 3,8 | 3,75 | 65% |
| hallucination_score | 3,4 | 4,1 | 3,75 | 55% |
| completeness | 3,5 | 3,9 | 3,70 | 65% |
| quiz_quality | 3,0 | 2,9 | 2,95 | 0% |
| bloom_fidelity | 2,9 | 2,9 | 2,90 | 15% |

- **Điểm trung bình theo môn:** Advanced Programming 4,03, Machine Learning 3,84, Operating Systems 3,78, Calculus 3,59,
  Scientific Socialism 3,53; chung 3,76.
- **Hai judge gần nhau:** trên 80 cặp điểm (10 module × 8 tiêu chí), 61% trùng hẳn, 95% lệch tối đa 1 điểm. Claude
  khắt khe hơn về hallucination (4,1 phát hiện mỗi lượt so với 1,7).
- **Quiz là điểm yếu rõ nhất, và hai judge nói cùng một lý do:** đáp án đúng thường là phương án dài và đầy đủ nhất
  nên lộ đáp án; phương án nhiễu dùng từ tuyệt đối ("toàn bộ", "hoàn toàn", "không bất kỳ") hoặc phủ định thẳng nội
  dung nên dễ loại; vài câu quiz và practice kiểm tra cùng một ý.
- **Bloom:** Learning Objective khai báo Understand, Apply hoặc Analyze, nhưng câu hỏi phần lớn chỉ đòi nhận lại định
  nghĩa (Remember); hiếm câu bắt áp dụng vào tình huống mới.
- **Hallucination:** 58 phát hiện trong 20 lượt chấm, nằm ở phần giải thích đáp án (27), Content (24) và quiz (7),
  không ở slide. Phần lớn là khẳng định thêm không có trong nguồn: một ứng dụng ngoài bài (sin(1/x) "mô tả dao động
  điện từ", cả hai judge cùng bắt), một lời giải thích cho phương án sai, hay một bối cảnh lịch sử cụ thể hơn nguồn.

Điểm này không so được với [pilot trước](2026-11-07-module-study-pairs.md): ở đó chưa có judge của cặp chấm đã định, và
đây không phải nghiên cứu mù có đối chứng mà là điểm tuyệt đối của module RAG.

## So với các lần đo trước

| Lần đo | Model | Rerank | Module | Rerank TB |
| --- | --- | --- | --- | --- |
| 2026-10, 5 module lần lượt (report, mục Generation latency) | Claude Haiku 5.5 | Jina, CPU | 368,9 s | 2,0 s |
| Hôm nay, 10 module lần lượt | Gemini 3.5 Flash Lite | Jev | 28,9 s | 0,44 s |

Hai lần khác nhau cả model lẫn reranker, và Haiku dùng khoảng 240.000 token mỗi module so với 60.000 ở đây, nên mức
giảm không thể quy hết cho Jev. Riêng phần rerank nhanh hơn khoảng 4,5 lần, khớp với đo trước
([2026-11-05](2026-11-05-real-llm-and-jev.md): p95 1,48 s so với 3,56 s).

## Hai lỗi cấu hình tìm ra trong lúc đo

- **Đọc lại công thức vẫn dùng gói miễn phí.** `FORMULA_VISION_MODEL` không được compose truyền vào, nên PDF của
  Calculus gọi `gemini-fast` đã hết hạn mức: mỗi trang nhận 429 rồi bỏ qua, gắn `formula_unverified`. Ở lượt đầu
  bị dừng, sáu PDF Calculus mất trung bình 71,0 s (max 115 s); sau khi compose truyền biến và đặt sang
  `gemini-fast-paid`, còn 42,1 s (max 66,7 s). Số trong bảng là của lượt sau.
- **Giới hạn 3 module/ngày cho tài khoản chưa liên kết Moodle** (`NON_TEACHER_DAILY_GENERATION_LIMIT`) chặn module thứ tư
  của lượt đầu. Đây là tính năng, không phải lỗi; lượt sau đặt giới hạn 50 cho tài khoản đo.

## Chi phí

Cả buổi đo tốn khoảng 1,60 $ trên OpenRouter: 0,52 $ cho tốc độ bốn môn văn bản (kể cả các lần đọc lại công thức và
một lượt đầu bị dừng), 1,08 $ cho Machine Learning, 50 câu chat hỏi lại, Ragas và hai judge chấm 10 module (khoảng
0,83 $). Thêm khoảng 0,21 $ trên Groq cho phiên âm.

## Giới hạn

- Một lần chạy, một người dùng, một máy; chưa có số khi nhiều người dùng cùng lúc với cấu hình này.
- Mỗi judge chấm mỗi module một lần; chưa có người chấm. Điểm Ragas dùng một judge.
- Mười module là mẫu nhỏ; điểm theo môn chỉ dựa trên hai module.
- Đi qua OpenRouter thêm một chặng mạng; gọi thẳng Gemini có thể nhanh hơn một chút nhưng bị giới hạn 15 request/phút.

## Chạy lại

```bash
LLM_MODEL=gemini-fast-paid LLM_EXPERIMENT_MODE=true FORMULA_VISION_MODEL=gemini-fast-paid \
  NON_TEACHER_DAILY_GENERATION_LIMIT=50 docker compose up -d --force-recreate backend worker
cd backend && uv run python -u scripts/measure_subjects.py out.jsonl \
  "advanced-programming,calculus 1,operating-system,scientific-socialism,machine-learning"
# Chat, trong container backend, với DATABASE_URL của chủ sở hữu; questions-*.json trỏ vào các khóa học mới:
python scripts/chat_eval.py answer --questions questions.json --alias gemini-fast-paid --out answers.jsonl
uv run --no-project --python 3.12 --with ragas==0.2.15 --with langchain-openai==0.3.12 \
  python scripts/chat_eval_ragas.py --answers answers.jsonl --judge groq-pro --gateway http://localhost:4000 --out ragas.jsonl
# Module:
python scripts/judge_modules.py --out rubric.jsonl --judges claude-pro,openai-pro <module_id>...
```
