import { useEffect, useState } from "react";
import { getStudyItems, setRaters, type Assignments, type StudyItemSummary } from "../api/studies";
import { apiErrorMessage } from "../api/client";
import { notifyDone, notifyFailed } from "../lib/notify";
import { rowsOf } from "../lib/studyRows";

// Who rates which pair. A rater with every pair ticked is stored as "all", so pairs added later
// reach them too; one with some pairs sees and rates only those, which the database enforces.
export function StudyAssignments({ studyId, emails, assignments, onSaved }: {
  studyId: string; emails: string[]; assignments: Assignments; onSaved: (saved: Assignments) => void;
}) {
  const [rows, setRows] = useState<StudyItemSummary[][] | null>(null);
  // Email -> the pair keys ticked; absent means every pair.
  const [given, setGiven] = useState<Record<string, Set<string>>>(() =>
    Object.fromEntries(Object.entries(assignments).map(([email, keys]) => [email, new Set(keys)])));
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let active = true;
    getStudyItems(studyId).then((items) => { if (active) setRows(rowsOf(items)); }).catch((e) => { if (active) setError(apiErrorMessage(e, "Không tải được phân công.")); });
    return () => { active = false; };
  }, [studyId]);

  if (error) return <section className="ui-card study-results"><p role="alert">{error}</p></section>;
  if (!rows) return <section className="ui-card study-results"><p className="muted">Đang tải phân công…</p></section>;

  const keys = rows.map((row) => row[0].pair_id ?? row[0].id);
  const ticked = (email: string, key: string) => !given[email] || given[email].has(key);
  function toggle(email: string, key: string) {
    setGiven((current) => {
      const next = new Set(current[email] ?? keys);
      if (!next.delete(key)) next.add(key);
      const copy = { ...current };
      if (next.size === keys.length) delete copy[email]; else copy[email] = next;
      return copy;
    });
  }

  async function save() {
    const empty = emails.find((email) => given[email]?.size === 0);
    if (empty) { notifyFailed(`${empty} chưa có cặp nào để chấm.`); return; }
    setBusy(true);
    try {
      const saved = await setRaters(studyId, emails, Object.fromEntries(
        emails.filter((email) => given[email]).map((email) => [email, [...given[email]]])));
      onSaved(saved.assignments);
      notifyDone("Đã lưu phân công.");
    } catch (e) { notifyFailed(apiErrorMessage(e, "Không lưu được phân công.")); }
    finally { setBusy(false); }
  }

  return <section className="ui-card study-results" aria-labelledby="study-assign-title">
    <h2 id="study-assign-title">Phân công</h2>
    {emails.length === 0 ? <p className="small muted">Chưa mời người chấm.</p>
      : rows.length === 0 ? <p className="small muted">Nghiên cứu chưa có gói nào.</p> : <>
        <div className="assign-table">
          <table>
            <thead><tr><th scope="col">Cặp</th>{emails.map((email) => <th scope="col" key={email} title={email}>{email.split("@")[0]}</th>)}</tr></thead>
            <tbody>{rows.map((row, i) => {
              const codes = row.map((item) => item.anon_code).join(", ");
              return <tr key={keys[i]}>
                <th scope="row"><strong>{codes}</strong><div className="small muted">{row[0].packet.topic}</div></th>
                {emails.map((email) => <td key={email}>
                  <input type="checkbox" aria-label={`${email} chấm ${codes}`} checked={ticked(email, keys[i])}
                    disabled={busy} onChange={() => toggle(email, keys[i])} />
                </td>)}
              </tr>;
            })}</tbody>
          </table>
        </div>
        <div><button type="button" className="ui-button primary" disabled={busy} onClick={() => void save()}>
          {busy ? "Đang lưu…" : "Lưu phân công"}
        </button></div>
      </>}
  </section>;
}
