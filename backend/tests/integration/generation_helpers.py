"""Deterministic outputs shared by generation self-check and language tests."""
from app.generation.content import ContentOutput
from app.generation.example import Example, ExampleOutput
from app.generation.objective import LearningObjective
from app.generation.planner import Concept, PlannerOutput
from app.generation.quiz_graph import QuizQuestion
from app.generation.relevance import RelevanceCheck
from app.generation.slides import Slide, SlidesOutput

SUB_OBJECTIVES = ["Nêu định nghĩa", "Phân biệt hai trường hợp", "Giải thích vai trò"]

OUTPUTS = {
    PlannerOutput: PlannerOutput(concepts=[Concept(name="Khái niệm", priority=1, summary="tóm tắt")]),
    LearningObjective: LearningObjective(
        bloom_level="understand", verb="giải thích", objective_text="Giải thích khái niệm",
        sub_objectives=SUB_OBJECTIVES,
    ),
    RelevanceCheck: RelevanceCheck(confidence="high", reason="đủ"),
    ContentOutput: ContentOutput(content="## Phần một\n- ý"),
    SlidesOutput: SlidesOutput(slides=[Slide(title="S1", bullets=["b"]), Slide(title="S2", bullets=["b"])]),
    ExampleOutput: ExampleOutput(examples=[Example(context="bối cảnh", analysis="phân tích")] * 2),
    # Các phương án dài bằng nhau: không vướng bẫy cú pháp, không phải cân bằng lại độ dài đáp án.
    QuizQuestion: QuizQuestion(
        common_misconceptions=["m1", "m2", "m3"], stem="Hỏi", reasoning="r",
        choices=["pa một", "pa hai", "pa ba", "pa bốn"], correct_index=1, explanation="lời giải",
    ),
}


def passing_check(response_model, correct_index=1):
    values = {name: True if name.endswith("_pass") else "đạt"
              for name in response_model.model_fields}
    if "option_checks" in values:
        values["option_checks"] = [
            {"is_correct": i == correct_index, "distinct_pass": True, "plausible_pass": True,
             "reason": "đạt"} for i in range(4)
        ]
    return response_model(**values)
