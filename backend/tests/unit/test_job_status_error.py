"""What the job status routes show the lecturer: never the raw exception of an internal failure."""

from types import SimpleNamespace

from app.jobs.failures import SYSTEM_FAILURE_MESSAGE, status_error
from app.models import JobStatus

RAW = "ConnectError: http://gateway:4000/v1/embeddings refused"


def _job(status, system_failure, last_error=RAW):
    return SimpleNamespace(status=status, system_failure=system_failure, last_error=last_error)


def test_a_job_waiting_for_its_next_try_shows_no_error():
    assert status_error(_job(JobStatus.queued, False)) is None
    assert status_error(_job(JobStatus.running, False)) is None


def test_a_system_failure_shows_the_general_message():
    assert status_error(_job(JobStatus.failed, True)) == SYSTEM_FAILURE_MESSAGE


def test_a_failure_caused_by_the_input_shows_its_message():
    assert status_error(_job(JobStatus.failed, False, "Tài liệu không có chữ.")) == "Tài liệu không có chữ."
