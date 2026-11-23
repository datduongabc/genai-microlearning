"""Examples: worked applications of the lesson, at most one per sub-objective."""

from pydantic import BaseModel, Field

from app.generation.common import (
    OutputLanguage,
    custom_instructions_instruction,
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
cấp — viết ĐÚNG 2 Example, mỗi Example cho MỘT sub-objective khác nhau (mỗi
phần của Content), chọn 2 phần mà một ví dụ thật sự giúp người học hiểu hơn.

Ràng buộc BẮT BUỘC:
1. Khác với Content, Example ĐƯỢC PHÉP chứa tình huống/ví dụ MỚI do bạn tự
   nghĩ ra — nhưng ví dụ đó PHẢI nhất quán, không mâu thuẫn với khái niệm đã
   nêu trong Content, và KHÔNG được dùng để suy ra thêm dữ kiện mới cho
   Content.
2. `context`: mô tả 1 bối cảnh ứng dụng thực tế cụ thể liên quan Learning
   Objective (không chung chung).
3. `analysis`: lời giải mẫu (worked example) cho bối cảnh đó, chia thành các
   bước đánh số, mỗi bước một dòng ("Bước 1: …", "Bước 2: …", 2-5 bước).
   Mỗi bước làm một việc người học có thể tự làm theo: áp dụng định nghĩa,
   quy tắc hay công thức nào của Content, vào dữ kiện nào của bối cảnh, ra kết
   quả gì. Dòng cuối "Kết luận: …" nối kết quả với khái niệm trong Content, và
   nêu một lỗi người học hay mắc ở một bước nếu Content có nói tới.
4. Bối cảnh (tên người, tình huống, số liệu của bài toán) được tự nghĩ ra,
   nhưng mọi KIẾN THỨC dùng trong các bước (định nghĩa, tính chất, quy tắc,
   công thức) phải có trong Content hoặc tài liệu được cung cấp. Không thêm
   ứng dụng, lịch sử hay tính chất mà Content không nói.
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
    analysis: str = Field(description="Lời giải mẫu: các dòng 'Bước 1: …', 'Bước 2: …', rồi 'Kết luận: …'")


class ExampleOutput(BaseModel):
    examples: list[Example] = Field(
        min_length=2, max_length=2,
        description="Đúng 2 Example, mỗi cái cho một sub-objective khác nhau, theo đúng thứ tự các phần của Content",
    )


def generate_example(
    lo: LearningObjective, content: str, context_chunks: list[Chunk] | None,
    output_language: OutputLanguage = "vi", custom_instructions: str = "",
) -> ExampleOutput:
    """One call, no self-check round: the schema fixes the count (exactly 2, at most one per sub-objective),
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
        prompt, ExampleOutput, system_instruction=instruction + output_language_instruction(output_language) + custom_instructions_instruction(custom_instructions, context_chunks is not None),
        temperature=0.0,
    )
