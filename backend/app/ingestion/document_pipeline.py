"""Xử lý tài liệu như 3 job step tuần tự (extract -> chunk -> embed) thay vì
1 HTTP request đồng bộ (upload_document() cũ, documents.py) -- cần thiết để
audio/video (transcribe qua Whisper) không giữ request
HTTP treo cả phiên, và để retry tự động khi gặp lỗi tạm thời (RateLimitError)
thay vì người dùng phải tải lại file."""

import json
import os
import uuid
from dataclasses import asdict

from app.db import WorkerSessionLocal
from app.ingestion.chunker import Chunk as ChunkerChunk
from app.ingestion.chunker import chunk_units, document_title
from app.ingestion.document_readers import DocumentUnit, read_document
from app.ingestion.embeddings import embed_texts
from app.ingestion.outline import OutlineEntry, extract_outline
from app.ingestion.section_ranges import SectionSpec, assign_chunk_ranges, chunk_anchor
from app.models import Chunk as ChunkModel
from app.models import Document, DocumentKind, DocumentSection, DocumentStatus, Job, JobStatus
from app.retrieval.search import invalidate_sparse_cache


def run_document_pipeline_step(job) -> None:
    if job.step == "extract":
        _run_extract(job)
    elif job.step == "chunk":
        _run_chunk(job)
    elif job.step == "embed":
        _run_embed(job)
    else:
        raise ValueError(f"Unknown document_pipeline step: {job.step}")


def _run_extract(job) -> None:
    db = WorkerSessionLocal()
    try:
        current_job = db.get(Job, job.id)
        doc = db.get(Document, current_job.document_id)
        if doc is None:
            raise ValueError(f"Document {current_job.document_id} not found")
        storage_path = current_job.payload["storage_path"]
        course_id, filename = str(doc.course_id), doc.filename

        doc.status = DocumentStatus.processing
        db.commit()

        units = read_document(storage_path, course_id=course_id, source_id=filename)
        doc.raw_text = "\n\n".join(u.text for u in units)

        next_payload = {**current_job.payload, "units": [asdict(u) for u in units]}
        # Only a textbook is offered for picking sections; slides and notes keep
        # the whole-document flow and never pay for outline detection.
        if doc.kind == DocumentKind.textbook:
            next_payload["outline"] = [asdict(e) for e in extract_outline(storage_path, units)]

        current_job.step = "chunk"
        current_job.status = JobStatus.queued
        current_job.payload = next_payload
        current_job.attempts = 0
        db.commit()
    finally:
        db.close()


def _run_chunk(job) -> None:
    db = WorkerSessionLocal()
    try:
        current_job = db.get(Job, job.id)
        raw_units = current_job.payload.get("units", [])
        if not raw_units:
            raise ValueError(f"chunk: no units in payload for document {current_job.document_id}")
        units = [DocumentUnit(**u) for u in raw_units]
        doc = db.get(Document, current_job.document_id)
        chunks = chunk_units(units, title=document_title(doc.filename) if doc is not None else None)

        next_payload = {**current_job.payload, "chunks": [asdict(c) for c in chunks]}
        del next_payload["units"]
        outline = [OutlineEntry(**e) for e in next_payload.pop("outline", [])]
        if outline:
            sections = assign_chunk_ranges(
                outline,
                [chunk_anchor(c.locator) for c in chunks],
                anchors_are_pages=current_job.payload["storage_path"].lower().endswith(".pdf"),
            )
            next_payload["sections"] = [asdict(s) for s in sections]
        current_job.step = "embed"
        current_job.status = JobStatus.queued
        current_job.payload = next_payload
        current_job.attempts = 0
        db.commit()
    finally:
        db.close()


def _run_embed(job) -> None:
    db = WorkerSessionLocal()
    try:
        current_job = db.get(Job, job.id)
        raw_chunks = current_job.payload.get("chunks", [])
        if not raw_chunks:
            raise ValueError(f"embed: no chunks in payload for document {current_job.document_id}")
        chunks = [ChunkerChunk(**c) for c in raw_chunks]

        doc = db.get(Document, current_job.document_id)
        if doc is None:
            raise ValueError(f"Document {current_job.document_id} not found")

        # The provider may make this wait for minutes (its limit is per minute): end the transaction
        # the reads above opened instead of holding it, and a connection with it, all that time.
        db.rollback()
        embeddings = embed_texts([c.content for c in chunks])
        for i, (c, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            db.add(ChunkModel(
                document_id=doc.id,
                course_id=doc.course_id,
                chunk_index=i,
                content=c.content,
                source_ref=json.dumps(c.locator, ensure_ascii=False),
                embedding=embedding,
            ))

        # Parents come before their children in position order, so each parent
        # already has an id when a child needs it. Everything commits together
        # with the chunks the ranges refer to.
        section_ids: dict[int, uuid.UUID] = {}
        for raw in current_job.payload.get("sections", []):
            spec = SectionSpec(**raw)
            section = DocumentSection(
                document_id=doc.id,
                parent_id=section_ids.get(spec.parent_position),
                title=spec.title,
                level=spec.level,
                position=spec.position,
                page_start=spec.page_start,
                page_end=spec.page_end,
                first_chunk_index=spec.first_chunk_index,
                last_chunk_index=spec.last_chunk_index,
            )
            db.add(section)
            db.flush()
            section_ids[spec.position] = section.id

        course_id = doc.course_id
        storage_path = current_job.payload.get("storage_path")

        doc.status = DocumentStatus.embedded
        current_job.status = JobStatus.done
        # The chunk text now lives in the chunks table; keeping it in the job
        # too would store every document a second time.
        current_job.payload = {}
        db.commit()
    finally:
        db.close()

    invalidate_sparse_cache(course_id)
    if storage_path and os.path.exists(storage_path):
        os.remove(storage_path)
