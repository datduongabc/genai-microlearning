"""Content: the lesson text written from the retrieved passages and checked against them, or, in the
no-RAG arm, written from the model's own knowledge with the same prompt minus the source block."""

from pydantic import BaseModel, Field

from app.generation.common import (
    OutputLanguage,
    _checked,
    custom_instructions_instruction,
    output_language_instruction,
    source_block,
    system_instruction,
)
from app.generation.objective import LearningObjective
from app.llm.gateway import complete_structured
from app.models import Chunk

# ---------------------------------------------------------------------------
# 3. Content — grounded qua retrieval pipeline (mục 5.2). Corrective RAG
# (chấm độ liên quan, truy hồi lại tối đa 1 lần) chạy một lần trước Content,
# trong module_generation_pipeline.py.
# ---------------------------------------------------------------------------

_CONTENT_BODY = """Bạn là chuyên gia biên soạn nội dung Microlearning cho giáo dục đại học.

Nhiệm vụ: từ 1 Learning Objective và các sub-objective của nó, viết phần
Content — nội dung cốt lõi CHỈ ĐỦ để người học đạt được đúng Learning
Objective đó, không thừa không thiếu.

Ràng buộc BẮT BUỘC:
1. Output là Markdown phân cấp (heading + bullet list), không dùng văn xuôi
   dài dòng.
2. Content PHẢI có 1 heading (mục/phần) riêng cho MỖI sub_objective được cung
   cấp bên dưới (đúng thứ tự, đủ số lượng) — đây là cơ chế ép bao phủ đủ rộng,
   không chỉ tổng hợp 1 góc hẹp trùng khớp objective_text tổng thể.
"""

_CONTENT_SOURCE_RULES = """
Nguồn: viết Content dựa trên các đoạn nguồn (SOURCE CHUNK) được cung cấp.
4. CHỈ được khẳng định điều THỰC SỰ có trong SOURCE CHUNK — không thêm dữ
   kiện/kiến thức ngoài nguồn, kể cả khi bạn biết nó đúng từ nơi khác. Rủi ro
   LỚN NHẤT không phải bịa điều sai, mà là "giúp đỡ quá tay": khi nguồn chỉ
   nhắc TÊN 1 khái niệm liên quan (vd chỉ 1 câu nhắc thoáng tên một phương pháp)
   mà không giải thích, TUYỆT ĐỐI KHÔNG được tự viết thêm định nghĩa/công
   thức/cơ chế chuẩn của khái niệm đó từ kiến thức nền của bạn dù đúng 100%
   trên thực tế — nếu SOURCE CHUNK không tự giải thích, bạn cũng KHÔNG được
   giải thích thay nó.
5. Nếu nguồn KHÔNG nêu rõ 1 chi tiết cụ thể (vd ngôn ngữ lập trình, phiên
   bản, tên công cụ) và không thể suy luận chắc chắn từ nguồn, KHÔNG được tự
   giả định giá trị cụ thể rồi khẳng định như sự thật — giữ chung chung.
6. Nếu 1 sub_objective thực sự không có căn cứ trong SOURCE CHUNK được cung
   cấp, vẫn giữ heading đó nhưng ghi rõ "Không đủ thông tin trong tài liệu
   nguồn cho phần này" — KHÔNG được bỏ qua heading hay tự bịa nội dung để
   lấp đầy.
7. source_locators chỉ liệt kê locator của những chunk THỰC SỰ được dùng để
   sinh content — không liệt kê locator của chunk bị bỏ qua vì không liên quan.
8. claim_citations: với MỖI khẳng định/bullet QUAN TRỌNG trong content (không
   cần từng câu nối/chuyển ý), tạo 1 mục claim_citations gồm `claim` (trích
   gần đúng nguyên văn khẳng định đó, đủ để định vị trong content) và
   `locator` (locator của ĐÚNG 1 source chunk bảo chứng trực tiếp cho khẳng
   định đó — không phải locator chung chung của cả content). Đây là cơ chế
   cho phép người đọc kiểm tra TỪNG câu thay vì tin cả khối content.
"""

# Nhánh không RAG (nhóm đối chứng llm_no_rag): cùng nhiệm vụ, chỉ không có nguồn nào để bám.
_CONTENT_NO_SOURCE_RULES = """
Nguồn: không có tài liệu nguồn nào được cung cấp; viết Content từ kiến thức
chuyên môn của chính bạn.
4. Không khẳng định một chi tiết cụ thể mà bạn không chắc chắn (tên công cụ,
   phiên bản, số liệu, năm) như thể đó là sự thật; khi không chắc, giữ ở mức
   khái niệm chung.
"""

