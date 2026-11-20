"""The file raters check a packet against, taken from the course document the packet was made
from, so the admin does not upload it again."""
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Document, StudyFile, StudyItem
from app.storage import copy_file, study_file_key, study_file_store, upload_key, upload_store

log = logging.getLogger(__name__)


def attach_source(db: Session, study_id: uuid.UUID, items: list[StudyItem], document_id: uuid.UUID) -> None:
    """Gives the items without a reference file a copy of their document, copied once per study.
    A document uploaded before originals were kept has no file left, and the items keep none."""
    if all(item.reference_file_id for item in items):
        return
    file = db.scalar(select(StudyFile).where(StudyFile.study_id == study_id, StudyFile.document_id == document_id,
                                             StudyFile.status == "ready"))
    if file is None:
        doc = db.get(Document, document_id)
        source, key = upload_store(), upload_key(document_id, doc.filename)
        size = source.size(key)
        if size is None:
            return
        file = StudyFile(study_id=study_id, filename=doc.filename, size=size, status="ready", document_id=document_id)
        db.add(file)
        db.flush()
        try:
            copy_file(source, key, study_file_store(), study_file_key(file.id, doc.filename))
        except Exception:
            # The packet still joins; the admin can attach a file by hand.
            log.exception("Could not copy document %s into study %s", document_id, study_id)
            db.delete(file)
            db.flush()
            return
    for item in items:
        item.reference_file_id = item.reference_file_id or file.id
