"""Quiz/Practice: một câu hỏi trắc nghiệm cho mỗi điều cần kiểm, cùng lời giải của nó.

Mỗi câu: truy hồi top-5 đoạn theo đúng điều cần kiểm (sub-objective) -> Question Generator
(misconception CoT + Bloom's + JSON schema) -> kiểm câu vừa sinh (Syntax/IWF heuristic, và khi bật
tự kiểm thì model chấm Groundedness, Answerability, Bloom's, Distractors, Wording) -> trượt thì
sinh lại đúng một lần với lý do trượt, và giữ bản ít lỗi nhất trong các lượt. Bẫy cú pháp không tốn
lượt gọi nào nên bản nháp trượt nó được sinh lại thêm tối đa SYNTAX_RETRY lần trước khi model kiểm.
Nhánh không RAG dùng đúng hàm này, chỉ thay đoạn truy hồi bằng bài học (Content) đã viết.
Không chấm độ liên quan riêng cho từng câu: Corrective RAG chỉ chạy một lần trước Content.

Practice tái dùng NGUYÊN hàm và gate này (mode="practice"), không tốn hạ tầng riêng.
"""

import random
import re
import statistics
import unicodedata
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import combinations
from typing import Literal, TypedDict

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.generation.common import (
    BLOOM_LEVEL_ANCHORS,
    MAX_CORRECTION_RETRY,
    OutputLanguage,
    custom_instructions_instruction,
    failed_criteria,
    output_language_instruction,
    source_block,
    system_instruction,
)
from app.generation.objective import LearningObjective
from app.llm.gateway import complete_structured
from app.retrieval.search import ChunkScope
from app.retrieval.search import retrieve_for_generation as retrieve

# Số đoạn truy hồi cho mỗi câu hỏi, theo đúng điều câu đó kiểm.
QUESTION_TOP_K = 5
# A question needs a few passages on one point, so the reranker scores fewer candidates here than for
# Content. Measured in docs/measurements/2026-11-05-generation-throughput.md.
QUESTION_CANDIDATE_K = 15

