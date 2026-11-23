"""Document generation, transaction release and tenant scope with offline providers."""

import uuid

import pytest

from app.db import SessionLocal
from app.generation import module_generation_pipeline as pipeline
from app.generation.content import ContentVerification, SourcedContent
from app.generation.example import Example, ExampleOutput
from app.generation.objective import LearningObjective
from app.generation.planner import Concept, PlannerOutput
from app.generation.quiz_graph import QuizQuestion
from app.generation.relevance import CorrectiveResult
from app.generation.slides import SlidesOutput
from app.generation.study_packets import resolved_citations
from app.ingestion.embeddings import embed_texts
from app.models import (
    Chunk,
    ComponentType,
    Course,
    Document,
    DocumentStatus,
    Job,
    JobStatus,
    JobType,
    Module,
    User,
)
from app.retrieval import search as retrieval
from tests.integration.generation_helpers import OUTPUTS

CHUNK_TEXTS = [f"CHUNK-{i} " + "filler text " * 20 for i in range(6)]


@pytest.fixture
def document_world():
    db = SessionLocal()
    try:
        user = User(
            email=f"gen-{uuid.uuid4().hex[:8]}@example.com", name="T", hashed_password="x", is_admin=False
        )
        db.add(user)
        db.flush()
        course = Course(name="Document generation", owner_id=user.id)
        db.add(course)
        db.flush()
        doc = Document(
            course_id=course.id,
            filename="book.pdf",
            status=DocumentStatus.embedded,
            raw_text="the whole book",
        )
        db.add(doc)
        db.flush()
        db.add_all(
            Chunk(document_id=doc.id, course_id=course.id, chunk_index=i, content=text)
            for i, text in enumerate(CHUNK_TEXTS)
        )
        db.commit()
        ids = {"user": user.id, "course": course.id, "doc": doc.id}
    finally:
        db.close()

    yield ids

    # Deleting the course cascades to the document, modules and jobs.
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
        passed = record.get("question_pass", True)
        return {"passed": passed, "question": OUTPUTS[QuizQuestion],
                "fail_reason": None if passed else "C and D both describe abstraction",
                "correction_retry_count": 0 if passed else 1}

    # Real outputs, not stand-ins: the job writes each finished step to its checkpoint as JSON.
    objective = OUTPUTS[LearningObjective].model_copy(
        update={"objective_text": "objective", "sub_objectives": ["first", "second"]})
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
            SourcedContent(content="content", source_map={}, source_locators=[], inline_citations=[]),
            ContentVerification(groundedness_pass=True, groundedness_reason="", completeness_pass=True,
                                completeness_reason="", scope_pass=True, scope_reason=""),
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "generate_slides",
        lambda lo, content, output_language="vi", custom_instructions="": OUTPUTS[SlidesOutput],
    )
    monkeypatch.setattr(
        pipeline,
        "generate_example",
        lambda lo, content, chunks, output_language="vi", custom_instructions="": ExampleOutput(examples=[Example(context="ctx", analysis="analysis")] * 2),
    )
    return record








def test_generation_from_the_whole_document_stays_in_that_document(document_world, seen):
    db = SessionLocal()
    try:
        doc = db.get(Document, document_world["doc"])

        module = pipeline.run_module_generation(db, doc)
        assert module.document_id == doc.id

        assert seen["retrieve_scopes"]
        assert all(s is not None and s.document_id == doc.id for s in seen["retrieve_scopes"])
        assert all(s is not None and s.document_id == doc.id for s in seen["expand_scopes"])
        assert all(s is not None and s.document_id == doc.id for s in seen["quiz_scopes"])
        assert seen["planner_inputs"] == ["the whole book"]
        db.refresh(doc)
        assert doc.planner_concepts is not None
    finally:
        db.close()


