"""Examples: worked applications of the lesson, at most one per sub-objective, checked not to
overreach their source."""

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


# Tầng 3 (GD2, cùng pattern LO/Content/Slides/Explanation): Example là thành
# phần CUỐI CÙNG trong 7 thành phần thiếu bước kiểm tra sau khi sinh. Khác
# các thành phần kia, Example được PHÉP sáng tạo tình huống mới -- nên không
# kiểm groundedness (không áp dụng), mà kiểm 3 tiêu chí khác: tình huống có
# thực sự minh họa đúng khái niệm không (không lạc đề/mâu thuẫn), có bị lợi
# dụng để "tuồn" thêm dữ kiện mới vào như thể đã có trong Content không
# (ràng buộc 1 trong prompt, chưa từng verify), và văn phong/thuật ngữ có
# nhất quán với nguồn (few-shot) không.
MAX_EXAMPLE_CORRECTION_RETRY = 1

EXAMPLE_VERIFY_SYSTEM_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + """Bạn là bộ kiểm tra chất lượng Example (Critic), dựa trên Content và các
đoạn nguồn được cung cấp.

Chấm đúng 3 tiêu chí:
Mỗi tiêu chí xét trên TỪNG Example trong danh sách: chỉ cần 1 Example vi
phạm là tiêu chí đó fail, và lý do phải nêu rõ Example số mấy.

- consistency_pass: bối cảnh (context) và phân tích (analysis) có thực sự
  minh họa ĐÚNG khái niệm trong Content không, hay lạc đề/mâu thuẫn với
  Content?
- no_overreach_pass: Example được PHÉP sáng tạo 1 tình huống minh họa mới,
  nhưng KHÔNG được dùng tình huống đó để khẳng định thêm dữ kiện/tính chất
  MỚI về chính khái niệm đang học (những dữ kiện không có trong Content) --
  chỉ được minh họa khái niệm đã có, không mở rộng thêm hiểu biết về nó.
  Nếu analysis suy ra thêm 1 đặc tính/quy tắc mới của khái niệm mà Content
  không hề nói tới, đặt fail.
- style_pass: văn phong và thuật ngữ dùng trong context/analysis có nhất
  quán với văn phong/thuật ngữ của source chunk (few-shot) không?

Nếu vi phạm bất kỳ tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể (để
dùng làm feedback sinh lại).
"""


class ExampleVerification(BaseModel):
    consistency_pass: bool
    consistency_reason: str
    no_overreach_pass: bool
    no_overreach_reason: str
    style_pass: bool
    style_reason: str


def _format_examples(example_out: ExampleOutput) -> str:
    return "\n".join(
        f"Example {n}:\n  context: {e.context}\n  analysis: {e.analysis}"
        for n, e in enumerate(example_out.examples, start=1)
    )


def _verify_example(example_out: ExampleOutput, content: str, context_chunks: list[Chunk]) -> ExampleVerification:
    prompt = (
        f"CONTENT:\n{content}\n\n"
        f"CÁC EXAMPLE ĐÃ SINH:\n{_format_examples(example_out)}\n\n"
        f"{format_source_chunks(context_chunks)}\n\n"
        f"Kiểm tra consistency_pass, no_overreach_pass, style_pass của các Example trên."
    )
    return complete_structured(
        prompt, ExampleVerification, system_instruction=EXAMPLE_VERIFY_SYSTEM_INSTRUCTION, temperature=0.0
    )


def generate_example(
    lo: LearningObjective, content: str, context_chunks: list[Chunk]
) -> tuple[ExampleOutput, ExampleVerification]:
    feedback: str | None = None
    example_out: ExampleOutput | None = None
    check: ExampleVerification | None = None
    sub_objectives_text = "\n".join(f"- {so}" for so in lo.sub_objectives)
    for _ in range(MAX_EXAMPLE_CORRECTION_RETRY + 1):
        prompt = (
            f"LEARNING OBJECTIVE: {lo.objective_text}\n"
            f"SUB-OBJECTIVES (tối đa 1 Example cho mỗi cái):\n{sub_objectives_text}\n\n"
            f"CONTENT:\n{content}\n\n"
            f"{format_source_chunks(context_chunks)}\n\n"
            f"Viết các Example cho Learning Objective/Content trên, tham khảo văn phong/thuật ngữ từ các SOURCE CHUNK (few-shot)."
        )
        if feedback:
            prompt += (
                f"\n\nLƯU Ý: lần sinh trước KHÔNG ĐẠT vì lý do sau -- sửa đúng lỗi này, "
                f"không lặp lại: {feedback}"
            )
        # temperature=0.0: du Example duoc phep sang tao tinh huong moi, viec
        # LUA CHON tinh huong nao + phan tich no van nen nhat quan/co the du
        # doan duoc giua cac lan goi, khong can bien thien ngau nhien -- nhat
        # quan voi phan con lai cua pipeline.
        example_out = complete_structured(
            prompt, ExampleOutput, system_instruction=EXAMPLE_SYSTEM_INSTRUCTION, temperature=0.0
        )

        check = _verify_example(example_out, content, context_chunks)
        if check.consistency_pass and check.no_overreach_pass and check.style_pass:
            return example_out, check

        reasons = []
        if not check.consistency_pass:
            reasons.append(f"Consistency fail: {check.consistency_reason}")
        if not check.no_overreach_pass:
            reasons.append(f"Overreach fail: {check.no_overreach_reason}")
        if not check.style_pass:
            reasons.append(f"Style fail: {check.style_reason}")
        feedback = "; ".join(reasons)

    return example_out, check  # best-effort sau khi hết retry budget, giống pattern LO/Content/Slides/Explanation
