from types import SimpleNamespace as NS

from app.api import modules
from app.generation.study_packets import module_packet
from app.models import ComponentType


def question(stem="AI question", explanation="AI explanation"):
    return NS(id="question", stem=stem, choices={"options": ["A", "B"]}, answer_key="0",
              explanation=explanation, position=0)


def lesson():
    components = [NS(type=kind, content=content, questions=questions) for kind, content, questions in [
        (ComponentType.content, {"markdown": "AI text"}, []),
        (ComponentType.slides, {"slides": [{"title": "AI", "bullets": ["AI bullet"]}]}, []),
        (ComponentType.example, {"examples": [{"context": "AI context", "analysis": "AI analysis"}]}, []),
        (ComponentType.quiz, {"questions": []}, [question()]),
        (ComponentType.practice, {"questions": []}, [question()]),
        (ComponentType.explanation, {"items": []}, []),
    ]]
    return NS(title="Topic", bloom_level="apply", learning_objective="Objective",
              sub_objectives=[], components=components)


def test_first_manual_edit_saves_original_and_later_edit_cannot_replace_it(monkeypatch):
    module = lesson()
    component = module.components[0]
    component.module_id = "module"
    component.id = "component"
    db = NS(get=lambda *a, **kw: component, commit=lambda: None)
    monkeypatch.setattr(modules, "_owned_module", lambda *a: module)
    for text in ["Teacher first", "Teacher second"]:
        modules.update_component("module", "component", modules.ComponentUpdate(
            content={"markdown": text, "ai_original": {"markdown": "Forged"}}), db, NS())
        assert component.content["ai_original"] == {"markdown": "AI text"}
        assert component.content["markdown"] == text


def test_packet_uses_original_components_and_question_snapshot():
    module = lesson()
    expected = module_packet(module, ["Reference"])
    for component in module.components[:3]:
        component.content = {"ai_original": component.content}
    for component in module.components[3:5]:
        q = component.questions[0]
        component.content = {"ai_original": {"content": component.content, "questions": [vars(q).copy()]}}
        q.stem = "Teacher question"
        q.explanation = "Teacher explanation"
    module.components[-1].content = {"items": [{"component": "quiz", "position": 0,
                                              "explanation_text": "Teacher explanation"}]}
    assert module_packet(module, ["Reference"]) == expected


def test_question_edit_after_manual_quiz_edit_keeps_original_rows(monkeypatch):
    module = lesson()
    component = module.components[3]
    component.module_id = "module"
    component.id = "quiz"
    db = NS(get=lambda *a, **kw: component, commit=lambda: None)
    monkeypatch.setattr(modules, "_owned_module", lambda *a: module)
    modules.update_component("module", "quiz", modules.ComponentUpdate(content={"questions": []}), db, NS())
    original = component.content["ai_original"]
    modules._apply_question_update(module, component, component.questions[0], modules.QuestionUpdate(
        stem="Teacher question", choices=["New A", "New B"], answer_key=1,
        explanation="Teacher explanation"))
    assert module_packet(module, []).quiz[0].stem == "AI question"
    assert module_packet(module, []).quiz[0].explanation == "AI explanation"
    assert component.content["ai_original"] == original


def test_original_explanation_component_is_used_for_unedited_questions():
    module = lesson()
    explanation = module.components[-1]
    explanation.content = {"items": [{"component": "quiz", "position": 0,
                                      "explanation_text": "Teacher explanation"}],
                           "ai_original": {"items": [{"component": "quiz", "position": 0,
                                                       "explanation_text": "AI override"}]}}
    assert module_packet(module, []).quiz[0].explanation == "AI override"


def test_legacy_quiz_edit_then_other_question_edit_keeps_ai_explanation():
    module = lesson()
    quiz, practice, explanation = module.components[3], module.components[4], module.components[-1]
    quiz_question = quiz.questions[0]
    quiz.content = {**quiz.content, "ai_original": {"content": quiz.content, "questions": [vars(quiz_question).copy()]}}
    quiz_question.explanation = "Teacher quiz explanation"
    explanation.content = {"items": [{"component": "quiz", "position": 0,
                                      "explanation_text": "Teacher quiz explanation"}]}
    modules._apply_question_update(module, practice, practice.questions[0], modules.QuestionUpdate(
        stem="Teacher practice", choices=["New A", "New B"], answer_key=1,
        explanation="Teacher practice explanation"))
    packet = module_packet(module, [])
    assert packet.quiz[0].explanation == "AI explanation"
    assert packet.practice.explanation == "AI explanation"
    assert packet.practice.stem == "AI question"
