import { useCallback, useEffect, useRef, useState } from "react";
import { addPair, listPairCandidates, type PairCandidate } from "../api/studies";
import { apiErrorMessage } from "../api/client";
import { confirmAction } from "../lib/confirm";
import { notifyDone, notifyFailed } from "../lib/notify";

const POLL_MS = 5000;

// The admin's own RAG modules, each one click from joining the study with its no-RAG twin.
// A module whose twin is not made yet is made first; the pair joins once it is done.
export function StudyPairs({ studyId, onAdded }: { studyId: string; onAdded: () => void }) {
  const [rows, setRows] = useState<PairCandidate[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  // Asked for here and still being made: added by itself once its twin is ready.
  const waiting = useRef(new Set<string>());

  const add = useCallback(async (row: PairCandidate) => {
    setBusy(row.id);
    try {
      if (await addPair(studyId, row.id) === "added") {
        waiting.current.delete(row.id);
        notifyDone(`Đã thêm cặp "${row.title}".`);
        onAdded();
      } else {
        waiting.current.add(row.id);
      }
      setRows(await listPairCandidates(studyId));
    } catch (e) {
      waiting.current.delete(row.id);
      notifyFailed(apiErrorMessage(e, `Không thêm được "${row.title}".`));
    } finally { setBusy(null); }
  }, [studyId, onAdded]);

  useEffect(() => {
    let active = true;
    listPairCandidates(studyId).then((data) => { if (active) setRows(data); })
      .catch((e) => { if (active) setError(apiErrorMessage(e, "Không tải được danh sách mô-đun.")); });
    return () => { active = false; };
  }, [studyId]);

  const generating = rows?.some((r) => r.state === "generating") ?? false;
  useEffect(() => {
    if (!generating) return;
    const timer = window.setInterval(() => {
      listPairCandidates(studyId).then((data) => {
        setRows(data);
        for (const row of data) if (row.state === "ready" && waiting.current.has(row.id)) void add(row);
      }, () => {});
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [generating, studyId, add]);

  async function ask(row: PairCandidate) {
    // Making the twin is a paid generation, like generating a module.
    if (row.state !== "ready" && !await confirmAction({
      title: `Tạo bản không RAG cho "${row.title}"?`,
      description: "Hệ thống sinh thêm một mô-đun cùng mục tiêu học tập nhưng không dùng tài liệu, rồi thêm cả hai vào nghiên cứu.",
      action: "Tạo và thêm",
    })) return;
    await add(row);
  }

  return <section className="ui-card study-results" aria-labelledby="study-pairs-title">
    <h2 id="study-pairs-title">Thêm cặp từ mô-đun</h2>
    {error && <p role="alert">{error}</p>}
    {!rows && !error && <p className="muted">Đang tải…</p>}
    {rows?.length === 0 && <p className="small muted">Chưa có mô-đun nào.</p>}
    {rows && rows.length > 0 && <ul className="reference-files" aria-label="Mô-đun có thể thêm">
      {rows.map((row) => <li key={row.id}>
        <div className="min-w-0">
          <div className="font-semibold [overflow-wrap:anywhere]">{row.title}</div>
          <div className="small muted [overflow-wrap:anywhere]">{row.course_name}</div>
        </div>
        {row.state === "in_study" ? <span className="small muted">Đã thêm</span>
          : row.state === "generating" ? <span className="status-pill" data-status="processing">Đang tạo…</span>
          : row.state === "failed" ? <span className="status-pill" data-status="failed">Tạo thất bại</span>
          : <span />}
        {row.state === "in_study" || row.state === "generating" ? <span /> :
          <button type="button" className="ui-button" disabled={busy !== null} onClick={() => void ask(row)}>
            {busy === row.id ? "Đang thêm…" : row.state === "failed" ? "Thử lại" : "Thêm cặp"}
          </button>}
      </li>)}
    </ul>}
  </section>;
}