def test_pdf_and_mp3_in_one_course_keep_generation_citations_in_their_document(document_world, seen, monkeypatch):
    # Reproduced live: a PDF module cited the MP3's chunk, which its resolver could not find.
    # Keep retrieval, parent expansion and resolution real; only external model calls are faked.
    monkeypatch.setattr(pipeline, "retrieve", retrieval.retrieve)
    monkeypatch.setattr(pipeline, "expand_with_parent_context", retrieval.expand_with_parent_context)
    monkeypatch.setattr(retrieval, "rerank_scored", lambda query, chunks: [(c, 1.0) for c in chunks])
    monkeypatch.setattr(pipeline, "corrective_retrieve", lambda objective, chunks, re_retrieve:
                        CorrectiveResult(re_retrieve("Sorting"), "high", 1))
    from app.generation.content import ContentOutput, resolve_citations
    monkeypatch.setattr(pipeline, "generate_content", lambda lo, chunks, **kwargs: (
        resolve_citations(ContentOutput(content="\n".join(
            f"{c.content} [S{i + 1}]" for i, c in enumerate(chunks))), chunks), None,
    ))
    question_sources = []

    def question(db, course_id, query, lo, mode="quiz", scope=None, **kwargs):
        question_sources.extend(retrieval.retrieve(db, course_id, query, scope=scope))
        return {"passed": False}

    monkeypatch.setattr(pipeline, "generate_quiz_question", question)
    db = SessionLocal()
    try:
        pdf = db.get(Document, document_world["doc"])
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
        retrieval.invalidate_sparse_cache(document_world["course"])
        db.close()




@pytest.mark.parametrize("quality_pass", [True, False])
def test_the_job_step_without_a_section_generates_from_the_whole_document(document_world, seen, quality_pass):
    seen["question_pass"] = quality_pass
    db = SessionLocal()
    try:
        job = Job(
            job_type=JobType.module_generation,
            step="generate",
            status=JobStatus.running,
            document_id=document_world["doc"],
        )
        db.add(job)
        db.commit()
        job_id = job.id

        pipeline.run_module_generation_step(job)

        db.expire_all()
        finished = db.get(Job, job_id)
        assert finished.status == JobStatus.done
        assert db.query(Module).filter(Module.document_id == document_world["doc"]).count() == 1
        if not quality_pass:
            module = db.get(Module, finished.module_id)
            checked = [c for c in module.components if c.type.value in ("quiz", "practice")]
            assert all(not q["passed"] and q["fail_reason"] == "C and D both describe abstraction"
                       and q["correction_retry_count"] == 1
                       for c in checked for q in c.content["questions"])
        assert all(s is not None and s.document_id == document_world["doc"] for s in seen["retrieve_scopes"])
    finally:
        db.close()