CONTENT_SYSTEM_INSTRUCTION = system_instruction(
    _CONTENT_BODY, _CONTENT_SOURCE_RULES, _CONTENT_NO_SOURCE_RULES, sourced=True
)
NO_SOURCE_CONTENT_SYSTEM_INSTRUCTION = system_instruction(
    _CONTENT_BODY, _CONTENT_SOURCE_RULES, _CONTENT_NO_SOURCE_RULES, sourced=False
)


class LessonText(BaseModel):
    """Content của nhánh không RAG: không có nguồn nên không có locator hay trích dẫn."""

    content: str = Field(description="Nội dung Markdown phân cấp")


class ClaimCitation(BaseModel):
    claim: str = Field(description="Khẳng định/bullet cụ thể trong content (trích gần đúng nguyên văn)")
    locator: str = Field(description="Locator của source chunk bảo chứng trực tiếp cho đúng khẳng định này")


class ContentOutput(LessonText):
    source_locators: list[str] = Field(description="Locator của chunk thực sự được dùng")
    claim_citations: list[ClaimCitation] = Field(
        default_factory=list,
        description="Trích dẫn cấp câu/khẳng định -- mỗi claim quan trọng gắn đúng 1 locator bảo chứng",
    )


# Tầng 3 (GD2): Content và Quiz/Practice là hai bước còn giữ vòng tự kiểm --
# mọi bước sau đọc lại Content, nên lỗi ở đây lan ra cả module. Nghiên cứu thật (claim-by-claim verification -- xem checklist GD2)
# xác nhận dạng lỗi RAG phổ biến nhất là "unfaithfulness": model CÓ nguồn
# đúng nhưng bỏ sót/hiểu sai/chỉ lấy phần tiện, không phải bịa từ hư không --
# khó bắt bằng mắt thường, cần kiểm chứng độc lập. Một lần sinh lại khi trượt
# (common.MAX_CORRECTION_RETRY).

_CONTENT_VERIFY_BODY = """Bạn là bộ kiểm tra chất lượng Content (Critic).

Chấm các tiêu chí sau:
- completeness_pass: MỖI heading ứng với 1 sub_objective có nội dung THẬT SỰ
  hữu ích để đạt sub_objective đó không, hay chỉ nêu tên rồi bỏ trống/nói
  chung chung?
- scope_pass: content có dừng đúng phạm vi Learning Objective/sub_objectives
  không, không lan sang chủ đề khác không liên quan?
"""

_CONTENT_VERIFY_SOURCE_RULES = """- groundedness_pass (chỉ khi có SOURCE CHUNK): kiểm tra THEO TỪNG MỤC trong
  CLAIM_CITATIONS được cung cấp bên dưới (không chỉ đọc lướt content rồi đánh
  giá cảm tính chung chung) -- với MỖI claim, mở ĐÚNG source chunk mà
  `locator` của claim đó trỏ tới, và xác nhận claim THỰC SỰ được nội dung
  CHÍNH XÁC chunk đó bảo chứng (không phải "nghe hợp lý"/"đúng về mặt kiến
  thức chung", không phải chunk KHÁC trong toàn bộ context tình cờ nói tới).
  Đây là lỗi THẬT đã từng xảy ra: model tự điền thêm kiến thức phổ biến/đúng
  nhưng KHÔNG có trong đúng chunk đã trích dẫn (vd tự thêm công thức/định
  nghĩa chuẩn của 1 khái niệm liên quan mà nguồn chỉ nhắc tên chứ không giải
  thích) -- claim nghe có vẻ đúng và cả content lẫn locator đều tồn tại KHÔNG
  có nghĩa là claim đó thực sự đến từ đúng locator ấy, PHẢI đối chiếu trực
  tiếp. Nếu content có khẳng định KHÔNG nằm trong claim_citations, cũng phải
  kiểm tra tương tự với TOÀN BỘ source chunk. Với completeness_pass: heading
  dùng câu "không đủ thông tin" để né dù nguồn thực ra có đủ dữ kiện cho phần
  đó cũng là fail.

Nếu vi phạm bất kỳ tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể, TRÍCH
NGUYÊN VĂN claim bị lỗi và locator của nó (để dùng làm feedback sinh lại).
"""

_CONTENT_VERIFY_NO_SOURCE_RULES = """
Nếu vi phạm tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể (để dùng làm
feedback sinh lại).
"""

CONTENT_VERIFY_SYSTEM_INSTRUCTION = system_instruction(
    _CONTENT_VERIFY_BODY, _CONTENT_VERIFY_SOURCE_RULES, _CONTENT_VERIFY_NO_SOURCE_RULES, sourced=True
)
NO_SOURCE_CONTENT_VERIFY_SYSTEM_INSTRUCTION = system_instruction(
    _CONTENT_VERIFY_BODY, _CONTENT_VERIFY_SOURCE_RULES, _CONTENT_VERIFY_NO_SOURCE_RULES, sourced=False
)


