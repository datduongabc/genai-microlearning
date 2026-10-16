"""Inline markers are the citation inventory; evidence remains a physical chunk."""
import types
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.generation.content import ContentOutput, resolve_citations
from app.generation.study_packets import resolved_citations
from app.models import Chunk, ComponentType, Module, ModuleComponent
from app.retrieval.search import expand_with_parent_context


def test_expansion_preserves_each_physical_chunk_through_prompt_and_resolution():
    engine = create_engine("sqlite://")
    Chunk.__table__.create(engine)
    with Session(engine) as db:
        doc, course = uuid.uuid4(), uuid.uuid4()
        chunks = [Chunk(document_id=doc, course_id=course, chunk_index=i, content=text,
                        source_ref=f"p{i+1}") for i, text in enumerate([
                            "Introductory definitions.", "Insertion takes logarithmic time.", "Next topic."])]
        db.add_all(chunks)
        db.flush()
        expanded = expand_with_parent_context(db, [chunks[0]], window=1)
        assert [(c.id, c.content) for c in expanded] == [
            (chunks[0].id, "Introductory definitions."), (chunks[1].id, "Insertion takes logarithmic time.")]
        stored = resolve_citations(ContentOutput(content="Insertion takes logarithmic time. [S2]"), expanded)
        component = ModuleComponent(type=ComponentType.content, content={
            **stored.model_dump(), "markdown": stored.content})
        module = Module(document_id=doc, course_id=course, components=[component])
        [citation] = resolved_citations(db, module)
        assert citation.chunk_id == str(chunks[1].id)
        assert citation.chunk_content == "Insertion takes logarithmic time."
        assert citation.claim == "Insertion takes logarithmic time."


def test_inline_markers_allow_multiple_sources_and_rederive_after_text_changes():
    chunks = [types.SimpleNamespace(id=uuid.uuid4(), content=text, source_ref="same-page")
              for text in ["one", "two"]]
    stored = resolve_citations(ContentOutput(content="## Title\n- A combined fact. [S2][S1]\n- Uncited fact."), chunks)
    assert [c.source_id for c in stored.inline_citations] == ["S2", "S1"]
    assert [c.claim for c in stored.inline_citations] == ["A combined fact.", "A combined fact."]
    assert stored.inline_citations[0].start == stored.inline_citations[1].start
    assert stored.source_map["S2"].chunk_id == str(chunks[1].id)
    component = types.SimpleNamespace(type=ComponentType.content, content={
        **stored.model_dump(mode="json"), "markdown": "- Rewritten fact. [S1]"})
    class Rows:
        def query(self, _): return self
        def filter(self, *_): return self
        def all(self): return chunks
    module = types.SimpleNamespace(document_id=uuid.uuid4(), course_id=uuid.uuid4(), components=[component])
    [citation] = resolved_citations(Rows(), module)
    assert citation.claim == "Rewritten fact." and citation.chunk_id == str(chunks[0].id)
    component.content["markdown"] = "Uncited replacement."
    assert resolved_citations(Rows(), module) == []


def test_resolver_rejects_codes_outside_the_generation_prompt():
    chunks = [types.SimpleNamespace(id=uuid.uuid4(), content="one", source_ref="p1")]
    with pytest.raises(ValueError, match="S2"):
        resolve_citations(ContentOutput(content="Fact. [S2]"), chunks)


def test_inline_citation_spans_exclude_previous_uncited_sentences():
    chunks = [types.SimpleNamespace(id=uuid.uuid4(), content="facts", source_ref="p1")]
    stored = resolve_citations(ContentOutput(content="Uncited first. Cited second. [S1] Third fact [S1]"), chunks)
    assert [c.claim for c in stored.inline_citations] == ["Cited second.", "Third fact"]
    for citation in stored.inline_citations:
        assert stored.content[citation.start:citation.end] == citation.claim
        assert stored.content[citation.marker_start:citation.marker_end] == "[S1]"


def test_blinded_packet_hides_codes_and_keeps_real_content_offsets():
    from app.generation.study_packets import module_packet
    from app.models import Question
    component = ModuleComponent(type=ComponentType.content, content={"markdown": "Fact. [S1][S2]\nUncited."})
    quiz = ModuleComponent(type=ComponentType.quiz, content={}, questions=[Question(position=0, stem="Q?",
        choices={"options": ["A", "B", "C", "D"]}, answer_key="0", explanation="E")])
    practice = ModuleComponent(type=ComponentType.practice, content={}, questions=[Question(position=0, stem="P?",
        choices={"options": ["A", "B", "C", "D"]}, answer_key="0", explanation="E")])
    module = Module(title="Topic", bloom_level="understand", learning_objective="Explain", sub_objectives=[],
                    components=[component, quiz, practice])
    packet = module_packet(module, ["Fact."])
    assert packet.content_markdown == "Fact.         \nUncited."
    assert len(packet.content_markdown) == len(component.content['markdown'])


@pytest.mark.parametrize('marker', ['[s1]', '[ S1 ]', '[S1,S99]', '[S1 S2]'])
def test_malformed_source_markers_are_rejected_instead_of_becoming_uncited_text(marker):
    chunks = [types.SimpleNamespace(id=uuid.uuid4(), content='fact', source_ref='p1')]
    with pytest.raises(ValueError, match='source'):
        resolve_citations(ContentOutput(content='Fact. '+marker), chunks)


def test_study_packet_and_citations_use_original_while_preview_uses_saved_edits():
    from app.generation.study_packets import module_packet
    from app.models import Question

    engine = create_engine("sqlite://")
    Chunk.__table__.create(engine)
    with Session(engine) as db:
        doc, course = uuid.uuid4(), uuid.uuid4()
        chunk = Chunk(document_id=doc, course_id=course, chunk_index=0,
                      content="Original fact.", source_ref="p1")
        db.add(chunk)
        db.flush()
        original = {"markdown": "Original fact. [S1]", "source_map": {
            "S1": {"chunk_id": str(chunk.id), "locator": "p1"}}}
        content = ModuleComponent(type=ComponentType.content, content={
            "markdown": "Teacher replacement.", "source_map": original["source_map"],
            "ai_original": original})
        questions = [ModuleComponent(type=kind, content={}, questions=[Question(position=0, stem="Q?",
            choices={"options": ["A", "B"]}, answer_key="0", explanation="E")])
            for kind in [ComponentType.quiz, ComponentType.practice]]
        module = Module(document_id=doc, course_id=course, title="Topic", bloom_level="understand",
                        learning_objective="Explain", sub_objectives=[], components=[content, *questions])
        packet = module_packet(module, [chunk.content])
        [citation] = resolved_citations(db, module, use_ai_original=True)
        assert citation.claim == "Original fact."
        assert packet.content_markdown[citation.start:citation.end] == citation.claim
        assert citation.chunk_content == "Original fact."
        assert resolved_citations(db, module) == []
