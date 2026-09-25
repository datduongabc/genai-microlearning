"""Sinh module không RAG: nhóm đối chứng llm_no_rag của nghiên cứu Giai đoạn 2.

So với pipeline RAG, chỉ một thứ khác: model không được đưa đoạn tài liệu nào, nên viết từ kiến
thức của chính nó. Mọi thứ khác giữ như nhau để khác biệt điểm chấm quy được về RAG: cùng
Learning Objective (do bản RAG đi cặp truyền vào), cùng cấu trúc module, cùng schema đầu ra,
cùng model, và cùng các bước tự kiểm trừ những kiểm cần tài liệu để đối chiếu (groundedness,
văn phong theo nguồn), vì ở đây không có tài liệu nào.

Bước sau trong module đọc bước trước như pipeline RAG: slide và ví dụ đọc Content; câu hỏi (kèm
lời giải) đọc Content thay cho đoạn truy hồi, vì đó là thứ duy nhất bài học đã dạy.

Prompt ở đây chỉ mô tả cấu trúc và cách làm, không có ví dụ lấy từ tài liệu hay môn học nào.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from app.generation.common import BLOOM_LEVEL_ANCHORS
from app.generation.example import ExampleOutput
from app.generation.objective import LearningObjective
from app.generation.quiz_graph import (
    LENGTH_TELL_FEEDBACK,
    QuizQuestion,
    _syntax_iwf_check,
    question_to_rebalance,
)
from app.generation.slides import SlidesOutput, generate_slides
from app.llm.gateway import complete_structured

# Cùng ngân sách sửa lỗi với pipeline RAG: một lần sinh lại cho mỗi bước.
MAX_CORRECTION_RETRY = 1
MAX_QUIZ_QUESTIONS = 3

_RETRY_SUFFIX = "\n\nLƯU Ý: lần sinh trước KHÔNG ĐẠT vì lý do sau -- sửa đúng lỗi này, không lặp lại: {feedback}"


def _checked[T, C](
    generate: Callable[[str | None], T], verify: Callable[[T], C], criteria: list[tuple[str, str]]
) -> tuple[T, C]:
    """Sinh, tự kiểm, sinh lại một lần với đúng lý do trượt; hết lượt thì trả bản cuối và kết quả
    kiểm của nó, như các bước của pipeline RAG. `criteria`: (tên trường, nhãn trong lý do)."""
    feedback: str | None = None
    for _ in range(MAX_CORRECTION_RETRY + 1):
        out = generate(feedback)
        check = verify(out)
        failed = [
            f"{label} fail: {getattr(check, f'{name}_reason')}"
            for name, label in criteria
            if not getattr(check, f"{name}_pass")
        ]
        if not failed:
            return out, check
        feedback = "; ".join(failed)
    return out, check


# --- Content ------------------------------------------------------------------------------------

CONTENT_SYSTEM_INSTRUCTION = """Bạn là chuyên gia biên soạn nội dung Microlearning cho giáo dục đại học.

Nhiệm vụ: từ 1 Learning Objective và các sub-objective của nó, viết phần Content — nội dung cốt
lõi CHỈ ĐỦ để người học đạt đúng Learning Objective đó, không thừa không thiếu. Bạn viết từ kiến
thức chuyên môn của chính bạn; không có tài liệu nguồn nào được cung cấp.

Ràng buộc BẮT BUỘC:
1. Output là Markdown phân cấp (heading + bullet list), không dùng văn xuôi dài dòng.
2. Content PHẢI có 1 heading riêng cho MỖI sub_objective, đúng thứ tự, đủ số lượng.
3. Không khẳng định một chi tiết cụ thể mà bạn không chắc chắn (tên công cụ, phiên bản, số liệu,
   năm) như thể đó là sự thật; khi không chắc, giữ ở mức khái niệm chung.
4. Ngôn ngữ của Content khớp ngôn ngữ của Learning Objective (tiếng Việt có dấu đầy đủ nếu Learning
   Objective là tiếng Việt); thuật ngữ kỹ thuật được phép giữ nguyên tiếng Anh.
"""


class NoRagContent(BaseModel):
    content: str = Field(description="Nội dung Markdown phân cấp, mỗi sub-objective một heading")


CONTENT_VERIFY_SYSTEM_INSTRUCTION = """Bạn là bộ kiểm tra chất lượng Content (Critic).

