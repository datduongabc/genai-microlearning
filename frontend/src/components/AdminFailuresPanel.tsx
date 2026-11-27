import { ChevronDown, FileWarning, RefreshCw, ServerCrash } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { notifyDone, notifyFailed } from "../lib/notify";
import { apiErrorMessage, listFailedJobs, retryFailedJobs, type AdminCounts, type FailedJobInfo } from "../api";

const JOB_TYPE_LABEL: Record<FailedJobInfo["job_type"], string> = {
  document_pipeline: "Xử lý tài liệu",
  module_generation: "Tạo mô-đun",
  flashcard_generation: "Tạo flashcard",
  video_script: "Viết lời thoại",
  video_images: "Vẽ hình",
  video_render: "Dựng video",
};

// The filters a lecturer would name: a video's three steps are one kind of work.
const FILTERS = {
  document: { label: "Xử lý tài liệu", types: ["document_pipeline"] },
  module: { label: "Tạo mô-đun", types: ["module_generation"] },
  flashcard: { label: "Tạo flashcard", types: ["flashcard_generation"] },
  video: { label: "Tạo video", types: ["video_script", "video_images", "video_render"] },
} as const satisfies Record<string, { label: string; types: readonly FailedJobInfo["job_type"][] }>;
type Filter = keyof typeof FILTERS | "all";

// The failed jobs of every lecturer: job data, the document's file name and the course name,
// never what is inside them. Retry one, or pick several and retry them together, once the cause is
// fixed: only what the system failed. A failure in the lecturer's input is theirs to fix and retry.
export function AdminFailuresPanel({ counts, onChange }: { counts: AdminCounts | null; onChange: () => void }) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [jobs, setJobs] = useState<FailedJobInfo[] | null>(null);
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [retrying, setRetrying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Bumped to load the list again (refresh button, after a retry), so the effect below is the only loader.
  const [reloads, setReloads] = useState(0);
  const reload = () => {
    setReloads((count) => count + 1);
    onChange();
  };

  // Sets state only from promise callbacks, and drops an answer that arrives after a newer request.
  useEffect(() => {
    let cancelled = false;
    // The server filters, so the list agrees with the count however many failures there are.
    listFailedJobs(filter === "all" ? undefined : FILTERS[filter].types).then(
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
    try {
      const result = await retryFailedJobs(ids);
      const explained = Object.keys(result.reasons).length;
      const stale = result.skipped.length - explained;
      // One line per reason the server gave, with how many jobs it held back.
      const byReason = new Map<string, number>();
      Object.values(result.reasons).forEach((reason) => byReason.set(reason, (byReason.get(reason) ?? 0) + 1));
      const skipped =
        (stale > 0 ? ` ${stale} tác vụ được bỏ qua vì không còn lỗi.` : "") +
        [...byReason].map(([reason, count]) => ` ${count} tác vụ: ${reason}`).join("");
      notifyDone(`Đã chạy lại ${result.retried.length} tác vụ.${skipped}`);
      reload();
    } catch (err) {
      notifyFailed(apiErrorMessage(err, "Thử lại thất bại"));
    } finally {
      setRetrying(false);
    }
  }

  const retriable = (jobs ?? []).filter((j) => j.system_failure);
  const allPicked = retriable.length > 0 && retriable.every((j) => selected.has(j.id));

  return (
    <div>
      <div className="admin-toolbar">
        <div className="segmented" role="group" aria-label="Loại tác vụ">
          <button aria-pressed={filter === "all"} onClick={() => setFilter("all")}>Tất cả</button>
          {(Object.keys(FILTERS) as (keyof typeof FILTERS)[]).map((key) => <button key={key}
            aria-pressed={filter === key} onClick={() => setFilter(key)}>{FILTERS[key].label}
            {counts && <>{" "}<span className="count">{FILTERS[key].types.reduce((sum, t) => sum + (counts.failed_jobs[t] ?? 0), 0)}</span></>}</button>)}
        </div>
        <div className="flex gap-3"><button onClick={reload} className="ui-button" aria-label="Làm mới"><RefreshCw aria-hidden="true" className="size-4" /></button>
          <button onClick={() => handleRetry([...selected])} disabled={retrying || selected.size === 0} className="ui-button primary">
            {selected.size === 0 ? "Thử lại đã chọn" : `Thử lại ${selected.size} tác vụ đã chọn`}
          </button>
        </div>
      </div>

      {error && <p className="mb-3 text-sm danger-text">{error}</p>}

      {jobs === null ? (
        !error && <p className="text-sm muted">Đang tải…</p>
      ) : jobs.length === 0 ? (
        <p className="rounded-[3px] border border-gray-200 p-4 text-sm muted">
          Không có tác vụ nào thất bại.
        </p>
      ) : (
        <div className="jobs-table ui-card">
          <table><thead><tr>
            <th><label className="select-all"><input type="checkbox" aria-label="Chọn tất cả" checked={allPicked}
              disabled={retriable.length === 0}
              onChange={() => setSelected(allPicked ? new Set() : new Set(retriable.map((j) => j.id)))} /><span aria-hidden="true">Chọn tất cả</span></label></th>
            <th>Tài liệu</th><th>Loại tác vụ</th><th>Người dùng</th><th>Thời gian</th><th>Nguyên nhân</th><th>Lỗi</th><th><span className="sr-only">Thao tác</span></th>
          </tr></thead><tbody>
            {jobs.map((job) => <Fragment key={job.id}>
              <tr>
                <td>{job.system_failure && <input type="checkbox" aria-label={`Chọn ${job.document_filename}`} checked={selected.has(job.id)} onChange={() => toggle(job.id)} />}</td>
                <td><div className="font-semibold truncate" title={job.document_filename}>{job.document_filename}</div><div className="small muted truncate" title={job.course_name}>{job.course_name}</div></td>
                <td className="small">{JOB_TYPE_LABEL[job.job_type]}</td>
                <td className="small">{job.owner_name}</td>
                <td className="small muted">{new Date(job.created_at).toLocaleString("vi-VN")}</td>
                <td className="small">{job.system_failure
                  ? <span className="failure-cause" data-cause="system"><ServerCrash aria-hidden="true" className="size-3.5" />Sự cố hệ thống</span>
                  : <span className="failure-cause" data-cause="input"><FileWarning aria-hidden="true" className="size-3.5" />Nội dung giảng viên</span>}</td>
                <td><p className={`small truncate ${job.last_error ? "danger-text" : "muted"}`}>{job.last_error || "—"}</p></td>
                <td><div className="flex gap-2">
                  {job.system_failure
                    ? <button className="ui-button" aria-label={`Thử lại ${job.document_filename}`} disabled={retrying} onClick={() => handleRetry([job.id])}>Thử lại</button>
                    : <span className="small muted handled-by" title="Giảng viên tự sửa nội dung rồi thử lại">Giảng viên xử lý</span>}
                  <button className="expand-job" aria-label={`Chi tiết ${job.document_filename}`} aria-expanded={expanded.has(job.id)}
                    aria-controls={expanded.has(job.id) ? `job-${job.id}` : undefined} onClick={() => toggleExpanded(job.id)}><ChevronDown aria-hidden="true" className="size-4" /></button>
                </div></td>
              </tr>
              {expanded.has(job.id) && <tr id={`job-${job.id}`}><td colSpan={8} className="job-detail">
                {job.last_error && <pre>{job.last_error}</pre>}
                <p className="small muted">Bước {job.step}, thử {job.attempts}/{job.max_attempts} lần, {job.owner_email}</p>
              </td></tr>}
            </Fragment>)}
          </tbody></table>
        </div>
      )}
    </div>
  );
}
