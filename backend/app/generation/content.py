"""Content: the lesson text written from the retrieved passages and checked against them, or, in the
no-RAG arm, written from the model's own knowledge with the same prompt minus the source block."""

from litellm.utils import type_to_response_format_param
from pydantic import BaseModel, ConfigDict, Field

from app.generation.common import (
    OutputLanguage,
    _checked,
    custom_instructions_instruction,
    output_language_instruction,
    source_block,
    source_id,
    system_instruction,
)
from app.generation.inline_citations import (
    CITATION_LIKE,
    CitationSource,
    InlineCitation,
    derive_citations,
    prompt_source_map,
    unknown_source_codes,
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
7. Mỗi SOURCE CHUNK vật lý có mã riêng [S1], [S2]... Chèn mã trực tiếp trong
   Content ngay SAU câu/khẳng định/bullet quan trọng mà đoạn đó bảo chứng.
   Ví dụ: "Heap insertion takes O(log n). [S2]". Một câu có thể dùng nhiều
   nguồn: "Combined fact. [S1][S3]". Mỗi cặp ngoặc chỉ chứa một mã.
8. Chỉ dùng đúng mã có trong SOURCE CHUNK của lượt này. Không viết source_ids,
   claim_citations, locator, URL hay chép lại câu trong một danh sách riêng.
   Marker phải theo sát đúng câu, không gắn một mã ở cuối đoạn để trích cả đoạn.
   Giữ nguyên dạng [S2] cho cả đầu ra tiếng Việt và tiếng Anh.
"""

# Nhánh không RAG (nhóm đối chứng llm_no_rag): cùng nhiệm vụ, chỉ không có nguồn nào để bám.
_CONTENT_NO_SOURCE_RULES = """
Nguồn: không có tài liệu nguồn nào được cung cấp; viết Content từ kiến thức
chuyên môn của chính bạn. Không có nguồn nên KHÔNG viết mã trích dẫn [S1], [S2] hay URL nguồn.
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
    """Content của nhánh không RAG: không có nguồn nên không có ID nguồn hay trích dẫn."""

    content: str = Field(description="Nội dung Markdown phân cấp")


class ContentOutput(LessonText):
    """The model writes only Markdown with inline source codes."""

    model_config = ConfigDict(extra="forbid")


class SourcedContent(LessonText):
    source_map: dict[str, CitationSource]
    source_locators: list[str]
    inline_citations: list[InlineCitation]


def _unknown_source_ids(content: ContentOutput, chunks: list[Chunk]) -> list[str]:
    return unknown_source_codes(content.content, prompt_source_map(chunks))


def resolve_citations(content: ContentOutput, chunks: list[Chunk]) -> SourcedContent:
    """Resolve only codes from this prompt; locators and claims come from the server."""
    mapping = prompt_source_map(chunks)
    citations = derive_citations(content.content, mapping)
    return SourcedContent(content=content.content, source_map=mapping, inline_citations=citations,
                         source_locators=list(dict.fromkeys(c.locator for c in citations if c.locator)))


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
  mã inline [S#] ngay sau câu trong Content (không chỉ đọc lướt content rồi đánh
  giá cảm tính chung chung) -- với MỖI claim, mở ĐÚNG source chunk mà
  mã inline của claim đó trỏ tới, và xác nhận claim THỰC SỰ được nội dung
  CHÍNH XÁC tập chunk đã gắn trực tiếp cùng câu đó bảo chứng (không phải "nghe hợp lý"/"đúng về mặt kiến
  thức chung", không phải chunk KHÁC trong toàn bộ context tình cờ nói tới). source_id
  không có trong các SOURCE CHUNK cũng là fail.
  Đây là lỗi THẬT đã từng xảy ra: model tự điền thêm kiến thức phổ biến/đúng
  nhưng KHÔNG có trong đúng chunk đã trích dẫn (vd tự thêm công thức/định
  nghĩa chuẩn của 1 khái niệm liên quan mà nguồn chỉ nhắc tên chứ không giải
  thích) -- claim nghe có vẻ đúng và cả content lẫn source_id đều tồn tại KHÔNG
  có nghĩa là claim đó thực sự đến từ đúng chunk ấy, PHẢI đối chiếu trực
  tiếp. Nếu content có khẳng định KHÔNG có mã inline, cũng phải
  kiểm tra tương tự với TOÀN BỘ source chunk. Với completeness_pass: heading
  dùng câu "không đủ thông tin" để né dù nguồn thực ra có đủ dữ kiện cho phần
  đó cũng là fail.

Nếu vi phạm bất kỳ tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể, TRÍCH
NGUYÊN VĂN claim bị lỗi và source_id của nó (để dùng làm feedback sinh lại).
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
        sources = source_block(context_chunks)
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
    check = complete_structured(
        prompt, ContentVerification, system_instruction=CONTENT_VERIFY_SYSTEM_INSTRUCTION, temperature=0.0
    )
    # The critic can approve an invented ID too; membership is a server check, not a judgement.
    unknown = _unknown_source_ids(content_out, context_chunks)
    if unknown:
        check.groundedness_pass = False
        check.groundedness_reason += f"; Unknown source IDs: {', '.join(unknown)}. Use only the supplied IDs."
    return check


def generate_content(
    lo: LearningObjective, context_chunks: list[Chunk] | None, self_check: bool = True,
    output_language: OutputLanguage = "vi", custom_instructions: str = "",
) -> tuple[LessonText | SourcedContent, ContentVerification | LessonVerification | None]:
    """`context_chunks=None` is the no-RAG arm: the same prompt without the source block, written from
    the model's own knowledge, and checked for completeness and scope only (there is nothing to hold
    it against). With `self_check` off the lesson is written once without a model check (the check is
    None): the arm of the self-check ablation. Source ID membership is always enforced."""
    sourced = context_chunks is not None
    sub_objectives_text = "\n".join(f"- {so}" for so in lo.sub_objectives)
    allowed_ids = [source_id(i) for i in range(len(context_chunks))] if sourced else []
    source_ids_rule = (
        f"Allowed inline citation codes: "
        f"{', '.join('[' + i + ']' for i in allowed_ids) or '(none)'}. "
        "Use only these IDs, never page numbers or IDs inferred from source text.\n"
        if sourced else ""
    )
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
                + output_language_instruction(output_language) + custom_instructions_instruction(custom_instructions, sourced=False), temperature=0.0,
            )
        return complete_structured(
            prompt, ContentOutput, system_instruction=CONTENT_SYSTEM_INSTRUCTION + "\n" + source_ids_rule
            + output_language_instruction(output_language) + custom_instructions_instruction(custom_instructions),
            response_format=type_to_response_format_param(ContentOutput), temperature=0.0,
        )

    criteria = [("completeness", "Completeness"), ("scope", "Scope")]
    if sourced:
        criteria.insert(0, ("groundedness", "Groundedness"))
    # Best-effort on semantic checks, but an unresolvable citation must not be published.
    out, check = _checked(
        generate, (lambda out: _verify_content(out, lo, context_chunks)) if self_check else None, criteria
    )
    if sourced and (unknown := _unknown_source_ids(out, context_chunks)):
        raise ValueError(f"Content cites unknown source IDs: {', '.join(unknown)}")
    if not sourced and CITATION_LIKE.search(out.content):
        raise ValueError("No-RAG Content must not contain citations")
    return (resolve_citations(out, context_chunks) if sourced else out), check
