import { useEffect, useRef, useState } from "react";
import {
  deleteStudyFile, downloadStudyReference, getStudyItems, listStudyFiles, removeStudyItem, setItemReference, setRaters,
  uploadStudyFile,
  type Assignments, type StudyFile, type StudyItemSummary,
} from "../api/studies";
import { apiErrorMessage } from "../api/client";
import { confirmAction } from "../lib/confirm";
import { formatSize } from "../lib/fileSize";
import { notifyDone, notifyFailed } from "../lib/notify";
import { rowsOf } from "../lib/studyRows";
import { AddStudyContent } from "./AddStudyContent";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";

const KIND: Record<StudyItemSummary["group"], string> = {
  rag_llm: "Mô-đun", llm_no_rag: "Mô-đun", human: "Mô-đun", flashcards: "Flashcard", video: "Video",
};

// One row per thing to rate (a pair of modules counts as one), by its real name: the file the
// raters check it against, and who rates it. Raters are picked per row in a dialog rather than
// as a column each, so the table stays readable with the 100 raters a study allows.
// A rater sees only the rows they are given.
export function StudyContent({ studyId, emails, assignments, version, onAdded, onSaved }: {
  studyId: string; emails: string[]; assignments: Assignments; version: number;
  onAdded: () => void; onSaved: (saved: Assignments) => void;
}) {
  const [items, setItems] = useState<StudyItemSummary[] | null>(null);
  const [files, setFiles] = useState<StudyFile[] | null>(null);
  const [error, setError] = useState("");
  // The row whose raters are being picked.
  const [picking, setPicking] = useState<{ key: string; title: string } | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let active = true;
    Promise.all([getStudyItems(studyId), listStudyFiles(studyId)])
      .then(([i, f]) => { if (active) { setItems(i); setFiles(f); } })
      .catch((e) => { if (active) setError(apiErrorMessage(e, "Không tải được nội dung chấm.")); });
    return () => { active = false; };
  }, [studyId, version]);

  const ratersOf = (key: string) => emails.filter((email) => assignments[email]?.includes(key));
  // Only this row changes; every other row keeps the raters it was saved with.
  async function saveRaters(key: string, chosen: Set<string>) {
    const next = Object.fromEntries(emails.map((email) => {
      const others = (assignments[email] ?? []).filter((k) => k !== key);
      return [email, chosen.has(email) ? [...others, key] : others];
    }));
    const saved = await setRaters(studyId, emails, next);
    onSaved(saved.assignments);
    notifyDone("Đã lưu người chấm.");
  }

  async function pick(item: StudyItemSummary, fileId: string) {
    setSaving(item.id);
    try {
      const refs = new Map((await setItemReference(studyId, item.id, fileId || null)).map((r) => [r.id, r.reference_file_id]));
      setItems((current) => current?.map((i) => ({ ...i, reference_file_id: refs.get(i.id) ?? null })) ?? null);
    } catch (e) { notifyFailed(apiErrorMessage(e, "Không lưu được tài liệu gốc.")); }
    finally { setSaving(null); }
  }
  async function upload(file: File) {
    setProgress(0);
    try {
      const added = await uploadStudyFile(studyId, file, setProgress);
      setFiles((current) => [...(current ?? []), added]);
      notifyDone(`Đã tải lên ${file.name}.`);
    } catch (e) { notifyFailed(apiErrorMessage(e, `Không tải lên được ${file.name}.`)); }
    finally { setProgress(null); if (input.current) input.current.value = ""; }
  }
  async function remove(file: StudyFile) {
    if (!await confirmAction({ title: `Xóa ${file.filename}?`, description: "Nội dung đang dùng tệp này sẽ không còn tài liệu gốc.", action: "Xóa tệp", destructive: true })) return;
    try {
      await deleteStudyFile(studyId, file.id);
      setFiles((current) => current?.filter((f) => f.id !== file.id) ?? null);
      setItems((current) => current?.map((i) => i.reference_file_id === file.id ? { ...i, reference_file_id: null } : i) ?? null);
      notifyDone(`Đã xóa ${file.filename}.`);
    } catch (e) { notifyFailed(apiErrorMessage(e, `Không xóa được ${file.filename}.`)); }
  }
  async function removeRow(item: StudyItemSummary, title: string) {
    if (!await confirmAction({
      title: `Xóa "${title}" khỏi nghiên cứu?`,
      description: "Điểm đã chấm cho nội dung này cũng bị xóa. Nội dung gốc trong môn học vẫn còn.",
      action: "Xóa", destructive: true,
    })) return;
    try {
      await removeStudyItem(studyId, item.id);
      notifyDone(`Đã xóa "${title}" khỏi nghiên cứu.`);
      onAdded();
    } catch (e) { notifyFailed(apiErrorMessage(e, `Không xóa được "${title}".`)); }
  }
  async function download(file: StudyFile) {
    try { await downloadStudyReference(studyId, file.id); }
    catch (e) { notifyFailed(apiErrorMessage(e, `Không tải được ${file.filename}.`)); }
  }

  const rows = items ? rowsOf(items) : [];
  return <section className="ui-card study-results" aria-labelledby="study-content-title">
    <div className="study-section-head">
      <h2 id="study-content-title">Nội dung chấm</h2>
      <AddStudyContent studyId={studyId} version={version} onAdded={onAdded} />
    </div>
    {error ? <p role="alert">{error}</p> : !items || !files ? <p className="muted">Đang tải…</p> : rows.length === 0
      ? <p className="small muted">Chưa có nội dung nào.</p> : <>
        <div className="assign-table">
          <table>
            <thead><tr>
              <th scope="col">Nội dung</th>
              <th scope="col">Tài liệu gốc</th>
              <th scope="col">Người chấm</th>
              <th scope="col"><span className="sr-only">Xóa</span></th>
            </tr></thead>
            <tbody>{rows.map((row) => {
              const first = row[0];
              const key = first.pair_id ?? first.id;
              const codes = row.map((item) => item.anon_code).join(", ");
              const paired = row.length > 1;
              const made = first.source ? new Date(first.source.created_at).toLocaleDateString("vi-VN") : null;
              const detail = paired ? "Có và không dùng tài liệu"
                : first.source && first.group !== "rag_llm" ? first.packet.topic : null;
              return <tr key={key}>
                <th scope="row"><div className="content-cell">
                  <span className="content-kind">{KIND[first.group]}</span>
                  <div className="font-semibold [overflow-wrap:anywhere]">{first.source?.title ?? first.packet.topic}</div>
                  <div className="small muted">{[detail, made, codes].filter(Boolean).join(" · ")}</div>
                </div></th>
                <td>
                  <select className="ui-select" aria-label={`Tài liệu gốc của ${codes}`} value={first.reference_file_id ?? ""}
                    disabled={saving !== null} onChange={(e) => void pick(first, e.target.value)}>
                    <option value="">Chưa gắn</option>
                    {files.map((file) => <option key={file.id} value={file.id}>{file.filename}</option>)}
                  </select>
                </td>
                <td><div className="raters-cell">
                  <span className={ratersOf(key).length ? "" : "muted"}>
                    {ratersOf(key).length ? `${ratersOf(key).length} người` : "Chưa giao"}
                  </span>
                  <button type="button" className="ui-button" disabled={emails.length === 0}
                    aria-label={`Chọn người chấm ${codes}`}
                    onClick={() => setPicking({ key, title: first.source?.title ?? first.packet.topic })}>Chọn</button>
                </div></td>
                <td><button type="button" className="link-action danger" aria-label={`Xóa ${codes}`}
                  onClick={() => void removeRow(first, first.source?.title ?? first.packet.topic)}>Xóa</button></td>
              </tr>;
            })}</tbody>
          </table>
        </div>
        {emails.length === 0 && <p className="small muted">Mời người chấm ở trên để giao nội dung.</p>}
      </>}
    {picking && <PickRaters title={picking.title} emails={emails} initial={ratersOf(picking.key)}
      onClose={() => setPicking(null)} onSave={(chosen) => saveRaters(picking.key, chosen)} />}

    {files && <>
      <h3 className="results-subtitle">Tệp tài liệu gốc</h3>
      {files.length > 0 && <ul className="reference-files" aria-label="Tệp tài liệu gốc">
        {files.map((file) => <li key={file.id}>
          <div className="min-w-0">
            <div className="font-semibold [overflow-wrap:anywhere]">{file.filename}</div>
            <div className="small muted">{formatSize(file.size)}</div>
          </div>
          <button type="button" className="link-action" onClick={() => download(file)}>
            Tải{" "}<span className="sr-only">{file.filename}</span>
          </button>
          <button type="button" className="link-action danger" onClick={() => remove(file)}>
            Xóa{" "}<span className="sr-only">{file.filename}</span>
          </button>
        </li>)}
      </ul>}
      <div className="reference-upload">
        <label className="ui-button" aria-disabled={progress !== null || undefined}>
          {progress === null ? "Tải tệp lên" : `Đang tải lên ${Math.round(progress * 100)}%`}
          <input ref={input} type="file" className="sr-only" disabled={progress !== null}
            onChange={(e) => { const file = e.target.files?.[0]; if (file) void upload(file); }} />
        </label>
      </div>
    </>}
  </section>;
}