Chấm đúng 2 tiêu chí:
- completeness_pass: MỖI heading ứng với 1 sub_objective có nội dung THẬT SỰ hữu ích để đạt
  sub_objective đó không, hay chỉ nêu tên rồi bỏ trống/nói chung chung?
- scope_pass: content có dừng đúng phạm vi Learning Objective/sub_objectives không, không lan sang
  chủ đề khác không liên quan?

Nếu vi phạm tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể (để dùng làm feedback sinh lại).
"""


class NoRagContentVerification(BaseModel):
    completeness_pass: bool
    completeness_reason: str
    scope_pass: bool
    scope_reason: str


def _objective_text(lo: LearningObjective) -> str:
    subs = "\n".join(f"- {s}" for s in lo.sub_objectives)
    return f"LEARNING OBJECTIVE: {lo.objective_text} (bloom_level={lo.bloom_level})\nSUB-OBJECTIVES:\n{subs}"


def generate_content(lo: LearningObjective) -> tuple[NoRagContent, NoRagContentVerification]:
    def generate(feedback):
        prompt = f"{_objective_text(lo)}\n\nViết phần Content cho Learning Objective trên."
        if feedback:
            prompt += _RETRY_SUFFIX.format(feedback=feedback)
        return complete_structured(prompt, NoRagContent, system_instruction=CONTENT_SYSTEM_INSTRUCTION, temperature=0.0)

    def verify(out):
        prompt = f"{_objective_text(lo)}\n\nCONTENT ĐÃ SINH:\n{out.content}\n\nKiểm tra completeness_pass, scope_pass."
        return complete_structured(
            prompt, NoRagContentVerification, system_instruction=CONTENT_VERIFY_SYSTEM_INSTRUCTION, temperature=0.0
        )

    return _checked(generate, verify, [("completeness", "Completeness"), ("scope", "Scope")])


# --- Examples -----------------------------------------------------------------------------------

EXAMPLE_SYSTEM_INSTRUCTION = """Bạn là chuyên gia biên soạn ví dụ minh họa cho giáo dục đại học.

Nhiệm vụ: viết các Example minh họa cho Learning Objective/Content được cung cấp — TỐI ĐA 1 Example
cho MỖI sub-objective (mỗi phần của Content), và chỉ cho những phần mà một ví dụ thật sự giúp người
học hiểu hơn; ít nhất 1 Example. Không viết thêm ví dụ cho đủ số.

Ràng buộc BẮT BUỘC:
1. Example được phép là tình huống MỚI do bạn nghĩ ra, nhưng PHẢI nhất quán với khái niệm đã nêu
   trong Content và KHÔNG được dùng để khẳng định thêm dữ kiện mới về khái niệm đó.
2. `context`: 1 bối cảnh ứng dụng thực tế cụ thể liên quan Learning Objective (không chung chung).
3. `analysis`: phân tích cách bối cảnh đó minh họa đúng khái niệm trong Content.
4. Ngôn ngữ khớp ngôn ngữ chính của Content.
"""

EXAMPLE_VERIFY_SYSTEM_INSTRUCTION = """Bạn là bộ kiểm tra chất lượng Example (Critic), dựa trên Content.

Mỗi tiêu chí xét trên TỪNG Example: chỉ cần 1 Example vi phạm là tiêu chí đó fail, và lý do phải nêu
rõ Example số mấy.

- consistency_pass: context và analysis có minh họa ĐÚNG khái niệm trong Content không, hay lạc
  đề/mâu thuẫn với Content?
- no_overreach_pass: analysis có dùng tình huống để khẳng định thêm dữ kiện/tính chất MỚI về khái
  niệm mà Content không hề nói tới không? Nếu có, đặt fail.

