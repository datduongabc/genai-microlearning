"""The selected language is an instruction delivered to every writing step, not inferred."""

import pytest

from app.db import SessionLocal
from app.generation import content, example, objective, planner, quiz_graph, slides
from app.generation import module_generation_pipeline as pipeline
from app.generation.module_generation_pipeline import generation_payload
from app.models import Document
from tests.test_self_check_flag import OUTPUTS, document  # noqa: F401


@pytest.mark.parametrize(
    "language,instruction",
    [
        (
            "vi",
            "Write ALL output in Vietnamese with full diacritics; keep technical terms in English only where natural, e.g. in parentheses.",
        ),
        ("en", "Write ALL output in English."),
    ],
)
@pytest.mark.parametrize("sourced", [True, False])
def test_every_writing_step_receives_language(monkeypatch, language, instruction, sourced):
    seen = []

    def llm(prompt, response_model, system_instruction=None, **kwargs):
        seen.append((response_model, system_instruction))
        if response_model is content.LessonText:
            return content.LessonText(content=OUTPUTS[content.ContentOutput].content)
        return OUTPUTS[response_model]

    for step in (planner, objective, content, slides, example, quiz_graph):
        monkeypatch.setattr(step, "complete_structured", llm)
    monkeypatch.setattr(quiz_graph, "retrieve", lambda *args, **kwargs: [])
    concepts = planner.run_planner("English source", output_language=language)
    lo = objective.generate_learning_objective(concepts.concepts[0], [], output_language=language)
    lesson, _ = content.generate_content(lo, [] if sourced else None, self_check=False, output_language=language)
    slides.generate_slides(lo, lesson.content, output_language=language)
    example.generate_example(lo, lesson.content, [] if sourced else None, output_language=language)
    for mode in ("quiz", "practice"):
        quiz_graph.generate_quiz_question(
            None, None, "target", lo, mode, self_check=False, output_language=language,
            lesson=None if sourced else lesson.content
        )
    assert len(seen) == 7
    assert all(instruction in prompt for _, prompt in seen)
    assert all("quoted source evidence must stay verbatim in its original language" in prompt for _, prompt in seen)
    assert generation_payload(None, output_language=language)["output_language"] == language



def test_language_reaches_steps_through_the_pipeline(request, monkeypatch):
    ids = request.getfixturevalue("document")
    seen = []
    def llm(prompt, response_model, system_instruction=None, **kwargs):
        if response_model in OUTPUTS:
            seen.append((response_model.__name__, system_instruction))
            return OUTPUTS[response_model]
        return response_model(**{name: True if name.endswith("_pass") else "ok" for name in response_model.model_fields})
    from app.generation import relevance
    for step in (planner, objective, content, slides, example, quiz_graph, relevance):
        monkeypatch.setattr(step, "complete_structured", llm)
    with SessionLocal() as db:
        pipeline.run_module_generation(db, db.get(Document, ids["doc"]), self_check=False, output_language="en")
    writing = [(name, prompt) for name, prompt in seen if name != "RelevanceCheck"]
    assert len(writing) == 9  # planner, LO, content, slides, example, three quiz + practice
    assert all("Write ALL output in English." in prompt for _, prompt in writing)


@pytest.mark.parametrize("locator,label", [
    ('{"spans":[{"page":9,"heading":null},{"page":10,"heading":"Search"}]}', 'source.pdf · trang 9–10 · Search'),
    ('{"spans":[{"page":10},{"page":9},{"page":9},{"page":12}]}', 'source.pdf · trang 9–10, 12'),
    ('{"heading":"Search","extraction_method":"text_layer"}', 'source.pdf · Search'),
    ('{"page":4}', 'source.pdf · trang 4'),
    ('{}', 'source.pdf'),
    ('{bad json', 'source.pdf'),
    ('[]', 'source.pdf'),
    ('old readable locator', 'old readable locator'),
    ('{"spans":4,"heading":9}', 'source.pdf'),
    ('{"slide_index":3,"slide_title":"A* search"}', 'source.pdf · slide 3 · A* search'),
    ('{"spans":[{"slide_index":3,"slide_title":""},{"slide_index":4,"slide_title":"BFS"}]}', 'source.pdf · slide 3–4 · BFS'),
    ('{"table_index":0,"row_index":2}', 'source.pdf · bảng 1 · hàng 3'),
    ('{"spans":[{"start_ms":65000,"end_ms":70000},{"start_ms":70000,"end_ms":125500}]}', 'source.pdf · 01:05–02:05'),
    ('{"paragraph_index":0}', 'source.pdf · đoạn 1'),
    ('{"heading":"Intro","heading_level":1}', 'source.pdf · Intro'),
])
def test_citation_label(locator, label):
    from app.api.modules import citation_label
    assert citation_label("source.pdf", locator) == label



def test_content_retry_keeps_language_and_leaves_judge_prompt_unchanged(monkeypatch):
    writing, judges = [], []
    def llm(prompt, response_model, system_instruction=None, **kwargs):
        if response_model is content.ContentOutput:
            writing.append(system_instruction)
            return OUTPUTS[response_model]
        judges.append(system_instruction)
        return content.ContentVerification(groundedness_pass=True, groundedness_reason="ok",
            completeness_pass=len(judges)>1, completeness_reason="retry", scope_pass=True, scope_reason="ok")
    monkeypatch.setattr(content, "complete_structured", llm)
    content.generate_content(OUTPUTS[objective.LearningObjective], [], output_language="en")
    assert len(writing) == 2
    assert all("Write ALL output in English." in prompt for prompt in writing)
    assert all("Write ALL output in English." not in prompt for prompt in judges)
