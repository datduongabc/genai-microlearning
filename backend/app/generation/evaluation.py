"""LLM chấm (LLM-as-judge) cho nghiên cứu RAG so với không RAG: chấm một gói nội dung module
(ModuleContentInput) theo rubric v3 ở docs/rubric.md, đúng rubric mà người chấm dùng, để
so được độ đồng thuận giữa mô hình chấm và người.

Không đọc module từ database: đầu vào là gói tự đủ theo hợp đồng nghiên cứu ở docs/studies.md,
cùng một cấu trúc cho mọi nhóm. scripts/export_study_pair.py xuất cặp gói của một
module, scripts/run_judge.py gọi judge_module cho cả thư mục gói, scripts/analyze_study.py tính
thống kê trên kết quả.
"""

import math
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, model_validator

from app.llm.gateway import complete_structured


class QuizQuestionInput(BaseModel):
    stem: str
    choices: list[str] = Field(min_length=1)
    correct_index: int = Field(ge=0)
    explanation: str = ""

    @model_validator(mode="after")
    def valid_answer(self):
        if self.correct_index >= len(self.choices):
            raise ValueError("correct_index must reference a choice")
        return self


class SlideInput(BaseModel):
    title: str
    bullets: list[str]


class ExampleInput(BaseModel):
    context: str
    analysis: str


class ModuleContentInput(BaseModel):
    """Group-agnostic module content -- the on-disk shape of one blind
    rating packet. Same shape regardless of source (RAG+LLM export,
    human-authored, or LLM-no-RAG) so the judge/human rater sees identical
    structure across all 3 study groups -- required for a valid blind
    comparison. Must NOT contain anything revealing group or source
    (no course name, no document filename, no model name)."""

    anon_id: str
    topic: str
    bloom_level: str
    learning_objective: str
    sub_objectives: list[str] = Field(default_factory=list)
    content_markdown: str
    slides: list[SlideInput]
    examples: list[ExampleInput]
    # Mỗi câu mang giải thích của riêng nó (QuizQuestionInput.explanation), như một bài giảng viên
    # soạn: lời giải đi ngay sau câu hỏi, không gom thành một mục chung cho cả quiz.
    quiz: list[QuizQuestionInput] = Field(min_length=1)
    practice: QuizQuestionInput
    source_chunks: list[str]


class DimensionScore(BaseModel):
    score: int = Field(ge=1, le=5)
    justification: str


class HallucinationFinding(BaseModel):
    claim: str = Field(description="Cau/y cu the trong module khong co can cu trong source chunk")
    field: str = Field(description="Phan chua claim nay: content/slides/example/quiz/practice/explanation")
    reason: str


RUBRIC_VERSION = "v3"

# The 19 item-writing flaws of Tarrant et al. (2006), after the wording of Moore et al. (2024), Table 1,
# in the Vietnamese the human raters read (frontend/src/lib/ratingSchema.ts), so that judges and raters
# score the same text. An item with at most one flaw is acceptable (Tarrant et al., 2006).
ITEM_WRITING_FLAWS: dict[str, str] = {
    "longest_option_correct": "Đáp án đúng dài, chi tiết hơn các phương án khác",
    "ambiguous_information": "Câu hỏi hoặc phương án mơ hồ, khó hiểu",
    "implausible_distractors": "Có phương án nhiễu vô lý",
    "true_or_false": "Các phương án là chuỗi mệnh đề đúng/sai",
    "absolute_terms": "Dùng từ tuyệt đối (luôn luôn, không bao giờ, tất cả)",
    "complex_k_type": "Phương án là tổ hợp các phương án khác (A và B, ...)",
    "negatively_worded": "Câu hỏi dùng dạng phủ định",
    "convergence_cues": "Đáp án đúng chung nhiều thành phần nhất với các phương án khác",
    "lost_sequence": "Phương án không xếp theo thứ tự số hoặc thời gian",
    "unfocused_stem": "Phải đọc phương án mới hiểu câu hỏi",
    "none_of_the_above": "Có phương án \"không có đáp án nào đúng\"",
    "word_repeats": "Từ trong câu hỏi chỉ lặp lại ở đáp án đúng",
    "more_than_one_correct": "Có hơn một đáp án đúng",
    "logical_cues": "Câu hỏi có gợi ý giúp đoán ra đáp án",
    "all_of_the_above": "Có phương án \"tất cả đều đúng\"",
    "fill_in_the_blank": "Câu hỏi dạng điền vào chỗ trống ở giữa",
    "vague_terms": "Phương án dùng từ mơ hồ (thường, đôi khi)",
    "grammatical_cues": "Phương án không khớp ngữ pháp với câu hỏi",
    "gratuitous_information": "Câu hỏi có thông tin thừa",
}
ItemWritingFlaw = Literal[tuple(ITEM_WRITING_FLAWS)]  # type: ignore[valid-type]


