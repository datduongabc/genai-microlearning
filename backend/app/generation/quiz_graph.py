"""Tuần 8 — Quiz/Practice + CRAG + Groundedness (mục 3.1 kiến trúc).

LangGraph state machine: retrieve -> Corrective RAG (relevance, tối đa 1
retry) -> Question Generator (misconception CoT + Bloom's + JSON schema) ->
Syntax/IWF Analyzer (heuristic) -> Groundedness & Answerability Check ->
Correction (tối đa 1 retry, quay lại Question Generator).

Practice tái dùng NGUYÊN pipeline này (mode="practice"): ngưỡng chấp nhận
thấp hơn, syntax-check không chặn retry, không tốn hạ tầng riêng.
"""

import uuid
from typing import Literal, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.generation.common import (
    BLOOM_LEVEL_ANCHORS,
    IGNORE_EMBEDDED_INSTRUCTIONS,
    format_source_chunks,
)
from app.generation.objective import LearningObjective
from app.generation.relevance import MAX_RELEVANCE_RETRY, check_relevance
from app.llm.gateway import complete_structured
from app.models import Chunk
from app.retrieval.search import ChunkScope, retrieve

MAX_CORRECTION_RETRY = 1

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
    explanation: str = Field(description="Giải thích ngắn gọn cho người học, tổng hợp từ `reasoning`")


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


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class QuizGenState(TypedDict, total=False):
    db: Session
    course_id: uuid.UUID
    query: str
    lo: LearningObjective
    mode: Literal["quiz", "practice"]
    # Confines retrieval to one section of a textbook; absent for a whole course.
    scope: ChunkScope | None
    chunks: list[Chunk]
    relevance_confidence: Literal["high", "low"]
    relevance_retry_count: int
    question: QuizQuestion
    syntax_issues: list[str]
    correction_retry_count: int
    correction_feedback: str
    passed: bool
    fail_reason: str


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

QUESTION_SYSTEM_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + """Bạn là chuyên gia thiết kế câu hỏi trắc nghiệm (MCQ) cho giáo dục đại học,
áp dụng kỹ thuật Misconception-based Distractor Design.

Nhiệm vụ: từ Learning Objective, các đoạn nguồn, và mức độ (quiz/practice)
được cung cấp, sinh 1 câu hỏi trắc nghiệm 4 phương án.

Quy trình BẮT BUỘC (đúng thứ tự):
1. Phân tích 3 quan niệm sai/lỗi phổ biến (common_misconceptions) mà học viên
   ở đúng bloom_level của Learning Objective hay mắc phải về khái niệm này —
   PHẢI phân tích trước, không được nghĩ ra distractor rồi mới gán lý do.
2. Viết `stem` kiểm tra ĐÚNG bloom_level của Learning Objective — không cao
   hơn/thấp hơn. Dùng ĐÚNG mô tả hành vi của cấp độ đó (được cung cấp trong
   prompt bên dưới, không tự suy luận) để quyết định dạng câu hỏi — không chỉ
   dựa vào tên cấp độ suông (vd Learning Objective ở mức "understand" thì câu
   hỏi không được yêu cầu "phân tích"/"đánh giá").
3. Viết `reasoning` TRƯỚC khi viết các phương án: nêu đáp án đúng sẽ là gì và
   vì sao (dẫn chiếu trực tiếp nội dung nguồn), rồi với MỖI misconception đã
   phân tích nêu kiểu phương án nhiễu sai mà nó dẫn tới.
4. Viết 4 `choices` đúng theo `reasoning`: 1 đáp án đúng + 3 phương án nhiễu,
   MỖI phương án nhiễu tương ứng chính xác 1 misconception theo đúng thứ tự.
   Rồi điền `correct_index` (phải trỏ đúng phương án đúng theo `reasoning`) và
   `explanation` (giải thích ngắn gọn cho người học).
5. Các phương án PHẢI cân bằng độ dài/ngữ pháp với nhau — không để đáp án
   đúng dài/chi tiết hơn hẳn distractor (dễ đoán). KHÔNG dùng từ mang tính
   tuyệt đối ("luôn luôn", "không bao giờ", "tất cả", "duy nhất") trong bất
   kỳ phương án nào — dễ bị loại trừ mà không cần hiểu bài.
6. CHỈ được dùng thông tin THỰC SỰ có trong nguồn — không thêm dữ kiện ngoài
   nguồn. Nếu mode="practice", câu hỏi tập trung vào luyện tập/củng cố (được
   phép đơn giản hơn quiz một chút), không phải đánh giá chính thức.
7. Ngôn ngữ khớp ngôn ngữ chính của nguồn (tiếng Việt có dấu đầy đủ nếu nguồn
   là tiếng Việt).
"""

