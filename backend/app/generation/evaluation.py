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

# The 19 item-writing flaws of Tarrant et al. (2006), worded as in Moore et al. (2024), Table 1:
# a question is flawed if ... An item with at most one flaw is acceptable (Tarrant et al., 2006).
ITEM_WRITING_FLAWS: dict[str, str] = {
    "longest_option_correct": "the correct option is longer and more detailed than the distractors",
    "ambiguous_information": "the question or an option is written unclearly, in ambiguous language",
    "implausible_distractors": "a distractor is implausible",
    "true_or_false": "the options are a series of true/false statements",
    "absolute_terms": "the question or options use absolute terms (never, always, all)",
    "complex_k_type": "the options are combinations of other options to choose from",
    "negatively_worded": "the question is negatively worded",
    "convergence_cues": "the correct option shares the most components with the other options",
    "lost_sequence": "options are not in chronological or numerical order",
    "unfocused_stem": "the stem cannot be understood and answered without reading the options",
    "none_of_the_above": "an option is 'none of the above'",
    "word_repeats": "a word of the stem is repeated only in the correct option",
    "more_than_one_correct": "there is not a single best answer",
    "logical_cues": "the stem and correct option contain clues a test-wise student can use",
    "all_of_the_above": "an option is 'all of the above'",
    "fill_in_the_blank": "the stem omits words in the middle that the options fill in",
    "vague_terms": "options use vague terms (frequently, occasionally)",
    "grammatical_cues": "not every option is grammatically consistent with the stem",
    "gratuitous_information": "the stem has information not needed to answer",
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

_SYSTEM_INSTRUCTION = """Ban la chuyen gia danh gia noi dung Microlearning cho giao duc dai
hoc, tham gia 1 nghien cuu so sanh mu (blind study) giua cac nguon sinh noi dung khac nhau.
TUYET DOI KHONG duoc doan hoac nhac den module nay co the thuoc nguon nao trong justification --
chi cham dua tren chat luong noi dung, khong duoc thien vi theo phong doan nguon goc. Do dai cua
module khong phai la chat luong: khong cong diem cho noi dung dai hon.

Cham theo rubric v3, thang 1-5 cho moi tieu chi. Moi diem PHAI kem justification ngan neu
bang chung cu the (trich y/cau lien quan). Chi dua vao SOURCE CHUNK de danh gia accuracy va
hallucination -- khong tu suy doan dung/sai bang kien thuc rieng.

1. accuracy (do chinh xac): content, slides, example, quiz, practice va explanation co dung voi
SOURCE CHUNK khong, muc do chi tiet co phu hop khong. 5 = khong co y nao sai lech so voi nguon;
4 = sai sot nho khong lam hieu sai; 3 = co mot y sai lam hieu sai mot phan; 2 = nhieu y sai;
1 = y cot loi sai.

2. completeness (do day du so voi muc tieu): doi chieu voi TOAN BO source chunk, nhung chi tru
diem nhung y CAN THIET de dat learning_objective da chon. Trong justification PHAI liet ke y lon
nao cua source ma content khong nhac toi, va noi ro y do co can cho learning_objective khong.
Y bo sot thuoc chu de khac (module tu gioi han pham vi hop le) thi ghi nhan nhung KHONG tru diem.
5 = du moi y can thiet; 3 = thieu mot y can thiet; 1 = thieu phan lon y can thiet.

3. consistency (nhat quan theo constructive alignment): dong tu hanh dong cua learning_objective
(vd "phan biet", "tinh", "giai thich") phai la viec content day, example minh hoa va cau hoi doi
hoi. Kiem tra: moi cau quiz/practice co hoi dung dieu content da day; giai thich cua MOI cau co
khop dap an dung cua chinh cau do; example co mau thuan voi content khong. 5 = khop hoan toan;
3 = mot thanh phan lech (vd quiz hoi viec content khong day); 1 = cac thanh phan khong lien quan.

4. hallucination_score + hallucination_findings: tach noi dung thanh tung y nho (atomic claim)
va kiem tung y co can cu trong SOURCE CHUNK khong. Liet ke MOI y khong co can cu, field =
"content"/"slides"/"example"/"quiz"/"practice"/"explanation". Boi canh gia dinh cua mot vi du
(ten nguoi, tinh huong) KHONG phai la claim; chi cac khang dinh ve kien thuc cua bai moi la claim.
5 = khong co claim nao khong can cu (findings rong); 4 = 1 claim phu, khong anh huong muc tieu;
3 = 2-3 claim, hoac 1 claim anh huong cach hieu; 2 = nhieu claim; 1 = phan cot loi khong can cu.
KHONG duoc bo qua tieu chi nay du module co ve sach -- phai kiem tra tung y.

5. slide_quality: theo nguyen tac thiet ke da phuong tien cua Mayer. Coherence: khong co chi tiet
thua khong phuc vu muc tieu. Signaling: tieu de va bullet lam noi bat y chinh va cau truc.
Segmenting: moi slide mot y, do dai vua phai. Bullet phai tom tat dung content. 5 = dat ca ba;
3 = vi pham ro mot nguyen tac hoac co bullet tom tat sai; 1 = slide khong dung de hoc duoc.

6. quiz_quality + quiz_flaws: danh so cac cau quiz 1..n theo thu tu, cau practice la n+1. Voi
TUNG cau, ghi cac loi viet cau hoi (item-writing flaw) sau day ma cau do mac (khong mac loi
nao thi flaws rong), moi cau dung mot muc trong quiz_flaws:
""" + _FLAW_LINES + """
   quiz_quality.score duoc he thong tinh tu quiz_flaws (ty le cau co toi da 1 loi); van ghi
   justification neu loi chinh. Do kho theo Bloom cham o bloom_fidelity, khong o day.

7. bloom_fidelity: muc do khop giua bloom_level da khai bao va cap do THUC TE ma content/quiz/
practice dat duoc (Bloom ban sua doi, Anderson & Krathwohl), dung bang neo sau:
   - Remember: chi doi hoi nho lai su kien/dinh nghia/thuat ngu, khong giai thich ly do.
   - Understand: dien giai lai bang loi rieng, neu quan he giua cac khai niem da hoc.
   - Apply: van dung khai niem vao tinh huong cu the CHUA gap trong nguon.
   - Analyze: tach nho van de, chi ro moi quan he/khac biet giua cac thanh phan.
   - Evaluate: dua ra phan doan co tieu chi, can nhac uu-nhuoc diem.
   - Create: ket hop cac phan da hoc thanh 1 san pham/giai phap moi.
   5 = khop hoan toan cap do khai bao; 1 = lech cap ro ret (vd LO ghi Remember nhung quiz doi
   Analyze, hoac LO ghi Apply nhung noi dung dung o Remember).

8. query_alignment (bam chu de va tinh micro): module co tra loi dung CHU DE GOC (truong "topic")
khong, va co tap trung vao MOT muc tieu chinh, khong lan sang chu de khac hay nhoi qua nhieu y.
Khac bloom_fidelity (khop voi learning_objective module tu khai bao). Module chinh xac, nhat
quan nhung lac chu de van PHAI bi cham thap o tieu chi nay. 5 = dung chu de, mot muc tieu ro;
3 = dung chu de nhung dan trai hoac lan sang y ngoai; 1 = lac chu de.

9. examples_feedback (vi du va loi giai): Example co dung, co trinh bay tung buoc/buoc con de
nguoi hoc theo duoc cach giai, va giup hieu khai niem khong (worked examples, Atkinson et al.).
Explanation cua moi cau co noi VI SAO dap an dung la dung va vi sao cac phuong an nhieu sai, cu
the va ngan gon, khong chi xac nhan dung/sai (elaborated feedback, Shute). 5 = vi du co buoc ro va
moi loi giai neu ly do; 3 = vi du chi neu ket qua hoac nhieu loi giai chi xac nhan dap an;
1 = vi du sai hoac khong co loi giai dung duoc."""


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
        f"  Example {n} - Boi canh: {e.context}\n  Example {n} - Phan tich: {e.analysis}"
        for n, e in enumerate(packet.examples, start=1)
    )
    quiz_text = "\n".join(f"  Cau {n}:\n{_question_block(q)}" for n, q in enumerate(packet.quiz, start=1))
    sub_objectives_text = "".join(f"\n  - {s}" for s in packet.sub_objectives)

    return (
        f"CHU DE GOC (dung de cham query_alignment):\n{packet.topic}\n\n"
        f"MODULE CAN DANH GIA (anon_id={packet.anon_id}):\n"
        f"Learning Objective (bloom_level={packet.bloom_level}): {packet.learning_objective}"
        f"{sub_objectives_text}\n"
        f"Content: {packet.content_markdown}\n"
        f"Slides:\n{slides_text}\n"
        f"Examples:\n{examples_text}\n"
        f"Quiz (moi cau kem giai thich rieng):\n{quiz_text}\n"
        f"Practice (cau {len(packet.quiz) + 1}):\n{_question_block(packet.practice)}\n\n"
        f"SOURCE CHUNK GOC (dung de doi chieu do chinh xac/hallucination):\n{source_text}\n\n"
        f"Hay danh gia module tren theo dung 9 tieu chi cua rubric v3. quiz_flaws co dung "
        f"{len(packet.quiz) + 1} muc: cau 1..{len(packet.quiz)} la quiz, cau {len(packet.quiz) + 1} la practice. "
        f"Truong anon_id trong ket qua phai dung bang \"{packet.anon_id}\"."
    )


def judge_module(packet: ModuleContentInput, **kwargs) -> ModuleEvaluation:
    prompt = _build_prompt(packet)
    return complete_structured(
        prompt, ModuleEvaluation, system_instruction=_SYSTEM_INSTRUCTION, temperature=0.1,
        validation_context=question_context(packet), **kwargs
    )
