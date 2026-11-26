"""Deterministic R9 tests; no network or existing database."""
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.generation import claim_evaluation as ce
from app.generation import study_packets as sp
from app.models import Chunk, ComponentType, Module, ModuleComponent
from app.retrieval.search import ChunkScope
from scripts import analyze_study, export_study_pair, run_claim_judge, run_judge
from tests.judge_helpers import GATEWAY, _evaluation, _packet


def reference(n=1, coverage="complete"):
    return ce.ReferenceEvidence(coverage=coverage, chunk_ids=[str(i) for i in range(n)], total_chunks=n)


@pytest.fixture
def judge(monkeypatch):
    calls = []

    def fake(prompt, model, **kwargs):
        data = json.loads(prompt)
        calls.append((model, data, kwargs))
        if model is ce.ClaimExtraction:
            start, claims = 0, []
            for line in data["content"].splitlines(keepends=True):
                claims.append(ce.ExtractedClaim(text=line.strip(), start=start, end=start + len(line.strip())))
                start += len(line)
            return ce.ClaimExtraction(claims=claims)
        return ce.SupportVerdict(supported=data["claim"] in data["evidence"], reason="Evidence membership")

    monkeypatch.setattr(ce, "complete_structured", fake)
    monkeypatch.setattr(run_judge, "fetch_model_info", lambda: GATEWAY)
    return calls


def test_two_invented_claims_in_ten_give_point_two(judge):
    facts = [f"Fact {i}" for i in range(10)]
    result = ce.assess_content("\n".join(facts), facts[:8], reference(8))
    assert (result.total_claims, result.unsupported_claims, result.unsupported_rate) == (10, 2, .2)
    assert len(judge) == 11


def test_wrong_chunk_is_incorrect_despite_support_elsewhere(judge):
    citation = ce.StoredCitation(claim="Fact", locator="p2", chunk_id="2", chunk_content="Other", resolution="resolved")
    result = ce.assess_content("Fact", ["Fact", "Other"], reference(2), [citation])
    assert result.unsupported_rate == 0 and result.citation_accuracy == 0
    assert result.citation_count == 1 and result.correct_citations == 0
    assert judge[-1][1]["evidence"] == ["Other"]


def test_groq_judge_requests_low_reasoning_for_extraction_and_support(judge):
    ce.assess_content("Fact", ["Fact"], reference(), judge_model="groq/openai/gpt-oss-120b")
    assert len(judge) == 2
    for _, _, kwargs in judge:
        assert kwargs["reasoning_effort"] == "low"
        assert kwargs["allowed_openai_params"] == ["reasoning_effort"]


def test_non_groq_judge_keeps_default_reasoning(judge):
    ce.assess_content("Fact", ["Fact"], reference(), judge_model="gemini/gemini-3.5-flash")
    assert all("reasoning_effort" not in kwargs for _, _, kwargs in judge)


@pytest.mark.parametrize("coverage", ["partial", "unknown"])
def test_incomplete_coverage_has_no_overall_rate(judge, coverage):
    result = ce.assess_content("Fake", ["Other"], reference(1, coverage))
    assert result.unsupported_claims == 1 and result.unsupported_rate is None


def test_empty_content_and_evidence_are_undefined(judge):
    empty = ce.assess_content("", ["Fact"], reference(), [])
    missing = ce.assess_content("Fact", [], reference(0))
    assert empty.total_claims == 0 and empty.unsupported_rate is None
    assert empty.citation_count == 0 and empty.citation_accuracy is None
    assert missing.claims[0].supported is None and missing.unsupported_rate is None
    assert missing.citation_count is None


@pytest.mark.parametrize("resolution", ["missing", "ambiguous"])
def test_unresolved_citation_is_incorrect(judge, resolution):
    citation = ce.StoredCitation(claim="Fact", locator="bad", resolution=resolution)
    result = ce.assess_content("Fact", ["Fact"], reference(), [citation])
    assert result.citation_accuracy == 0 and len(judge) == 2