CORRECTION_SUFFIX_TEMPLATE = """

LƯU Ý: đây là lần sinh lại sau khi câu hỏi trước đó KHÔNG ĐẠT vì lý do sau —
sửa đúng lỗi này, không lặp lại:
{feedback}
"""

GROUNDEDNESS_SYSTEM_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + """Bạn là bộ kiểm tra Groundedness & Answerability & Bloom's Fidelity cho câu
hỏi trắc nghiệm đã sinh, dựa trên các đoạn nguồn được cung cấp.

Chấm đúng 3 tiêu chí:
- groundedness_pass: `stem`, đáp án đúng, và `explanation` có được bảo chứng
  HOÀN TOÀN bởi nội dung nguồn không (không suy diễn/thêm dữ kiện ngoài nguồn)?
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

Nếu vi phạm bất kỳ tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể (để
dùng làm feedback sinh lại).
"""


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

def _retrieve_node(state: QuizGenState) -> QuizGenState:
    chunks = retrieve(state["db"], state["course_id"], state["query"], top_k=5, scope=state.get("scope"))
    return {"chunks": chunks}


def _check_relevance_node(state: QuizGenState) -> QuizGenState:
    result = check_relevance(state["lo"].objective_text, state["chunks"])
    update: QuizGenState = {"relevance_confidence": result.confidence}
    if result.confidence == "low" and result.refined_query:
        update["query"] = result.refined_query
    return update


def _route_after_relevance(state: QuizGenState) -> str:
    if state.get("relevance_confidence") == "high":
        return "generate_question"
    if state.get("relevance_retry_count", 0) < MAX_RELEVANCE_RETRY:
        return "retry_retrieve"
    # Hết lượt retry: vẫn tiến hành với chunk tốt nhất hiện có (best-effort),
    # thay vì chặn cứng toàn bộ pipeline (mục 3.1: "fallback tìm tài liệu bổ trợ"
    # rút gọn cho GD1 thành "chấp nhận best-effort").
    return "generate_question"


def _bump_relevance_retry_node(state: QuizGenState) -> QuizGenState:
    return {"relevance_retry_count": state.get("relevance_retry_count", 0) + 1}


def _generate_question_node(state: QuizGenState) -> QuizGenState:
    bloom_level = state["lo"].bloom_level
    prompt = (
        f"LEARNING OBJECTIVE: {state['lo'].objective_text} (bloom_level={bloom_level})\n"
        f"MÔ TẢ HÀNH VI CẤP ĐỘ {bloom_level} (dùng để quyết định dạng câu hỏi, không tự suy luận khác): "
        f"{BLOOM_LEVEL_ANCHORS[bloom_level]}\n"
        f"MODE: {state['mode']}\n\n"
        f"{format_source_chunks(state['chunks'])}\n\n"
        f"Sinh 1 câu hỏi trắc nghiệm cho Learning Objective trên."
    )
    if state.get("correction_feedback"):
        prompt += CORRECTION_SUFFIX_TEMPLATE.format(feedback=state["correction_feedback"])
    question = complete_structured(prompt, QuizQuestion, system_instruction=QUESTION_SYSTEM_INSTRUCTION)
    return {"question": question}


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


