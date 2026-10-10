"""Module generation from one section of a textbook, with every LLM-backed
step replaced by a fake that records what the pipeline asked for."""

import types
import uuid

import pytest

from app.db import SessionLocal
from app.generation import module_generation_pipeline as pipeline
from app.generation.example import Example, ExampleOutput
from app.generation.planner import Concept, PlannerOutput
from app.generation.relevance import CorrectiveResult
from app.generation.study_packets import resolved_citations
from app.ingestion.embeddings import embed_texts
from app.models import (
    Chunk,
    Course,
    Document,
    DocumentKind,
    DocumentSection,
    DocumentStatus,
    Job,
    JobStatus,
    JobType,
    Module,
    User,
)
from app.retrieval import search as retrieval
from app.retrieval.search import ChunkScope

CHUNK_TEXTS = [f"CHUNK-{i} " + "filler text " * 20 for i in range(6)]


@pytest.fixture
def textbook():
    db = SessionLocal()
    try:
        user = User(
            email=f"gen-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False
        )
        db.add(user)
        db.flush()
        course = Course(name="Section generation", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(
            course_id=course.id,
            filename="book.pdf",
            status=DocumentStatus.embedded,
            kind=DocumentKind.textbook,
            raw_text="the whole book",
        )
        db.add(doc)
        db.flush()
        db.add_all(
            Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=text)
            for i, text in enumerate(CHUNK_TEXTS)
        )
        section = DocumentSection(
            document_id=doc.id, title="Sorting", level=1, position=0, first_chunk_index=2, last_chunk_index=4
        )
        db.add(section)
        db.commit()
        ids = {"user": user.id, "course": course.id, "doc": doc.id, "section": section.id}
    finally:
        db.close()

    yield ids

    # Deleting the course cascades to the document, sections, modules and jobs.
    db = SessionLocal()
    try:
        db.query(Course).filter(Course.id == ids["course"]).delete(synchronize_session=False)
        db.query(User).filter(User.id == ids["user"]).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