def test_inconsistent_complete_metadata_and_invalid_spans_are_rejected(judge, monkeypatch):
    with pytest.raises(ValueError, match="identity"):
        ce.assess_content("Fact", ["Fact"], ce.ReferenceEvidence(coverage="complete", total_chunks=1))
    assert judge == []
    monkeypatch.setattr(ce, "complete_structured", lambda *a, **k: ce.ClaimExtraction(
        claims=[ce.ExtractedClaim(text="Fact", start=0, end=999)]))
    with pytest.raises(ValueError, match="span"):
        ce.assess_content("Fact", ["Fact"], reference())


def write_pair(folder, coverage="complete"):
    for group in ("rag_llm", "llm_no_rag"):
        study = sp.StudyFile(
            pair_id="pair", group=group, generator_model="gemini-flash",
            packet=_packet().model_copy(update={"content_markdown": "Fact\nFake", "source_chunks": ["Fact"]}),
            reference=reference(1, coverage), claim_citations=[] if group == "rag_llm" else None,
        )
        (folder / f"pair-{group}.json").write_text(study.model_dump_json(), encoding="utf-8")


def test_runner_persists_resumes_analyzes_and_refuses_changed_packet(tmp_path, judge):
    write_pair(tmp_path)
    out = tmp_path / "out.jsonl"
    args = [str(tmp_path), "--judge", "claude-fast", "--out", str(out)]
    run_claim_judge.main(args)
    count = len(judge)
    run_claim_judge.main(args)
    assert len(judge) == count
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 2 and all(r["claim_evaluation"]["unsupported_rate"] == .5 for r in rows)
    report = analyze_study.analyze_claims([str(out)])["claude-fast"]
    assert report["rag_llm"]["unsupported_rate"] == .5
    assert report["llm_no_rag"]["citation_packets"] == 0
    path = tmp_path / "pair-rag_llm.json"
    study = sp.StudyFile.model_validate_json(path.read_text())
    study.packet.content_markdown = "Changed"
    path.write_text(study.model_dump_json())
    with pytest.raises(SystemExit, match="changed"):
        run_claim_judge.main(args)
    assert len(judge) == count


def test_unfair_evidence_and_same_family_refused_before_calls(tmp_path, judge):
    write_pair(tmp_path)
    args = [str(tmp_path), "--judge", "gemini-flash", "--out", str(tmp_path / "out.jsonl")]
    with pytest.raises(SystemExit, match="google"):
        run_claim_judge.main(args)
    path = tmp_path / "pair-rag_llm.json"
    study = sp.StudyFile.model_validate_json(path.read_text())
    study.packet.source_chunks = ["Different"]
    path.write_text(study.model_dump_json())
    with pytest.raises(SystemExit, match="identical"):
        run_claim_judge.main(args)
    assert judge == []


def test_analysis_excludes_partial_reference_packets(tmp_path, judge):
    write_pair(tmp_path, "partial")
    out = tmp_path / "out.jsonl"
    run_claim_judge.main([str(tmp_path), "--judge", "claude-fast", "--out", str(out)])
    report = analyze_study.analyze_claims([str(out)])["claude-fast"]["rag_llm"]
    assert report["excluded_packets"] == 1 and report["total_claims"] == 0
    assert report["unsupported_rate"] is None


@pytest.mark.parametrize("group", ["rag_llm", "llm_no_rag"])
@pytest.mark.parametrize("coverage", ["complete", "partial"])
def test_analysis_refuses_conflicting_models_across_distinct_packets(tmp_path, judge, group, coverage):
    write_pair(tmp_path, coverage)
    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    run_claim_judge.main([str(tmp_path), "--judge", "claude-fast", "--out", str(first)])
    rows = [json.loads(line) for line in first.read_text().splitlines()]
    row = next(row for row in rows if row["group"] == group)
    row["anon_id"] = "distinct-packet"
    row["judge_model"] = "groq/openai/gpt-oss-20b"
    second.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="conflicting R9 judge_model for claude-fast"):
        analyze_study.analyze_claims([str(first), str(second)])


