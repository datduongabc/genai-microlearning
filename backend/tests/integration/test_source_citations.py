"""Source citations by ID: the model cites the short ID a chunk carries in the prompt, the server maps
it back to the chunk, and a stored citation points at exactly one passage."""

import json
import types
import uuid

import pytest
from jsonschema import Draft202012Validator

from app.generation import content
from app.generation.common import format_source_chunks, resolve_source_id
from app.generation.content import ContentOutput, resolve_citations
from app.generation.objective import LearningObjective
from app.generation.study_packets import resolved_citations
from app.llm import gateway
from app.models import ComponentType


def chunk(content="text", source_ref='{"heading": "Sorting"}'):
    return types.SimpleNamespace(id=uuid.uuid4(), content=content, source_ref=source_ref)


def test_every_chunk_carries_its_own_id_and_no_locator_in_the_prompt():
    chunks = [chunk("one"), chunk("two")]

    prompt = format_source_chunks(chunks)

    assert "SOURCE CHUNK [S1]:" in prompt and "SOURCE CHUNK [S2]:" in prompt
    assert "Sorting" not in prompt and "locator" not in prompt


def test_an_id_maps_to_its_chunk_and_an_invented_one_to_nothing():
    chunks = [chunk(), chunk()]

    assert resolve_source_id(chunks, "S2") is chunks[1]
    assert resolve_source_id(chunks, " [s1] ") is chunks[0]
    for invented in ("S0", "S3", "S", "1", "", "chunk-1", '{"page": 1}', "S1 and S2"):
        assert resolve_source_id(chunks, invented) is None


def test_chunks_that_share_a_locator_are_still_cited_exactly():
    """Every chunk under one heading shares its locator, which is what made them ambiguous."""
    chunks = [chunk("a"), chunk("b")]
    out = ContentOutput(content="b [S2]")

    stored = resolve_citations(out, chunks)

    assert stored.inline_citations[0].chunk_id == str(chunks[1].id)
    assert stored.source_locators == ['{"heading": "Sorting"}']


def test_an_id_the_model_invented_is_rejected():
    with pytest.raises(ValueError, match="S5"):
        resolve_citations(ContentOutput(content="c [S5]"), [chunk()])


LO = LearningObjective(
    bloom_level="understand", verb="Explain", objective_text="Explain heap insertion",
    sub_objectives=["Describe reheapUp", "Explain insertion complexity"],
)


def test_unknown_ids_trigger_regeneration_even_when_the_critic_passes(monkeypatch):
    chunks = [chunk("reheapUp takes O(log n)")]
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
def test_content_with_unknown_ids_is_never_returned_after_the_retry_budget(monkeypatch, self_check):
    chunks = [chunk()]

    def complete(prompt, response_model, **kwargs):
        if response_model is ContentOutput:
            return ContentOutput(content="heap claim. [S33]")
        return content.ContentVerification(
            groundedness_pass=True, groundedness_reason="supported",
            completeness_pass=True, completeness_reason="complete",
            scope_pass=True, scope_reason="in scope",
        )

    monkeypatch.setattr(content, "complete_structured", complete)

    with pytest.raises(ValueError, match="S33"):
        content.generate_content(LO, chunks, self_check=self_check)


@pytest.mark.parametrize("count", [0, 1, 2])
def test_the_gateway_schema_accepts_only_content_and_the_server_maps_inline_codes(monkeypatch, count):
    chunks = [chunk() for _ in range(count)]
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
    content = types.SimpleNamespace(type=ComponentType.content, content={"claim_citations": saved})
    return types.SimpleNamespace(components=[content], document_id=1, course_id=1)


def test_a_citation_with_a_chunk_id_resolves_even_when_chunks_share_a_locator():
    chunks = [chunk("a"), chunk("b")]
    saved = [{"claim": "b", "locator": chunks[1].source_ref, "chunk_id": str(chunks[1].id)}]

    [citation] = resolved_citations(Rows(chunks), module_with(saved))

    assert (citation.resolution, citation.chunk_content) == ("resolved", "b")


def test_a_citation_without_a_chunk_is_missing_and_never_matched_by_its_locator():
    chunks = [chunk("a", source_ref="S5")]
    saved = [{"claim": "c", "locator": "S5", "chunk_id": None},
             {"claim": "d", "locator": "x", "chunk_id": str(uuid.uuid4())}]

    assert [c.resolution for c in resolved_citations(Rows(chunks), module_with(saved))] == ["missing", "missing"]


def test_a_citation_saved_before_chunk_ids_still_resolves_by_locator():
    chunks = [chunk("a", source_ref="p1"), chunk("b", source_ref="p2"), chunk("c", source_ref="p2")]
    saved = [{"claim": "a", "locator": "p1"}, {"claim": "b", "locator": "p2"}, {"claim": "z", "locator": "p9"}]

    assert [c.resolution for c in resolved_citations(Rows(chunks), module_with(saved))] == [
        "resolved", "ambiguous", "missing"]