# mục 3.1 bước 3 (Syntax/IWF Analyzer) — loại bỏ từ mang tính tuyệt đối
# thường lộ đáp án ("luôn luôn"/"never" hiếm khi đúng làm distractor).
ABSOLUTE_WORDS = [
    "luôn luôn", "không bao giờ", "tất cả đều", "duy nhất", "chỉ có",
    "mọi trường hợp", "always", "never", "all of the above", "none of the above",
    # Found by the two rubric judges in the distractors of 2026-11-08 (docs/measurements/2026-11-08-jev-five-subjects.md):
    # a distractor saying "entirely", "any", "purely" is eliminated without knowing the lesson.
    "hoàn toàn", "bất kỳ", "tuyệt đối", "thuần túy", "không hề", "entirely", "completely",
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


class OptionCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    is_correct: bool = Field(description="Phương án có thể bảo vệ là đúng theo stem và nguồn, độc lập với key")
    distinct_pass: bool = Field(description="Không diễn đạt lại/trùng ý với BẤT KỲ phương án nào khác")
    plausible_pass: bool = Field(description="Nếu là nhiễu: sai nhưng hợp lý, không loại được bằng mẹo/kiến thức ngoài bài")
    reason: str = Field(min_length=1, description="Vì sao đúng/sai theo nguồn; chỉ rõ phương án trùng ý hoặc nhiễu quá rõ")


class OptionReview(BaseModel):
    """The blind half of the review: the reviewer sees the stem, the options and the sources, never the
    key, the reasoning or the explanation, so it cannot reason its way to the generator's answer."""

    model_config = ConfigDict(extra="forbid", strict=True)

    option_checks: list[OptionCheck] = Field(min_length=4, max_length=4)
    wording_pass: bool = Field(description="Không đoán được đáp án nào qua độ dài, chi tiết, ngữ pháp, lặp từ hoặc cách diễn đạt")
    wording_reason: str


class AnswerabilityCheck(BaseModel):
    """The half of the review that needs the key: does the reasoning lead to it, and does the question ask
    for the declared level."""

    model_config = ConfigDict(extra="forbid", strict=True)

    answerability_pass: bool = Field(
        description="Đúng 1 đáp án đúng, không mâu thuẫn nội tại, không có 2 phương án cùng hợp lý"
    )
    answerability_reason: str
    bloom_level_pass: bool = Field(
        description="Câu hỏi có THỰC SỰ đúng cấp độ Bloom's đã khai báo không (không cao hơn/thấp hơn) -- "
        "kiểm chứng độc lập với bước sinh, vì chỉ yêu cầu qua prompt không đảm bảo model tuân thủ đúng"
    )
    bloom_level_reason: str


class GroundednessAnswerabilityCheck(AnswerabilityCheck):
    groundedness_pass: bool = Field(description="Câu hỏi/đáp án có được bảo chứng hoàn toàn bởi nguồn không")
    groundedness_reason: str = Field(description="Dẫn ID SOURCE CHUNK và chi tiết bảo chứng key; fail khi thiếu bằng chứng")


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
2. Viết `stem` kiểm tra ĐÚNG hành vi nhận thức của điều cần kiểm (target
   sub-objective); không tự nâng lên mức Bloom của Learning Objective tổng thể.
   Dùng ĐÚNG mô tả hành vi của cấp độ đó (được cung cấp trong
   prompt bên dưới, không tự suy luận) để quyết định dạng câu hỏi — không chỉ
   dựa vào tên cấp độ suông (vd Learning Objective ở mức "understand" thì câu
   hỏi không được yêu cầu "phân tích"/"đánh giá").
   Mức Bloom nằm ở VIỆC người học phải làm, không nằm ở lời dẫn: KHÔNG mở đầu
   bằng "Khi phân tích…", "Đánh giá…" nếu câu hỏi thực chất chỉ đòi nhận lại một
   câu trong tài liệu. Từ mức understand trở lên, đáp án đúng KHÔNG được chép
   nguyên văn một câu của tài liệu: diễn đạt lại, hoặc đặt người học vào một
   tình huống, ví dụ hay đoạn mã cụ thể (khác ví dụ trong tài liệu nhưng chỉ cần
   kiến thức trong tài liệu để giải) mà họ phải nhận ra, áp dụng hay suy ra.
3. Viết `reasoning` TRƯỚC khi viết các phương án: nêu đáp án đúng sẽ là gì và
   vì sao (dẫn chiếu trực tiếp tài liệu được cung cấp), rồi với MỖI
   misconception đã phân tích nêu kiểu phương án nhiễu sai mà nó dẫn tới.
4. Viết 4 `choices` đúng theo `reasoning`: 1 đáp án đúng + 3 phương án nhiễu,
   MỖI phương án nhiễu tương ứng chính xác 1 misconception theo đúng thứ tự.
   Rồi điền `correct_index` (phải trỏ đúng phương án đúng theo `reasoning`) và
   `explanation` (giải thích ngắn gọn cho người học, tổng hợp từ `reasoning`:
   vì sao đáp án đúng là đúng và vì sao MỖI phương án nhiễu sai). Trong
   `explanation`, gọi mỗi phương án bằng chính nội dung của nó, KHÔNG bằng số
   thứ tự hay chữ cái: thứ tự các phương án được xáo lại khi lưu.
5. Các phương án PHẢI cân bằng độ dài/ngữ pháp với nhau — không để đáp án
   đúng dài/chi tiết hơn hẳn distractor (dễ đoán): đáp án đúng thường KHÔNG phải
   phương án dài nhất, và mỗi phương án nhiễu cụ thể, đầy đủ ngang đáp án đúng.
   KHÔNG dùng từ mang tính tuyệt đối hay cực đoan ("luôn luôn", "không bao giờ",
   "tất cả", "duy nhất", "hoàn toàn", "toàn bộ", "bất kỳ", "thuần túy") trong bất
   kỳ phương án nào — dễ bị loại trừ mà không cần hiểu bài. Phương án nhiễu là
   một nhầm lẫn mà người học có thể tin thật (đổi vai trò hai khái niệm, áp dụng
   đúng quy tắc vào sai trường hợp, bỏ sót một điều kiện), KHÔNG phải câu phủ định
   thẳng nội dung tài liệu hay một khẳng định vô lý. KHÔNG dùng: câu hỏi phủ
   định ("KHÔNG đúng", "sai", "ngoại trừ"), câu bỏ trống ở giữa để điền, phương
   án gộp ("tất cả các ý trên", "không có đáp án nào", "cả A và B"), từ mơ hồ
   ("đôi khi", "hầu hết") trong phương án.
6. Nếu mode="practice", đây là câu tổng hợp của cả bài: kiểm ĐÚNG mức Bloom của
   Learning Objective tổng thể (không phải của từng target), thường bằng một
   tình huống cần kết hợp nhiều ý của bài; vẫn qua cùng gate chất lượng như quiz.
7. `explanation` chỉ dùng điều có trong tài liệu: giải thích vì sao phương án
   nhiễu sai bằng chính nội dung tài liệu, KHÔNG thêm ứng dụng, số liệu, bối
   cảnh lịch sử hay hệ quả mà tài liệu không nêu.
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

_OPTION_REVIEW_BODY = """Bạn tự giải một câu hỏi trắc nghiệm dựa trên tài liệu được cung cấp,
như một người học giỏi đã đọc kỹ tài liệu. Bạn KHÔNG được biết đáp án mà người ra đề chọn.

- option_checks: xét từng phương án 0, 1, 2, 3 theo đúng thứ tự.
  is_correct=true nếu có thể bảo vệ phương án đó là đúng theo stem và tài liệu.
  Phương án diễn đạt lại hoặc tương đương với một phương án đúng khác cũng là đúng.
  distinct_pass=false nếu phương án trùng ý hoặc diễn đạt lại một phương án khác.
  plausible_pass=false nếu một phương án sai mà vô lý, lạc đề hoặc loại được không cần hiểu bài.
  reason nêu vì sao đúng hoặc sai, kèm bằng chứng trong tài liệu.
- wording_pass: fail nếu đoán được phương án đúng mà không cần hiểu bài: qua độ dài, mức
  chi tiết, lặp từ của stem, ngữ pháp, hoặc vì các phương án còn lại dùng từ tuyệt đối/cực
  đoan ("hoàn toàn", "toàn bộ", "bất kỳ"…) hay phủ định thẳng tài liệu.
"""

_OPTION_REVIEW_SOURCE_RULES = """- Tài liệu là các đoạn nguồn (SOURCE CHUNK): một phương án đúng hay sai theo chính các
  đoạn đó, không theo hiểu biết riêng của bạn.
"""

_OPTION_REVIEW_NO_SOURCE_RULES = """- Tài liệu là NỘI DUNG BÀI HỌC đã viết: một phương án đúng hay sai theo điều bài học
  đã dạy, không theo hiểu biết riêng của bạn.
"""

_QUESTION_CHECK_BODY = """Bạn là bộ kiểm tra Answerability & Bloom's Fidelity cho câu hỏi trắc nghiệm
đã sinh, dựa trên tài liệu được cung cấp. Từng phương án đã được một người khác giải độc lập;
bạn kiểm phần còn lại, cần biết đáp án đã chọn.

Chấm các tiêu chí sau:
- answerability_pass: câu hỏi có ĐÚNG 1 đáp án đúng duy nhất, không mâu thuẫn
  nội tại, và KHÔNG có 2 phương án cùng hợp lý (gây khó phân biệt) không?
  `reasoning` đi kèm (viết trước khi có các phương án) nêu đáp án đúng là gì:
  nội dung đó PHẢI khớp với phương án mà `correct_index` trỏ tới, và các
  phương án nhiễu phải đúng là những kiểu sai nó dự tính — nếu phương án được
  chọn làm đáp án khác với lập luận thì đặt fail.
- bloom_level_pass: câu hỏi có THỰC SỰ đòi hỏi đúng hành vi nhận thức của cấp
  độ Bloom's của ĐIỀU CẦN KIỂM không: suy ra hành vi từ động từ của target,
  không nâng target lên mức mục tiêu tổng thể (target có thể thấp hơn).
  Dùng các neo hành vi được cung cấp cùng
  câu hỏi — KHÔNG chỉ dựa vào việc prompt sinh câu hỏi CÓ NHẮC ĐẾN tên cấp độ
  đó; câu hỏi hoàn toàn có thể lặp lại đúng tên cấp độ trong lời dẫn nhưng
  thực chất đòi hỏi hành vi nhận thức khác (thường bị lệch cao hơn khai báo,
  hoặc dễ dàng mặc định ở mức "understand" dù nguồn/LO hỗ trợ cấp cao hơn) —
  đây chính là lý do cần kiểm chứng ĐỘC LẬP ở bước này, không tin cậy hoàn
  toàn vào bước sinh. Fail nếu lời dẫn nói "phân tích"/"đánh giá"/"áp dụng"
  nhưng chỉ cần nhận lại một câu có sẵn trong tài liệu là chọn được đáp án.
"""

_QUESTION_CHECK_SOURCE_RULES = """- groundedness_pass (tài liệu là các đoạn nguồn): `stem`, đáp án đúng, và
  `explanation` có được bảo chứng HOÀN TOÀN bởi nội dung nguồn không (không
  suy diễn/thêm dữ kiện ngoài nguồn)? Kiểm TỪNG ý của `explanation`, kể cả
  lý do bác bỏ mỗi phương án nhiễu: một ứng dụng, ví dụ, con số hay khái niệm
  mà nguồn không có là fail, kể cả khi nó đúng ngoài đời.

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

# Item-writing flaws of Tarrant et al. (2006), the list rubric v3 scores quiz quality by, that a
# pattern finds without false alarms on the stored questions (docs/rubric.md section 3.6). Word
# repeats are left to the model's blind review: at the syllable level Vietnamese repeats words such as
# "nghĩa" or "hội" between a stem and its options without giving the answer away.
_NEGATIVE_STEM = re.compile(r"\b(?:không đúng|ngoại trừ|except|not true)\b|\b(?:sai|false|incorrect)\s*\?*\s*$", re.IGNORECASE)
_NEGATIVE_STEM_EMPHASIS = re.compile(r"\b(?:KHÔNG|SAI|NOT|EXCEPT)\b")
_COMBINED_OPTION = re.compile(
    r"tất cả (?:các )?(?:phương án|đáp án|ý)|không (?:có )?(?:phương án|đáp án) nào|cả (?:hai|ba) (?:phương án|đáp án)"
    r"|all of the above|none of the above|\bcả [A-D] (?:và|lẫn) [A-D]\b|\bboth [A-D] and [A-D]\b", re.IGNORECASE)
_BLANK = re.compile(r"_{2,}|\.{3,}|…")
_VAGUE = re.compile(r"\b(?:đôi khi|thỉnh thoảng|thường thường|hầu hết|usually|sometimes|often|generally|mostly)\b", re.IGNORECASE)
# The options are shuffled after generation (shuffled_choices), so an explanation that names an option by
# its letter or number would point at the wrong one.
_POSITIONAL = re.compile(r"(?:phương án|đáp án|lựa chọn|câu|option|choice|answer)\s*[\"“(]?\s*[A-D1-4]\b(?!\w)", re.IGNORECASE)


def _syntax_iwf_check(question: QuizQuestion) -> list[str]:
    issues = []
    if len(question.choices) != 4 or not 0 <= question.correct_index < len(question.choices):
        issues.append("Key/phương án không hợp lệ: cần 4 phương án và correct_index trong 0..3")
        return issues
    normalized = [" ".join(re.findall(r"\w+|!=|==|<=|>=|[<>+*/%-]", unicodedata.normalize("NFKC", c).casefold()))
                  for c in question.choices]
    if any(not c for c in normalized):
        issues.append("Phương án rỗng")
    for i, j in combinations(range(4), 2):
        a, b = normalized[i], normalized[j]
        # Conservative lexical similarity only; semantic paraphrases belong to the model review.
        # Preserve negation, numeric and operator changes (often the point of a distractor).
        meaningful = r"\b(?:not|no|never|không|chẳng|chưa)\b|\d+|!=|==|<=|>=|[<>+*/%-]"
        near = (min(len(a.split()), len(b.split())) >= 5
                and re.findall(meaningful, a) == re.findall(meaningful, b)
                and SequenceMatcher(None, a, b).ratio() >= 0.94)
        if a == b or near:
            issues.append(f"Phương án {i} và {j} trùng/gần trùng nội dung")
    lengths = [len(c.strip()) for c in question.choices]
    if min(lengths) > 0 and max(lengths) > 2 * min(lengths):
        issues.append("Độ dài phương án chênh lệch quá lớn (có thể lộ đáp án)")
    key_length = lengths[question.correct_index]
    others = [n for i, n in enumerate(lengths) if i != question.correct_index]
    # Against the typical distractor, not the longest one: on the 80 stored questions the key was at most
    # 1.29 times the longest distractor, so a 1.5 threshold on that never fired.
    typical = statistics.median(others)
    if key_length > max(others) and key_length >= 1.25 * max(1, typical) and key_length - typical >= 15:
        issues.append("Đáp án đúng dài vượt trội so với các phương án nhiễu (lộ đáp án)")
    if _NEGATIVE_STEM.search(question.stem) or _NEGATIVE_STEM_EMPHASIS.search(question.stem):
        issues.append("Câu hỏi dạng phủ định (KHÔNG, SAI, ngoại trừ): hỏi điều đúng thay vì điều sai")
    blank = _BLANK.search(question.stem)
    if blank and re.search(r"\w", question.stem[blank.end():]):
        issues.append("Câu hỏi bỏ trống ở giữa để điền: viết thành một câu hỏi trọn vẹn")
    if _POSITIONAL.search(question.explanation):
        issues.append("Lời giải gọi phương án theo số thứ tự hoặc chữ cái: gọi bằng chính nội dung của phương án")
    for i, choice in enumerate(question.choices):
        lowered = choice.lower()
        if any(w in lowered for w in ABSOLUTE_WORDS):
            issues.append(f"Phương án {i} chứa từ mang tính tuyệt đối (dễ bị loại trừ)")
        if _COMBINED_OPTION.search(choice):
            issues.append(f"Phương án {i} gộp các phương án khác (tất cả/không có/cả A và B)")
        if _VAGUE.search(choice):
            issues.append(f"Phương án {i} dùng từ mơ hồ (đôi khi, hầu hết...)")
    return issues


def shuffled_choices(question: QuizQuestion) -> QuizQuestion:
    """The options in an order that does not depend on the model: models put the key early (on the 80
    stored questions positions 0-3 held it 24, 30, 17 and 9 times). Seeded by the stem, so the same
    question always gets the same order."""
    order = list(range(len(question.choices)))
    random.Random(question.stem).shuffle(order)
    return question.model_copy(update={
        "choices": [question.choices[i] for i in order], "correct_index": order.index(question.correct_index),
    })


@dataclass
class QuestionCheck:
    """Kết quả kiểm một câu, theo dạng `<tên>_pass`/`<tên>_reason` mà `failed_criteria` đọc: ba tiêu chí
    model chấm (đạt sẵn khi tắt tự kiểm) cộng bẫy cú pháp, vốn không tốn lượt gọi nào."""

    groundedness_pass: bool = True
    groundedness_reason: str = ""
    answerability_pass: bool = True
    answerability_reason: str = ""
    bloom_level_pass: bool = True
    bloom_level_reason: str = ""
    distractors_pass: bool = True
    distractors_reason: str = ""
    wording_pass: bool = True
    wording_reason: str = ""
    syntax_pass: bool = True
    syntax_reason: str = ""
    # Mọi lỗi cú pháp tìm thấy, ở cả quiz và practice.
    syntax_issues: list[str] = field(default_factory=list)


QUESTION_CRITERIA = [
    ("groundedness", "Groundedness"),
    ("answerability", "Answerability"),
    ("bloom_level", "Bloom's level"),
    ("distractors", "Distractors"),
    ("wording", "Wording"),
    ("syntax", "Syntax/IWF"),
]


def _verify_question(
    q: QuizQuestion, lo: LearningObjective, material: str, mode: Literal["quiz", "practice"], self_check: bool,
    sourced: bool = True,
    target: str = "",
) -> QuestionCheck:
    syntax_issues = _syntax_iwf_check(q)
    result = QuestionCheck(
        syntax_pass=not syntax_issues, syntax_reason="; ".join(syntax_issues), syntax_issues=syntax_issues
    )
    if not self_check or not 0 <= q.correct_index < len(q.choices):
        return result

    review = complete_structured(
        f"{material}CÂU HỎI:\nstem: {q.stem}\n"
        + "".join(f"phương án {i}: {choice}\n" for i, choice in enumerate(q.choices))
        + "\nXét từng phương án.",
        OptionReview, system_instruction=system_instruction(
            _OPTION_REVIEW_BODY, _OPTION_REVIEW_SOURCE_RULES, _OPTION_REVIEW_NO_SOURCE_RULES, sourced=sourced),
    )
    bloom_level = lo.bloom_level
    prompt = (
        f"{material}"
        f"CÂU HỎI ĐÃ SINH:\n"
        f"stem: {q.stem}\n"
        f"choices: {q.choices}\n"
        f"reasoning: {q.reasoning}\n"
        f"correct_index: {q.correct_index} (đáp án: {q.choices[q.correct_index]})\n"
        f"explanation: {q.explanation}\n\n"
        f"ĐIỀU CẦN KIỂM (target sub-objective): {target or lo.objective_text}\n"
        f"LEARNING OBJECTIVE TỔNG THỂ: {lo.objective_text}\n"
        f"BLOOM_LEVEL ĐÃ KHAI BÁO: {bloom_level}\n"
        f"NEO HÀNH VI BLOOM: {BLOOM_LEVEL_ANCHORS}\n"
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
    # Options are named by their text, not their position: shuffled_choices reorders them after this
    # check, and the lecturer reads these reasons next to the shuffled A-D.
    def option(i: int) -> str:
        return f"«{q.choices[i]}»" if i < len(q.choices) else f"phương án thừa {i + 1}"

    correct = [i for i, check in enumerate(review.option_checks) if check.is_correct]
    if correct != [q.correct_index]:
        result.answerability_pass = False
        result.answerability_reason = (
            f"Đáp án đã chọn: {option(q.correct_index)}; có thể đúng: "
            + (", ".join(option(i) for i in correct) or "không phương án nào") + ". "
            + "; ".join(f"{option(i)}: {o.reason}" for i, o in enumerate(review.option_checks))
        )
    bad_options = [f"{option(i)}: {o.reason}" for i, o in enumerate(review.option_checks)
                   if not o.distinct_pass or (i != q.correct_index and not o.plausible_pass)]
    result.distractors_pass = not bad_options
    result.distractors_reason = "; ".join(bad_options)
    result.wording_pass, result.wording_reason = review.wording_pass, review.wording_reason
    result.bloom_level_pass, result.bloom_level_reason = check.bloom_level_pass, check.bloom_level_reason
    return result


# ---------------------------------------------------------------------------
# Sinh một câu
# ---------------------------------------------------------------------------

# Bẫy cú pháp chạy ở máy chủ, không tốn lượt gọi model nào, nên bản nháp trượt nó được sinh lại
# ngay, trước khi trả tiền cho lượt model tự kiểm. Đây là số lượt sinh thêm tối đa cho cả câu, ngoài
# MAX_CORRECTION_RETRY lượt sinh lại sau khi model kiểm.
SYNTAX_RETRY = 2


def _syntax_draft(generate, feedback: str | None, budget: list[int]) -> QuizQuestion:
    """Sinh một bản nháp; trượt bẫy cú pháp thì sinh lại với đúng lỗi đó trong khi `budget[0]` còn,
    và trả bản ít lỗi cú pháp nhất (hòa thì bản sau, vì nó đã đọc lỗi của bản trước)."""
    best = generate(feedback)
    best_issues = _syntax_iwf_check(best)
    while best_issues and budget[0] > 0:
        budget[0] -= 1
        reason = "Syntax/IWF fail: " + "; ".join(best_issues)
        draft = generate("; ".join(r for r in (feedback, reason) if r))
        issues = _syntax_iwf_check(draft)
        if len(issues) <= len(best_issues):
            best, best_issues = draft, issues
    return best


def _best_attempt(generate, verify) -> tuple[QuizQuestion, QuestionCheck, list[str]]:
    """Như `_checked` (sinh, kiểm, trượt thì sinh lại với lý do trượt), với hai khác biệt chỉ ở
    quiz: bản nháp trượt bẫy cú pháp được sinh lại trước khi model kiểm (`_syntax_draft`), và hết
    lượt thì giữ bản ít tiêu chí trượt nhất, rồi ít lỗi cú pháp nhất, thay cho bản cuối. Bản sinh lại
    có khi sửa được lỗi cũ nhưng sinh lỗi mới nặng hơn; giữ bản cuối thì bỏ mất bản tốt hơn."""
    feedback: str | None = None
    budget = [SYNTAX_RETRY]
    best = None
    for _ in range(MAX_CORRECTION_RETRY + 1):
        question = _syntax_draft(generate, feedback, budget)
        check = verify(question)
        failed = failed_criteria(check, QUESTION_CRITERIA)
        score = (len(failed), len(check.syntax_issues))
        if best is None or score <= best[0]:
            best = (score, question, check, failed)
        if not failed:
            break
        feedback = "; ".join(failed)
    _, question, check, failed = best
    return question, check, failed


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
    output_language: OutputLanguage = "vi", custom_instructions: str = "",
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
        material = source_block(retrieve(
            db, course_id, query, top_k=QUESTION_TOP_K, candidate_k=QUESTION_CANDIDATE_K, scope=scope,
        ))
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
            f"Target ở đúng cấp của LO, trừ tối đa một target nền thấp hơn một bậc; bám động từ của target. "
            f"NEO HÀNH VI BLOOM: {BLOOM_LEVEL_ANCHORS}\n"
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
            system_instruction=instruction + output_language_instruction(output_language) + custom_instructions_instruction(custom_instructions, sourced),
        )

    question, check, failed = _best_attempt(
        generate, lambda q: _verify_question(q, lo, material, mode, self_check, sourced, query)
    )
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
    "Ở hầu hết câu hỏi của bài, đáp án đúng là phương án dài nhất, nên người học đoán được đáp án "
    "chỉ nhờ độ dài. Viết lại câu này sao cho đáp án đúng không dài hơn các phương án nhiễu: các "
    "phương án nhiễu phải chi tiết và cụ thể ngang đáp án đúng."
)


def question_to_rebalance(questions: list[QuizQuestion]) -> int | None:
    """Câu nên sinh lại khi đáp án đúng dài nhất ở từ hai câu trở lên và ở hơn một phần tư số câu
    của bộ (ngẫu nhiên thì khoảng một phần tư): trong các câu đó, câu có đáp án đúng dài vượt trội
    nhất so với phương án dài kế tiếp. None khi bộ chỉ có một câu.

    Ngưỡng cũ là quá nửa số câu, và chỉ sửa một câu: trên 80 câu đã lưu, đáp án đúng vẫn dài nhất ở
    44 câu (55%). Bộ gọi hàm này nhiều lượt (MAX_REBALANCES), mỗi lượt sửa một câu.

    Trước 2026-11-08 luật chỉ bắt khi đáp án đúng dài nhất ở MỌI câu, nên bộ 4 câu có 3 câu lộ đáp
    án vẫn lọt; cả hai judge rubric đều chỉ ra lỗi này (docs/measurements/2026-11-08-jev-five-subjects.md)."""
    if len(questions) < 2:
        return None
    margins = {}
    for index, q in enumerate(questions):
        lengths = [len(c.strip()) for c in q.choices]
        longest_other = max(n for i, n in enumerate(lengths) if i != q.correct_index)
        if lengths[q.correct_index] > longest_other:
            margins[index] = lengths[q.correct_index] / max(1, longest_other)
    if len(margins) < 2 or 4 * len(margins) <= len(questions):
        return None
    return max(margins, key=margins.__getitem__)
