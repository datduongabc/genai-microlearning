"""Content: the lesson text written from the retrieved passages, checked against them."""

from pydantic import BaseModel, Field

from app.generation.common import (
    IGNORE_EMBEDDED_INSTRUCTIONS,
    format_source_chunks,
)
from app.generation.objective import LearningObjective
from app.llm.gateway import complete_structured
from app.models import Chunk

# ---------------------------------------------------------------------------
# 3. Content — grounded qua retrieval pipeline (mục 5.2). CRAG relevance-check
# đầy đủ (LangGraph, retry) được xây ở Tuần 8 cho Quiz/Practice; Content ở
# Tuần 6 dựa vào chất lượng retrieve() (hybrid+rerank) đã có sẵn từ Tuần 4-5.
# ---------------------------------------------------------------------------

CONTENT_SYSTEM_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + """Bạn là chuyên gia biên soạn nội dung Microlearning cho giáo dục đại học.

Nhiệm vụ: từ 1 Learning Objective và các đoạn nguồn (source chunk) liên quan
được cung cấp, viết phần Content — nội dung cốt lõi CHỈ ĐỦ để người học đạt
được đúng Learning Objective đó, không thừa không thiếu.

Ràng buộc BẮT BUỘC:
1. CHỈ được khẳng định điều THỰC SỰ có trong SOURCE CHUNK — không thêm dữ
   kiện/kiến thức ngoài nguồn, kể cả khi bạn biết nó đúng từ nơi khác. Rủi ro
   LỚN NHẤT không phải bịa điều sai, mà là "giúp đỡ quá tay": khi nguồn chỉ
   nhắc TÊN 1 khái niệm liên quan (vd chỉ 1 câu nhắc thoáng tên một phương pháp)
   mà không giải thích, TUYỆT ĐỐI KHÔNG được tự viết thêm định nghĩa/công
   thức/cơ chế chuẩn của khái niệm đó từ kiến thức nền của bạn dù đúng 100%
   trên thực tế — nếu SOURCE CHUNK không tự giải thích, bạn cũng KHÔNG được
   giải thích thay nó.
1b. Nếu nguồn KHÔNG nêu rõ 1 chi tiết cụ thể (vd ngôn ngữ lập trình, phiên
    bản, tên công cụ) và không thể suy luận chắc chắn từ nguồn, KHÔNG được tự
    giả định giá trị cụ thể rồi khẳng định như sự thật — giữ chung chung.
2. Output là Markdown phân cấp (heading + bullet list), không dùng văn xuôi
   dài dòng.
3. Ngôn ngữ của content PHẢI khớp ngôn ngữ chính của SOURCE CHUNK (tiếng
   Việt có dấu đầy đủ nếu nguồn là tiếng Việt).
4. source_locators chỉ liệt kê locator của những chunk THỰC SỰ được dùng để
   sinh content — không liệt kê locator của chunk bị bỏ qua vì không liên quan.
5. Content PHẢI có 1 heading (mục/phần) riêng cho MỖI sub_objective được cung
   cấp bên dưới (đúng thứ tự, đủ số lượng) — đây là cơ chế ép bao phủ đủ rộng
   nguồn, không chỉ tổng hợp 1 góc hẹp trùng khớp objective_text tổng thể.
   Nếu 1 sub_objective thực sự không có căn cứ trong SOURCE CHUNK được cung
   cấp, vẫn giữ heading đó nhưng ghi rõ "Không đủ thông tin trong tài liệu
   nguồn cho phần này" — KHÔNG được bỏ qua heading hay tự bịa nội dung để
   lấp đầy.
6. claim_citations: với MỖI khẳng định/bullet QUAN TRỌNG trong content (không
   cần từng câu nối/chuyển ý), tạo 1 mục claim_citations gồm `claim` (trích
   gần đúng nguyên văn khẳng định đó, đủ để định vị trong content) và
   `locator` (locator của ĐÚNG 1 source chunk bảo chứng trực tiếp cho khẳng
   định đó — không phải locator chung chung của cả content). Đây là cơ chế
   cho phép người đọc kiểm tra TỪNG câu thay vì tin cả khối content.
"""


class ClaimCitation(BaseModel):
    claim: str = Field(description="Khẳng định/bullet cụ thể trong content (trích gần đúng nguyên văn)")
    locator: str = Field(description="Locator của source chunk bảo chứng trực tiếp cho đúng khẳng định này")


class ContentOutput(BaseModel):
    content: str = Field(description="Nội dung Markdown phân cấp")
    source_locators: list[str] = Field(description="Locator của chunk thực sự được dùng")
    claim_citations: list[ClaimCitation] = Field(
        default_factory=list,
        description="Trích dẫn cấp câu/khẳng định -- mỗi claim quan trọng gắn đúng 1 locator bảo chứng",
    )


# Tầng 3 (GD2, giống LO/Quiz): Content trước đây sinh 1 lần, không ai kiểm
# lại -- đây là lỗ hổng cuối cùng trong 7 thành phần (LO và Quiz/Practice đã
# vá). Nghiên cứu thật (claim-by-claim verification -- xem checklist GD2)
# xác nhận dạng lỗi RAG phổ biến nhất là "unfaithfulness": model CÓ nguồn
# đúng nhưng bỏ sót/hiểu sai/chỉ lấy phần tiện, không phải bịa từ hư không --
# khó bắt bằng mắt thường, cần kiểm chứng độc lập.
MAX_CONTENT_CORRECTION_RETRY = 1

