import { useEffect, useRef, useState } from "react";
import {
  deleteStudyFile, downloadStudyReference, getStudyItems, listStudyFiles, setItemReference, setRaters, uploadStudyFile,
  type Assignments, type StudyFile, type StudyItemSummary,
} from "../api/studies";
import { apiErrorMessage } from "../api/client";
import { confirmAction } from "../lib/confirm";
import { formatSize } from "../lib/fileSize";
import { notifyDone, notifyFailed } from "../lib/notify";
import { rowsOf } from "../lib/studyRows";
import { AddStudyContent } from "./AddStudyContent";

const KIND: Record<StudyItemSummary["group"], string> = {
  rag_llm: "Mô-đun", llm_no_rag: "Mô-đun", human: "Mô-đun", flashcards: "Flashcard", video: "Video",
};

// One row per thing to rate (a pair of modules counts as one), by its real name: the file the
// raters check it against, and who rates it. A rater sees only the rows ticked for them.
export function StudyContent({ studyId, emails, assignments, version, onAdded, onSaved }: {
  studyId: string; emails: string[]; assignments: Assignments; version: number;
  onAdded: () => void; onSaved: (saved: Assignments) => void;
}) {
  const [items, setItems] = useState<StudyItemSummary[] | null>(null);
  const [files, setFiles] = useState<StudyFile[] | null>(null);
  const [error, setError] = useState("");
  // Email -> the row keys ticked; absent means none.
  const [given, setGiven] = useState<Record<string, Set<string>>>(() =>
    Object.fromEntries(Object.entries(assignments).map(([email, keys]) => [email, new Set(keys)])));
  const [busy, setBusy] = useState(false);
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

  const ticked = (email: string, key: string) => given[email]?.has(key) ?? false;
  function toggle(email: string, key: string) {
    setGiven((current) => {
      const next = new Set(current[email]);
      if (!next.delete(key)) next.add(key);
      return { ...current, [email]: next };
    });
  }
  async function saveAssignments() {
    setBusy(true);
    try {
      const saved = await setRaters(studyId, emails, Object.fromEntries(emails.map((email) => [email, [...(given[email] ?? [])]])));
      onSaved(saved.assignments);
      notifyDone("Đã lưu phân công.");
    } catch (e) { notifyFailed(apiErrorMessage(e, "Không lưu được phân công.")); }
    finally { setBusy(false); }
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
  async function download(file: StudyFile) {
    try { await downloadStudyReference(studyId, file.id); }
    catch (e) { notifyFailed(apiErrorMessage(e, `Không tải được ${file.filename}.`)); }
  }

  const rows = items ? rowsOf(items) : [];
  return <section className="ui-card study-results" aria-labelledby="study-content-title">
    <div className="study-section-head">
      <h2 id="study-content-title">Nội dung chấm</h2>
      <AddStudyContent studyId={studyId} onAdded={onAdded} />
    </div>
    {error ? <p role="alert">{error}</p> : !items || !files ? <p className="muted">Đang tải…</p> : rows.length === 0
      ? <p className="small muted">Chưa có nội dung nào.</p> : <>
        <div className="assign-table">
          <table>
            <thead><tr>
              <th scope="col">Nội dung</th>
              <th scope="col">Tài liệu gốc</th>
              {emails.map((email) => <th scope="col" key={email} title={email}>{email.split("@")[0]}</th>)}
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
                {emails.map((email) => <td key={email}>
                  <input type="checkbox" aria-label={`${email} chấm ${codes}`} checked={ticked(email, key)}
                    disabled={busy} onChange={() => toggle(email, key)} />
                </td>)}
              </tr>;
            })}</tbody>
          </table>
        </div>
        {emails.length === 0 ? <p className="small muted">Mời người chấm ở trên để giao nội dung.</p>
          : <div><button type="button" className="ui-button primary" disabled={busy} onClick={() => void saveAssignments()}>
            {busy ? "Đang lưu…" : "Lưu phân công"}
          </button></div>}
      </>}

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