@pytest.fixture
def seen(monkeypatch):
    record = {"planner_inputs": [], "retrieve_scopes": [], "expand_scopes": [], "quiz_scopes": []}

    def fake_planner(text, output_language="vi", custom_instructions=""):
        record["planner_inputs"].append(text)
        return PlannerOutput(concepts=[Concept(name="Sorting", priority=1, summary="how to sort")])

    def fake_retrieve(db, course_id, query, top_k=8, candidate_k=30, min_k=2, scope=None):
        record["retrieve_scopes"].append(scope)
        return db.query(Chunk).filter(Chunk.course_id == course_id).order_by(Chunk.chunk_index).limit(2).all()

    def fake_expand(db, chunks, window=2, scope=None):
        record["expand_scopes"].append(scope)
        return chunks

    def fake_quiz(db, course_id, query, lo, mode="quiz", scope=None, **kwargs):
        record["quiz_scopes"].append(scope)
        return {"passed": False}

    objective = types.SimpleNamespace(
        objective_text="objective", bloom_level="understand", sub_objectives=["first", "second"]
    )
    monkeypatch.setattr(
        pipeline, "corrective_retrieve", lambda objective, chunks, re_retrieve: CorrectiveResult(chunks, "high", 0)
    )
    monkeypatch.setattr(pipeline, "run_planner", fake_planner)
    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline, "expand_with_parent_context", fake_expand)
    monkeypatch.setattr(pipeline, "generate_quiz_question", fake_quiz)
    monkeypatch.setattr(
        pipeline,
        "generate_learning_objective",
        lambda concept, chunks, output_language="vi", custom_instructions="": objective,
    )
    monkeypatch.setattr(
        pipeline,
        "generate_content",
        lambda lo, chunks, self_check=True, output_language="vi", custom_instructions="": (
            types.SimpleNamespace(content="content", source_locators=[], claim_citations=[]),
            types.SimpleNamespace(groundedness_pass=True),
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "generate_slides",
        lambda lo, content, output_language="vi", custom_instructions="": types.SimpleNamespace(slides=[]),
    )
    monkeypatch.setattr(
        pipeline,
        "generate_example",
        lambda lo, content, chunks, output_language="vi", custom_instructions="": ExampleOutput(examples=[Example(context="ctx", analysis="analysis")] * 2),
    )
    return record


def test_generation_for_a_section_only_uses_that_sections_chunks(textbook, seen):
    db = SessionLocal()
    try:
        doc = db.get(Document, textbook["doc"])
        section = db.get(DocumentSection, textbook["section"])
        scope = ChunkScope(doc.id, 2, 4)

        module = pipeline.run_module_generation(db, doc, section)

        assert module.section_id == section.id
        assert seen["retrieve_scopes"]
        assert all(s == scope for s in seen["retrieve_scopes"])
        assert seen["expand_scopes"] == [scope]
        assert seen["quiz_scopes"] and all(s == scope for s in seen["quiz_scopes"])
    finally:
        db.close()


def test_the_planner_reads_only_the_section(textbook, seen):
    db = SessionLocal()
    try:
        pipeline.run_module_generation(
            db, db.get(Document, textbook["doc"]), db.get(DocumentSection, textbook["section"])
        )

        planner_input = seen["planner_inputs"][0]
        assert "CHUNK-2" in planner_input
        assert "CHUNK-4" in planner_input
        assert "CHUNK-1" not in planner_input
        assert "CHUNK-5" not in planner_input
    finally:
        db.close()


def test_the_planner_result_is_cached_on_the_section_not_the_document(textbook, seen):
    db = SessionLocal()
    try:
        doc = db.get(Document, textbook["doc"])
        section = db.get(DocumentSection, textbook["section"])

        pipeline.run_module_generation(db, doc, section)
        pipeline.run_module_generation(db, doc, section)

        assert len(seen["planner_inputs"]) == 1
        db.refresh(section)
        db.refresh(doc)
        assert section.planner_concepts is not None
        assert doc.planner_concepts is None
    finally:
        db.close()


def test_generation_from_the_whole_document_stays_in_that_document(textbook, seen):
    db = SessionLocal()
    try:
        doc = db.get(Document, textbook["doc"])

        module = pipeline.run_module_generation(db, doc)

        assert module.section_id is None
        assert seen["retrieve_scopes"]
        assert all(s is not None and s.document_id == doc.id for s in seen["retrieve_scopes"])
        assert all(s is not None and s.document_id == doc.id for s in seen["expand_scopes"])
        assert all(s is not None and s.document_id == doc.id for s in seen["quiz_scopes"])
        assert seen["planner_inputs"] == ["the whole book"]
        db.refresh(doc)
        assert doc.planner_concepts is not None
    finally:
        db.close()


def test_pdf_and_mp3_in_one_course_keep_generation_citations_in_their_document(textbook, seen, monkeypatch):
    # Reproduced live: a PDF module cited the MP3's chunk, which its resolver could not find.
    # Keep retrieval, parent expansion and resolution real; only external model calls are faked.
    monkeypatch.setattr(pipeline, "retrieve", retrieval.retrieve)
    monkeypatch.setattr(pipeline, "expand_with_parent_context", retrieval.expand_with_parent_context)
    monkeypatch.setattr(retrieval, "rerank_scored", lambda query, chunks: [(c, 1.0) for c in chunks])
    monkeypatch.setattr(pipeline, "corrective_retrieve", lambda objective, chunks, re_retrieve:
                        CorrectiveResult(re_retrieve("Sorting"), "high", 1))
    monkeypatch.setattr(pipeline, "generate_content", lambda lo, chunks, **kwargs: (
        types.SimpleNamespace(content="content", source_locators=[], claim_citations=[
            types.SimpleNamespace(model_dump=lambda c=c: {"claim": c.content, "locator": c.source_ref})
            for c in chunks
        ]), None,
    ))
    question_sources = []

    def question(db, course_id, query, lo, mode="quiz", scope=None, **kwargs):
        question_sources.extend(retrieval.retrieve(db, course_id, query, scope=scope))
        return {"passed": False}

    monkeypatch.setattr(pipeline, "generate_quiz_question", question)
    db = SessionLocal()
    try:
        pdf = db.get(Document, textbook["doc"])
        mp3 = Document(course_id=pdf.course_id, filename="lecture.mp3", raw_text="Sorting",
                       status=DocumentStatus.embedded)
        db.add(mp3)
        db.flush()
        db.add(Chunk(document_id=mp3.id, course_id=pdf.course_id, chunk_index=0,
                     content="Sorting algorithm", source_ref='{"heading": null, "unit_index": 0}'))
        db.flush()
        chunks = db.query(Chunk).filter(Chunk.course_id == pdf.course_id).all()
        for chunk, vector in zip(chunks, embed_texts([c.content for c in chunks]), strict=True):
            chunk.embedding = vector
            if chunk.document_id == pdf.id:
                chunk.source_ref = f'{{"page": {chunk.chunk_index + 1}}}'
        db.commit()
        for doc in (pdf, mp3):
            question_sources.clear()
            module = pipeline.run_module_generation(db, doc, self_check=False)
            citations = resolved_citations(db, module)
            assert citations and all(c.resolution == "resolved" for c in citations)
            assert question_sources and all(c.document_id == doc.id for c in question_sources)
    finally:
        retrieval.invalidate_sparse_cache(textbook["course"])
        db.close()


def test_the_job_step_generates_for_the_section_the_job_names(textbook, seen):
    db = SessionLocal()
    try:
        job = Job(
            job_type=JobType.module_generation,
            step="generate",
            status=JobStatus.running,
            document_id=textbook["doc"],
            section_id=textbook["section"],
        )
        db.add(job)
        db.commit()
        job_id = job.id

        pipeline.run_module_generation_step(job)

        db.expire_all()
        finished = db.get(Job, job_id)
        assert finished.status == JobStatus.done
        assert db.get(Module, finished.module_id).section_id == textbook["section"]
    finally:
        db.close()


def test_the_job_step_without_a_section_generates_from_the_whole_document(textbook, seen):
    db = SessionLocal()
    try:
        job = Job(
            job_type=JobType.module_generation,
            step="generate",
            status=JobStatus.running,
            document_id=textbook["doc"],
        )
        db.add(job)
        db.commit()
        job_id = job.id

        pipeline.run_module_generation_step(job)

        db.expire_all()
        finished = db.get(Job, job_id)
        assert finished.status == JobStatus.done
        assert db.get(Module, finished.module_id).section_id is None
        assert all(s is not None and s.document_id == textbook["doc"] for s in seen["retrieve_scopes"])
    finally:
        db.close()


@pytest.mark.parametrize("mode", ["whole", "section", "cached", "control"])
def test_every_generation_provider_call_releases_worker_transactions(textbook, monkeypatch, mode):
    from sqlalchemy import select, text

    from app.generation import content, example, objective, planner, quiz_graph, relevance, slides
    from app.generation.content import ContentVerification, LessonText, LessonVerification
    from app.generation.objective import LearningObjective
    from app.generation.quiz_graph import AnswerabilityCheck, GroundednessAnswerabilityCheck
    from app.ingestion import embeddings
    from tests.conftest import _offline_embedding
    from tests.integration.generation_helpers import OUTPUTS

    calls = []

    def assert_released(kind):
        with SessionLocal() as probe:
            assert not probe.execute(text(
                "SELECT pid FROM pg_stat_activity WHERE datname = current_database() AND usename = 'app_worker' "
                "AND xact_start IS NOT NULL"
            )).all(), kind
            probe.execute(text("SET LOCAL lock_timeout = '500ms'"))
            probe.execute(select(Document).where(Document.id == textbook["doc"]).with_for_update())
        calls.append(kind)

    def fake_llm(prompt, response_model, **kwargs):
        assert_released(response_model.__name__)
        if response_model is LessonText:
            return LessonText(content="## Bài học\nKhái niệm")
        if response_model in (ContentVerification, LessonVerification):
            if response_model is LessonVerification:
                return LessonVerification(completeness_pass=True, completeness_reason="ok",
                                          scope_pass=True, scope_reason="ok")
            return ContentVerification(groundedness_pass=True, groundedness_reason="ok",
                                       completeness_pass=True, completeness_reason="ok",
                                       scope_pass=True, scope_reason="ok")
        if response_model in (GroundednessAnswerabilityCheck, AnswerabilityCheck):
            if response_model is AnswerabilityCheck:
                return AnswerabilityCheck(answerability_pass=True, answerability_reason="ok",
                                          bloom_level_pass=True, bloom_level_reason="ok")
            return GroundednessAnswerabilityCheck(groundedness_pass=True, groundedness_reason="ok",
                                                 answerability_pass=True, answerability_reason="ok",
                                                 bloom_level_pass=True, bloom_level_reason="ok")
        return OUTPUTS[response_model].model_copy(deep=True)

    def fake_embedding(**kwargs):
        assert_released("embedding")
        return _offline_embedding(**kwargs)

    def fake_rerank(query, chunks):
        assert_released("rerank")
        return [(chunk, 1.0) for chunk in chunks]

    for step in (content, example, objective, planner, quiz_graph, relevance, slides):
        monkeypatch.setattr(step, "complete_structured", fake_llm)
    monkeypatch.setattr(embeddings.litellm, "embedding", fake_embedding)
    monkeypatch.setattr(retrieval, "rerank_scored", fake_rerank)
    with SessionLocal() as db:
        payload = {}
        if mode in ("cached", "control"):
            db.get(Document, textbook["doc"]).planner_concepts = [
                {**concept.model_dump(), "output_language": "vi"}
                for concept in OUTPUTS[PlannerOutput].concepts
            ]
        if mode == "control":
            lo = OUTPUTS[LearningObjective]
            paired = Module(document_id=textbook["doc"], course_id=textbook["course"],
                            title="Khái niệm", learning_objective=lo.objective_text,
                            bloom_level=lo.bloom_level, sub_objectives=lo.sub_objectives)
            db.add(paired)
            db.flush()
            payload = {"use_retrieval": False, "paired_module_id": str(paired.id)}
        job = Job(job_type=JobType.module_generation, step="generate", status=JobStatus.running,
                  document_id=textbook["doc"], section_id=textbook["section"] if mode == "section" else None,
                  payload=payload)
        db.add(job)
        db.commit()
        pipeline.run_module_generation_step(job)
        db.refresh(job)
        assert job.status == JobStatus.done
        assert {"SlidesOutput", "ExampleOutput", "QuizQuestion"} <= set(calls)
        if mode == "control":
            assert {"LessonVerification", "AnswerabilityCheck"} <= set(calls)
            assert not {"embedding", "rerank", "PlannerOutput"} & set(calls)
        else:
            assert {"LearningObjective", "RelevanceCheck", "ContentOutput", "ContentVerification",
                    "GroundednessAnswerabilityCheck", "embedding", "rerank"} <= set(calls)
            assert ("PlannerOutput" in calls) == (mode != "cached")


def test_provider_failure_leaves_no_partial_module_and_keeps_retry_policy(textbook, seen, monkeypatch):
    from app.jobs.queue import handle_job_error

    def unavailable(*args, **kwargs):
        raise RuntimeError("LLM gateway unavailable")

    monkeypatch.setattr(pipeline, "generate_slides", unavailable)
    with SessionLocal() as db:
        job = Job(job_type=JobType.module_generation, step="generate", status=JobStatus.running,
                  document_id=textbook["doc"])
        db.add(job)
        db.commit()
        job_id = job.id
        with pytest.raises(RuntimeError, match="LLM gateway unavailable") as failure:
            pipeline.run_module_generation_step(job)
        handle_job_error(job_id, failure.value)
        db.refresh(job)
        assert job.status == JobStatus.queued
        assert job.attempts == 1
        assert job.module_id is None
        assert db.query(Module).filter(Module.document_id == textbook["doc"]).count() == 0
