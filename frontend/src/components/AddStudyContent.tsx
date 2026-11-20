import { Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { addMedia, addPair, listMediaCandidates, listPairCandidates, type MediaCandidate, type PairCandidate } from "../api/studies";
import { apiErrorMessage } from "../api/client";
import { confirmAction } from "../lib/confirm";
import { notifyDone, notifyFailed } from "../lib/notify";
import { Tabs } from "./Tabs";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";

const POLL_MS = 5000;
type Kind = "module" | "flashcards" | "video";
const TABS: { id: Kind; label: string }[] = [
  { id: "module", label: "Mô-đun" }, { id: "flashcards", label: "Flashcard" }, { id: "video", label: "Video" },
];

// The button and dialog that put the admin's own modules, flashcard sets and videos into the study.
// The module state lives here, not in the dialog, so a pair whose control is still being made is
// still added once it is done after the dialog is closed.
export function AddStudyContent({ studyId, onAdded }: { studyId: string; onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Kind>("module");
  const [modules, setModules] = useState<PairCandidate[] | null>(null);
  const [media, setMedia] = useState<MediaCandidate[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  // Asked for here and still being made: added by itself once its control is ready.
  const waiting = useRef(new Set<string>());

  const addModule = useCallback(async (row: PairCandidate) => {
    setBusy(row.id);
    try {
      if (await addPair(studyId, row.id) === "added") {
        waiting.current.delete(row.id);
        notifyDone(`Đã thêm "${row.title}".`);
        onAdded();
      } else {
        waiting.current.add(row.id);
      }
      setModules(await listPairCandidates(studyId));
    } catch (e) {
      waiting.current.delete(row.id);
      notifyFailed(apiErrorMessage(e, `Không thêm được "${row.title}".`));
    } finally { setBusy(null); }
  }, [studyId, onAdded]);

  useEffect(() => {
    let active = true;
    Promise.all([listPairCandidates(studyId), listMediaCandidates(studyId)])
      .then(([m, f]) => { if (active) { setModules(m); setMedia(f); } })
      .catch((e) => { if (active) setError(apiErrorMessage(e, "Không tải được nội dung có thể thêm.")); });
    return () => { active = false; };
  }, [studyId]);

  const generating = modules?.filter((r) => r.state === "generating") ?? [];
  useEffect(() => {
    if (generating.length === 0) return;
    const timer = window.setInterval(() => {
      listPairCandidates(studyId).then((data) => {
        setModules(data);
        for (const row of data) if (row.state === "ready" && waiting.current.has(row.id)) void addModule(row);
      }, () => {});
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [generating.length, studyId, addModule]);

  async function askModule(row: PairCandidate) {
    // Making the control is a paid generation, like generating a module.
    if (row.state !== "ready" && !await confirmAction({
      title: `Tạo bản đối chứng cho "${row.title}"?`,
      description: "Hệ thống sinh một bản cùng mục tiêu học tập nhưng không dùng tài liệu, rồi thêm cả hai để so sánh.",
      action: "Tạo và thêm",
    })) return;
    await addModule(row);
  }

  async function addOne(row: MediaCandidate) {
    setBusy(row.id);
    try {
      await addMedia(studyId, row.kind, row.id);
      setMedia((current) => current?.map((r) => r.id === row.id ? { ...r, state: "in_study" } : r) ?? null);
      notifyDone(`Đã thêm "${row.title}".`);
      onAdded();
    } catch (e) { notifyFailed(apiErrorMessage(e, `Không thêm được "${row.title}".`)); }
    finally { setBusy(null); }
  }

  // Two sets made from one file with no focus share a title: the day they were made tells them apart.
  const detail = (r: PairCandidate | MediaCandidate) =>
    [r.created_at && new Date(r.created_at).toLocaleDateString("vi-VN"), r.course_name].filter(Boolean).join(" · ");
  const rows: { id: string; title: string; detail: string; state: string; add: () => void }[] = tab === "module"
    ? (modules ?? []).map((r) => ({ id: r.id, title: r.title, detail: detail(r), state: r.state, add: () => void askModule(r) }))
    : (media ?? []).filter((r) => r.kind === tab)
      .map((r) => ({ id: r.id, title: r.title, detail: detail(r), state: r.state, add: () => void addOne(r) }));
  const loaded = tab === "module" ? modules : media;
  const label = TABS.find((t) => t.id === tab)!.label;

  return <>
    <button type="button" className="ui-button primary" onClick={() => setOpen(true)}>
      <Plus aria-hidden="true" className="size-4" />Thêm nội dung
    </button>
    {generating.map((r) => <p key={r.id} className="status-pill" data-status="processing">Đang tạo bản đối chứng cho "{r.title}"…</p>)}
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="ui-dialog add-content-dialog" aria-describedby={undefined}>
        <DialogTitle className="ui-dialog-title">Thêm nội dung</DialogTitle>
        <Tabs tabs={TABS} selected={tab} onSelect={(id) => setTab(id as Kind)} label="Loại nội dung">
          {tab === "module" && <p className="small muted add-content-note">
            Mỗi mô-đun được chấm cùng một bản đối chứng: cùng mục tiêu học tập nhưng sinh không dùng tài liệu.
          </p>}
          {error ? <p role="alert">{error}</p> : !loaded ? <p className="muted">Đang tải…</p> : rows.length === 0
            ? <p className="small muted add-content-note">Chưa có {label.toLowerCase()} nào.</p>
            : <ul className="reference-files add-content-list" aria-label={`${label} có thể thêm`}>
              {rows.map((row) => <li key={row.id}>
                <div className="min-w-0">
                  <div className="font-semibold [overflow-wrap:anywhere]">{row.title}</div>
                  <div className="small muted [overflow-wrap:anywhere]">{row.detail}</div>
                </div>
                {row.state === "in_study" ? <span className="small muted">Đã thêm</span>
                  : row.state === "generating" ? <span className="status-pill" data-status="processing">Đang tạo…</span>
                  : <button type="button" className="ui-button" disabled={busy !== null} onClick={row.add}>
                    {busy === row.id ? "Đang thêm…" : row.state === "failed" ? "Thử lại" : "Thêm"}
                  </button>}
              </li>)}
            </ul>}
        </Tabs>
      </DialogContent>
    </Dialog>
  </>;
}