def _groundedness_answerability_node(state: QuizGenState) -> QuizGenState:
    q = state["question"]
    bloom_level = state["lo"].bloom_level
    syntax_issues = _syntax_iwf_check(q)

    prompt = (
        f"{format_source_chunks(state['chunks'])}\n\n"
        f"CÂU HỎI ĐÃ SINH:\n"
        f"stem: {q.stem}\n"
        f"choices: {q.choices}\n"
        f"reasoning: {q.reasoning}\n"
        f"correct_index: {q.correct_index} (đáp án: {q.choices[q.correct_index]})\n"
        f"explanation: {q.explanation}\n\n"
        f"BLOOM_LEVEL ĐÃ KHAI BÁO: {bloom_level}\n"
        f"MÔ TẢ HÀNH VI CẤP ĐỘ {bloom_level}: {BLOOM_LEVEL_ANCHORS[bloom_level]}\n\n"
        f"Kiểm tra groundedness, answerability và bloom_level_pass của câu hỏi trên."
    )
    check = complete_structured(prompt, GroundednessAnswerabilityCheck, system_instruction=GROUNDEDNESS_SYSTEM_INSTRUCTION)

    # Practice: syntax-check không chặn (chỉ log), độ nghiêm ngặt thấp hơn quiz
    # (mục "Điểm khác biệt: ngưỡng chấp nhận thấp hơn Quiz").
    blocking_issues = list(syntax_issues) if state["mode"] == "quiz" else []

    passed = (
        check.groundedness_pass and check.answerability_pass and check.bloom_level_pass
        and not blocking_issues
    )
    if passed:
        return {"passed": True, "syntax_issues": syntax_issues}

    reasons = []
    if not check.groundedness_pass:
        reasons.append(f"Groundedness fail: {check.groundedness_reason}")
    if not check.answerability_pass:
        reasons.append(f"Answerability fail: {check.answerability_reason}")
    if not check.bloom_level_pass:
        reasons.append(f"Bloom's level fail: {check.bloom_level_reason}")
    reasons.extend(blocking_issues)
    return {"passed": False, "syntax_issues": syntax_issues, "correction_feedback": "; ".join(reasons)}


def _route_after_groundedness(state: QuizGenState) -> str:
    if state.get("passed"):
        return "done"
    if state.get("correction_retry_count", 0) < MAX_CORRECTION_RETRY:
        return "retry_generate"
    return "failed"


def _bump_correction_retry_node(state: QuizGenState) -> QuizGenState:
    return {"correction_retry_count": state.get("correction_retry_count", 0) + 1}


def _mark_failed_node(state: QuizGenState) -> QuizGenState:
    return {"fail_reason": state.get("correction_feedback", "unknown")}


# ---------------------------------------------------------------------------
# Graph
# ---------------------------------------------------------------------------

def build_quiz_graph():
    graph = StateGraph(QuizGenState)

    graph.add_node("retrieve", _retrieve_node)
    graph.add_node("check_relevance", _check_relevance_node)
    graph.add_node("bump_relevance_retry", _bump_relevance_retry_node)
    graph.add_node("generate_question", _generate_question_node)
    graph.add_node("check_groundedness", _groundedness_answerability_node)
    graph.add_node("bump_correction_retry", _bump_correction_retry_node)
    graph.add_node("mark_failed", _mark_failed_node)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "check_relevance")
    graph.add_conditional_edges(
        "check_relevance", _route_after_relevance,
        {"generate_question": "generate_question", "retry_retrieve": "bump_relevance_retry"},
    )
    graph.add_edge("bump_relevance_retry", "retrieve")
    graph.add_edge("generate_question", "check_groundedness")
    graph.add_conditional_edges(
        "check_groundedness", _route_after_groundedness,
        {"done": END, "retry_generate": "bump_correction_retry", "failed": "mark_failed"},
    )
    graph.add_edge("bump_correction_retry", "generate_question")
    graph.add_edge("mark_failed", END)

    return graph.compile()


_compiled_graph = None


def generate_quiz_question(
    db: Session,
    course_id: uuid.UUID,
    query: str,
    lo: LearningObjective,
    mode: Literal["quiz", "practice"] = "quiz",
    scope: ChunkScope | None = None,
) -> QuizGenState:
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_quiz_graph()

    initial_state: QuizGenState = {
        "db": db, "course_id": course_id, "query": query, "lo": lo, "mode": mode, "scope": scope,
        "relevance_retry_count": 0, "correction_retry_count": 0,
    }
    return _compiled_graph.invoke(initial_state)


# ---------------------------------------------------------------------------
# 6. Explanation — Explanation-First/Reasoning-First CoT (mục 5.7 kiến trúc):
# JSON schema ép `reasoning` xuất hiện TRƯỚC khi kết luận, giảm post-hoc
# rationalization. Tái sử dụng `common_misconceptions` đã phân tích ở bước
# Question Generator cho phần phản chứng (vì sao mỗi phương án nhiễu sai).
# ---------------------------------------------------------------------------