def test_analysis_pools_consistent_models_across_files(tmp_path, judge):
    write_pair(tmp_path)
    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    run_claim_judge.main([str(tmp_path), "--judge", "claude-fast", "--out", str(first)])
    rows = [json.loads(line) for line in first.read_text().splitlines()]
    for row in rows:
        row["anon_id"] += "-second"
    second.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    report = analyze_study.analyze_claims([str(first), str(second)])["claude-fast"]

    for group in ("rag_llm", "llm_no_rag"):
        assert report[group]["packets"] == 2
        assert report[group]["total_claims"] == 4
        assert report[group]["unsupported_rate"] == .5
    assert report["rag_llm"]["citation_packets"] == 2
    assert report["llm_no_rag"]["citation_packets"] == 0


def test_interrupted_output_rejudges_tail_and_another_judge_runs_all(tmp_path, judge):
    write_pair(tmp_path)
    out = tmp_path / "out.jsonl"
    args = [str(tmp_path), "--judge", "claude-fast", "--out", str(out)]
    run_claim_judge.main(args)
    complete = out.read_bytes().splitlines(keepends=True)
    out.write_bytes(complete[0] + complete[1][:50])
    judge.clear()
    run_claim_judge.main(args)
    assert len(judge) == 3  # extraction and two claim checks for only the lost packet
    judge.clear()
    run_claim_judge.main([str(tmp_path), "--judge", "openai-fast", "--out", str(out)])
    assert len(judge) == 6
    assert len(out.read_text().splitlines()) == 4


def test_resume_refuses_alias_repointed_to_another_model(tmp_path, judge, monkeypatch):
    write_pair(tmp_path)
    args = [str(tmp_path), "--judge", "claude-fast", "--out", str(tmp_path / "out.jsonl")]
    run_claim_judge.main(args)
    judge.clear()
    models = [dict(m) for m in GATEWAY]
    models[next(i for i, m in enumerate(models) if m["model_name"] == "claude-fast")] = {
        "model_name": "claude-fast", "litellm_params": {"model": "openrouter/anthropic/claude-sonnet-5.5"}}
    monkeypatch.setattr(run_judge, "fetch_model_info", lambda: models)
    with pytest.raises(SystemExit, match="Judge model changed"):
        run_claim_judge.main(args)
    assert judge == []


def test_export_persists_shared_reference_and_only_rag_citations(tmp_path, monkeypatch):
    module = Module(id=uuid.uuid4())

    class ExportSession:
        def get(self, model, identity):
            assert model is Module and identity == module.id
            return module

        def close(self):
            pass

    citation = ce.StoredCitation(claim="Fact", locator="p1", chunk_id="0",
                                 chunk_content="Fact", resolution="resolved")
    monkeypatch.setattr(export_study_pair, "SessionLocal", ExportSession)
    monkeypatch.setattr(export_study_pair, "model_that_made", lambda *a: "gemini-flash")
    monkeypatch.setattr(export_study_pair, "self_check_of", lambda *a: True)
    monkeypatch.setattr(export_study_pair, "study_pair", lambda *a: (_packet(), _packet()))
    monkeypatch.setattr(export_study_pair, "study_evidence", lambda *a: (reference(), [citation]))
    export_study_pair.main([str(module.id), "--out", str(tmp_path)])
    rag, control = [sp.StudyFile.model_validate_json(
        (tmp_path / f"{module.id}-{group}.json").read_text()
    ) for group in ("rag_llm", "llm_no_rag")]
    assert rag.reference == control.reference == reference()
    assert rag.claim_citations == [citation] and control.claim_citations is None


def test_r8_analysis_cli_includes_separate_r9_results(tmp_path, judge, monkeypatch):
    write_pair(tmp_path)
    rubric, claims, report = [tmp_path / f"{name}.jsonl" for name in ("r8", "r9", "report")]

    def evaluate(packet, **kwargs):
        run_judge.gateway.complete("judge")
        return _evaluation(packet.anon_id)

    monkeypatch.setattr(run_judge, "judge_module", evaluate)
    monkeypatch.setattr("litellm.completion", lambda **kwargs: SimpleNamespace(
        model=next(m["litellm_params"]["model"] for m in GATEWAY if m["model_name"] == run_judge.gateway.active_model()),
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]))
    run_judge.main([str(tmp_path), "--out", str(rubric)])
    run_claim_judge.main([str(tmp_path), "--judge", "claude-fast", "--out", str(claims)])
    analyze_study.main([str(rubric), "--claims", str(claims), "--out", str(report)])
    result = json.loads(report.read_text())
    assert result["claims"]["claude-fast"]["rag_llm"]["unsupported_rate"] == .5
    assert result["judges"]["openai-fast"]["means"]["rag_llm"]["accuracy"] == 4


