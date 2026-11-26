"""Source citations by ID: the model cites the short ID a chunk carries in the prompt, the server maps
it back to the chunk, and a stored citation points at exactly one passage."""

import json
import types
import uuid

import pytest
from jsonschema import Draft202012Validator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.generation import content
from app.generation.common import format_source_chunks, resolve_source_id
from app.generation.content import ContentOutput, resolve_citations
from app.generation.inline_citations import (
    citation_content,
    derive_citations,
    prompt_source_map,
    unknown_source_codes,
)
from app.generation.objective import LearningObjective
from app.generation.study_packets import module_packet, resolved_citations
from app.llm import gateway
from app.models import Chunk, ComponentType, Module, ModuleComponent, Question
from app.retrieval.search import expand_with_parent_context


def _chunk(content_text="text", source_ref='{"heading": "Sorting"}'):
    return types.SimpleNamespace(id=uuid.uuid4(), content=content_text, source_ref=source_ref)


class Rows:
    """The rows a module's citations are resolved against."""

    def __init__(self, chunks):
        self.chunks = chunks

    def query(self, _model):
        return self

    def filter(self, *_):
        return self

    def all(self):
        return self.chunks


def module_with(saved):
    comp = types.SimpleNamespace(type=ComponentType.content, content={"claim_citations": saved})
    return types.SimpleNamespace(components=[comp], document_id=1, course_id=1)


def test_every_chunk_carries_its_own_id_and_no_locator_in_the_prompt():
    chunks = [_chunk("one"), _chunk("two")]

    prompt = format_source_chunks(chunks)

    assert "SOURCE CHUNK [S1]:" in prompt and "SOURCE CHUNK [S2]:" in prompt
    assert "Sorting" not in prompt and "locator" not in prompt


def test_an_id_maps_to_its_chunk_and_an_invented_one_to_nothing():
    chunks = [_chunk(), _chunk()]

    assert resolve_source_id(chunks, "S2") is chunks[1]
    assert resolve_source_id(chunks, " [s1] ") is chunks[0]
    for invented in ("S0", "S3", "S", "1", "", "chunk-1", '{"page": 1}', "S1 and S2"):
        assert resolve_source_id(chunks, invented) is None


def test_chunks_that_share_a_locator_are_still_cited_exactly():
    """Every chunk under one heading shares its locator, which is what made them ambiguous."""
    chunks = [_chunk("a"), _chunk("b")]
    out = ContentOutput(content="b [S2]")

    stored = resolve_citations(out, chunks)

    assert stored.inline_citations[0].chunk_id == str(chunks[1].id)
    assert stored.source_locators == ['{"heading": "Sorting"}']


def test_an_id_the_model_invented_is_rejected():
    with pytest.raises(ValueError, match="S5"):
        resolve_citations(ContentOutput(content="c [S5]"), [_chunk()])


LO = LearningObjective(
    bloom_level="understand", verb="Explain", objective_text="Explain heap insertion",
    sub_objectives=["Describe reheapUp", "Explain insertion complexity"],
)


def test_unknown_ids_trigger_regeneration_even_when_the_critic_passes(monkeypatch):
    chunks = [_chunk("reheapUp takes O(log n)")]
    bad = ContentOutput(content="reheapUp takes O(log n). [S33]")
    good = ContentOutput(content="reheapUp takes O(log n). [S1]")
    generations = []

    def complete(prompt, response_model, **kwargs):
        if response_model is ContentOutput:
            generations.append(prompt)
            return bad if len(generations) == 1 else good
        return content.ContentVerification(
            groundedness_pass=True, groundedness_reason="supported",
            completeness_pass=True, completeness_reason="complete",
            scope_pass=True, scope_reason="in scope",
        )

    monkeypatch.setattr(content, "complete_structured", complete)

    stored, check = content.generate_content(LO, chunks)

    assert stored.inline_citations[0].chunk_id == str(chunks[0].id)
    assert stored.source_locators == [chunks[0].source_ref]
    assert len(generations) == 2
    assert "S33" in generations[1] and "S1" in generations[1]
    assert check.groundedness_pass


@pytest.mark.parametrize("self_check", [True, False])
@pytest.mark.parametrize("bad", ["heap claim. [S1, S2]", "heap claim.\n[S1]"])
def test_malformed_markers_are_sent_back_through_the_correction_retry(monkeypatch, self_check, bad):
    chunks = [_chunk("heap claim")]
    generations = []

    def complete(prompt, response_model, **kwargs):
        if response_model is ContentOutput:
            generations.append(prompt)
            return ContentOutput(content=bad if len(generations) == 1 else "heap claim. [S1]")
        return content.ContentVerification(
            groundedness_pass=True, groundedness_reason="supported",
            completeness_pass=True, completeness_reason="complete",
            scope_pass=True, scope_reason="in scope",
        )

    monkeypatch.setattr(content, "complete_structured", complete)

    stored, check = content.generate_content(LO, chunks, self_check=self_check)

    assert len(generations) == 2 and "[S1]" in generations[1].split("LƯU Ý")[1]
    assert stored.inline_citations[0].chunk_id == str(chunks[0].id)
    assert (check is not None) == self_check


