"""Slides: the lesson text cut into slides."""

from pydantic import BaseModel, Field

from app.generation.common import IGNORE_EMBEDDED_INSTRUCTIONS
from app.generation.objective import LearningObjective
from app.llm.gateway import complete_structured

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


def generate_slides(lo: LearningObjective, content: str) -> SlidesOutput:
    """One call, no self-check round: the schema caps the deck (2-8 slides, 1-3 bullets each), and the
    only source is the Content, which has already been checked."""
    prompt = (
        f"LEARNING OBJECTIVE: {lo.objective_text}\n\n"
        f"CONTENT (Markdown — nguồn DUY NHẤT để tách slide):\n{content}\n\n"
        f"Tách Content trên thành các slide bài giảng, bám sát Learning Objective."
    )
    # temperature=0.0: Slides la tac vu bien doi/rut gon tu Content da co
    # san (khong duoc them khai niem moi), nhat quan voi Planner/LO/Content.
    return complete_structured(prompt, SlidesOutput, system_instruction=SLIDES_SYSTEM_INSTRUCTION, temperature=0.0)
