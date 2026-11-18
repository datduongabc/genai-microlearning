import { toast } from "sonner";
import { useEffect, useState } from "react";
import { getRaters, listStudies, setRaters, type Study } from "../api/studies";
import { apiErrorMessage } from "../api/client";
import { raterEmailsSchema } from "../lib/ratingSchema";
import { StudyReferences } from "./StudyReferences";
import { StudyResults } from "./StudyResults";

export function AdminStudiesPanel() {
  const [studies, setStudies] = useState<Study[] | null>(null);
  const [studyId, setStudyId] = useState("");
  const [loadedId, setLoadedId] = useState("");
  const [emails, setEmails] = useState<string[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // Bumped on every pick, so picking the same study again retries a failed load.
  const [picks, setPicks] = useState(0);
  useEffect(() => {
    let active = true;
    listStudies().then((data) => { if (active) setStudies(data); })
      .catch(() => { if (active) setError("Không tải được nghiên cứu."); });
    return () => { active = false; };
  }, []);
  useEffect(() => {
    let active = true;
    if (!studyId) return;
    getRaters(studyId).then((data) => {
      if (active) { setEmails(data.emails); setLoadedId(studyId); }
    }).catch(() => { if (active) setError("Không tải được người chấm."); });
    return () => { active = false; };
  }, [studyId, picks]);

  // Typed or pasted text may hold a whole list, separated by spaces, new lines, commas or semicolons.
  function withTyped(text: string) {
    return [...new Set([...emails, ...text.toLowerCase().split(/[\s,;]+/).filter(Boolean)])];
  }
  function addEmails(text: string) {
    const next = withTyped(text);
    if (!raterEmailsSchema.safeParse({ emails: next }).success) {
      setInput(text.trim().replace(/\s+/g, " "));
      setError("Email không hợp lệ, tối đa 100 người chấm.");
      return;
    }
    setEmails(next); setInput(""); setError("");
  }
  const selected = studies?.find((s) => s.id === studyId);
  return <section className="studies-layout">
    <nav aria-label="Nghiên cứu" className="study-list">
      {studies?.map((study) => <button key={study.id} disabled={busy}
        aria-pressed={studyId === study.id} className="ui-card"
        onClick={() => { if (loadedId === study.id) return; setStudyId(study.id); setPicks((n) => n + 1); setLoadedId(""); setInput(""); setError(""); }}>
        {study.name}<span aria-hidden="true">›</span>
      </button>)}
      {!studies && !error && <p>Đang tải…</p>}
      {studies?.length === 0 && <p>Chưa có nghiên cứu.</p>}
    </nav>
    <div className="study-main">
    <div className="ui-card study-detail">
      {selected ? <><h2>{selected.name}</h2><p className="small muted">Rubric {selected.rubric_version}</p></>
        : <p>Chọn nghiên cứu</p>}
      {error && <p role="alert">{error}</p>}
      {studyId && loadedId !== studyId && !error && <p>Đang tải người chấm…</p>}
      {studyId && loadedId === studyId && <form noValidate onSubmit={async (event) => {
        event.preventDefault();
        const next = withTyped(input);
        if (!raterEmailsSchema.safeParse({ emails: next }).success) { setError("Email không hợp lệ, tối đa 100 người chấm."); return; }
        setBusy(true); setError("");
        try {
          const data = await setRaters(studyId, next);
          setEmails(data.emails); setInput(""); toast.success("Đã lưu người chấm.");
        } catch (e) { setError(apiErrorMessage(e, "Không lưu được người chấm.")); }
        finally { setBusy(false); }
      }}>
        <label htmlFor="rater-email" className="text-label font-medium">Người chấm</label>
        <div className="rater-chips">
          {emails.map((email) => <span className="rater-chip" key={email}>{email}
            <button type="button" aria-label={`Bỏ ${email}`} disabled={busy} onClick={() => { setEmails(emails.filter((e) => e !== email)); setError(""); }}>×</button>
          </span>)}
          <input id="rater-email" type="email" autoComplete="off" placeholder="Nhập email rồi Enter" value={input} disabled={busy}
            onChange={(e) => { setInput(e.target.value); }}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addEmails(input); } }}
            onPaste={(e) => {
              // An email field drops the new lines of a pasted list, so a list is split here instead.
              const pasted = e.clipboardData.getData("text/plain");
              if (!/[\s,;]/.test(pasted.trim())) return;
              e.preventDefault();
              addEmails(`${input} ${pasted}`);
            }} />
        </div>
        <button disabled={busy} className="ui-button primary mt-4">{busy ? "Đang lưu…" : "Lưu người chấm"}</button>
      </form>}
    </div>
    {studyId && <StudyReferences key={`refs-${studyId}-${picks}`} studyId={studyId} />}
    {studyId && <StudyResults key={`results-${studyId}-${picks}`} studyId={studyId} />}
    </div>
  </section>;
}
