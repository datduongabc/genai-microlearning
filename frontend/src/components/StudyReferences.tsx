import { useEffect, useRef, useState } from "react";
import {
  deleteStudyFile,
  downloadStudyReference,
  getStudyItems,
  listStudyFiles,
  setItemReference,
  uploadStudyFile,
  type StudyFile,
  type StudyItemSummary,
} from "../api/studies";
import { apiErrorMessage } from "../api/client";
import { formatSize } from "../lib/fileSize";

const GROUP_LABEL: Record<string, string> = { rag_llm: "RAG", llm_no_rag: "Không RAG", human: "Người viết" };

// One row per RAG/no-RAG pair, which always shares its file, and one per human-written packet.
function rowsOf(items: StudyItemSummary[]) {
  const rows = new Map<string, StudyItemSummary[]>();
  for (const item of items) {
    const key = item.pair_id ?? item.id;
    rows.set(key, [...(rows.get(key) ?? []), item]);
  }
  return [...rows.values()];
}

// The files the raters judge the packets against: the admin uploads them to the study once and
// picks one per packet. The pipeline keeps no course file once it is read, so they come from here.
export function StudyReferences({ studyId }: { studyId: string }) {
  const [files, setFiles] = useState<StudyFile[] | null>(null);
  const [items, setItems] = useState<StudyItemSummary[] | null>(null);
  const [error, setError] = useState("");
  const [progress, setProgress] = useState<number | null>(null);
  const [saving, setSaving] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let active = true;
    Promise.all([listStudyFiles(studyId), getStudyItems(studyId)])
      .then(([f, i]) => { if (active) { setFiles(f); setItems(i); } })
      .catch((e) => { if (active) setError(apiErrorMessage(e, "Không tải được tài liệu tham khảo.")); });
    return () => { active = false; };
  }, [studyId]);

  async function upload(file: File) {
    setError(""); setProgress(0);
    try {
      const added = await uploadStudyFile(studyId, file, setProgress);
      setFiles((current) => [...(current ?? []), added]);
    } catch (e) { setError(apiErrorMessage(e, `Không tải lên được ${file.name}.`)); }
    finally { setProgress(null); if (input.current) input.current.value = ""; }
  }

  async function remove(file: StudyFile) {
    if (!window.confirm(`Xóa ${file.filename}? Các gói đang dùng file này sẽ không còn tài liệu gốc.`)) return;
    setError("");
    try {
      await deleteStudyFile(studyId, file.id);
      setFiles((current) => current?.filter((f) => f.id !== file.id) ?? null);
      setItems((current) => current?.map((i) => i.reference_file_id === file.id ? { ...i, reference_file_id: null } : i) ?? null);
    } catch (e) { setError(apiErrorMessage(e, `Không xóa được ${file.filename}.`)); }
  }

  async function pick(item: StudyItemSummary, fileId: string) {
    setError(""); setSaving(item.id);
    try {
      const refs = new Map((await setItemReference(studyId, item.id, fileId || null)).map((r) => [r.id, r.reference_file_id]));
      setItems((current) => current?.map((i) => ({ ...i, reference_file_id: refs.get(i.id) ?? null })) ?? null);
    } catch (e) { setError(apiErrorMessage(e, "Không lưu được tài liệu của gói.")); }
    finally { setSaving(null); }
  }

  async function download(file: StudyFile) {
    setError("");
    try { await downloadStudyReference(studyId, file.id); }
    catch (e) { setError(apiErrorMessage(e, `Không tải được ${file.filename}.`)); }
  }

  if (!files || !items) {
    return <section className="ui-card study-results">
      {error ? <p role="alert">{error}</p> : <p className="muted">Đang tải tài liệu tham khảo…</p>}
    </section>;
  }
  return <section className="ui-card study-results" aria-labelledby="study-references-title">
    <h2 id="study-references-title">Tài liệu gốc</h2>
    <p className="small muted">Người chấm tải file của gói đang chấm để đối chiếu. Hai gói cùng cặp luôn dùng chung một file.</p>
    {error && <p role="alert" className="ui-alert">{error}</p>}

    <h3 className="results-subtitle">File</h3>
    {files.length === 0 ? <p className="small muted">Chưa có file nào.</p> : (
      <ul className="reference-files" aria-label="File tài liệu gốc">
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
      </ul>
    )}
    <div className="reference-upload">
      <label className="ui-button" aria-disabled={progress !== null || undefined}>
        {progress === null ? "Tải file lên" : `Đang tải lên ${Math.round(progress * 100)}%`}
        <input ref={input} type="file" className="sr-only" disabled={progress !== null}
          onChange={(e) => { const file = e.target.files?.[0]; if (file) void upload(file); }} />
      </label>
      <span className="small muted">PDF, PPTX, DOCX, HTML: tối đa 25 MB. Audio tối đa 200 MB, video tối đa 1 GB.</span>
    </div>

    <h3 className="results-subtitle">Theo từng gói</h3>
    {items.length === 0 ? <p className="small muted">Nghiên cứu chưa có gói nào.</p> : (
      <ul className="reference-items" aria-label="Tài liệu gốc theo gói">
        {rowsOf(items).map((row) => {
          const first = row[0];
          const codes = row.map((i) => i.anon_code).join(", ");
          return <li key={first.id}>
            <div className="min-w-0">
              <div className="reference-codes">
                {row.map((i) => <span key={i.id}><strong>{i.anon_code}</strong>{" "}
                  <span className="result-group" data-group={i.group}>{GROUP_LABEL[i.group]}</span></span>)}
              </div>
              <div className="small muted [overflow-wrap:anywhere]">{first.packet.topic}</div>
            </div>
            <select className="ui-select" aria-label={`Tài liệu gốc của ${codes}`} value={first.reference_file_id ?? ""}
              disabled={saving !== null} onChange={(e) => void pick(first, e.target.value)}>
              <option value="">Chưa chọn</option>
              {files.map((file) => <option key={file.id} value={file.id}>{file.filename}</option>)}
            </select>
          </li>;
        })}
      </ul>
    )}
  </section>;
}
