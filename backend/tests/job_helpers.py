"""Running a pipeline step from a test the way the worker does."""
from app.models import Job, JobStatus


def claimed(db, job_id, worker_id: str = "test-worker") -> Job:
    """The job, claimed by `worker_id` as claim_next_job() would. A step writes its result only
    while its worker holds the job (app/jobs/queue.py, hold), and the worker claims it again before
    every step, because each step puts it back in the queue."""
    job = db.get(Job, job_id)
    job.status, job.locked_by = JobStatus.running, worker_id
    db.commit()
    return job
