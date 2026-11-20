import { ChevronRight } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { getRaters, listStudies, setRaters, type Assignments, type Study } from "../api/studies";
import { apiErrorMessage } from "../api/client";
import { notifyDone, notifyFailed } from "../lib/notify";
import { RaterInvite } from "./RaterInvite";
import { StudyContent } from "./StudyContent";
import { StudyResults } from "./StudyResults";

export function AdminStudiesPanel() {
  const [studies, setStudies] = useState<Study[] | null>(null);
  const [params, setParams] = useSearchParams();
  const [studyId, setStudyId] = useState(() => params.get("study") ?? "");
  const [loadedId, setLoadedId] = useState("");
  const [emails, setEmails] = useState<string[]>([]);
  // The invitations as saved: what the assignment table offers, never an unsaved edit.
  const [savedEmails, setSavedEmails] = useState<string[]>([]);
  const [assignments, setAssignments] = useState<Assignments>({});
  // Bumped when content joins, so the cards that list the study's packets load them again.
  const [version, setVersion] = useState(0);
  const pairAdded = useCallback(() => setVersion((n) => n + 1), []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Bumped on every pick, so picking the same study again retries a failed load.
  const [picks, setPicks] = useState(0);
  useEffect(() => {
    let active = true;
    listStudies().then((data) => {
      if (!active) return;
      setStudies(data);
      // An address may name a study no longer listed (closed): it does not open. With one study
      // there is nothing to choose: it opens, and the list is not shown.
      setStudyId((current) => data.some((s) => s.id === current) ? current : data.length === 1 ? data[0].id : "");
    })
      .catch(() => { if (active) setError("Không tải được nghiên cứu."); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    let active = true;
    if (!studies?.some((s) => s.id === studyId)) return;
    getRaters(studyId).then((data) => {
      if (active) { setEmails(data.emails); setSavedEmails(data.emails); setAssignments(data.assignments); setLoadedId(studyId); }
    }).catch(() => { if (active) setError("Không tải được người chấm."); });
    return () => { active = false; };
  }, [studyId, picks, studies]);

  const selected = studies?.find((s) => s.id === studyId);
  const single = studies?.length === 1;
  return <section className={single ? "studies-layout single" : "studies-layout"}>
    {!single && <nav aria-label="Nghiên cứu" className="study-list">
      {studies?.map((study) => <button key={study.id} disabled={busy}
        aria-pressed={studyId === study.id} className="ui-card"
        onClick={() => { if (loadedId === study.id) return; setStudyId(study.id); setParams((p) => { p.set("study", study.id); return p; }, { replace: true }); setPicks((n) => n + 1); setLoadedId(""); setError(""); }}>
        {study.name}<ChevronRight aria-hidden="true" className="size-4" />
      </button>)}
      {!studies && !error && <p>Đang tải…</p>}
      {studies?.length === 0 && <p>Chưa có nghiên cứu.</p>}
    </nav>}
    <div className="study-main">
    <div className="ui-card study-detail">
      {selected ? <h2>{selected.name}</h2>
        : <p className="muted">Chọn một nghiên cứu để mời người chấm và xem kết quả.</p>}
      {error && <p role="alert">{error}</p>}
      {selected && loadedId !== studyId && !error && <p>Đang tải người chấm…</p>}
      {selected && loadedId === studyId && <form noValidate onSubmit={async (event) => {
        event.preventDefault();
        setBusy(true); setError("");
        try {
          const data = await setRaters(studyId, emails);
          setEmails(data.emails); setSavedEmails(data.emails); setAssignments(data.assignments); notifyDone("Đã lưu người chấm.");
        } catch (e) { notifyFailed(apiErrorMessage(e, "Không lưu được người chấm.")); }
        finally { setBusy(false); }
      }}>
        <label htmlFor="rater-email" className="text-label font-medium">Người chấm</label>
        <RaterInvite emails={emails} onChange={setEmails} disabled={busy} />
        <button disabled={busy} className="ui-button primary mt-4">{busy ? "Đang lưu…" : "Lưu người chấm"}</button>
      </form>}
    </div>
    {selected && loadedId === studyId && <StudyContent key={`content-${studyId}-${picks}-${savedEmails.join()}`}
      studyId={studyId} emails={savedEmails} assignments={assignments} version={version}
      onAdded={pairAdded} onSaved={setAssignments} />}
    {selected && <StudyResults key={`results-${studyId}-${picks}-${version}`} studyId={studyId} />}
    </div>
  </section>;
}