CONTENT_VERIFY_SYSTEM_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + """Bạn là bộ kiểm tra chất lượng Content (Critic), dựa trên các đoạn nguồn
được cung cấp.

Chấm đúng 3 tiêu chí:
- groundedness_pass: kiểm tra THEO TỪNG MỤC trong CLAIM_CITATIONS được cung
  cấp bên dưới (không chỉ đọc lướt content rồi đánh giá cảm tính chung
  chung) -- với MỖI claim, mở ĐÚNG source chunk mà `locator` của claim đó
  trỏ tới, và xác nhận claim THỰC SỰ được nội dung CHÍNH XÁC chunk đó bảo
  chứng (không phải "nghe hợp lý"/"đúng về mặt kiến thức chung", không phải
  chunk KHÁC trong toàn bộ context tình cờ nói tới). Đây là lỗi THẬT đã từng
  xảy ra: model tự điền thêm kiến thức phổ biến/đúng nhưng KHÔNG có trong
  đúng chunk đã trích dẫn (vd tự thêm công thức/định nghĩa chuẩn của 1 khái
  niệm liên quan mà nguồn chỉ nhắc tên chứ không giải thích) -- claim nghe
  có vẻ đúng và cả content lẫn locator đều tồn tại KHÔNG có nghĩa là claim
  đó thực sự đến từ đúng locator ấy, PHẢI đối chiếu trực tiếp. Nếu content
  có khẳng định KHÔNG nằm trong claim_citations, cũng phải kiểm tra tương tự
  với TOÀN BỘ source chunk.
- completeness_pass: MỖI heading ứng với 1 sub_objective có nội dung THẬT SỰ
  hữu ích không, hay dùng câu "không đủ thông tin" để né dù nguồn thực ra có
  đủ dữ kiện cho phần đó?
- scope_pass: content có dừng đúng phạm vi Learning Objective/sub_objectives
  không, không lan sang chủ đề khác không liên quan?

Nếu vi phạm bất kỳ tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể, TRÍCH
NGUYÊN VĂN claim bị lỗi và locator của nó (để dùng làm feedback sinh lại).
"""


class ContentVerification(BaseModel):
    groundedness_pass: bool
    groundedness_reason: str
    completeness_pass: bool
    completeness_reason: str
    scope_pass: bool
    scope_reason: str


def _verify_content(content_out: ContentOutput, lo: LearningObjective, context_chunks: list[Chunk]) -> ContentVerification:
    citations_text = "\n".join(
        f'- claim="{c.claim}" -> locator="{c.locator}"' for c in content_out.claim_citations
    ) or "(content không có claim_citations nào)"
    prompt = (
        f"LEARNING OBJECTIVE: {lo.objective_text}\n"
        f"SUB-OBJECTIVES: {lo.sub_objectives}\n\n"
        f"CONTENT ĐÃ SINH:\n{content_out.content}\n\n"
        f"CLAIM_CITATIONS (mỗi claim PHẢI đối chiếu ĐÚNG locator của chính nó, "
        f"không phải đối chiếu chung với toàn bộ source chunk):\n{citations_text}\n\n"
        f"{format_source_chunks(context_chunks)}\n\n"
        f"Kiểm tra groundedness_pass, completeness_pass, scope_pass của Content trên."
    )
    return complete_structured(
        prompt, ContentVerification, system_instruction=CONTENT_VERIFY_SYSTEM_INSTRUCTION, temperature=0.0
    )


def generate_content(
    lo: LearningObjective, context_chunks: list[Chunk]
) -> tuple[ContentOutput, ContentVerification]:
    sub_objectives_text = "\n".join(f"- {so}" for so in lo.sub_objectives)
    feedback: str | None = None
    content_out: ContentOutput | None = None
    check: ContentVerification | None = None
    for _ in range(MAX_CONTENT_CORRECTION_RETRY + 1):
        prompt = (
            f"LEARNING OBJECTIVE (Terminal): {lo.objective_text} (bloom_level={lo.bloom_level})\n"
            f"SUB-OBJECTIVES (Enabling — mỗi cái cần 1 heading riêng trong Content):\n{sub_objectives_text}\n\n"
            f"{format_source_chunks(context_chunks)}\n\n"
            f"Sinh phần Content cho Learning Objective trên, dựa trên các đoạn nguồn đã cung cấp."
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
        content_out = complete_structured(
            prompt, ContentOutput, system_instruction=CONTENT_SYSTEM_INSTRUCTION, temperature=0.0
        )

        check = _verify_content(content_out, lo, context_chunks)
        if check.groundedness_pass and check.completeness_pass and check.scope_pass:
            return content_out, check

        reasons = []
        if not check.groundedness_pass:
            reasons.append(f"Groundedness fail: {check.groundedness_reason}")
        if not check.completeness_pass:
            reasons.append(f"Completeness fail: {check.completeness_reason}")
        if not check.scope_pass:
            reasons.append(f"Scope fail: {check.scope_reason}")
        feedback = "; ".join(reasons)

    return content_out, check  # best-effort sau khi hết retry budget, giống pattern LO/quiz_graph.py
