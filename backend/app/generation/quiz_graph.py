"""Quiz/Practice: một câu hỏi trắc nghiệm cho mỗi điều cần kiểm, cùng lời giải của nó.

Mỗi câu: truy hồi top-5 đoạn theo đúng điều cần kiểm (sub-objective) -> Question Generator
(misconception CoT + Bloom's + JSON schema) -> kiểm câu vừa sinh (Syntax/IWF heuristic, và khi bật
tự kiểm thì model chấm Groundedness, Answerability, Bloom's) -> trượt thì sinh lại đúng một lần với
lý do trượt. Vòng sinh-kiểm-sinh lại là `_checked` dùng chung với Content. Nhánh không RAG dùng
đúng hàm này, chỉ thay đoạn truy hồi bằng bài học (Content) đã viết.
Không chấm độ liên quan riêng cho từng câu: Corrective RAG chỉ chạy một lần trước Content.

Practice tái dùng NGUYÊN hàm này (mode="practice"): ngưỡng chấp nhận thấp hơn, syntax-check không
chặn, không tốn hạ tầng riêng.
"""

import uuid
from dataclasses import dataclass, field
from typing import Literal, TypedDict

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.generation.common import (
    BLOOM_LEVEL_ANCHORS,
    OutputLanguage,
    _checked,
    failed_criteria,
    output_language_instruction,
    source_block,
    system_instruction,
)
from app.generation.objective import LearningObjective
from app.llm.gateway import complete_structured
from app.retrieval.search import ChunkScope, retrieve

# Số đoạn truy hồi cho mỗi câu hỏi, theo đúng điều câu đó kiểm.
QUESTION_TOP_K = 5

# mục 3.1 bước 3 (Syntax/IWF Analyzer) — loại bỏ từ mang tính tuyệt đối
# thường lộ đáp án ("luôn luôn"/"never" hiếm khi đúng làm distractor).
ABSOLUTE_WORDS = [
    "luôn luôn", "không bao giờ", "tất cả đều", "duy nhất", "chỉ có",
    "mọi trường hợp", "always", "never", "all of the above", "none of the above",
]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class QuizQuestion(BaseModel):
    common_misconceptions: list[str] = Field(
        min_length=3, max_length=3,
        description="3 quan niệm sai/lỗi phổ biến học viên hay mắc về khái niệm này -- PHÂN TÍCH TRƯỚC khi viết choices",
    )
    stem: str
    # Declared before the choices and the key on purpose: the model fills the fields in the order
    # they are declared, so the options and the answer are written from this reasoning instead of the
    # reasoning being written afterwards to fit them. In a paired run on the same 19 source passages
    # the correct option was the longest one in 84% of the questions when the reasoning came after the
    # choices, against 26% (chance is 25%) when it came before them.
    reasoning: str = Field(
        description="Lập luận TRƯỚC khi viết phương án: đáp án đúng sẽ là gì và vì sao (dẫn chiếu nguồn), "
        "và mỗi misconception dẫn tới kiểu phương án nhiễu sai nào"
    )
    choices: list[str] = Field(
        min_length=4, max_length=4,
        description="1 đáp án đúng + 3 phương án nhiễu viết theo `reasoning`, MỖI nhiễu tương ứng 1 misconception "
        "ở trên theo đúng thứ tự",
    )
    correct_index: int = Field(ge=0, le=3, description="Phương án mà `reasoning` kết luận là đúng")
    # This is the module's Explanation component: no separate step writes or checks another one.
    explanation: str = Field(
        description="Giải thích cho người học, tổng hợp từ `reasoning`: vì sao đáp án đúng là đúng và vì sao "
        "MỖI phương án nhiễu sai"
    )


class GroundednessAnswerabilityCheck(BaseModel):
    groundedness_pass: bool = Field(description="Câu hỏi/đáp án có được bảo chứng hoàn toàn bởi nguồn không")
    groundedness_reason: str
    answerability_pass: bool = Field(
        description="Đúng 1 đáp án đúng, không mâu thuẫn nội tại, không có 2 phương án cùng hợp lý"
    )
    answerability_reason: str
    bloom_level_pass: bool = Field(
        description="Câu hỏi có THỰC SỰ đúng cấp độ Bloom's đã khai báo không (không cao hơn/thấp hơn) -- "
        "kiểm chứng độc lập với bước sinh, vì chỉ yêu cầu qua prompt không đảm bảo model tuân thủ đúng"
    )
    bloom_level_reason: str


