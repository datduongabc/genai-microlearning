"""Gói nội dung đem chấm và prompt của LLM chấm: cùng một cấu trúc cho cả ba nhóm nghiên cứu, và
mô hình chấm phải thấy đủ mọi phần của module thì mới chấm được đủ chín tiêu chí."""

import pytest
from pydantic import ValidationError

from app.generation.evaluation import (
    _SYSTEM_INSTRUCTION,
    ITEM_WRITING_FLAWS,
    ExampleInput,
    ModuleContentInput,
    ModuleEvaluation,
    QuestionFlaws,
    QuizQuestionInput,
    SlideInput,
    _build_prompt,
    question_context,
    quiz_quality_score,
)


def _question(stem: str, correct: int, explanation: str) -> QuizQuestionInput:
    return QuizQuestionInput(stem=stem, choices=["P", "Q", "R", "S"], correct_index=correct, explanation=explanation)


def _packet(**overrides) -> ModuleContentInput:
    fields = {
        "anon_id": "M01",
        "topic": "Chủ đề gốc của bài",
        "bloom_level": "understand",
        "learning_objective": "Sau khi hoàn thành module, người học có thể giải thích khái niệm.",
        "sub_objectives": ["MUC-TIEU-CON-1", "MUC-TIEU-CON-2"],
        "content_markdown": "## Phần 1\n\nNội dung.",
        "slides": [SlideInput(title="Slide 1", bullets=["ý chính"])],
        "examples": [
            ExampleInput(context="BOI-CANH-1", analysis="PHAN-TICH-1"),
            ExampleInput(context="BOI-CANH-2", analysis="PHAN-TICH-2"),
        ],
        "quiz": [_question("CAU-HOI-1", 1, "GIAI-THICH-1"), _question("CAU-HOI-2", 2, "GIAI-THICH-2")],
        "practice": _question("CAU-LUYEN-TAP", 0, "GIAI-THICH-LUYEN-TAP"),
        "source_chunks": ["Đoạn nguồn gốc."],
    }
    return ModuleContentInput(**{**fields, **overrides})


def test_the_prompt_includes_topic_anon_id_examples_sub_objectives_and_answers():
    prompt = _build_prompt(_packet())

    assert "Chủ đề gốc của bài" in prompt
    assert "anon_id=M01" in prompt
    assert 'phải đúng bằng "M01"' in prompt
    for text in (
        "BOI-CANH-1",
        "PHAN-TICH-1",
        "BOI-CANH-2",
        "PHAN-TICH-2",
        "MUC-TIEU-CON-1",
        "MUC-TIEU-CON-2",
    ):
        assert text in prompt
    first, second = prompt.index("CAU-HOI-1"), prompt.index("CAU-HOI-2")
    assert first < prompt.index("GIAI-THICH-1") < second < prompt.index("GIAI-THICH-2")
    assert "CAU-LUYEN-TAP" in prompt and "GIAI-THICH-LUYEN-TAP" in prompt
    assert "Correct: Q" in prompt and "Correct: R" in prompt and "Correct: P" in prompt


def test_a_packet_without_any_quiz_question_is_rejected():
    with pytest.raises(ValidationError):
        _packet(quiz=[])



@pytest.mark.parametrize("field", ["quiz", "practice"])
@pytest.mark.parametrize("choices,index", [([], 0), (["X", "Y"], -1), (["X", "Y"], 2)])
def test_packet_rejects_invalid_question_answers(field, choices, index):
    packet = _packet().model_dump()
    question = {"stem": "Q", "choices": choices, "correct_index": index}
    packet[field] = [packet["quiz"][0], question] if field == "quiz" else question
    with pytest.raises(ValidationError):
        ModuleContentInput.model_validate(packet)


@pytest.mark.parametrize("field", ["quiz", "practice"])
@pytest.mark.parametrize("index", [0, 1])
def test_packet_accepts_answers_at_both_choice_boundaries(field, index):
    packet = _packet().model_dump()
    question = {"stem": "Q", "choices": ["X", "Y"], "correct_index": index}
    packet[field] = [question] if field == "quiz" else question
    validated = ModuleContentInput.model_validate(packet)
    answer = validated.quiz[0] if field == "quiz" else validated.practice
    assert answer.choices[answer.correct_index] in ("X", "Y")


def test_quiz_quality_is_the_share_of_questions_with_at_most_one_flaw():
    """Six questions, n of them with two flaws: 1 + round(4 x (6 - n) / 6), the values docs/rubric.md lists."""
    def flawed(n):
        return [QuestionFlaws(question=i + 1, flaws=["vague_terms", "lost_sequence"] if i < n else ["vague_terms"])
                for i in range(6)]
    assert [quiz_quality_score(flawed(n)) for n in range(7)] == [5, 4, 4, 3, 2, 2, 1]
    # The same flaw marked twice is one flaw.
    assert quiz_quality_score([QuestionFlaws(question=1, flaws=["vague_terms", "vague_terms"])]) == 5


def _evaluation(flaws: list[dict]) -> dict:
    score = {"score": 1, "justification": "x"}
    return {"anon_id": "M01", **{c: score for c in (
        "accuracy", "completeness", "consistency", "hallucination_score", "slide_quality", "quiz_quality",
        "bloom_fidelity", "query_alignment", "examples_feedback")}, "quiz_flaws": flaws, "overall_notes": ""}


def test_the_judge_must_check_every_question_of_the_packet_once():
    context = question_context(_packet())  # two quiz questions and the practice question
    every = [{"question": n} for n in (1, 2, 3)]
    evaluation = ModuleEvaluation.model_validate(_evaluation(every), context=context)
    assert evaluation.quiz_quality.score == 5 and evaluation.accuracy.score == 1
    for flaws in (every[:2], [*every, {"question": 4}], [every[1], every[0], every[2]]):
        with pytest.raises(ValidationError):
            ModuleEvaluation.model_validate(_evaluation(flaws), context=context)


def test_the_judge_is_told_every_flaw_and_how_many_questions_to_check():
    assert all(f"- {key}:" in _SYSTEM_INSTRUCTION for key in ITEM_WRITING_FLAWS)
    assert len(ITEM_WRITING_FLAWS) == 19
    assert "quiz_flaws có đúng 3 mục: câu 1..2 là quiz, câu 3 là practice" in _build_prompt(_packet())


def test_the_rating_form_and_judges_share_the_same_flaws_and_wording():
    """The form lists the flaws in Vietnamese on its own; keys and wording must match the server."""
    import re
    from pathlib import Path

    form = (Path(__file__).resolve().parents[3] / "frontend/src/lib/ratingSchema.ts").read_text(encoding="utf-8")
    block = form.split("export const ITEM_WRITING_FLAWS")[1].split("];")[0]
    assert re.findall(r'\["(\w+)",', block) == list(ITEM_WRITING_FLAWS)
    raw_pairs = re.findall(r'\["(\w+)", "((?:[^"\\]|\\.)*)"\]', block)
    pairs = [(key, text.replace('\\"', '"')) for key, text in raw_pairs]
    assert list(pairs) == list(ITEM_WRITING_FLAWS.items())


def test_the_judge_prompt_is_written_with_vietnamese_diacritics():
    assert "Bạn là chuyên gia đánh giá" in _SYSTEM_INSTRUCTION and "Ban la chuyen gia" not in _SYSTEM_INSTRUCTION