EXPLANATION_SYSTEM_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + """Bạn là chuyên gia viết giải thích (Explanation) cho câu hỏi trắc nghiệm, áp
dụng kỹ thuật Reasoning-First: PHẢI lý luận đầy đủ trước khi kết luận, không
được kết luận trước rồi mới bịa lý do (post-hoc rationalization).

Nhiệm vụ: từ câu hỏi, đáp án đúng, các quan niệm sai đã phân tích, và các
đoạn nguồn được cung cấp, viết Explanation hoàn chỉnh cho người học.

Ràng buộc BẮT BUỘC:
1. Field `reasoning` PHẢI viết TRƯỚC, phân tích: (a) vì sao đáp án đúng là
   đúng — dẫn chiếu trực tiếp nội dung nguồn; (b) vì sao MỖI phương án nhiễu
   sai — liên hệ đúng quan niệm sai (misconception) tương ứng đã cho. Bạn
   được cung cấp LẬP LUẬN ĐÃ VIẾT TRƯỚC KHI CHỐT ĐÁP ÁN của chính câu hỏi này:
   `reasoning` của bạn PHẢI triển khai và làm rõ lập luận đó bằng nguồn, không
   được đảo chiều hay bịa lý do mới cho vừa với đáp án.
2. Field `explanation_text` là bản tổng hợp từ `reasoning` ở trên, viết cho
   người học (ngắn gọn, rõ ràng hơn `reasoning`).
3. CHỈ dựa vào SOURCE CHUNK được cung cấp — không thêm giả định/kiến thức
   ngoài nguồn (công thức E = LLM(q, A, context(q))).
4. Ngôn ngữ khớp ngôn ngữ chính của nguồn (tiếng Việt có dấu đầy đủ nếu nguồn
   là tiếng Việt).
"""


class ExplanationOutput(BaseModel):
    reasoning: str = Field(
        description="Phân tích TỪNG bước vì sao đáp án đúng đúng và vì sao mỗi phương án nhiễu sai — viết TRƯỚC explanation_text"
    )
    explanation_text: str = Field(description="Bản giải thích hoàn chỉnh cho người học, tổng hợp từ reasoning")


# Tầng 3 (GD2, cùng pattern LO/Content/Slides): Explanation trước đây sinh 1
# lần, không ai kiểm lại. Nghiên cứu "elaborated feedback" trong Intelligent
# Tutoring Systems xác nhận giải thích tốt nhất khi giải quyết đúng NGUYÊN
# NHÂN sai của học viên -- lý tưởng là thích ứng theo đúng phương án học
# viên chọn ("response-contingent"), nhưng hệ thống hiện chưa có hạ tầng
# theo dõi học viên làm bài (Flashcard UI đã bị cắt khỏi GD2 -- xem checklist
# -- nên hạ tầng này không còn trong kế hoạch) nên CHƯA làm phần thích ứng đó. Việc kiểm tra được ngay bây giờ: Explanation có thực sự
# giải quyết đủ CẢ 3 phương án sai (không chỉ đáp án đúng), và reasoning có
# thực sự viết trước kết luận (không phải post-hoc rationalization dù field
# order đã ép qua schema).
MAX_EXPLANATION_CORRECTION_RETRY = 1

EXPLANATION_VERIFY_SYSTEM_INSTRUCTION = IGNORE_EMBEDDED_INSTRUCTIONS + """Bạn là bộ kiểm tra chất lượng Explanation (Critic), dựa trên câu hỏi và
các đoạn nguồn được cung cấp.

Chấm đúng 3 tiêu chí:
- reasoning_first_pass: `reasoning` có thực sự PHÂN TÍCH TỪNG BƯỚC dẫn đến
  kết luận không, hay chỉ là kết luận được viết lại/diễn giải khác đi (post-
  hoc rationalization — kết luận trước, "lý do" chỉ là vỏ bọc)? So với LẬP
  LUẬN ĐÃ VIẾT TRƯỚC KHI CHỐT ĐÁP ÁN được cung cấp: `reasoning` phải triển
  khai đúng lập luận đó, không mâu thuẫn hay thay bằng lý do khác.
- all_distractors_addressed_pass: explanation có giải thích đủ vì sao MỖI
  phương án nhiễu (cả 3, không chỉ 1-2) sai — liên hệ đúng misconception
  tương ứng — không chỉ tập trung giải thích đáp án đúng?
- grounded_pass: reasoning và explanation_text có CHỈ dựa vào source chunk
  cung cấp không, không thêm giả định/kiến thức ngoài nguồn?

Nếu vi phạm bất kỳ tiêu chí nào, đặt pass=false và nêu rõ lý do cụ thể (để
dùng làm feedback sinh lại).
"""