class AnswerabilityCheck(BaseModel):
    """Bản kiểm của nhánh không RAG: không có nguồn nên không chấm groundedness."""

    answerability_pass: bool = Field(
        description="Đúng 1 đáp án đúng, không mâu thuẫn nội tại, không có 2 phương án cùng hợp lý"
    )
    answerability_reason: str
    bloom_level_pass: bool = Field(description="Câu hỏi có THỰC SỰ đúng cấp độ Bloom's đã khai báo không")
    bloom_level_reason: str


class QuizResult(TypedDict, total=False):
    """Kết quả sinh một câu. Thiếu `question` khi không sinh được câu nào (chỉ gặp ở hàm giả trong test)."""

    question: QuizQuestion
    passed: bool
    # Lý do trượt của lần kiểm cuối; None khi đạt.
    fail_reason: str | None
    syntax_issues: list[str]
    # Số lần sinh lại sau lần đầu: 0 hoặc 1.
    correction_retry_count: int


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_QUESTION_BODY = """Bạn là chuyên gia thiết kế câu hỏi trắc nghiệm (MCQ) cho giáo dục đại học,
áp dụng kỹ thuật Misconception-based Distractor Design.

Nhiệm vụ: từ Learning Objective, điều cần kiểm, tài liệu được cung cấp bên
dưới, và mức độ (quiz/practice), sinh 1 câu hỏi trắc nghiệm 4 phương án.

Quy trình BẮT BUỘC (đúng thứ tự):
1. Phân tích 3 quan niệm sai/lỗi phổ biến (common_misconceptions) mà học viên
   ở đúng bloom_level của Learning Objective hay mắc phải về điều cần kiểm —
   PHẢI phân tích trước, không được nghĩ ra distractor rồi mới gán lý do.
2. Viết `stem` kiểm tra ĐÚNG bloom_level của Learning Objective — không cao
   hơn/thấp hơn. Dùng ĐÚNG mô tả hành vi của cấp độ đó (được cung cấp trong
   prompt bên dưới, không tự suy luận) để quyết định dạng câu hỏi — không chỉ
   dựa vào tên cấp độ suông (vd Learning Objective ở mức "understand" thì câu
   hỏi không được yêu cầu "phân tích"/"đánh giá").
3. Viết `reasoning` TRƯỚC khi viết các phương án: nêu đáp án đúng sẽ là gì và
   vì sao (dẫn chiếu trực tiếp tài liệu được cung cấp), rồi với MỖI
   misconception đã phân tích nêu kiểu phương án nhiễu sai mà nó dẫn tới.
4. Viết 4 `choices` đúng theo `reasoning`: 1 đáp án đúng + 3 phương án nhiễu,
   MỖI phương án nhiễu tương ứng chính xác 1 misconception theo đúng thứ tự.
   Rồi điền `correct_index` (phải trỏ đúng phương án đúng theo `reasoning`) và
   `explanation` (giải thích ngắn gọn cho người học, tổng hợp từ `reasoning`:
   vì sao đáp án đúng là đúng và vì sao MỖI phương án nhiễu sai).
5. Các phương án PHẢI cân bằng độ dài/ngữ pháp với nhau — không để đáp án
   đúng dài/chi tiết hơn hẳn distractor (dễ đoán). KHÔNG dùng từ mang tính
   tuyệt đối ("luôn luôn", "không bao giờ", "tất cả", "duy nhất") trong bất
   kỳ phương án nào — dễ bị loại trừ mà không cần hiểu bài.
6. Nếu mode="practice", câu hỏi tập trung vào luyện tập/củng cố (được phép
   đơn giản hơn quiz một chút), không phải đánh giá chính thức.
"""

_QUESTION_SOURCE_RULES = """8. Tài liệu là các đoạn nguồn (SOURCE CHUNK): CHỈ được dùng thông tin THỰC SỰ
   có trong nguồn — không thêm dữ kiện ngoài nguồn.
"""

