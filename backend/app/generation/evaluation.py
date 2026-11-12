"""LLM chấm (LLM-as-judge) cho nghiên cứu RAG so với không RAG: chấm một gói nội dung module
(ModuleContentInput) theo rubric chín tiêu chí ở docs/rubric.md, đúng rubric mà người chấm dùng, để
so được độ đồng thuận giữa mô hình chấm và người.

Không đọc module từ database: đầu vào là gói tự đủ theo hợp đồng nghiên cứu ở docs/studies.md,
cùng một cấu trúc cho mọi nhóm. scripts/export_study_pair.py xuất cặp gói của một
module, scripts/run_judge.py gọi judge_module cho cả thư mục gói, scripts/analyze_study.py tính
thống kê trên kết quả.
"""

from pydantic import BaseModel, Field, model_validator

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


class ModuleEvaluation(BaseModel):
    anon_id: str
    accuracy: DimensionScore
    completeness: DimensionScore
    consistency: DimensionScore
    hallucination_score: DimensionScore
    hallucination_findings: list[HallucinationFinding] = Field(default_factory=list)
    slide_quality: DimensionScore
    quiz_quality: DimensionScore
    bloom_fidelity: DimensionScore
    query_alignment: DimensionScore
    overall_notes: str


_SYSTEM_INSTRUCTION = """Ban la chuyen gia danh gia noi dung Microlearning cho giao duc dai
hoc, tham gia 1 nghien cuu so sanh mu (blind study) giua 3 nguon sinh noi dung khac nhau
(Human-created / LLM-generated khong RAG / RAG+LLM). TUYET DOI KHONG duoc doan hoac nhac den
module nay co the thuoc nguon nao trong justification -- chi cham dua tren chat luong noi dung,
khong duoc thien vi theo phong doan nguon goc.

Cham dung 9 tieu chi sau, thang diem 1-5 cho moi tieu chi (tru hallucination_findings la danh
sach, khong phai diem so):

1. accuracy: Content, example, quiz, practice va explanation co dung voi SOURCE CHUNK cung cap
khong. Cham thap neu co thong tin sai lech.

2. completeness: So sanh voi TOAN BO source chunk duoc cung cap -- KHONG duoc chi doi chieu voi
pham vi hep cua learning_objective da chon roi coi la du (loi phuong phap da tung gap: content
duoc cham 5/5 chi vi khop voi chinh LO no tu chon, du bo sot han 1 khai niem lon khac trong
source). Trong justification PHAI liet ke cu the y/khai niem lon nao trong source ma content
khong nhac toi. Neu y bi bo sot LA CAN THIET de dat chinh learning_objective da chon, PHAI cham
thap tuong ung. Neu y bi bo sot la chu de khac, khong lien quan learning_objective (module tu
gioi han pham vi hop le), ghi nhan trong justification nhung KHONG tru diem.

3. consistency: Moi cau quiz va cau practice co dung noi dung da day trong content khong; giai
thich cua MOI cau co khop dap an dung cua chinh cau do khong; cac example co mau thuan voi content
khong.

4. hallucination_score + hallucination_findings: Liet ke CU THE tung claim trong content, slides,
example, quiz, practice va explanation KHONG co can cu trong SOURCE CHUNK (field = "content"/
"slides"/"example"/"quiz"/"practice"/"explanation"). Khong
co claim nao thi hallucination_findings rong va hallucination_score = 5. KHONG duoc bo qua tieu
chi nay du module co ve sach -- phai kiem tra tung cau.

5. slide_quality: Cac bullet trong slides co tom tat dung, ro rang, do dai hop ly so voi content
khong.

6. quiz_quality: Cac cau quiz va cau practice co dung do kho theo bloom_level khai bao khong. Cac distractor co
hop ly, khong tam thuong/ro rang sai ngay khong. Ca bo cau hoi co de lo dap an khong (vd dap an
dung luon la phuong an dai nhat), va cac cau co kiem cac y khac nhau cua bai hay lap lai mot y.

7. bloom_fidelity: Muc do khop giua bloom_level da khai bao va cap do THUC TE ma content/quiz/
practice dat duoc, dung bang neo sau:
   - Remember: chi doi hoi nho lai su kien/dinh nghia/thuat ngu, khong giai thich ly do.
   - Understand: dien giai lai bang loi rieng, neu quan he giua cac khai niem da hoc.
   - Apply: van dung khai niem vao tinh huong cu the CHUA gap trong nguon.
   - Analyze: tach nho van de, chi ro moi quan he/khac biet giua cac thanh phan.
   - Evaluate: dua ra phan doan co tieu chi, can nhac uu-nhuoc diem.
   - Create: ket hop cac phan da hoc thanh 1 san pham/giai phap moi.
   Diem 5 = khop hoan toan cap do khai bao; diem 1 = lech cap ro ret (vd LO ghi Remember nhung
   quiz doi Analyze, hoac LO ghi Apply nhung noi dung dung o Remember).

8. query_alignment: Module co thuc su tra loi dung CHU DE GOC duoc giao khong (xem truong
"topic" duoi day) -- khac bloom_fidelity (kiem tra khop VOI CHINH learning_objective module tu
khai bao, khong lien quan chu de goc). Mot module co the chinh xac, nhat quan, khong
hallucination nhung lac chu de -- PHAI cham thap tieu chi nay du cac tieu chi khac cao. Day la
tieu chi chong tautology quan trong nhat.

Moi diem so PHAI kem justification ngan gon giai thich ly do cham diem do. Chi duoc dua vao
SOURCE CHUNK cung cap de danh gia accuracy/hallucination -- khong duoc tu suy doan dung/sai dua
tren kien thuc rieng cua ban ve chu de nay."""


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
        f"Practice:\n{_question_block(packet.practice)}\n\n"
        f"SOURCE CHUNK GOC (dung de doi chieu do chinh xac/hallucination):\n{source_text}\n\n"
        f"Hay danh gia module tren theo dung 9 tieu chi da huong dan. Truong anon_id trong ket "
        f"qua phai dung bang \"{packet.anon_id}\"."
    )


def judge_module(packet: ModuleContentInput, **kwargs) -> ModuleEvaluation:
    prompt = _build_prompt(packet)
    return complete_structured(
        prompt, ModuleEvaluation, system_instruction=_SYSTEM_INSTRUCTION, temperature=0.1, **kwargs
    )