class QuestionFlaws(BaseModel):
    question: int = Field(ge=1, description="So thu tu cau: 1..so cau quiz, cau cuoi la practice")
    flaws: list[ItemWritingFlaw] = Field(default_factory=list)


def quiz_quality_score(flaws: list[QuestionFlaws]) -> int:
    """1 to 5 from the share of questions with at most one flaw: all of them 5, none of them 1."""
    acceptable = sum(len(set(q.flaws)) <= 1 for q in flaws) / len(flaws)
    return 1 + math.floor(4 * acceptable + 0.5)


class ModuleEvaluation(BaseModel):
    """Rubric v3 (docs/rubric.md). quiz_quality.score is not a judgement: it is computed from
    quiz_flaws, so a human and a model who mark the same flaws give the same score."""
    anon_id: str
    accuracy: DimensionScore
    completeness: DimensionScore
    consistency: DimensionScore
    hallucination_score: DimensionScore
    hallucination_findings: list[HallucinationFinding] = Field(default_factory=list)
    slide_quality: DimensionScore
    quiz_quality: DimensionScore
    quiz_flaws: list[QuestionFlaws] = Field(min_length=1)
    bloom_fidelity: DimensionScore
    query_alignment: DimensionScore
    examples_feedback: DimensionScore
    overall_notes: str

    @model_validator(mode="after")
    def derived_quiz_score(self, info: ValidationInfo):
        numbers = [q.question for q in self.quiz_flaws]
        # The packet's quiz questions and its practice question, when the caller knows the packet.
        expected = (info.context or {}).get("questions", len(numbers))
        if numbers != list(range(1, expected + 1)):
            raise ValueError(f"quiz_flaws must list each of the {expected} questions once, numbered 1 to {expected}")
        # A copy: the caller may have passed one DimensionScore object for several criteria.
        self.quiz_quality = self.quiz_quality.model_copy(update={"score": quiz_quality_score(self.quiz_flaws)})
        return self


def question_context(packet: ModuleContentInput) -> dict:
    """What ModuleEvaluation checks quiz_flaws against: the quiz questions and the practice question."""
    return {"questions": len(packet.quiz) + 1}


_FLAW_LINES = "\n".join(f"   - {key}: {text}" for key, text in ITEM_WRITING_FLAWS.items())

