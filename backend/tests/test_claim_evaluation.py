"""Deterministic R9 tests; no network or existing database."""
import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.generation import claim_evaluation as ce
from app.generation import study_packets as sp
from app.models import Chunk, ComponentType, DocumentSection, Module, ModuleComponent
from app.retrieval.search import ChunkScope
from scripts import analyze_study, export_study_pair, run_claim_judge, run_judge
from tests.test_run_judge import GATEWAY, _evaluation, _packet


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
        study = sp.StudyFile(pair_id="pair", group=group, generator_model="gemini-fast",
            packet=_packet().model_copy(update={"content_markdown": "Fact\nFake", "source_chunks": ["Fact"]}),
            reference=reference(1, coverage), claim_citations=[] if group == "rag_llm" else None)
        (folder / f"pair-{group}.json").write_text(study.model_dump_json(), encoding="utf-8")


def test_runner_persists_resumes_analyzes_and_refuses_changed_packet(tmp_path, judge):
    write_pair(tmp_path)
    out = tmp_path / "out.jsonl"
    args = [str(tmp_path), "--judge", "groq-pro", "--out", str(out)]
    run_claim_judge.main(args)
    count = len(judge)
    assert all(kwargs.get("reasoning_effort") == "low" for _, _, kwargs in judge)
    run_claim_judge.main(args)
    assert len(judge) == count
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert len(rows) == 2 and all(r["claim_evaluation"]["unsupported_rate"] == .5 for r in rows)
    report = analyze_study.analyze_claims([str(out)])["groq-pro"]
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
    args = [str(tmp_path), "--judge", "gemini-fast", "--out", str(tmp_path / "out.jsonl")]
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
    run_claim_judge.main([str(tmp_path), "--judge", "groq-pro", "--out", str(out)])
    report = analyze_study.analyze_claims([str(out)])["groq-pro"]["rag_llm"]
    assert report["excluded_packets"] == 1 and report["total_claims"] == 0
    assert report["unsupported_rate"] is None


@pytest.mark.parametrize("group", ["rag_llm", "llm_no_rag"])
@pytest.mark.parametrize("coverage", ["complete", "partial"])
def test_analysis_refuses_conflicting_models_across_distinct_packets(tmp_path, judge, group, coverage):
    write_pair(tmp_path, coverage)
    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    run_claim_judge.main([str(tmp_path), "--judge", "groq-pro", "--out", str(first)])
    rows = [json.loads(line) for line in first.read_text().splitlines()]
    row = next(row for row in rows if row["group"] == group)
    row["anon_id"] = "distinct-packet"
    row["judge_model"] = "groq/openai/gpt-oss-20b"
    second.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="conflicting R9 judge_model for groq-pro"):
        analyze_study.analyze_claims([str(first), str(second)])


def test_analysis_pools_consistent_models_across_files(tmp_path, judge):
    write_pair(tmp_path)
    first, second = tmp_path / "first.jsonl", tmp_path / "second.jsonl"
    run_claim_judge.main([str(tmp_path), "--judge", "groq-pro", "--out", str(first)])
    rows = [json.loads(line) for line in first.read_text().splitlines()]
    for row in rows:
        row["anon_id"] += "-second"
    second.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    report = analyze_study.analyze_claims([str(first), str(second)])["groq-pro"]

    for group in ("rag_llm", "llm_no_rag"):
        assert report[group]["packets"] == 2
        assert report[group]["total_claims"] == 4
        assert report[group]["unsupported_rate"] == .5
    assert report["rag_llm"]["citation_packets"] == 2
    assert report["llm_no_rag"]["citation_packets"] == 0


def test_interrupted_output_rejudges_tail_and_another_judge_runs_all(tmp_path, judge):
    write_pair(tmp_path)
    out = tmp_path / "out.jsonl"
    args = [str(tmp_path), "--judge", "groq-pro", "--out", str(out)]
    run_claim_judge.main(args)
    complete = out.read_bytes().splitlines(keepends=True)
    out.write_bytes(complete[0] + complete[1][:50])
    judge.clear()
    run_claim_judge.main(args)
    assert len(judge) == 3  # extraction and two claim checks for only the lost packet
    judge.clear()
    run_claim_judge.main([str(tmp_path), "--judge", "claude-pro", "--out", str(out)])
    assert len(judge) == 6
    assert len(out.read_text().splitlines()) == 4