@pytest.mark.parametrize("self_check", [True, False])
def test_content_with_unknown_ids_is_never_returned_after_the_retry_budget(monkeypatch, self_check):
    chunks = [_chunk()]
    generations = []

    def complete(prompt, response_model, **kwargs):
        if response_model is ContentOutput:
            generations.append(prompt)
            return ContentOutput(content="heap claim. [S33]")
        return content.ContentVerification(
            groundedness_pass=True, groundedness_reason="supported",
            completeness_pass=True, completeness_reason="complete",
            scope_pass=True, scope_reason="in scope",
        )

    monkeypatch.setattr(content, "complete_structured", complete)

    with pytest.raises(ValueError, match="S33"):
        content.generate_content(LO, chunks, self_check=self_check)
    assert len(generations) == 2


@pytest.mark.parametrize("self_check", [True, False])
def test_no_rag_content_keeps_bracketed_text_but_retries_real_markers(monkeypatch, self_check):
    outputs = iter(["Use an [S3 bucket] for storage. [S3]", "Use an [S3 bucket] for storage."])
    generations = []

    def complete(prompt, response_model, **kwargs):
        if response_model is content.LessonText:
            generations.append(prompt)
            return content.LessonText(content=next(outputs))
        return content.LessonVerification(
            completeness_pass=True, completeness_reason="complete", scope_pass=True, scope_reason="in scope",
        )

    monkeypatch.setattr(content, "complete_structured", complete)

    out, _ = content.generate_content(LO, None, self_check=self_check)

    assert out.content == "Use an [S3 bucket] for storage."
    assert len(generations) == 2 and "citations" in generations[1]


@pytest.mark.parametrize("count", [0, 1, 2])
def test_the_gateway_schema_accepts_only_content_and_the_server_maps_inline_codes(monkeypatch, count):
    chunks = [_chunk() for _ in range(count)]
    requests = []
    output = {"content": f"heap claim. [S{count}]" if count else "heap claim"}

    def completion(**kwargs):
        requests.append(kwargs)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(
            message=types.SimpleNamespace(content=json.dumps(output)),
        )])

    monkeypatch.setattr(gateway.litellm, "completion", completion)

    stored, _ = content.generate_content(LO, chunks, self_check=False)

    response_format = requests[0]["response_format"]
    assert isinstance(response_format, dict)
    validator = Draft202012Validator(response_format["json_schema"]["schema"])
    assert validator.is_valid(output)
    assert not validator.is_valid({**output, "source_ids": ["S1"]})
    assert set(response_format["json_schema"]["schema"]["properties"]) == {"content"}
    assert [c.chunk_id for c in stored.inline_citations] == ([str(chunks[-1].id)] if count else [])


def test_a_citation_with_a_chunk_id_resolves_even_when_chunks_share_a_locator():
    chunks = [_chunk("a"), _chunk("b")]
    saved = [{"claim": "b", "locator": chunks[1].source_ref, "chunk_id": str(chunks[1].id)}]

    [citation] = resolved_citations(Rows(chunks), module_with(saved))

    assert (citation.resolution, citation.chunk_content) == ("resolved", "b")


def test_a_citation_without_a_chunk_is_missing_and_never_matched_by_its_locator():
    chunks = [_chunk("a", source_ref="S5")]
    saved = [{"claim": "c", "locator": "S5", "chunk_id": None},
             {"claim": "d", "locator": "x", "chunk_id": str(uuid.uuid4())}]

    assert [c.resolution for c in resolved_citations(Rows(chunks), module_with(saved))] == ["missing", "missing"]


def test_a_citation_saved_before_chunk_ids_still_resolves_by_locator():
    chunks = [_chunk("a", source_ref="p1"), _chunk("b", source_ref="p2"), _chunk("c", source_ref="p2")]
    saved = [{"claim": "a", "locator": "p1"}, {"claim": "b", "locator": "p2"}, {"claim": "z", "locator": "p9"}]

    assert [c.resolution for c in resolved_citations(Rows(chunks), module_with(saved))] == [
        "resolved", "ambiguous", "missing"]


def test_prompt_source_map_and_unknown_source_codes_catch_invented_and_malformed_codes():
    chunks = [_chunk(source_ref='{"page": 1}'), _chunk(source_ref="")]
    smap = prompt_source_map(chunks)

    assert list(smap) == ["S1", "S2"]
    assert smap["S1"].chunk_id == str(chunks[0].id)
    assert smap["S2"].locator == ""

    md = "Valid [S1]. Bad [S9]. Case [s1]. Spaced [ S1 ]. Multi [S1, S2]. SpaceSep [S1 S2]. Dup [S9]."
    assert unknown_source_codes(md, smap) == ["S9", "[s1]", "[ S1 ]", "[S1, S2]", "[S1 S2]"]