_SYSTEM_INSTRUCTION = """Bạn là chuyên gia đánh giá nội dung Microlearning cho giáo dục đại học, tham gia một
nghiên cứu so sánh mù (blind study) giữa các nguồn sinh nội dung khác nhau. TUYỆT ĐỐI KHÔNG đoán
hoặc nhắc đến việc module này có thể thuộc nguồn nào trong justification; chỉ chấm dựa trên chất
lượng nội dung, không thiên vị theo phỏng đoán nguồn gốc. Độ dài của module không phải là chất
lượng: không cộng điểm cho nội dung dài hơn.

Chấm theo rubric v3, thang 1-5 cho mỗi tiêu chí. Mỗi điểm PHẢI kèm justification ngắn nêu bằng
chứng cụ thể (trích ý hoặc câu liên quan). Chỉ dựa vào SOURCE CHUNK để đánh giá accuracy và
hallucination, không tự suy đoán đúng sai bằng kiến thức riêng.

1. accuracy (độ chính xác): content, slides, example, quiz, practice và explanation có đúng với
SOURCE CHUNK không, mức độ chi tiết có phù hợp không. 5 = không có ý nào sai lệch so với nguồn;
4 = sai sót nhỏ không làm hiểu sai; 3 = có một ý sai làm hiểu sai một phần; 2 = nhiều ý sai;
1 = ý cốt lõi sai.

2. completeness (độ đầy đủ so với mục tiêu): đối chiếu với TOÀN BỘ source chunk, nhưng chỉ trừ
điểm những ý CẦN THIẾT để đạt learning_objective đã chọn. Trong justification PHẢI liệt kê ý lớn
nào của nguồn mà content không nhắc tới, và nói rõ ý đó có cần cho learning_objective không. Ý bị
bỏ sót thuộc chủ đề khác (module tự giới hạn phạm vi hợp lệ) thì ghi nhận nhưng KHÔNG trừ điểm.
5 = đủ mọi ý cần thiết; 3 = thiếu một ý cần thiết; 1 = thiếu phần lớn ý cần thiết.

3. consistency (nhất quán theo constructive alignment): động từ hành động của learning_objective
(ví dụ "phân biệt", "tính", "giải thích") phải là việc content dạy, example minh họa và câu hỏi
đòi hỏi. Kiểm tra: mỗi câu quiz và practice có hỏi đúng điều content đã dạy; lời giải của MỖI câu
có khớp đáp án đúng của chính câu đó; example có mâu thuẫn với content không. 5 = khớp hoàn toàn;
3 = một thành phần lệch (ví dụ quiz hỏi việc content không dạy); 1 = các thành phần không liên quan.

4. hallucination_score và hallucination_findings: tách nội dung thành từng ý nhỏ (atomic claim)
và kiểm từng ý có căn cứ trong SOURCE CHUNK không. Liệt kê MỌI ý không có căn cứ, field là một
trong "content", "slides", "example", "quiz", "practice", "explanation". Bối cảnh giả định của
một ví dụ (tên người, tình huống) KHÔNG phải là claim; chỉ các khẳng định về kiến thức của bài mới
là claim. 5 = không có claim nào không căn cứ (findings rỗng); 4 = một claim phụ, không ảnh hưởng
mục tiêu; 3 = hai đến ba claim, hoặc một claim ảnh hưởng cách hiểu; 2 = nhiều claim; 1 = phần cốt
lõi không có căn cứ. KHÔNG được bỏ qua tiêu chí này dù module có vẻ sạch: phải kiểm từng ý.

5. slide_quality: theo nguyên tắc thiết kế đa phương tiện của Mayer. Coherence: không có chi tiết
thừa không phục vụ mục tiêu. Signaling: tiêu đề và gạch đầu dòng làm nổi bật ý chính và cấu trúc.
Segmenting: mỗi slide một ý, độ dài vừa phải. Gạch đầu dòng phải tóm tắt đúng content. 5 = đạt
cả ba; 3 = vi phạm rõ một nguyên tắc hoặc có gạch đầu dòng tóm tắt sai; 1 = slide không dùng để
học được.

6. quiz_quality và quiz_flaws: đánh số các câu quiz 1..n theo thứ tự, câu practice là n+1. Với
TỪNG câu, ghi các lỗi viết câu hỏi (item-writing flaw) sau đây mà câu đó mắc (không mắc lỗi nào
thì flaws rỗng), mỗi câu đúng một mục trong quiz_flaws:
""" + _FLAW_LINES + """
   quiz_quality.score do hệ thống tính từ quiz_flaws (tỉ lệ câu có tối đa một lỗi); vẫn ghi
   justification nêu lỗi chính. Độ khó theo Bloom chấm ở bloom_fidelity, không ở đây.

7. bloom_fidelity: mức độ khớp giữa bloom_level đã khai báo và cấp độ THỰC TẾ mà content, quiz và
practice đạt được (Bloom bản sửa đổi, Anderson & Krathwohl), dùng bảng neo sau:
   - Remember: chỉ đòi hỏi nhớ lại sự kiện, định nghĩa, thuật ngữ, không giải thích lý do.
   - Understand: diễn giải lại bằng lời riêng, nêu quan hệ giữa các khái niệm đã học.
   - Apply: vận dụng khái niệm vào tình huống cụ thể CHƯA gặp trong nguồn.
   - Analyze: tách nhỏ vấn đề, chỉ rõ mối quan hệ hoặc khác biệt giữa các thành phần.
   - Evaluate: đưa ra phán đoán có tiêu chí, cân nhắc ưu nhược điểm.
   - Create: kết hợp các phần đã học thành một sản phẩm hoặc giải pháp mới.
   5 = khớp hoàn toàn cấp độ khai báo; 1 = lệch cấp rõ rệt (ví dụ LO ghi Remember nhưng quiz đòi
   Analyze, hoặc LO ghi Apply nhưng nội dung dừng ở Remember).

8. query_alignment (bám chủ đề và tính micro): module có trả lời đúng CHỦ ĐỀ GỐC (trường "topic")
không, và có tập trung vào MỘT mục tiêu chính, không lan sang chủ đề khác hay nhồi quá nhiều ý.
Khác bloom_fidelity (khớp với learning_objective do module tự khai báo). Module chính xác, nhất
quán nhưng lạc chủ đề vẫn PHẢI bị chấm thấp ở tiêu chí này. 5 = đúng chủ đề, một mục tiêu rõ;
3 = đúng chủ đề nhưng dàn trải hoặc lan sang ý ngoài; 1 = lạc chủ đề.

9. examples_feedback (ví dụ và lời giải): example có đúng, có trình bày từng bước hoặc bước con
để người học theo được cách giải, và giúp hiểu khái niệm không (worked examples, Atkinson et al.).
Explanation của mỗi câu có nói VÌ SAO đáp án đúng là đúng và vì sao các phương án nhiễu sai, cụ
thể và ngắn gọn, không chỉ xác nhận đúng sai (elaborated feedback, Shute). 5 = ví dụ có bước rõ
và mọi lời giải nêu lý do; 3 = ví dụ chỉ nêu kết quả hoặc nhiều lời giải chỉ xác nhận đáp án;
1 = ví dụ sai hoặc không có lời giải dùng được."""