# Nhánh không RAG: câu hỏi đọc bài học (Content) thay cho đoạn truy hồi.
_QUESTION_NO_SOURCE_RULES = """8. Tài liệu là NỘI DUNG BÀI HỌC đã viết (không có tài liệu nguồn nào): câu hỏi
   chỉ kiểm điều ĐÃ ĐƯỢC DẠY trong nội dung đó.
"""

QUESTION_SYSTEM_INSTRUCTION = system_instruction(
    _QUESTION_BODY, _QUESTION_SOURCE_RULES, _QUESTION_NO_SOURCE_RULES, sourced=True
)
NO_SOURCE_QUESTION_SYSTEM_INSTRUCTION = system_instruction(
    _QUESTION_BODY, _QUESTION_SOURCE_RULES, _QUESTION_NO_SOURCE_RULES, sourced=False
)

CORRECTION_SUFFIX_TEMPLATE = """

LƯU Ý: đây là lần sinh lại sau khi câu hỏi trước đó KHÔNG ĐẠT vì lý do sau —
sửa đúng lỗi này, không lặp lại:
{feedback}
"""

_QUESTION_CHECK_BODY = """Bạn là bộ kiểm tra Answerability & Bloom's Fidelity cho câu hỏi trắc nghiệm
đã sinh, dựa trên tài liệu được cung cấp.

Chấm các tiêu chí sau:
- answerability_pass: câu hỏi có ĐÚNG 1 đáp án đúng duy nhất, không mâu thuẫn
  nội tại, và KHÔNG có 2 phương án cùng hợp lý (gây khó phân biệt) không?
  `reasoning` đi kèm (viết trước khi có các phương án) nêu đáp án đúng là gì:
  nội dung đó PHẢI khớp với phương án mà `correct_index` trỏ tới, và các
  phương án nhiễu phải đúng là những kiểu sai nó dự tính — nếu phương án được
  chọn làm đáp án khác với lập luận thì đặt fail.
- bloom_level_pass: câu hỏi có THỰC SỰ đòi hỏi đúng hành vi nhận thức của cấp
  độ Bloom's đã khai báo không (xem mô tả hành vi cụ thể được cung cấp cùng
  câu hỏi) — KHÔNG chỉ dựa vào việc prompt sinh câu hỏi CÓ NHẮC ĐẾN tên cấp độ
  đó; câu hỏi hoàn toàn có thể lặp lại đúng tên cấp độ trong lời dẫn nhưng
  thực chất đòi hỏi hành vi nhận thức khác (thường bị lệch cao hơn khai báo,
  hoặc dễ dàng mặc định ở mức "understand" dù nguồn/LO hỗ trợ cấp cao hơn) —
  đây chính là lý do cần kiểm chứng ĐỘC LẬP ở bước này, không tin cậy hoàn
  toàn vào bước sinh.
"""

_QUESTION_CHECK_SOURCE_RULES = """- groundedness_pass (tài liệu là các đoạn nguồn): `stem`, đáp án đúng, và
  `explanation` có được bảo chứng HOÀN TOÀN bởi nội dung nguồn không (không
  suy diễn/thêm dữ kiện ngoài nguồn)?

Nếu vi phạm bất kỳ tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể (để
dùng làm feedback sinh lại).
"""

_QUESTION_CHECK_NO_SOURCE_RULES = """
Nếu vi phạm bất kỳ tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể (để
dùng làm feedback sinh lại).
"""

GROUNDEDNESS_SYSTEM_INSTRUCTION = system_instruction(
    _QUESTION_CHECK_BODY, _QUESTION_CHECK_SOURCE_RULES, _QUESTION_CHECK_NO_SOURCE_RULES, sourced=True
)
NO_SOURCE_QUESTION_CHECK_SYSTEM_INSTRUCTION = system_instruction(
    _QUESTION_CHECK_BODY, _QUESTION_CHECK_SOURCE_RULES, _QUESTION_CHECK_NO_SOURCE_RULES, sourced=False
)


# ---------------------------------------------------------------------------
# Kiểm một câu
# ---------------------------------------------------------------------------

def _syntax_iwf_check(question: QuizQuestion) -> list[str]:
    issues = []
    lengths = [len(c) for c in question.choices]
    if min(lengths) > 0 and max(lengths) > 2 * min(lengths):
        issues.append("Độ dài phương án chênh lệch quá lớn (có thể lộ đáp án)")
    for i, choice in enumerate(question.choices):
        lowered = choice.lower()
        if any(w in lowered for w in ABSOLUTE_WORDS):
            issues.append(f"Phương án {i} chứa từ mang tính tuyệt đối (dễ bị loại trừ)")
    return issues


