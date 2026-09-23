import { useEffect, useState } from "react";
import { AppShell } from "../components/AppShell";
import { listFailedJobs, type FailedJobInfo } from "../api";

const JOB_TYPE_LABEL: Record<FailedJobInfo["job_type"], string> = {
  document_pipeline: "Xử lý tài liệu",
  module_generation: "Sinh mô-đun",
};

export function MonitoringPage() {
  const [jobs, setJobs] = useState<FailedJobInfo[]>([]);
  const [filter, setFilter] = useState<FailedJobInfo["job_type"] | "all">("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Bumped by the refresh button to load again with an unchanged filter.
  const [reloads, setReloads] = useState(0);

  // The page shows "loading" and clears the last error in the handler that asks
  // for a load (below); the effect only fetches, and sets state from promise
  // callbacks so a result arriving after a newer request is dropped.
  useEffect(() => {
    let cancelled = false;
    listFailedJobs(filter === "all" ? undefined : filter)
      .then((list) => {
        if (!cancelled) setJobs(list);
      })
      .catch(() => {
        if (!cancelled) setError("Không tải được danh sách lỗi.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [filter, reloads]);

  function startLoading() {
    setLoading(true);
    setError(null);
  }

  function handleFilterChange(next: FailedJobInfo["job_type"] | "all") {
    startLoading();
    setFilter(next);
  }

  function handleRefresh() {
    startLoading();
    setReloads((count) => count + 1);
  }

  return (
    <AppShell title="Theo dõi lỗi pipeline">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <label htmlFor="job-type-filter" className="text-sm text-gray-600">
            Loại job:
          </label>
          <select
            id="job-type-filter"
            value={filter}
            onChange={(e) => handleFilterChange(e.target.value as FailedJobInfo["job_type"] | "all")}
            className="rounded-[3px] border border-gray-300 px-2 py-1 text-sm"
          >
            <option value="all">Tất cả</option>
            <option value="document_pipeline">Xử lý tài liệu</option>
            <option value="module_generation">Sinh mô-đun</option>
          </select>
        </div>
        <button
          onClick={handleRefresh}
          className="rounded-[3px] border border-gray-300 px-3 py-1.5 text-sm hover:bg-gray-50"
        >
          Làm mới
        </button>
      </div>

      {error && <p className="mb-4 text-sm text-red-700">{error}</p>}

      {loading ? (
        <p className="text-sm text-gray-500">Đang tải...</p>
      ) : jobs.length === 0 ? (
        <p className="rounded-[3px] border border-gray-200 p-4 text-sm text-gray-500">
          Không có job nào thất bại — mọi thứ đang chạy tốt.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-gray-200">
          {jobs.map((job) => (
            <li key={job.id} className="py-4">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <span className="rounded-[3px] bg-red-50 px-2 py-0.5 text-xs font-semibold text-red-700">
                    {JOB_TYPE_LABEL[job.job_type]} — {job.step}
                  </span>
                  <div className="mt-1 text-sm font-medium">
                    {job.course_name} / {job.document_filename}
                  </div>
                </div>
                <div className="shrink-0 text-right text-xs text-gray-500">
                  <div>{new Date(job.created_at).toLocaleString("vi-VN")}</div>
                  <div>
                    {job.attempts}/{job.max_attempts} lần thử
                  </div>
                </div>
              </div>
              {job.last_error && (
                <pre className="mt-2 overflow-x-auto rounded-[3px] bg-gray-50 p-2 text-xs whitespace-pre-wrap text-gray-700">
                  {job.last_error}
                </pre>
              )}
            </li>
          ))}
        </ul>
      )}
    </AppShell>
  );
}