function PickRaters({ title, emails, initial, onClose, onSave }: {
  title: string; emails: string[]; initial: string[]; onClose: () => void; onSave: (chosen: Set<string>) => Promise<void>;
}) {
  const [chosen, setChosen] = useState(() => new Set(initial));
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const shown = emails.filter((email) => email.includes(query.trim().toLowerCase()));
  const allShown = shown.length > 0 && shown.every((email) => chosen.has(email));
  function toggle(email: string) {
    setChosen((current) => { const next = new Set(current); if (!next.delete(email)) next.add(email); return next; });
  }
  function toggleShown() {
    setChosen((current) => {
      const next = new Set(current);
      for (const email of shown) { if (allShown) next.delete(email); else next.add(email); }
      return next;
    });
  }
  async function save() {
    setBusy(true);
    try { await onSave(chosen); onClose(); }
    catch (e) { notifyFailed(apiErrorMessage(e, "Không lưu được người chấm.")); }
    finally { setBusy(false); }
  }
  return <Dialog open onOpenChange={(open) => { if (!open && !busy) onClose(); }}>
    <DialogContent className="ui-dialog pick-raters" aria-describedby={undefined}>
      <DialogTitle className="ui-dialog-title [overflow-wrap:anywhere]">Người chấm "{title}"</DialogTitle>
      {emails.length > 8 && <input type="search" className="ui-input" placeholder="Tìm email" aria-label="Tìm người chấm"
        value={query} onChange={(e) => setQuery(e.target.value)} />}
      <div className="pick-raters-bar">
        <span className="small muted">Đã chọn {chosen.size}/{emails.length}</span>
        {shown.length > 0 && <button type="button" className="link-action" onClick={toggleShown}>
          {allShown ? "Bỏ chọn tất cả" : "Chọn tất cả"}
        </button>}
      </div>
      <ul className="pick-raters-list" aria-label="Người chấm">
        {shown.map((email) => <li key={email}><label>
          <input type="checkbox" checked={chosen.has(email)} disabled={busy} onChange={() => toggle(email)} />
          <span className="[overflow-wrap:anywhere]">{email}</span>
        </label></li>)}
        {shown.length === 0 && <li className="small muted">Không có email nào khớp.</li>}
      </ul>
      <div className="pick-raters-actions">
        <button type="button" className="ui-button" disabled={busy} onClick={onClose}>Hủy</button>
        <button type="button" className="ui-button primary" disabled={busy} onClick={() => void save()}>
          {busy ? "Đang lưu…" : "Lưu"}
        </button>
      </div>
    </DialogContent>
  </Dialog>;
}