def _build_prompt(packet: ModuleContentInput) -> str:
    slides_text = "\n".join(
        f"  - {s.title}: {'; '.join(s.bullets)}" for s in packet.slides
    )
    source_text = "\n\n".join(
        f"[Source chunk {i + 1}]\n{c}" for i, c in enumerate(packet.source_chunks)
    )

    def _question_block(q: QuizQuestionInput) -> str:
        correct = q.choices[q.correct_index]
        return f"  {q.stem}\n    Choices: {q.choices}\n    Correct: {correct}\n    Explanation: {q.explanation}"

    examples_text = "\n".join(
        f"  Example {n} - Bối cảnh: {e.context}\n  Example {n} - Phân tích: {e.analysis}"
        for n, e in enumerate(packet.examples, start=1)
    )
    quiz_text = "\n".join(f"  Câu {n}:\n{_question_block(q)}" for n, q in enumerate(packet.quiz, start=1))
    sub_objectives_text = "".join(f"\n  - {s}" for s in packet.sub_objectives)

    return (
        f"CHỦ ĐỀ GỐC (dùng để chấm query_alignment):\n{packet.topic}\n\n"
        f"MODULE CẦN ĐÁNH GIÁ (anon_id={packet.anon_id}):\n"
        f"Learning Objective (bloom_level={packet.bloom_level}): {packet.learning_objective}"
        f"{sub_objectives_text}\n"
        f"Content: {packet.content_markdown}\n"
        f"Slides:\n{slides_text}\n"
        f"Examples:\n{examples_text}\n"
        f"Quiz (mỗi câu kèm lời giải riêng):\n{quiz_text}\n"
        f"Practice (câu {len(packet.quiz) + 1}):\n{_question_block(packet.practice)}\n\n"
        f"SOURCE CHUNK GỐC (dùng để đối chiếu độ chính xác và hallucination):\n{source_text}\n\n"
        f"Hãy đánh giá module trên theo đúng 9 tiêu chí của rubric v3. quiz_flaws có đúng "
        f"{len(packet.quiz) + 1} mục: câu 1..{len(packet.quiz)} là quiz, câu {len(packet.quiz) + 1} là practice. "
        f"Trường anon_id trong kết quả phải đúng bằng \"{packet.anon_id}\"."
    )


def judge_module(packet: ModuleContentInput, **kwargs) -> ModuleEvaluation:
    prompt = _build_prompt(packet)
    return complete_structured(
        prompt, ModuleEvaluation, system_instruction=_SYSTEM_INSTRUCTION, temperature=0.1,
        validation_context=question_context(packet), **kwargs
    )
