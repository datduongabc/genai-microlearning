import { Fragment, useEffect, useState } from "react";
import { apiErrorMessage, listFailedJobs, retryFailedJobs, type AdminCounts, type FailedJobInfo } from "../api";

const JOB_TYPE_LABEL: Record<FailedJobInfo["job_type"], string> = {
  document_pipeline: "Xử lý tài liệu",
  module_generation: "Sinh mô-đun",
};

// The failed jobs of every lecturer: job data, the document's file name and the course name,
// never what is inside them. Retry one, or pick several and retry them together.
export function AdminFailuresPanel({ counts, onChange }: { counts: AdminCounts | null; onChange: () => void }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [jobs, setJobs] = useState<FailedJobInfo[] | null>(null);
  const [filter, setFilter] = useState<FailedJobInfo["job_type"] | "all">("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Bumped to load the list again (refresh button, after a retry), so the effect below is the only loader.
  const [reloads, setReloads] = useState(0);
  const reload = () => {
    setReloads((count) => count + 1);
    onChange();
  };

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

  function toggleExpanded(id: string) {
    setExpanded((current) => {
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
      const videoOnly = Object.keys(result.reasons).length;
      const stale = result.skipped.length - videoOnly;
      const skipped =
        (stale > 0 ? ` ${stale} tác vụ bị bỏ qua vì không còn ở trạng thái lỗi.` : "") +
        (videoOnly > 0 ? ` ${videoOnly} tác vụ video chỉ chạy lại được từ trình sửa video của giảng viên.` : "");
      setNotice(`Đã đưa ${result.retried.length} tác vụ vào hàng đợi chạy lại.${skipped}`);
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
      <div className="admin-toolbar">
        <div className="segmented" role="group" aria-label="Loại tác vụ">
          {(["all", "document_pipeline", "module_generation"] as const).map((type) => <button key={type}
            aria-pressed={filter === type} onClick={() => setFilter(type)}>{type === "all" ? "Tất cả" : JOB_TYPE_LABEL[type]}
            {type !== "all" && counts && ` (${counts.failed_jobs[type] ?? 0})`}</button>)}
        </div>
        <div className="flex gap-3"><button onClick={reload} className="ui-button" aria-label="Làm mới">↻</button>
          <button onClick={() => handleRetry([...selected])} disabled={retrying || selected.size === 0} className="ui-button primary">
            {selected.size === 0 ? "Thử lại đã chọn" : `Thử lại ${selected.size} tác vụ đã chọn`}
          </button>
        </div>
      </div>

      {notice && <p className="mb-3 text-sm text-green-800">{notice}</p>}
      {error && <p className="mb-3 text-sm text-red-700">{error}</p>}

      {jobs === null ? (
        !error && <p className="text-sm text-gray-500">Đang tải...</p>
      ) : jobs.length === 0 ? (
        <p className="rounded-[3px] border border-gray-200 p-4 text-sm text-gray-500">
          Không có tác vụ nào thất bại.
        </p>
      ) : (
        <div className="jobs-table ui-card">
          <table><thead><tr>
            <th><input type="checkbox" aria-label="Chọn tất cả" checked={allPicked}
              onChange={() => setSelected(allPicked ? new Set() : new Set(jobs.map((j) => j.id)))} /></th>
            <th>Tài liệu</th><th>Loại tác vụ</th><th>Người dùng</th><th>Thời gian</th><th>Lỗi</th><th><span className="sr-only">Thao tác</span></th>
          </tr></thead><tbody>
            {jobs.map((job) => <Fragment key={job.id}>
              <tr>
                <td><input type="checkbox" aria-label={`Chọn ${job.document_filename}`} checked={selected.has(job.id)} onChange={() => toggle(job.id)} /></td>
                <td><div className="font-semibold truncate" title={job.document_filename}>{job.document_filename}</div><div className="small muted truncate" title={job.course_name}>{job.course_name}</div></td>
                <td className="small">{JOB_TYPE_LABEL[job.job_type]}</td>
                <td className="small">{job.owner_name}</td>
                <td className="small muted">{new Date(job.created_at).toLocaleString("vi-VN")}</td>
                <td><p className="small muted truncate font-mono">{job.last_error || "—"}</p></td>
                <td><div className="flex gap-2">
                  <button className="ui-button" aria-label={`Thử lại ${job.document_filename}`} disabled={retrying} onClick={() => handleRetry([job.id])}>↻</button>
                  <button className="expand-job" aria-label={`Chi tiết ${job.document_filename}`} aria-expanded={expanded.has(job.id)}
                    aria-controls={expanded.has(job.id) ? `job-${job.id}` : undefined} onClick={() => toggleExpanded(job.id)}>{expanded.has(job.id) ? "⌃" : "⌄"}</button>
                </div></td>
              </tr>
              {expanded.has(job.id) && <tr id={`job-${job.id}`}><td colSpan={7} className="job-detail">
                {job.last_error && <pre>{job.last_error}</pre>}
                <p className="small muted">Bước: {job.step} · {job.attempts}/{job.max_attempts} lần thử · {job.owner_email}</p>
              </td></tr>}
            </Fragment>)}
          </tbody></table>
        </div>
      )}
    </div>
  );
}
