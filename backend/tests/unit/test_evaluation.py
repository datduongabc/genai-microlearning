"""Gói nội dung đem chấm và prompt của LLM chấm: cùng một cấu trúc cho cả ba nhóm nghiên cứu, và
mô hình chấm phải thấy đủ mọi phần của module thì mới chấm được đủ chín tiêu chí."""

import pytest
from pydantic import ValidationError

from app.generation.evaluation import (
    ExampleInput,
    ModuleContentInput,
    QuizQuestionInput,
    SlideInput,
    _build_prompt,
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


def test_the_prompt_names_the_topic_and_the_anonymous_id_only():
    prompt = _build_prompt(_packet())

    assert "Chủ đề gốc của bài" in prompt
    assert "anon_id=M01" in prompt
    assert 'phai dung bang "M01"' in prompt


def test_every_example_and_every_sub_objective_reaches_the_judge():
    prompt = _build_prompt(_packet())

    for text in ("BOI-CANH-1", "PHAN-TICH-1", "BOI-CANH-2", "PHAN-TICH-2", "MUC-TIEU-CON-1", "MUC-TIEU-CON-2"):
        assert text in prompt


def test_each_quiz_question_is_shown_with_its_own_explanation():
    prompt = _build_prompt(_packet())

    first, second = prompt.index("CAU-HOI-1"), prompt.index("CAU-HOI-2")
    assert first < prompt.index("GIAI-THICH-1") < second < prompt.index("GIAI-THICH-2")
    assert "CAU-LUYEN-TAP" in prompt and "GIAI-THICH-LUYEN-TAP" in prompt


def test_the_correct_answer_is_spelled_out_for_every_question():
    prompt = _build_prompt(_packet())

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