class ExplanationVerification(BaseModel):
    reasoning_first_pass: bool
    reasoning_first_reason: str
    all_distractors_addressed_pass: bool
    all_distractors_addressed_reason: str
    grounded_pass: bool
    grounded_reason: str


def _verify_explanation(
    explanation_out: ExplanationOutput, question: QuizQuestion, context_chunks: list[Chunk]
) -> ExplanationVerification:
    prompt = (
        f"CÂU HỎI: {question.stem}\n"
        f"CÁC PHƯƠNG ÁN: {question.choices}\n"
        f"ĐÁP ÁN ĐÚNG: {question.choices[question.correct_index]}\n\n"
        f"LẬP LUẬN ĐÃ VIẾT TRƯỚC KHI CHỐT ĐÁP ÁN: {question.reasoning}\n\n"
        f"EXPLANATION ĐÃ SINH:\n"
        f"reasoning: {explanation_out.reasoning}\n"
        f"explanation_text: {explanation_out.explanation_text}\n\n"
        f"{format_source_chunks(context_chunks)}\n\n"
        f"Kiểm tra reasoning_first_pass, all_distractors_addressed_pass, grounded_pass của Explanation trên."
    )
    return complete_structured(
        prompt, ExplanationVerification, system_instruction=EXPLANATION_VERIFY_SYSTEM_INSTRUCTION, temperature=0.0
    )


def generate_explanation(
    question: QuizQuestion, context_chunks: list[Chunk]
) -> tuple[ExplanationOutput, ExplanationVerification]:
    feedback: str | None = None
    explanation_out: ExplanationOutput | None = None
    check: ExplanationVerification | None = None
    for _ in range(MAX_EXPLANATION_CORRECTION_RETRY + 1):
        prompt = (
            f"CÂU HỎI: {question.stem}\n"
            f"CÁC PHƯƠNG ÁN: {question.choices}\n"
            f"ĐÁP ÁN ĐÚNG: {question.choices[question.correct_index]}\n"
            f"CÁC QUAN NIỆM SAI ĐÃ PHÂN TÍCH (tương ứng thứ tự với 3 phương án nhiễu): {question.common_misconceptions}\n"
            f"LẬP LUẬN ĐÃ VIẾT TRƯỚC KHI CHỐT ĐÁP ÁN (triển khai từ đây, không đảo chiều): {question.reasoning}\n\n"
            f"{format_source_chunks(context_chunks)}\n\n"
            f"Viết Explanation cho câu hỏi trên."
        )
        if feedback:
            prompt += (
                f"\n\nLƯU Ý: lần sinh trước KHÔNG ĐẠT vì lý do sau -- sửa đúng lỗi này, "
                f"không lặp lại: {feedback}"
            )
        # temperature=0.0: nhat quan voi Planner/LO/Content/Slides -- day la
        # tac vu ly luan co can cu tu nguon, khong phai sang tac tu do.
        explanation_out = complete_structured(
            prompt, ExplanationOutput, system_instruction=EXPLANATION_SYSTEM_INSTRUCTION, temperature=0.0
        )

        check = _verify_explanation(explanation_out, question, context_chunks)
        if check.reasoning_first_pass and check.all_distractors_addressed_pass and check.grounded_pass:
            return explanation_out, check

        reasons = []
        if not check.reasoning_first_pass:
            reasons.append(f"Reasoning-first fail: {check.reasoning_first_reason}")
        if not check.all_distractors_addressed_pass:
            reasons.append(f"Distractors fail: {check.all_distractors_addressed_reason}")
        if not check.grounded_pass:
            reasons.append(f"Grounded fail: {check.grounded_reason}")
        feedback = "; ".join(reasons)

    return explanation_out, check  # best-effort sau khi hết retry budget, giống pattern LO/Content/Slides
