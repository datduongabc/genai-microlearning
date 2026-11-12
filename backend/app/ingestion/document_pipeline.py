"""Xử lý tài liệu như 3 job step tuần tự (extract -> chunk -> embed) thay vì
1 HTTP request đồng bộ (upload_document() cũ, documents.py) -- cần thiết để
audio/video (transcribe qua Whisper) không giữ request
HTTP treo cả phiên, và để retry tự động khi gặp lỗi tạm thời (RateLimitError)
thay vì người dùng phải tải lại file."""

import json
from dataclasses import asdict

from app.db import WorkerSessionLocal
from app.ingestion.chunker import Chunk as ChunkerChunk
from app.ingestion.chunker import chunk_units, document_title
from app.ingestion.document_readers import DocumentUnit, InputLimitError, preflight_limits, read_document
from app.ingestion.embeddings import embed_texts
from app.jobs.queue import hold
from app.models import Chunk as ChunkModel
from app.models import Document, DocumentStatus, Job, JobStatus
from app.retrieval.search import invalidate_sparse_cache
from app.storage import uploaded_file


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
        upload = uploaded_file(current_job.payload)
        if upload is None:
            raise ValueError(f"extract: no uploaded file in payload for document {doc.id}")
        store, storage_key = upload
        course_id, filename = str(doc.course_id), doc.filename

        doc.status = DocumentStatus.processing
        db.commit()

        with store.local_copy(storage_key) as path:
            try:
                # The upload request only saw the declared size: pages and duration need the file.
                preflight_limits(path)
            except InputLimitError:
                # Terminal, and retrying cannot fix the input: the file has no further use.
                store.delete(storage_key)
                raise
            units = read_document(path, course_id=course_id, source_id=filename)
        doc.raw_text = "\n\n".join(u.text for u in units if u.text)
        # Persist before rejecting an unreadable document, and keep them after job cleanup.
        doc.extraction_warnings = [w for u in units for w in u.warnings]
        if not doc.raw_text.strip():
            db.commit()
            raise ValueError("Không đọc được nội dung tài liệu; hãy tải lại bản có lớp chữ.")

        current_job = hold(db, job)
        next_payload = {**current_job.payload, "units": [asdict(u) for u in units]}
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

        current_job = hold(db, job)
        next_payload = {**current_job.payload, "chunks": [asdict(c) for c in chunks]}
        del next_payload["units"]
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

        course_id = doc.course_id
        upload = uploaded_file(current_job.payload)

        current_job = hold(db, job)
        doc.status = DocumentStatus.embedded
        current_job.status = JobStatus.done
        # The chunk text now lives in the chunks table; keeping it in the job
        # too would store every document a second time.
        current_job.payload = {}
        db.commit()
    finally:
        db.close()

    invalidate_sparse_cache(course_id)
    if upload is not None:
        store, storage_key = upload
        store.delete(storage_key)