def test_rubric_judges_can_be_limited_to_named_pool_aliases(tmp_path, judge, monkeypatch):
    write_pair(tmp_path)
    out = tmp_path / "r8.jsonl"

    def evaluate(packet, **kwargs):
        run_judge.gateway.complete("judge")
        return _evaluation(packet.anon_id)

    monkeypatch.setattr(run_judge, "judge_module", evaluate)
    monkeypatch.setattr("litellm.completion", lambda **kwargs: SimpleNamespace(
        model=next(m["litellm_params"]["model"] for m in GATEWAY if m["model_name"] == run_judge.gateway.active_model()),
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))]))
    run_judge.main([str(tmp_path), "--out", str(out), "--judges", "openai-fast,deepseek-flash"])
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert sorted({r["judge"] for r in rows}) == ["deepseek-flash", "openai-fast"] and len(rows) == 4
    with pytest.raises(SystemExit, match="Không thuộc pool"):
        run_judge.main([str(tmp_path), "--out", str(out), "--judges", "groq-pro"])


def test_document_reference_and_citation_identities_in_isolated_sqlite(monkeypatch):
    engine = create_engine("sqlite://")
    Chunk.__table__.create(engine)
    with Session(engine) as db:
        document_id, course_id = uuid.uuid4(), uuid.uuid4()
        chunks = [Chunk(document_id=document_id, course_id=course_id, chunk_index=i, content=text, source_ref=locator)
            for i, (text, locator) in enumerate([("Fact", "p1"), ("Other", "p2"), ("Outside", "p3")])]
        db.add_all(chunks)
        db.flush()
        content = ModuleComponent(type=ComponentType.content, content={"claim_citations": [
            {"claim": "Fact", "locator": "p2"}, {"claim": "Outside", "locator": "p3"},
            {"claim": "Absent", "locator": "bad"}]})
        module = Module(document_id=document_id, course_id=course_id, components=[content])
        monkeypatch.setattr(sp, "retrieve", lambda *a, **k: pytest.fail("small document must not retrieve"))
        sources = sp.reference_chunks(db, module)
        evidence, citations = sp.study_evidence(db, module, sources)
        assert sources == ["Fact", "Other", "Outside"] and evidence.coverage == "complete"
        assert evidence.chunk_ids == [str(c.id) for c in chunks]
        assert citations[0].chunk_id == str(chunks[1].id) and citations[0].chunk_content == "Other"
        assert citations[1].chunk_id == str(chunks[2].id) and citations[2].resolution == "missing"
        chunks[2].source_ref = "p2"
        db.flush()
        assert sp.study_evidence(db, module, sources)[1][0].resolution == "ambiguous"
        monkeypatch.setattr(sp, "concept_query_of", lambda module: "query")
        monkeypatch.setattr(sp, "REFERENCE_MAX_CHUNKS", 1)
        monkeypatch.setattr(sp, "retrieve", lambda *a, **k: chunks[:1])
        sources = sp.reference_chunks(db, module)
        evidence, _ = sp.study_evidence(db, module, sources)
        assert sources == ["Fact"] and evidence.coverage == "unknown" and evidence.total_chunks is None


