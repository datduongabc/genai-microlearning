import { useEffect, useState } from "react";
import { apiErrorMessage, listFailedJobs, retryFailedJobs, type FailedJobInfo } from "../api";

const JOB_TYPE_LABEL: Record<FailedJobInfo["job_type"], string> = {
  document_pipeline: "Xử lý tài liệu",
  module_generation: "Sinh mô-đun",
};

// The failed jobs of every lecturer: job data, the document's file name and the course name,
// never what is inside them. Retry one, or pick several and retry them together.
export function AdminFailuresPanel() {
  const [jobs, setJobs] = useState<FailedJobInfo[] | null>(null);
  const [filter, setFilter] = useState<FailedJobInfo["job_type"] | "all">("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Bumped to load the list again (refresh button, after a retry), so the effect below is the only loader.
  const [reloads, setReloads] = useState(0);
  const reload = () => setReloads((count) => count + 1);

  // Sets state only from promise callbacks, and drops an answer that arrives after a newer request.
  useEffect(() => {
    let cancelled = false;
    listFailedJobs(filter === "all" ? undefined : filter).then(
      (list) => {
        if (cancelled) return;
        setJobs(list);
        // What is no longer failed (retried here or by its lecturer) cannot stay picked.
        setSelected((current) => new Set(list.filter((j) => current.has(j.id)).map((j) => j.id)));
        setError(null);
      },
      (err) => {
        if (!cancelled) setError(apiErrorMessage(err, "Không tải được danh sách lỗi."));
      },
    );
    return () => {
      cancelled = true;
    };
  }, [filter, reloads]);

  function toggle(id: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (!next.delete(id)) next.add(id);
      return next;
    });
  }

  async function handleRetry(ids: string[]) {
    setRetrying(true);
    setError(null);
    setNotice(null);
    try {
      const result = await retryFailedJobs(ids);
      const skipped =
        result.skipped.length > 0 ? ` ${result.skipped.length} job bị bỏ qua vì không còn ở trạng thái lỗi.` : "";
      setNotice(`Đã đưa ${result.retried.length} job vào hàng đợi chạy lại.${skipped}`);
      reload();
    } catch (err) {
      setError(apiErrorMessage(err, "Thử lại thất bại"));
    } finally {
      setRetrying(false);
    }
  }

  const allPicked = jobs !== null && jobs.length > 0 && jobs.every((j) => selected.has(j.id));

  return (
    <div>
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <label htmlFor="job-type-filter" className="text-sm text-gray-600">
            Loại job:
          </label>
          <select
            id="job-type-filter"
            value={filter}
            onChange={(e) => setFilter(e.target.value as FailedJobInfo["job_type"] | "all")}
            className="rounded-[3px] border border-gray-300 px-2 py-1 text-sm"
          >
            <option value="all">Tất cả</option>
            <option value="document_pipeline">Xử lý tài liệu</option>
            <option value="module_generation">Sinh mô-đun</option>
          </select>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={reload}
            className="rounded-[3px] border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50"
          >
            Làm mới
          </button>
          <button
            onClick={() => handleRetry([...selected])}
            disabled={retrying || selected.size === 0}
            className="rounded-[3px] bg-terracotta px-3 py-1.5 text-sm font-semibold text-white hover:bg-terracotta-dark disabled:opacity-60"
          >
            {selected.size === 0 ? "Thử lại đã chọn" : `Thử lại ${selected.size} job đã chọn`}
          </button>
        </div>
      </div>

      {notice && <p className="mb-3 text-sm text-green-800">{notice}</p>}
      {error && <p className="mb-3 text-sm text-red-700">{error}</p>}

      {jobs === null ? (
        !error && <p className="text-sm text-gray-500">Đang tải...</p>
      ) : jobs.length === 0 ? (
        <p className="rounded-[3px] border border-gray-200 p-4 text-sm text-gray-500">
          Không có job nào thất bại — mọi thứ đang chạy tốt.
        </p>
      ) : (
        <>
          <label className="mb-2 flex items-center gap-2 text-sm text-gray-600">
            <input
              type="checkbox"
              className="size-4 accent-terracotta"
              checked={allPicked}
              onChange={() => setSelected(allPicked ? new Set() : new Set(jobs.map((j) => j.id)))}
            />
            Chọn tất cả
          </label>
          <ul className="flex flex-col divide-y divide-gray-200">
            {jobs.map((job) => (
              <li key={job.id} className="py-4">
                <div className="flex items-start gap-3">
                  <input
                    type="checkbox"
                    className="mt-1 size-4 accent-terracotta"
                    aria-label={`Chọn ${job.document_filename}`}
                    checked={selected.has(job.id)}
                    onChange={() => toggle(job.id)}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center justify-between gap-3">
                      <div className="min-w-0">
                        <span className="rounded-[3px] bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700">
                          {JOB_TYPE_LABEL[job.job_type]} — {job.step}
                        </span>
                        <div className="mt-1 break-words text-sm font-medium">
                          {job.course_name} / {job.document_filename}
                        </div>
                        <div className="text-xs text-gray-500">
                          {job.owner_name} ({job.owner_email})
                        </div>
                      </div>
                      <div className="flex shrink-0 items-center gap-4">
                        <div className="text-right text-xs text-gray-500">
                          <div>{new Date(job.created_at).toLocaleString("vi-VN")}</div>
                          <div>
                            {job.attempts}/{job.max_attempts} lần thử
                          </div>
                        </div>
                        <button
                          onClick={() => handleRetry([job.id])}
                          disabled={retrying}
                          aria-label={`Thử lại ${job.document_filename}`}
                          className="text-sm font-semibold text-terracotta hover:underline disabled:opacity-60"
                        >
                          Thử lại
                        </button>
                      </div>
                    </div>
                    {job.last_error && (
                      <pre className="mt-2 overflow-x-auto rounded-[3px] bg-gray-50 p-2 text-xs whitespace-pre-wrap text-gray-700">
                        {job.last_error}
                      </pre>
                    )}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}
