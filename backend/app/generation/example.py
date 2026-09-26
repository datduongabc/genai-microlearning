"""Examples: worked applications of the lesson, at most one per sub-objective."""

from pydantic import BaseModel, Field

from app.generation.common import (
    IGNORE_EMBEDDED_INSTRUCTIONS,
    format_source_chunks,
)
from app.generation.objective import LearningObjective
from app.llm.gateway import complete_structured
from app.models import Chunk

# ---------------------------------------------------------------------------
# 5. Example — schema {context, analysis} bắt buộc, few-shot bằng chính đoạn
# nguồn (giáo trình) để định hình văn phong/thuật ngữ (mục 5.4). Khác Content:
# ĐƯỢC PHÉP sáng tạo ví dụ mới, miễn nhất quán với khái niệm đã nêu.
# ---------------------------------------------------------------------------

EXAMPLE_SYSTEM_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + """Bạn là chuyên gia biên soạn ví dụ minh họa cho giáo dục đại học.

Nhiệm vụ: viết các Example minh họa cho Learning Objective/Content được cung
cấp — TỐI ĐA 1 Example cho MỖI sub-objective (mỗi phần của Content), và chỉ
cho những phần mà một ví dụ thật sự giúp người học hiểu hơn; ít nhất 1 Example.
Không viết thêm ví dụ cho đủ số. Các đoạn SOURCE CHUNK bên dưới đóng vai trò
few-shot — dùng để định hình VĂN PHONG và THUẬT NGỮ (không phải để chép lại
nguyên văn).

Ràng buộc BẮT BUỘC:
1. Khác với Content, Example ĐƯỢC PHÉP chứa tình huống/ví dụ MỚI do bạn tự
   nghĩ ra (nguồn thường không có sẵn ví dụ ứng dụng thực tế) — nhưng ví dụ
   đó PHẢI nhất quán, không mâu thuẫn với khái niệm đã nêu trong Content, và
   KHÔNG được dùng để suy ra thêm dữ kiện mới cho Content.
2. `context`: mô tả 1 bối cảnh ứng dụng thực tế cụ thể liên quan Learning
   Objective (không chung chung).
3. `analysis`: phân tích cách bối cảnh đó minh họa đúng khái niệm trong
   Content — liên hệ trực tiếp, không lạc đề.
4. Văn phong và thuật ngữ dùng trong context/analysis phải NHẤT QUÁN với văn
   phong/thuật ngữ của các SOURCE CHUNK bên dưới (few-shot).
5. Ngôn ngữ khớp ngôn ngữ chính của Content (tiếng Việt có dấu đầy đủ nếu
   Content là tiếng Việt).
"""


class Example(BaseModel):
    context: str = Field(description="Bối cảnh ứng dụng thực tế")
    analysis: str = Field(description="Phân tích liên hệ với khái niệm trong Content")


class ExampleOutput(BaseModel):
    # Trần 4 bằng trần số sub-objective: một bài 5-10 phút không cần nhiều ví dụ hơn số phần của nó.
    examples: list[Example] = Field(
        min_length=1, max_length=4,
        description="Tối đa 1 Example cho mỗi sub-objective, theo đúng thứ tự các phần của Content",
    )


def generate_example(lo: LearningObjective, content: str, context_chunks: list[Chunk]) -> ExampleOutput:
    """One call, no self-check round: the schema caps the count (1-4, at most one per sub-objective),
    and an example may invent its situation, so there is no source to hold it against."""
    sub_objectives_text = "\n".join(f"- {so}" for so in lo.sub_objectives)
    prompt = (
        f"LEARNING OBJECTIVE: {lo.objective_text}\n"
        f"SUB-OBJECTIVES (tối đa 1 Example cho mỗi cái):\n{sub_objectives_text}\n\n"
        f"CONTENT:\n{content}\n\n"
        f"{format_source_chunks(context_chunks)}\n\n"
        f"Viết các Example cho Learning Objective/Content trên, tham khảo văn phong/thuật ngữ từ các SOURCE CHUNK (few-shot)."
    )
    # temperature=0.0: du Example duoc phep sang tao tinh huong moi, viec
    # LUA CHON tinh huong nao + phan tich no van nen nhat quan/co the du
    # doan duoc giua cac lan goi, khong can bien thien ngau nhien -- nhat
    # quan voi phan con lai cua pipeline.
    return complete_structured(prompt, ExampleOutput, system_instruction=EXAMPLE_SYSTEM_INSTRUCTION, temperature=0.0)