@pytest.mark.parametrize("mode", ["whole" , "cached", "control"])
def test_every_generation_provider_call_releases_worker_transactions(document_world, monkeypatch, mode):
    from sqlalchemy import select, text

    from app.generation import content, example, objective, planner, quiz_graph, relevance, slides
    from app.generation.content import ContentVerification, LessonText, LessonVerification
    from app.generation.objective import LearningObjective
    from app.generation.quiz_graph import AnswerabilityCheck, GroundednessAnswerabilityCheck, OptionReview
    from app.ingestion import embeddings
    from tests.conftest import _offline_embedding
    from tests.integration.generation_helpers import OUTPUTS, passing_check

    calls = []

    def assert_released(kind):
        with SessionLocal() as probe:
            assert not probe.execute(text(
                "SELECT pid FROM pg_stat_activity WHERE datname = current_database() AND usename = 'app_worker' "
                "AND xact_start IS NOT NULL"
            )).all(), kind
            probe.execute(text("SET LOCAL lock_timeout = '500ms'"))
            probe.execute(select(Document).where(Document.id == document_world["doc"]).with_for_update())
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
        if response_model in (GroundednessAnswerabilityCheck, AnswerabilityCheck, OptionReview):
            return passing_check(response_model)
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
            db.get(Document, document_world["doc"]).planner_concepts = [
                {**concept.model_dump(), "output_language": "vi"}
                for concept in OUTPUTS[PlannerOutput].concepts
            ]
        if mode == "control":
            lo = OUTPUTS[LearningObjective]
            paired = Module(document_id=document_world["doc"], course_id=document_world["course"],
                            title="Khái niệm", learning_objective=lo.objective_text,
                            bloom_level=lo.bloom_level, sub_objectives=lo.sub_objectives)
            db.add(paired)
            db.flush()
            payload = {"use_retrieval": False, "paired_module_id": str(paired.id)}
        job = Job(job_type=JobType.module_generation, step="generate", status=JobStatus.running,
                  document_id=document_world["doc"],
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


def test_provider_failure_leaves_no_partial_module_and_keeps_retry_policy(document_world, seen, monkeypatch):
    from app.jobs.queue import handle_job_error

    def unavailable(*args, **kwargs):
        raise RuntimeError("LLM gateway unavailable")

    monkeypatch.setattr(pipeline, "generate_slides", unavailable)
    with SessionLocal() as db:
        job = Job(job_type=JobType.module_generation, step="generate", status=JobStatus.running,
                  document_id=document_world["doc"])
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
        assert db.query(Module).filter(Module.document_id == document_world["doc"]).count() == 0


def test_the_module_and_its_job_being_done_are_one_commit(document_world, seen, monkeypatch):
    """A worker killed right after the module is saved must not leave the job running: the sweeper would
    requeue it and the whole paid generation would run again into a second module."""
    generate = pipeline.run_module_generation

    def killed_after_saving(*args, **kwargs):
        generate(*args, **kwargs)
        raise SystemExit("worker killed")

    monkeypatch.setattr(pipeline, "run_module_generation", killed_after_saving)
    with SessionLocal() as db:
        job = Job(job_type=JobType.module_generation, step="generate", status=JobStatus.running,
                  document_id=document_world["doc"])
        db.add(job)
        db.commit()
        with pytest.raises(SystemExit):
            pipeline.run_module_generation_step(job)
        db.expire_all()
        module = db.query(Module).filter(Module.document_id == document_world["doc"]).one()
        assert (job.status, job.module_id) == (JobStatus.done, module.id)


def test_a_job_run_again_after_a_failure_pays_only_for_the_steps_not_yet_done(document_world, seen, monkeypatch):
    """A worker stopped part way, or a step that failed, used to make the module again from the
    objective: every model call that had succeeded was paid for a second time."""
    calls = {"objective": 0, "content": 0, "example": 0, "quiz": []}
    fail = {"example": True, "question": "second"}

    def counted(name, real):
        def call(*args, **kwargs):
            calls[name] += 1
            if fail.get(name):
                fail[name] = False
                raise RuntimeError(f"{name} stopped")
            return real(*args, **kwargs)
        return call

    for name, attr in (("objective", "generate_learning_objective"), ("content", "generate_content"),
                       ("example", "generate_example")):
        monkeypatch.setattr(pipeline, attr, counted(name, getattr(pipeline, attr)))
    real_quiz = pipeline.generate_quiz_question

    def quiz(db, course_id, query, *args, **kwargs):
        calls["quiz"].append(query)
        if fail["question"] == query:
            fail["question"] = None
            raise RuntimeError("question stopped")
        return real_quiz(db, course_id, query, *args, **kwargs)
    monkeypatch.setattr(pipeline, "generate_quiz_question", quiz)

    db = SessionLocal()
    try:
        job = Job(job_type=JobType.module_generation, step="generate", status=JobStatus.running,
                  document_id=document_world["doc"])
        db.add(job)
        db.commit()
        for stop in ("example stopped", "question stopped"):
            with pytest.raises(RuntimeError, match=stop):
                pipeline.run_module_generation_step(job)
        pipeline.run_module_generation_step(job)

        db.expire_all()
        assert db.get(Job, job.id).status == JobStatus.done
        # Each step was paid for once, except the two that failed; of the questions, only the one that failed was asked again.
        assert (calls["objective"], calls["content"], calls["example"]) == (1, 1, 2)
        assert sorted(calls["quiz"]) == sorted(["first", "second", "Sorting: how to sort", "second"])
        module = db.get(Module, db.get(Job, job.id).module_id)
        quiz_part = next(c for c in module.components if c.type == ComponentType.quiz)
        assert len(quiz_part.questions) == 2
    finally:
        db.close()
