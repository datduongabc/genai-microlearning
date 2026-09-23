from app.generation.evaluation import (
    ModuleContentInput,
    QuizQuestionInput,
    SlideInput,
    _build_prompt,
)


def _sample_packet() -> ModuleContentInput:
    return ModuleContentInput(
        anon_id="M01",
        topic="Encapsulation trong OOP",
        bloom_level="understand",
        learning_objective="Sau khi hoàn thành module, người học có thể mô tả Encapsulation.",
        content_markdown="## Encapsulation\n\nBundles data and methods.",
        slides=[SlideInput(title="Encapsulation", bullets=["data hiding", "access control"])],
        example_context="Lớp BankAccount có thuộc tính balance private.",
        example_analysis="Chỉ getBalance() được phép đọc balance.",
        quiz=QuizQuestionInput(
            stem="Encapsulation là gì?", choices=["A", "B", "C"], correct_index=1, explanation="B đúng vì..."
        ),
        practice=QuizQuestionInput(
            stem="Chọn ví dụ đúng về Encapsulation", choices=["X", "Y"], correct_index=0, explanation="X đúng vì..."
        ),
        explanation="B là đáp án đúng vì bundling data+methods là Encapsulation.",
        source_chunks=["Encapsulation: bundling data and behavior into a class."],
    )


def test_build_prompt_includes_topic_and_anon_id():
    prompt = _build_prompt(_sample_packet())
    assert "Encapsulation trong OOP" in prompt
    assert "anon_id=M01" in prompt
    assert 'phai dung bang "M01"' in prompt


def test_build_prompt_resolves_correct_choice_text():
    prompt = _build_prompt(_sample_packet())
    assert "Correct: B" in prompt
    assert "Correct: X" in prompt


def test_build_prompt_flags_invalid_correct_index():
    packet = _sample_packet()
    packet.quiz.correct_index = 99
    prompt = _build_prompt(packet)
    assert "(invalid correct_index)" in prompt