@dataclass
class QuestionCheck:
    """Kết quả kiểm một câu, theo dạng `<tên>_pass`/`<tên>_reason` mà `_checked` đọc: ba tiêu chí
    model chấm (đạt sẵn khi tắt tự kiểm) cộng bẫy cú pháp, vốn không tốn lượt gọi nào."""

    groundedness_pass: bool = True
    groundedness_reason: str = ""
    answerability_pass: bool = True
    answerability_reason: str = ""
    bloom_level_pass: bool = True
    bloom_level_reason: str = ""
    syntax_pass: bool = True
    syntax_reason: str = ""
    # Mọi lỗi cú pháp tìm thấy, kể cả ở practice nơi chúng không chặn câu.
    syntax_issues: list[str] = field(default_factory=list)


QUESTION_CRITERIA = [
    ("groundedness", "Groundedness"),
    ("answerability", "Answerability"),
    ("bloom_level", "Bloom's level"),
    ("syntax", "Syntax/IWF"),
]


def _verify_question(
    q: QuizQuestion, lo: LearningObjective, material: str, mode: Literal["quiz", "practice"], self_check: bool,
    sourced: bool = True,
) -> QuestionCheck:
    syntax_issues = _syntax_iwf_check(q)
    # Practice: syntax-check không chặn (chỉ ghi lại), độ nghiêm ngặt thấp hơn quiz
    # (mục "Điểm khác biệt: ngưỡng chấp nhận thấp hơn Quiz").
    blocking = syntax_issues if mode == "quiz" else []
    result = QuestionCheck(
        syntax_pass=not blocking, syntax_reason="; ".join(blocking), syntax_issues=syntax_issues
    )
    if not self_check:
        return result

    bloom_level = lo.bloom_level
    prompt = (
        f"{material}"
        f"CÂU HỎI ĐÃ SINH:\n"
        f"stem: {q.stem}\n"
        f"choices: {q.choices}\n"
        f"reasoning: {q.reasoning}\n"
        f"correct_index: {q.correct_index} (đáp án: {q.choices[q.correct_index]})\n"
        f"explanation: {q.explanation}\n\n"
        f"BLOOM_LEVEL ĐÃ KHAI BÁO: {bloom_level}\n"
        f"MÔ TẢ HÀNH VI CẤP ĐỘ {bloom_level}: {BLOOM_LEVEL_ANCHORS[bloom_level]}\n\n"
        f"Kiểm tra các tiêu chí đã nêu cho câu hỏi trên."
    )
    if sourced:
        check = complete_structured(
            prompt, GroundednessAnswerabilityCheck, system_instruction=GROUNDEDNESS_SYSTEM_INSTRUCTION
        )
        result.groundedness_pass, result.groundedness_reason = check.groundedness_pass, check.groundedness_reason
    else:
        check = complete_structured(
            prompt, AnswerabilityCheck, system_instruction=NO_SOURCE_QUESTION_CHECK_SYSTEM_INSTRUCTION
        )
    result.answerability_pass, result.answerability_reason = check.answerability_pass, check.answerability_reason
    result.bloom_level_pass, result.bloom_level_reason = check.bloom_level_pass, check.bloom_level_reason
    return result


# ---------------------------------------------------------------------------
# Sinh một câu
# ---------------------------------------------------------------------------