def test_resume_refuses_alias_repointed_to_another_model(tmp_path, judge, monkeypatch):
    write_pair(tmp_path)
    args = [str(tmp_path), "--judge", "groq-pro", "--out", str(tmp_path / "out.jsonl")]
    run_claim_judge.main(args)
    judge.clear()
    models = [dict(m) for m in GATEWAY]
    models[2] = {"model_name": "groq-pro", "litellm_params": {"model": "groq/openai/gpt-oss-20b"}}
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
    monkeypatch.setattr(export_study_pair, "model_that_made", lambda *a: "gemini-fast")
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
    monkeypatch.setattr(run_judge, "judge_module", lambda packet: _evaluation(packet.anon_id))
    run_judge.main([str(tmp_path), "--judge", "groq-pro", "--out", str(rubric)])
    run_claim_judge.main([str(tmp_path), "--judge", "groq-pro", "--out", str(claims)])
    analyze_study.main([str(rubric), "--claims", str(claims), "--out", str(report)])
    result = json.loads(report.read_text())
    assert result["claims"]["groq-pro"]["rag_llm"]["unsupported_rate"] == .5
    assert result["judges"]["groq-pro"]["means"]["rag_llm"]["accuracy"] == 4


def test_small_section_reference_and_citation_identities_in_isolated_sqlite(monkeypatch):
    engine = create_engine("sqlite://")
    Chunk.__table__.create(engine)
    with Session(engine) as db:
        document_id, course_id = uuid.uuid4(), uuid.uuid4()
        # Only Chunk queries need persistence here. Section metadata is supplied
        # at the lookup boundary: its PostgreSQL JSONB schema is not SQLite DDL.
        section = DocumentSection(id=uuid.uuid4(), document_id=document_id, title="Section",
                                  first_chunk_index=0, last_chunk_index=1)
        def get_section(model, identity):
            assert model is DocumentSection and identity == section.id
            return section
        monkeypatch.setattr(db, "get", get_section)
        chunks = [Chunk(document_id=document_id, course_id=course_id, chunk_index=i, content=text, source_ref=locator)
            for i, (text, locator) in enumerate([("Fact", "p1"), ("Other", "p2"), ("Outside", "p3")])]
        db.add_all(chunks)
        db.flush()
        content = ModuleComponent(type=ComponentType.content, content={"claim_citations": [
            {"claim": "Fact", "locator": "p2"}, {"claim": "Outside", "locator": "p3"},
            {"claim": "Absent", "locator": "bad"}]})
        module = Module(document_id=document_id, course_id=course_id, section_id=section.id, components=[content])
        monkeypatch.setattr(sp, "retrieve", lambda *a, **k: pytest.fail("small section must not retrieve"))
        sources = sp.reference_chunks(db, module)
        evidence, citations = sp.study_evidence(db, module, sources)
        assert sources == ["Fact", "Other"] and evidence.coverage == "complete"
        assert evidence.chunk_ids == [str(c.id) for c in chunks[:2]]
        assert citations[0].chunk_id == str(chunks[1].id) and citations[0].chunk_content == "Other"
        assert citations[1].chunk_id == str(chunks[2].id) and citations[2].resolution == "missing"
        chunks[2].source_ref = "p2"
        db.flush()
        assert sp.study_evidence(db, module, sources)[1][0].resolution == "ambiguous"
        monkeypatch.setattr(sp, "REFERENCE_MAX_CHUNKS", 1)
        monkeypatch.setattr(sp, "retrieve", lambda *a, **k: chunks[:1])
        sources = sp.reference_chunks(db, module)
        evidence, _ = sp.study_evidence(db, module, sources)
        assert sources == ["Fact"] and evidence.coverage == "partial" and evidence.total_chunks == 2


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
        module = Module(document_id=document_id, course_id=course_id, section_id=None, components=[])
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