def test_small_document_without_section_is_complete_reference(monkeypatch):
    engine = create_engine("sqlite://")
    Chunk.__table__.create(engine)
    with Session(engine) as db:
        document_id, course_id = uuid.uuid4(), uuid.uuid4()
        chunks = [Chunk(document_id=document_id, course_id=course_id, chunk_index=i, content=text)
                  for i, text in enumerate(["First fact", "Second fact"])]
        db.add_all(chunks)
        db.add(Chunk(document_id=uuid.uuid4(), course_id=course_id, chunk_index=0, content="Other document"))
        db.flush()
        module = Module(document_id=document_id, course_id=course_id, components=[])
        monkeypatch.setattr(sp, "concept_query_of", lambda *a: "query")
        monkeypatch.setattr(sp, "retrieve", lambda *a, **k: pytest.fail("small document must not retrieve"))

        sources = sp.reference_chunks(db, module)
        evidence, _ = sp.study_evidence(db, module, sources)

        assert sources == ["First fact", "Second fact"]
        assert evidence.coverage == "complete"
        assert evidence.total_chunks == 2
        assert evidence.chunk_ids == [str(c.id) for c in chunks]

        monkeypatch.setattr(sp, "REFERENCE_MAX_CHUNKS", 1)
        scopes = []
        monkeypatch.setattr(sp, "retrieve", lambda *a, **k: scopes.append(k["scope"]) or chunks[:1])
        sources = sp.reference_chunks(db, module)
        evidence, _ = sp.study_evidence(db, module, sources)
        assert sources == ["First fact"]
        assert scopes == [ChunkScope(document_id)]
        assert evidence.coverage == "unknown" and evidence.total_chunks is None


def test_each_inline_code_is_checked_against_its_own_chunk_and_uncited_claims_are_extracted(monkeypatch):
    calls = []

    def complete(prompt, model, **kwargs):
        data = json.loads(prompt)
        calls.append((model, data))
        if model is ce.ClaimExtraction:
            return ce.ClaimExtraction(claims=[
                ce.ExtractedClaim(text="A holds.", start=0, end=8),
                ce.ExtractedClaim(text="Uncited C.", start=19, end=29)])
        return ce.SupportVerdict(supported="A" in data["evidence"], reason="own chunk")

    monkeypatch.setattr(ce, "complete_structured", complete)
    citations = [
        ce.StoredCitation(
            claim="A holds.", source_id=f"S{i+1}", start=0, end=8,
            locator=f"p{i}", chunk_id=str(i), chunk_content=text, resolution="resolved",
        )
        for i, text in enumerate(["A", "irrelevant"])
    ]
    result = ce.assess_content("A holds. [S1][S2]\n  Uncited C.", ["A", "irrelevant"], reference(2), citations)
    assert result.total_claims == 2
    assert result.citation_count == 2 and result.correct_citations == 1
    assert [c.supported for c in result.citations] == [True, False]
    assert calls[0][1]["content"] == "A holds. [S1][S2]\n  Uncited C."
    assert [c[1]["evidence"] for c in calls[3:]] == [["A", "irrelevant"], ["A"], ["irrelevant"], ["A"]]


def test_a_sentence_citing_two_chunks_is_checked_against_both_together(monkeypatch):
    """Cell theory is in S5 and Darwin in S6: each alone does not support the sentence, both do."""
    seen = []

    def fake(prompt, model, **kwargs):
        data = json.loads(prompt)
        if model is ce.ClaimExtraction:
            return ce.ClaimExtraction(claims=[])
        seen.append(data["evidence"])
        evidence = " ".join(data["evidence"])
        return ce.SupportVerdict(supported="cells" in evidence and "Darwin" in evidence, reason="both parts")

    monkeypatch.setattr(ce, "complete_structured", fake)
    claim = "The premises are cell theory and Darwin's evolution"
    cited = [ce.StoredCitation(claim=claim, start=0, end=len(claim), source_id=f"S{i}", locator=f"p{i}",
                               chunk_id=str(i), chunk_content=text, resolution="resolved")
             for i, text in ((5, "All living things are made of cells."), (6, "Darwin described evolution."))]
    unresolved = ce.StoredCitation(claim="Other", start=60, end=65, locator="?", resolution="missing")

    result = ce.assess_content(claim, [], reference(0, "partial"), [*cited, unresolved])

    assert seen[0] == ["All living things are made of cells.", "Darwin described evolution."]
    assert [c.supported for c in result.citations] == [True, True, False]
    assert result.citation_accuracy == pytest.approx(2 / 3)