Nếu vi phạm tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể (để dùng làm feedback sinh lại).
"""


class NoRagExampleVerification(BaseModel):
    consistency_pass: bool
    consistency_reason: str
    no_overreach_pass: bool
    no_overreach_reason: str


def generate_examples(lo: LearningObjective, content: str) -> tuple[ExampleOutput, NoRagExampleVerification]:
    def generate(feedback):
        prompt = f"{_objective_text(lo)}\n\nCONTENT:\n{content}\n\nViết các Example cho Learning Objective/Content trên."
        if feedback:
            prompt += _RETRY_SUFFIX.format(feedback=feedback)
        return complete_structured(prompt, ExampleOutput, system_instruction=EXAMPLE_SYSTEM_INSTRUCTION, temperature=0.0)

    def verify(out):
        examples = "\n".join(
            f"Example {n}:\n  context: {e.context}\n  analysis: {e.analysis}" for n, e in enumerate(out.examples, 1)
        )
        prompt = f"CONTENT:\n{content}\n\nCÁC EXAMPLE ĐÃ SINH:\n{examples}\n\nKiểm tra consistency_pass, no_overreach_pass."
        return complete_structured(
            prompt, NoRagExampleVerification, system_instruction=EXAMPLE_VERIFY_SYSTEM_INSTRUCTION, temperature=0.0
        )

    return _checked(generate, verify, [("consistency", "Consistency"), ("no_overreach", "Overreach")])


# --- Questions ----------------------------------------------------------------------------------

QUESTION_SYSTEM_INSTRUCTION = """Bạn là chuyên gia thiết kế câu hỏi trắc nghiệm (MCQ) cho giáo dục đại học,
áp dụng kỹ thuật Misconception-based Distractor Design.

Nhiệm vụ: từ Learning Objective, sub-objective cần kiểm, NỘI DUNG BÀI HỌC đã viết, và mức độ
(quiz/practice), sinh 1 câu hỏi trắc nghiệm 4 phương án.

Quy trình BẮT BUỘC (đúng thứ tự):
1. Phân tích 3 quan niệm sai (common_misconceptions) mà học viên ở đúng bloom_level hay mắc về
   điều đang kiểm — phân tích TRƯỚC, không nghĩ ra distractor rồi mới gán lý do.
2. Viết `stem` kiểm đúng bloom_level, dùng ĐÚNG mô tả hành vi của cấp độ đó được cung cấp.
3. Viết `reasoning` TRƯỚC các phương án: đáp án đúng là gì và vì sao (dựa vào nội dung bài học),
   và mỗi misconception dẫn tới kiểu phương án nhiễu nào.
4. Viết 4 `choices` theo `reasoning`: 1 đáp án đúng + 3 phương án nhiễu, mỗi nhiễu ứng với đúng 1
   misconception theo thứ tự; rồi điền `correct_index` và `explanation` ngắn, tổng hợp từ `reasoning`:
   vì sao đáp án đúng là đúng và vì sao MỖI phương án nhiễu sai.
5. Các phương án cân bằng độ dài/ngữ pháp, không để đáp án đúng dài/chi tiết hơn hẳn; không dùng từ
   tuyệt đối ("luôn luôn", "không bao giờ", "tất cả", "duy nhất").
6. Câu hỏi chỉ kiểm điều ĐÃ ĐƯỢC DẠY trong nội dung bài học. Nếu mode="practice", câu hỏi dùng để
   luyện tập, được phép đơn giản hơn quiz một chút.
7. Ngôn ngữ khớp ngôn ngữ chính của nội dung bài học.
"""

QUESTION_CHECK_SYSTEM_INSTRUCTION = """Bạn là bộ kiểm tra Answerability & Bloom's Fidelity cho câu hỏi trắc
nghiệm đã sinh.

Chấm đúng 2 tiêu chí:
- answerability_pass: câu hỏi có ĐÚNG 1 đáp án đúng, không mâu thuẫn nội tại, không có 2 phương án
  cùng hợp lý không? `reasoning` (viết trước các phương án) nêu đáp án đúng là gì: nội dung đó PHẢI
  khớp phương án mà `correct_index` trỏ tới, không thì đặt fail.
- bloom_level_pass: câu hỏi có THỰC SỰ đòi hỏi đúng hành vi nhận thức của cấp độ Bloom's đã khai báo
  không (xem mô tả hành vi được cung cấp), không chỉ nhắc tên cấp độ?