def generate_quiz_question(
    db: Session,
    course_id: uuid.UUID,
    query: str,
    lo: LearningObjective,
    mode: Literal["quiz", "practice"] = "quiz",
    scope: ChunkScope | None = None,
    feedback: str | None = None,
    self_check: bool = True,
    lesson: str | None = None,
    output_language: OutputLanguage = "vi",
) -> QuizResult:
    """`query`: điều câu hỏi kiểm (một sub-objective, hoặc khái niệm chính với practice); truy hồi
    top-5 đoạn theo nó, không chấm độ liên quan riêng.

    `lesson`: nhánh không RAG. Câu hỏi đọc bài học (Content) thay cho đoạn truy hồi, không truy hồi
    gì, và không chấm groundedness vì không có nguồn nào để đối chiếu; prompt còn lại giữ nguyên.

    `feedback`: lý do lần sinh trước của chính câu này không đạt, khi lỗi chỉ thấy được ở cấp
    cả bộ câu hỏi (ví dụ đáp án đúng dài nhất ở mọi câu) nên bước kiểm từng câu không bắt được.
    Nó đi cùng câu hỏi qua cả lần sinh lại, cộng với lý do trượt của lần kiểm.

    `self_check=False` bỏ lượt model tự kiểm groundedness/answerability/Bloom của câu hỏi; bẫy cú
    pháp (không tốn lượt gọi nào) vẫn chặn câu quiz như khi bật."""
    sourced = lesson is None
    if sourced:
        material = source_block(retrieve(db, course_id, query, top_k=QUESTION_TOP_K, scope=scope))
        instruction = QUESTION_SYSTEM_INSTRUCTION
    else:
        material = f"NỘI DUNG BÀI HỌC:\n{lesson}\n\n"
        instruction = NO_SOURCE_QUESTION_SYSTEM_INSTRUCTION
    bloom_level = lo.bloom_level
    attempts = 0

    def generate(retry_feedback: str | None) -> QuizQuestion:
        nonlocal attempts
        attempts += 1
        prompt = (
            f"LEARNING OBJECTIVE: {lo.objective_text} (bloom_level={bloom_level})\n"
            f"MÔ TẢ HÀNH VI CẤP ĐỘ {bloom_level} (dùng để quyết định dạng câu hỏi, không tự suy luận khác): "
            f"{BLOOM_LEVEL_ANCHORS[bloom_level]}\n"
            f"ĐIỀU CẦN KIỂM: {query}\n"
            f"MODE: {mode}\n\n"
            f"{material}"
            f"Sinh 1 câu hỏi trắc nghiệm cho Learning Objective trên."
        )
        reasons = "; ".join(r for r in (feedback, retry_feedback) if r)
        if reasons:
            prompt += CORRECTION_SUFFIX_TEMPLATE.format(feedback=reasons)
        return complete_structured(
            prompt, QuizQuestion,
            system_instruction=instruction + output_language_instruction(output_language),
        )

    question, check = _checked(
        generate, lambda q: _verify_question(q, lo, material, mode, self_check, sourced), QUESTION_CRITERIA
    )
    failed = failed_criteria(check, QUESTION_CRITERIA)
    return {
        "question": question,
        "passed": not failed,
        "fail_reason": "; ".join(failed) or None,
        "syntax_issues": check.syntax_issues,
        "correction_retry_count": attempts - 1,
    }


# Đáp án đúng được phép là phương án dài nhất ở một số câu, nhưng không phải ở mọi câu: khi cả bộ
# đều như vậy, người học đoán đúng chỉ bằng cách chọn phương án dài nhất mà không cần hiểu bài. Cấm
# ở từng câu thì lại tạo ra mẹo ngược lại ("phương án dài nhất chắc chắn sai"), nên luật đặt ở cấp
# cả bộ câu hỏi của một module.
LENGTH_TELL_FEEDBACK = (
    "Ở mọi câu hỏi của bài, đáp án đúng đều là phương án dài nhất, nên người học đoán được đáp án "
    "chỉ nhờ độ dài. Viết lại câu này sao cho đáp án đúng không dài hơn các phương án nhiễu: các "
    "phương án nhiễu phải chi tiết và cụ thể ngang đáp án đúng."
)


def question_to_rebalance(questions: list[QuizQuestion]) -> int | None:
    """Câu nên sinh lại khi đáp án đúng dài nhất ở mọi câu của bộ: câu có đáp án đúng dài vượt
    trội nhất so với phương án dài kế tiếp. None khi có ít nhất một câu mà đáp án đúng không dài
    nhất, hoặc bộ chỉ có một câu (một câu đơn lẻ thì dài nhất là chuyện bình thường)."""
    if len(questions) < 2:
        return None
    margins = []
    for q in questions:
        lengths = [len(c.strip()) for c in q.choices]
        longest_other = max(n for i, n in enumerate(lengths) if i != q.correct_index)
        if lengths[q.correct_index] <= longest_other:
            return None
        margins.append(lengths[q.correct_index] / max(1, longest_other))
    return max(range(len(margins)), key=margins.__getitem__)
