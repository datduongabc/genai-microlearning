import { useEffect, useState } from "react";
import { downloadStudyFile, getStudyResults, type StudyResults as Results } from "../api/studies";
import { apiErrorMessage } from "../api/client";
import { rubric } from "../lib/ratingSchema";
import { notifyFailed } from "../lib/notify";

const GROUP_LABEL: Record<string, string> = { rag_llm: "RAG", llm_no_rag: "Không RAG", human: "Người viết" };
const FIELD_LABEL: Record<string, string> = {
  content: "Nội dung", slides: "Slide", example: "Ví dụ", quiz: "Quiz", practice: "Bài luyện tập", explanation: "Lời giải thích",
};
const CRITERION_LABEL: Record<string, string> = Object.fromEntries(rubric.map((c) => [c.key, c.label]));

async function save(studyId: string, file: string, name: string) {
  const url = URL.createObjectURL(await downloadStudyFile(studyId, file));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

// What the raters gave in one study: who has rated how much, and each packet's scores and
// findings, so the admin can read what to fix without running the export on the server.
export function StudyResults({ studyId }: { studyId: string }) {
  const [results, setResults] = useState<Results | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    getStudyResults(studyId).then((data) => { if (active) setResults(data); })
      .catch((e) => { if (active) setError(apiErrorMessage(e, "Không tải được kết quả chấm.")); });
    return () => { active = false; };
  }, [studyId]);

  async function download(file: string, name: string) {
    try { await save(studyId, file, name); }
    catch (e) { notifyFailed(apiErrorMessage(e, "Không tải được tệp.")); }
  }

  if (error) return <section className="ui-card study-results"><p role="alert">{error}</p></section>;
  if (!results) return <section className="ui-card study-results"><p className="muted">Đang tải kết quả chấm…</p></section>;
  const rated = results.items.filter((item) => item.ratings.length > 0);
  return <section className="ui-card study-results" aria-labelledby="study-results-title">
    <h2 id="study-results-title">Kết quả chấm</h2>

    <h3 className="results-subtitle">Tiến độ</h3>
    {results.raters.length === 0 ? <p className="small muted">Chưa mời người chấm nào.</p> : (
      <ul className="results-raters" aria-label="Tiến độ người chấm">
        {results.raters.map((r) => <li key={r.email}>
          <div className="min-w-0">
            <div className="font-semibold [overflow-wrap:anywhere]">{r.email}</div>
            <div className="small muted">{r.number ? `Người chấm ${r.number}` : "Chưa chấm gói nào"}{!r.invited && ", đã thôi mời"}</div>
          </div>
          <div className="results-progress">
            <span className="small">{r.rated}/{results.total}</span>
            <progress aria-label={`Tiến độ ${r.email}`} value={r.rated} max={results.total || 1} />
          </div>
          {r.number && <button type="button" className="link-action"
            onClick={() => void download(`raters/${r.number}.csv`, `rater-${r.number}.csv`)}>Tải CSV</button>}
        </li>)}
      </ul>
    )}
    {results.has_judge && <button type="button" className="ui-button mt-3"
      onClick={() => void download("judge.jsonl", "judge.jsonl")}>Tải kết quả mô hình chấm</button>}
    {results.unpaired.length > 0 && <p className="mt-3 small muted">
      Không ghép được cặp cho {results.unpaired.join(", ")}: các gói này không vào phép so sánh RAG và không RAG.
    </p>}

    <h3 className="results-subtitle">Theo từng gói</h3>
    {rated.length === 0 ? <p className="small muted">Chưa có gói nào được chấm.</p> : rated.map((item) => (
      <details key={item.id} className="result-item">
        <summary>
          <span className="font-semibold">{item.anon_code}</span>
          <span className="result-group" data-group={item.group}>{GROUP_LABEL[item.group] ?? item.group}</span>
          <span className="min-w-0 flex-1 truncate">{item.topic}</span>
          <span className="small muted">{item.ratings.length} lượt chấm</span>
        </summary>
        <div className="result-table">
          <table>
            <thead><tr><th>Tiêu chí</th>{item.ratings.map((r) => <th key={r.rater}>{r.rater}</th>)}</tr></thead>
            <tbody>{results.criteria.map((c) => <tr key={c}>
              <td>{CRITERION_LABEL[c] ?? c}</td>
              {item.ratings.map((r) => <td key={r.rater} title={r.scores[c]?.justification}>{r.scores[c]?.score ?? ""}</td>)}
            </tr>)}</tbody>
          </table>
        </div>
        {item.ratings.map((r) => <div key={r.rater} className="result-rater">
          <h4>{r.rater}</h4>
          {r.findings.length > 0 ? <ul className="result-findings">
            {r.findings.map((f, i) => <li key={i}>
              <span className="result-field">{FIELD_LABEL[f.field] ?? f.field}</span>
              <span>{f.claim}</span>
              {f.reason && <span className="muted">Lý do: {f.reason}</span>}
            </li>)}
          </ul> : <p className="small muted">Không ghi thông tin bịa nào.</p>}
          {r.notes && <p className="small mt-2">Ghi chú: {r.notes}</p>}
          <details className="result-comments"><summary>Nhận xét theo tiêu chí</summary>
            <dl>{results.criteria.filter((c) => r.scores[c]?.justification).map((c) => <div key={c}>
              <dt>{CRITERION_LABEL[c] ?? c}</dt><dd>{r.scores[c].justification}</dd>
            </div>)}</dl>
          </details>
        </div>)}
      </details>
    ))}
  </section>;
}
