"""Examples: worked applications of the lesson, at most one per sub-objective."""

from pydantic import BaseModel, Field

from app.generation.common import (
    OutputLanguage,
    output_language_instruction,
    source_block,
    system_instruction,
)
from app.generation.objective import LearningObjective
from app.llm.gateway import complete_structured
from app.models import Chunk

# ---------------------------------------------------------------------------
# 5. Example — schema {context, analysis} bắt buộc, few-shot bằng chính đoạn
# nguồn (giáo trình) để định hình văn phong/thuật ngữ (mục 5.4). Khác Content:
# ĐƯỢC PHÉP sáng tạo ví dụ mới, miễn nhất quán với khái niệm đã nêu.
# ---------------------------------------------------------------------------

_EXAMPLE_BODY = """Bạn là chuyên gia biên soạn ví dụ minh họa cho giáo dục đại học.

Nhiệm vụ: viết các Example minh họa cho Learning Objective/Content được cung
cấp — TỐI ĐA 1 Example cho MỖI sub-objective (mỗi phần của Content), và chỉ
cho những phần mà một ví dụ thật sự giúp người học hiểu hơn; ít nhất 1 Example.
Chọn tối đa 2 ví dụ hữu ích nhất. Không viết thêm ví dụ cho đủ số.

Ràng buộc BẮT BUỘC:
1. Khác với Content, Example ĐƯỢC PHÉP chứa tình huống/ví dụ MỚI do bạn tự
   nghĩ ra — nhưng ví dụ đó PHẢI nhất quán, không mâu thuẫn với khái niệm đã
   nêu trong Content, và KHÔNG được dùng để suy ra thêm dữ kiện mới cho
   Content.
2. `context`: mô tả 1 bối cảnh ứng dụng thực tế cụ thể liên quan Learning
   Objective (không chung chung).
3. `analysis`: phân tích cách bối cảnh đó minh họa đúng khái niệm trong
   Content — liên hệ trực tiếp, không lạc đề.
"""

_EXAMPLE_SOURCE_RULES = """5. Các đoạn SOURCE CHUNK bên dưới đóng vai trò few-shot — văn phong và thuật
   ngữ dùng trong context/analysis phải NHẤT QUÁN với chúng (không phải để
   chép lại nguyên văn).
"""

# Nhánh không RAG: không có nguồn để lấy văn phong, Content là chuẩn duy nhất.
_EXAMPLE_NO_SOURCE_RULES = """5. Văn phong và thuật ngữ dùng trong context/analysis phải NHẤT QUÁN với
   Content.
"""

EXAMPLE_SYSTEM_INSTRUCTION = system_instruction(
    _EXAMPLE_BODY, _EXAMPLE_SOURCE_RULES, _EXAMPLE_NO_SOURCE_RULES, sourced=True
)
NO_SOURCE_EXAMPLE_SYSTEM_INSTRUCTION = system_instruction(
    _EXAMPLE_BODY, _EXAMPLE_SOURCE_RULES, _EXAMPLE_NO_SOURCE_RULES, sourced=False
)


class Example(BaseModel):
    context: str = Field(description="Bối cảnh ứng dụng thực tế")
    analysis: str = Field(description="Phân tích liên hệ với khái niệm trong Content")


class ExampleOutput(BaseModel):
    examples: list[Example] = Field(
        min_length=1, max_length=2,
        description="Tối đa 1 Example cho mỗi sub-objective, theo đúng thứ tự các phần của Content",
    )


def generate_example(
    lo: LearningObjective, content: str, context_chunks: list[Chunk] | None,
    output_language: OutputLanguage = "vi",
) -> ExampleOutput:
    """One call, no self-check round: the schema caps the count (1-2, at most one per sub-objective),
    and an example may invent its situation, so there is no source to hold it against.
    `context_chunks=None` is the no-RAG arm: the same prompt without the source block."""
    sub_objectives_text = "\n".join(f"- {so}" for so in lo.sub_objectives)
    prompt = (
        f"LEARNING OBJECTIVE: {lo.objective_text}\n"
        f"SUB-OBJECTIVES (tối đa 1 Example cho mỗi cái):\n{sub_objectives_text}\n\n"
        f"CONTENT:\n{content}\n\n"
        f"{source_block(context_chunks)}"
        f"Viết các Example cho Learning Objective/Content trên."
    )
    instruction = EXAMPLE_SYSTEM_INSTRUCTION if context_chunks is not None else NO_SOURCE_EXAMPLE_SYSTEM_INSTRUCTION
    # temperature=0.0: du Example duoc phep sang tao tinh huong moi, viec
    # LUA CHON tinh huong nao + phan tich no van nen nhat quan/co the du
    # doan duoc giua cac lan goi, khong can bien thien ngau nhien -- nhat
    # quan voi phan con lai cua pipeline.
    return complete_structured(
        prompt, ExampleOutput, system_instruction=instruction + output_language_instruction(output_language),
        temperature=0.0,
    )
