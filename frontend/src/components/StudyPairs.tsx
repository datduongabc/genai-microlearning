import { useCallback, useEffect, useRef, useState } from "react";
import { addMedia, addPair, listMediaCandidates, listPairCandidates, type MediaCandidate, type PairCandidate } from "../api/studies";
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
    <h2 id="study-pairs-title">Thêm nội dung</h2>
    <h3 className="results-subtitle">Mô-đun</h3>
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
    <MediaRows studyId={studyId} onAdded={onAdded} />
  </section>;
}

// The admin's flashcard sets and videos: each joins as a packet of its own, with nothing generated.
function MediaRows({ studyId, onAdded }: { studyId: string; onAdded: () => void }) {
  const [rows, setRows] = useState<MediaCandidate[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    listMediaCandidates(studyId).then((data) => { if (active) setRows(data); })
      .catch((e) => { if (active) setError(apiErrorMessage(e, "Không tải được flashcard và video.")); });
    return () => { active = false; };
  }, [studyId]);

  async function add(row: MediaCandidate) {
    setBusy(row.id);
    try {
      await addMedia(studyId, row.kind, row.id);
      setRows((current) => current?.map((r) => r.id === row.id ? { ...r, state: "in_study" } : r) ?? null);
      notifyDone(`Đã thêm "${row.title}".`);
      onAdded();
    } catch (e) { notifyFailed(apiErrorMessage(e, `Không thêm được "${row.title}".`)); }
    finally { setBusy(null); }
  }

  if (error) return <p role="alert">{error}</p>;
  return <>{(["flashcards", "video"] as const).map((kind) => {
    const list = rows?.filter((r) => r.kind === kind);
    return <div key={kind}>
      <h3 className="results-subtitle">{kind === "flashcards" ? "Flashcard" : "Video"}</h3>
      {!list ? <p className="muted">Đang tải…</p> : list.length === 0
        ? <p className="small muted">{kind === "flashcards" ? "Chưa có bộ flashcard nào." : "Chưa có video nào."}</p>
        : <ul className="reference-files" aria-label={kind === "flashcards" ? "Flashcard có thể thêm" : "Video có thể thêm"}>
          {list.map((row) => <li key={row.id}>
            <div className="min-w-0">
              <div className="font-semibold [overflow-wrap:anywhere]">{row.title}</div>
              <div className="small muted [overflow-wrap:anywhere]">{row.course_name}</div>
            </div>
            <span />
            {row.state === "in_study" ? <span className="small muted">Đã thêm</span>
              : <button type="button" className="ui-button" disabled={busy !== null} onClick={() => void add(row)}>
                {busy === row.id ? "Đang thêm…" : "Thêm"}
              </button>}
          </li>)}
        </ul>}
    </div>;
  })}</>;
}
