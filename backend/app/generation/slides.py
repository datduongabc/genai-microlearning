"""Slides: the lesson text cut into slides, checked to add nothing new."""

from pydantic import BaseModel, Field

from app.generation.common import IGNORE_EMBEDDED_INSTRUCTIONS
from app.generation.objective import LearningObjective
from app.services.llm import complete_structured

# ---------------------------------------------------------------------------
# 4. Slides — nhận trực tiếp Markdown từ Content, Backward Design bám Learning
# Objective, Cognitive Load Constraint tối đa 2-3 khái niệm/slide (mục 5.3).
# ---------------------------------------------------------------------------

SLIDES_SYSTEM_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + """Bạn là chuyên gia thiết kế slide bài giảng cho giáo dục đại học, áp dụng
Backward Design (bám sát Learning Objective) và Cognitive Load Theory.

Nhiệm vụ: tách nội dung Content (Markdown) được cung cấp thành các slide bài
giảng ngắn gọn.

Ràng buộc BẮT BUỘC:
1. CHỈ được dùng lại thông tin ĐÃ CÓ trong Content — không thêm khái niệm hay
   dữ kiện mới ngoài Content.
2. Mỗi slide tối đa 2-3 khái niệm cốt lõi (Cognitive Load Constraint) — không
   nhồi nhét nhiều ý vào 1 slide.
3. Mỗi bullet là 1 cụm từ/câu NGẮN GỌN (không phải nguyên đoạn văn copy từ
   Content) — slide dùng để trình chiếu, không phải để đọc như văn bản.
4. Thứ tự slide phải phục vụ đúng Learning Objective — đi từ khái niệm nền
   tảng đến khái niệm phụ thuộc, không đảo lộn logic.
5. Ngôn ngữ của title/bullets khớp ngôn ngữ chính của Content (tiếng Việt có
   dấu đầy đủ nếu Content là tiếng Việt).
"""


class Slide(BaseModel):
    title: str
    bullets: list[str] = Field(min_length=1, max_length=3)


class SlidesOutput(BaseModel):
    slides: list[Slide] = Field(min_length=2, max_length=8)


# Tầng 3 (GD2, giống LO/Content/Quiz): Slides trước đây sinh 1 lần, 3 ràng
# buộc trong prompt (không thêm khái niệm mới, bullet ngắn gọn không copy
# nguyên văn, tối đa 2-3 khái niệm/slide) nhưng KHÔNG ai kiểm lại có tuân
# thủ không. Nghiên cứu "Textual-to-Visual Iterative Self-Verification for
# Slide Generation" (arXiv:2502.15412) xác nhận self-verification loop cải
# thiện chất lượng slide sinh tự động; LLM có xu hướng chép verbatim câu dài
# từ nguồn khi được yêu cầu "tóm tắt" thay vì thực sự diễn giải lại.
MAX_SLIDES_CORRECTION_RETRY = 1

SLIDES_VERIFY_SYSTEM_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + """Bạn là bộ kiểm tra chất lượng Slides (Critic), dựa trên Content gốc được
cung cấp.

Chấm đúng 3 tiêu chí:
- no_new_info_pass: slides có CHỈ dùng lại thông tin đã có trong Content
  không, hay thêm khái niệm/dữ kiện mới không có trong Content?
- conciseness_pass: mỗi bullet có thực sự NGẮN GỌN, đã diễn giải lại bằng
  cách riêng không, hay copy gần như nguyên văn 1 câu/đoạn dài từ Content
  (dấu hiệu: bullet dài hơn 1 câu, hoặc trùng khớp câu chữ với Content)?
- cognitive_load_pass: mỗi slide có thực sự chỉ chứa 2-3 KHÁI NIỆM (không
  phải chỉ đếm số bullet — 1 bullet dài có thể nhồi nhét 2+ khái niệm khác
  nhau) không?

Nếu vi phạm bất kỳ tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể (để
dùng làm feedback sinh lại).
"""


class SlidesVerification(BaseModel):
    no_new_info_pass: bool
    no_new_info_reason: str
    conciseness_pass: bool
    conciseness_reason: str
    cognitive_load_pass: bool
    cognitive_load_reason: str


def _verify_slides(slides_out: SlidesOutput, content: str) -> SlidesVerification:
    slides_text = "\n".join(
        f"- {s.title}: {'; '.join(s.bullets)}" for s in slides_out.slides
    )
    prompt = (
        f"CONTENT GỐC:\n{content}\n\n"
        f"SLIDES ĐÃ SINH:\n{slides_text}\n\n"
        f"Kiểm tra no_new_info_pass, conciseness_pass, cognitive_load_pass của Slides trên."
    )
    return complete_structured(
        prompt, SlidesVerification, system_instruction=SLIDES_VERIFY_SYSTEM_INSTRUCTION, temperature=0.0
    )


def generate_slides(lo: LearningObjective, content: str) -> tuple[SlidesOutput, SlidesVerification]:
    feedback: str | None = None
    slides_out: SlidesOutput | None = None
    check: SlidesVerification | None = None
    for _ in range(MAX_SLIDES_CORRECTION_RETRY + 1):
        prompt = (
            f"LEARNING OBJECTIVE: {lo.objective_text}\n\n"
            f"CONTENT (Markdown — nguồn DUY NHẤT để tách slide):\n{content}\n\n"
            f"Tách Content trên thành các slide bài giảng, bám sát Learning Objective."
        )
        if feedback:
            prompt += (
                f"\n\nLƯU Ý: lần sinh trước KHÔNG ĐẠT vì lý do sau -- sửa đúng lỗi này, "
                f"không lặp lại: {feedback}"
            )
        # temperature=0.0: Slides la tac vu bien doi/rut gon tu Content da co
        # san (khong duoc them khai niem moi), nhat quan voi Planner/LO/Content.
        slides_out = complete_structured(
            prompt, SlidesOutput, system_instruction=SLIDES_SYSTEM_INSTRUCTION, temperature=0.0
        )

        check = _verify_slides(slides_out, content)
        if check.no_new_info_pass and check.conciseness_pass and check.cognitive_load_pass:
            return slides_out, check

        reasons = []
        if not check.no_new_info_pass:
            reasons.append(f"New info fail: {check.no_new_info_reason}")
        if not check.conciseness_pass:
            reasons.append(f"Conciseness fail: {check.conciseness_reason}")
        if not check.cognitive_load_pass:
            reasons.append(f"Cognitive load fail: {check.cognitive_load_reason}")
        feedback = "; ".join(reasons)

    return slides_out, check  # best-effort sau khi hết retry budget, giống pattern LO/Content