def test_derive_citations_slices_last_sentence_strips_decorations_and_keeps_exact_offsets():
    chunks = [_chunk(source_ref='{"heading": "A"}'), _chunk(source_ref='{"heading": "B"}')]
    smap = prompt_source_map(chunks)

    md = (
        "Uncited lead. Second claim! [S1]\n"
        "- Bullet item [S1][S2]\n"
        "### Heading claim [S2]\n"
        "1) Numbered claim [S1]\n"
        "1. First item. Actual claim [S2]"
    )
    cited = derive_citations(md, smap)

    assert len(cited) == 6
    assert cited[0].claim == "Second claim!"
    assert md[cited[0].start:cited[0].end] == "Second claim!"
    assert md[cited[0].marker_start:cited[0].marker_end] == "[S1]"
    assert cited[0].source_id == "S1"

    assert cited[1].claim == "Bullet item"
    assert cited[2].claim == "Bullet item"
    assert (cited[1].start, cited[1].end) == (cited[2].start, cited[2].end)
    assert [cited[1].source_id, cited[2].source_id] == ["S1", "S2"]

    assert cited[3].claim == "Heading claim"
    assert cited[4].claim == "Numbered claim"
    assert cited[5].claim == "Actual claim"
    assert md[cited[3].start:cited[3].end] == "Heading claim"
    assert md[cited[4].start:cited[4].end] == "Numbered claim"
    assert md[cited[5].start:cited[5].end] == "Actual claim"


def test_derive_citations_rejects_empty_claim_and_citation_content_deduplicates_locators():
    chunks = [_chunk(source_ref='{"p": 1}'), _chunk(source_ref='{"p": 1}'), _chunk(source_ref="")]
    smap = prompt_source_map(chunks)

    for bare in ("[S1]", "   [S1]", "First. [S1]\n   [S2]"):
        with pytest.raises(ValueError, match="must follow a sentence or bullet"):
            derive_citations(bare, smap)

    with pytest.raises(ValueError, match="unknown source IDs: S8"):
        derive_citations("Fact [S8]", smap)

    payload = citation_content("Claim A. [S1] Claim B. [S2] Claim C. [S3]", smap)
    assert payload["markdown"].startswith("Claim A.")
    assert len(payload["inline_citations"]) == 3
    assert payload["source_locators"] == ['{"p": 1}']
    assert set(payload["source_map"]) == {"S1", "S2", "S3"}


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
    chunks = [_chunk(text, source_ref="same-page") for text in ["one", "two"]]
    stored = resolve_citations(ContentOutput(content="## Title\n- A combined fact. [S2][S1]\n- Uncited fact."), chunks)
    assert [c.source_id for c in stored.inline_citations] == ["S2", "S1"]
    assert [c.claim for c in stored.inline_citations] == ["A combined fact.", "A combined fact."]
    assert stored.inline_citations[0].start == stored.inline_citations[1].start
    assert stored.source_map["S2"].chunk_id == str(chunks[1].id)
    component = types.SimpleNamespace(type=ComponentType.content, content={
        **stored.model_dump(mode="json"), "markdown": "- Rewritten fact. [S1]"})
    module = types.SimpleNamespace(document_id=uuid.uuid4(), course_id=uuid.uuid4(), components=[component])
    [citation] = resolved_citations(Rows(chunks), module)
    assert citation.claim == "Rewritten fact." and citation.chunk_id == str(chunks[0].id)
    component.content["markdown"] = "Uncited replacement."
    assert resolved_citations(Rows(chunks), module) == []


def test_blinded_packet_hides_codes_and_keeps_real_content_offsets():
    component = ModuleComponent(type=ComponentType.content, content={"markdown": "Fact. [S1][S2]\nUncited."})
    quiz = ModuleComponent(type=ComponentType.quiz, content={}, questions=[Question(position=0, stem="Q?",
        choices={"options": ["A", "B", "C", "D"]}, answer_key="0", explanation="E")])
    practice = ModuleComponent(type=ComponentType.practice, content={}, questions=[Question(position=0, stem="P?",
        choices={"options": ["A", "B", "C", "D"]}, answer_key="0", explanation="E")])
    module = Module(title="Topic", bloom_level="understand", learning_objective="Explain", sub_objectives=[],
                    components=[component, quiz, practice])
    packet = module_packet(module, ["Fact."])
    assert packet.content_markdown == "Fact.         \nUncited."
    assert len(packet.content_markdown) == len(component.content["markdown"])


def test_study_packet_and_citations_use_original_while_preview_uses_saved_edits():
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
        comp = ModuleComponent(type=ComponentType.content, content={
            "markdown": "Teacher replacement.", "source_map": original["source_map"],
            "ai_original": original})
        questions = [ModuleComponent(type=kind, content={}, questions=[Question(position=0, stem="Q?",
            choices={"options": ["A", "B"]}, answer_key="0", explanation="E")])
            for kind in [ComponentType.quiz, ComponentType.practice]]
        module = Module(document_id=doc, course_id=course, title="Topic", bloom_level="understand",
                        learning_objective="Explain", sub_objectives=[], components=[comp, *questions])
        packet = module_packet(module, [chunk.content])
        [citation] = resolved_citations(db, module, use_ai_original=True)
        assert citation.claim == "Original fact."
        assert packet.content_markdown[citation.start:citation.end] == citation.claim
        assert citation.chunk_content == "Original fact."
        assert resolved_citations(db, module) == []
