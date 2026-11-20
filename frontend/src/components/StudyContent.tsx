import { useEffect, useRef, useState } from "react";
import { Ellipsis, Pencil, Trash2 } from "lucide-react";
import { listUsers, type AdminUser } from "../api/admin";
import {
  deleteStudyFile, downloadStudyReference, getStudyItems, listStudyFiles, removeStudyItem, renameStudyItem, setItemReference, setRaters,
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
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "./ui/dropdown-menu";

const KIND: Record<StudyItemSummary["group"], string> = {
  rag_llm: "Mô-đun", llm_no_rag: "Mô-đun", human: "Mô-đun", flashcards: "Flashcard", video: "Video",
};

// One row per thing to rate (a pair of modules counts as one), by its real name: the file the
// raters check it against, and who rates it. Raters are picked per row from the active accounts,
// so each row can go to different people; a rater sees only the rows they are given, and the
// study's invitations are exactly the accounts given at least one row.
export function StudyContent({ studyId, assignments, version, onAdded, onSaved }: {
  studyId: string; assignments: Assignments; version: number;
  onAdded: () => void; onSaved: (saved: Assignments) => void;
}) {
  const [items, setItems] = useState<StudyItemSummary[] | null>(null);
  const [files, setFiles] = useState<StudyFile[] | null>(null);
  const [error, setError] = useState("");
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  // The row being renamed.
  const [renaming, setRenaming] = useState<string | null>(null);
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
  useEffect(() => {
    let active = true;
    listUsers(true).then((data) => { if (active) setUsers(data); }, () => { if (active) setUsers([]); });
    return () => { active = false; };
  }, []);

  const nameOf = new Map((users ?? []).map((u) => [u.email.toLowerCase(), u.name]));
  const ratersOf = (key: string) => Object.keys(assignments).filter((email) => assignments[email].includes(key)).sort();
  // Only this row changes; every other row keeps the raters it was saved with.
  async function saveRaters(key: string, chosen: Set<string>) {
    const next: Assignments = {};
    for (const email of new Set([...Object.keys(assignments), ...chosen])) {
      const keys = [...(assignments[email] ?? []).filter((k) => k !== key), ...(chosen.has(email) ? [key] : [])];
      if (keys.length) next[email] = keys;
    }
    const saved = await setRaters(studyId, Object.keys(next), next);
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
  async function rename(item: StudyItemSummary, name: string) {
    try {
      setItems(await renameStudyItem(studyId, item.id, name));
      setRenaming(null);
      notifyDone("Đã đổi tên.");
    } catch (e) { notifyFailed(apiErrorMessage(e, "Không đổi được tên.")); }
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
              <th scope="col"><span className="sr-only">Thao tác</span></th>
            </tr></thead>
            <tbody>{rows.map((row) => {
              const first = row[0];
              const key = first.pair_id ?? first.id;
              const codes = row.map((item) => item.anon_code).join(", ");
              const paired = row.length > 1;
              // A flashcard set or video goes by the name raters see, which the admin can change; its
              // file is the line under it. A module goes by its own title.
              const media = first.group === "flashcards" || first.group === "video";
              const title = media ? first.packet.topic : first.source?.title ?? first.packet.topic;
              const detail = paired ? "Có và không dùng tài liệu" : media ? first.source?.title : null;
              const raters = ratersOf(key);
              return <tr key={key}>
                <th scope="row"><div className="content-cell">
                  <span className="media-kind">{KIND[first.group]}</span>
                  {renaming === first.id
                    ? <RenameRow name={title} onCancel={() => setRenaming(null)} onSave={(name) => rename(first, name)} />
                    : <div className="font-semibold [overflow-wrap:anywhere]">{title}</div>}
                  {detail && <div className="small muted">{detail}</div>}
                  <div className="small muted">Mã {codes}</div>
                </div></th>
                <td>
                  <select className="ui-select" aria-label={`Tài liệu gốc của ${codes}`} value={first.reference_file_id ?? ""}
                    disabled={saving !== null} onChange={(e) => void pick(first, e.target.value)}>
                    <option value="">Chưa gắn</option>
                    {files.map((file) => <option key={file.id} value={file.id}>{file.filename}</option>)}
                  </select>
                </td>
                <td><div className="raters-cell">
                  {raters.length === 0 ? <span className="small muted">Chưa giao</span>
                    : <ul className="rater-names" aria-label={`Người chấm ${codes}`}>
                      {raters.slice(0, 3).map((email) => <li key={email} title={email}>{nameOf.get(email) ?? email}</li>)}
                      {raters.length > 3 && <li className="muted">và {raters.length - 3} người khác</li>}
                    </ul>}
                  <button type="button" className="link-action" disabled={!users} aria-label={`Chọn người chấm ${codes}`}
                    onClick={() => setPicking({ key, title: first.source?.title ?? first.packet.topic })}>
                    {raters.length ? "Sửa" : "Giao"}
                  </button>
                </div></td>
                <td>
                  {/* As on the course list: what can be done to a row sits behind its "⋯" menu. */}
                  <DropdownMenu>
                    <DropdownMenuTrigger className="icon-button" aria-label={`Thao tác khác với ${codes}`}>
                      <Ellipsis aria-hidden="true" />
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end" className="doc-menu">
                      {media && <DropdownMenuItem onSelect={() => setRenaming(first.id)}><Pencil aria-hidden="true" />Đổi tên</DropdownMenuItem>}
                      <DropdownMenuItem variant="destructive" onSelect={() => void removeRow(first, title)}>
                        <Trash2 aria-hidden="true" />Xóa khỏi nghiên cứu
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </td>
              </tr>;
            })}</tbody>
          </table>
        </div>
      </>}
    {picking && users && <PickRaters title={picking.title} users={users} initial={ratersOf(picking.key)}
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

// The name raters see, edited in place as a course name is on the course list.
function RenameRow({ name, onCancel, onSave }: { name: string; onCancel: () => void; onSave: (name: string) => Promise<void> }) {
  const [value, setValue] = useState(name);
  const [busy, setBusy] = useState(false);
  const trimmed = value.trim();
  return <form className="rename-row" noValidate onSubmit={async (e) => {
    e.preventDefault();
    if (!trimmed || trimmed.length > 200) return;
    if (trimmed === name) { onCancel(); return; }
    setBusy(true);
    try { await onSave(trimmed); } finally { setBusy(false); }
  }}>
    <input className="ui-input" aria-label="Tên hiển thị cho người chấm" value={value} maxLength={200} autoFocus
      disabled={busy} onChange={(e) => setValue(e.target.value)} onKeyDown={(e) => { if (e.key === "Escape") onCancel(); }} />
    <button className="ui-button primary" disabled={busy || !trimmed}>{busy ? "Đang lưu…" : "Lưu"}</button>
    <button type="button" className="ui-button" disabled={busy} onClick={onCancel}>Hủy</button>
  </form>;
}

function PickRaters({ title, users, initial, onClose, onSave }: {
  title: string; users: AdminUser[]; initial: string[]; onClose: () => void; onSave: (chosen: Set<string>) => Promise<void>;
}) {
  const [chosen, setChosen] = useState(() => new Set(initial));
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const q = query.trim().toLowerCase();
  const shown = users.filter((u) => !q || u.email.toLowerCase().includes(q) || u.name.toLowerCase().includes(q));
  function toggle(email: string) {
    setChosen((current) => { const next = new Set(current); if (!next.delete(email)) next.add(email); return next; });
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
      <input type="search" className="ui-input" aria-label="Tìm người chấm"
        value={query} onChange={(e) => setQuery(e.target.value)} />
      <p className="small muted">Đã chọn {chosen.size}</p>
      <ul className="pick-raters-list" aria-label="Tài khoản">
        {shown.map((user) => {
          const email = user.email.toLowerCase();
          return <li key={user.id}><label>
            <input type="checkbox" checked={chosen.has(email)} disabled={busy} onChange={() => toggle(email)} />
            <span className="min-w-0"><span className="block font-medium">{user.name}</span>
              <span className="block small muted [overflow-wrap:anywhere]">{user.email}</span></span>
          </label></li>;
        })}
        {shown.length === 0 && <li className="small muted">Không có tài khoản nào khớp.</li>}
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