Nếu vi phạm tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể (để dùng làm feedback sinh lại).
"""


class NoRagQuestionCheck(BaseModel):
    answerability_pass: bool
    answerability_reason: str
    bloom_level_pass: bool
    bloom_level_reason: str


@dataclass
class AskedQuestion:
    mode: Literal["quiz", "practice"]
    target: str
    question: QuizQuestion
    passed: bool


def generate_question(
    lo: LearningObjective, content: str, target: str, mode: Literal["quiz", "practice"], feedback: str | None = None
) -> AskedQuestion:
    anchor = BLOOM_LEVEL_ANCHORS[lo.bloom_level]
    first_feedback = feedback

    def generate(retry_feedback):
        prompt = (
            f"LEARNING OBJECTIVE: {lo.objective_text} (bloom_level={lo.bloom_level})\n"
            f"MÔ TẢ HÀNH VI CẤP ĐỘ {lo.bloom_level}: {anchor}\n"
            f"ĐIỀU CẦN KIỂM: {target}\nMODE: {mode}\n\n"
            f"NỘI DUNG BÀI HỌC:\n{content}\n\nSinh 1 câu hỏi trắc nghiệm cho Learning Objective trên."
        )
        reasons = "; ".join(r for r in (first_feedback, retry_feedback) if r)
        if reasons:
            prompt += _RETRY_SUFFIX.format(feedback=reasons)
        return complete_structured(prompt, QuizQuestion, system_instruction=QUESTION_SYSTEM_INSTRUCTION)

    def verify(q):
        prompt = (
            f"CÂU HỎI ĐÃ SINH:\nstem: {q.stem}\nchoices: {q.choices}\nreasoning: {q.reasoning}\n"
            f"correct_index: {q.correct_index} (đáp án: {q.choices[q.correct_index]})\n\n"
            f"BLOOM_LEVEL ĐÃ KHAI BÁO: {lo.bloom_level}\nMÔ TẢ HÀNH VI CẤP ĐỘ {lo.bloom_level}: {anchor}\n\n"
            f"Kiểm tra answerability_pass, bloom_level_pass."
        )
        check = complete_structured(prompt, NoRagQuestionCheck, system_instruction=QUESTION_CHECK_SYSTEM_INSTRUCTION)
        # Bẫy cú pháp (độ dài chênh, từ tuyệt đối) chặn quiz như ở pipeline RAG, practice thì không.
        issues = _syntax_iwf_check(q) if mode == "quiz" else []
        if issues:
            check.answerability_pass = False
            check.answerability_reason = "; ".join([check.answerability_reason, *issues])
        return check

    question, check = _checked(
        generate, verify, [("answerability", "Answerability"), ("bloom_level", "Bloom's level")]
    )
    return AskedQuestion(mode, target, question, check.answerability_pass and check.bloom_level_pass)


# --- Module ---------------------------------------------------------------------------------------


@dataclass
class NoRagModule:
    content: str
    slides: SlidesOutput
    examples: ExampleOutput
    # Mỗi câu mang lời giải của chính nó (QuizQuestion.explanation), như pipeline RAG.
    questions: list[AskedQuestion]
    # Kết quả các bước tự kiểm, để biết phần nào của bản đối chứng không đạt.
    checks: dict[str, bool] = field(default_factory=dict)


def generate_module(lo: LearningObjective, practice_target: str) -> NoRagModule:
    """Cùng chuỗi và cùng cấu trúc với pipeline RAG, trên cùng Learning Objective: Content, Slides,
    tối đa một Example mỗi phần, một câu quiz mỗi sub-objective (tối đa ba), một câu practice; lời giải
    của mỗi câu sinh cùng câu đó."""
    content, content_check = generate_content(lo)
    slides, slides_check = generate_slides(lo, content.content)
    examples, example_check = generate_examples(lo, content.content)

    questions = [generate_question(lo, content.content, t, "quiz") for t in lo.sub_objectives[:MAX_QUIZ_QUESTIONS]]
    questions.append(generate_question(lo, content.content, practice_target, "practice"))
    index = question_to_rebalance([q.question for q in questions])
    if index is not None:
        stale = questions[index]
        questions[index] = generate_question(lo, content.content, stale.target, stale.mode, LENGTH_TELL_FEEDBACK)

    return NoRagModule(
        content=content.content,
        slides=slides,
        examples=examples,
        questions=questions,
        checks={
            "content_pass": content_check.completeness_pass and content_check.scope_pass,
            "slides_pass": slides_check.no_new_info_pass and slides_check.conciseness_pass
            and slides_check.cognitive_load_pass,
            "example_pass": example_check.consistency_pass and example_check.no_overreach_pass,
            "questions_pass": all(q.passed for q in questions),
        },
    )