class LessonVerification(BaseModel):
    """Kết quả kiểm Content của nhánh không RAG: không có nguồn nên không chấm groundedness."""

    completeness_pass: bool
    completeness_reason: str
    scope_pass: bool
    scope_reason: str


class ContentVerification(BaseModel):
    groundedness_pass: bool
    groundedness_reason: str
    completeness_pass: bool
    completeness_reason: str
    scope_pass: bool
    scope_reason: str


def _verify_content(
    content_out: LessonText, lo: LearningObjective, context_chunks: list[Chunk] | None
) -> ContentVerification | LessonVerification:
    # Khối nguồn của bước kiểm gồm trích dẫn cấp khẳng định và các SOURCE CHUNK chúng trỏ tới.
    sources = ""
    if context_chunks is not None:
        citations_text = "\n".join(
            f'- claim="{c.claim}" -> locator="{c.locator}"' for c in content_out.claim_citations
        ) or "(content không có claim_citations nào)"
        sources = (
            f"CLAIM_CITATIONS (mỗi claim PHẢI đối chiếu ĐÚNG locator của chính nó, "
            f"không phải đối chiếu chung với toàn bộ source chunk):\n{citations_text}\n\n"
            f"{source_block(context_chunks)}"
        )
    prompt = (
        f"LEARNING OBJECTIVE: {lo.objective_text}\n"
        f"SUB-OBJECTIVES: {lo.sub_objectives}\n\n"
        f"CONTENT ĐÃ SINH:\n{content_out.content}\n\n"
        f"{sources}"
        f"Kiểm tra các tiêu chí đã nêu cho Content trên."
    )
    if context_chunks is None:
        return complete_structured(
            prompt, LessonVerification, system_instruction=NO_SOURCE_CONTENT_VERIFY_SYSTEM_INSTRUCTION, temperature=0.0
        )
    return complete_structured(
        prompt, ContentVerification, system_instruction=CONTENT_VERIFY_SYSTEM_INSTRUCTION, temperature=0.0
    )


def generate_content(
    lo: LearningObjective, context_chunks: list[Chunk] | None, self_check: bool = True,
    output_language: OutputLanguage = "vi", custom_instructions: str = "",
) -> tuple[LessonText, ContentVerification | LessonVerification | None]:
    """`context_chunks=None` is the no-RAG arm: the same prompt without the source block, written from
    the model's own knowledge, and checked for completeness and scope only (there is nothing to hold
    it against). With `self_check` off the lesson is written once and nothing checks it (the check is
    None): the arm of the self-check ablation."""
    sourced = context_chunks is not None
    sub_objectives_text = "\n".join(f"- {so}" for so in lo.sub_objectives)

    def generate(feedback: str | None) -> LessonText:
        prompt = (
            f"LEARNING OBJECTIVE (Terminal): {lo.objective_text} (bloom_level={lo.bloom_level})\n"
            f"SUB-OBJECTIVES (Enabling — mỗi cái cần 1 heading riêng trong Content):\n{sub_objectives_text}\n\n"
            f"{source_block(context_chunks)}"
            f"Sinh phần Content cho Learning Objective trên."
        )
        if feedback:
            prompt += (
                f"\n\nLƯU Ý: lần sinh trước KHÔNG ĐẠT vì lý do sau -- sửa đúng lỗi này, "
                f"không lặp lại: {feedback}"
            )
        # temperature=0.0: Content la tac vu trich xuat/tong hop co can cu tu
        # nguon ("CHI duoc khang dinh dieu THUC SU co trong SOURCE CHUNK"),
        # khong phai sang tac tu do -- giam sampling giup giam rui ro tu
        # "dien giai qua tay" (unfaithfulness), nhat quan voi Planner/LO.
        if not sourced:
            return complete_structured(
                prompt, LessonText, system_instruction=NO_SOURCE_CONTENT_SYSTEM_INSTRUCTION
                + output_language_instruction(output_language) + custom_instructions_instruction(custom_instructions), temperature=0.0,
            )
        return complete_structured(
            prompt, ContentOutput, system_instruction=CONTENT_SYSTEM_INSTRUCTION
            + output_language_instruction(output_language) + custom_instructions_instruction(custom_instructions), temperature=0.0,
        )

    criteria = [("completeness", "Completeness"), ("scope", "Scope")]
    if sourced:
        criteria.insert(0, ("groundedness", "Groundedness"))
    # Hết lượt sinh lại thì trả bản cuối (best-effort), như câu hỏi.
    return _checked(
        generate, (lambda out: _verify_content(out, lo, context_chunks)) if self_check else None, criteria
    )
