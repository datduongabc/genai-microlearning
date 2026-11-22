import { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { downloadStudyFile, getStudyResults, type StudyResults as Results } from "../api/studies";
import { apiErrorMessage } from "../api/client";
import { GROUP_LABEL, ITEM_WRITING_FLAWS, mediaRubric, rubric } from "../lib/ratingSchema";
import { notifyFailed } from "../lib/notify";

const FIELD_LABEL: Record<string, string> = {
  content: "Nội dung", slides: "Slide", example: "Ví dụ", quiz: "Quiz", practice: "Bài luyện tập", explanation: "Lời giải thích",
  card: "Thẻ", narration: "Lời đọc",
};
const FLAW_LABEL: Record<string, string> = Object.fromEntries(ITEM_WRITING_FLAWS);
const CRITERION_LABEL: Record<string, string> = Object.fromEntries(
  [...rubric, ...mediaRubric.flashcards, ...mediaRubric.video].map((c) => [c.key, c.label]));

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
            <div className="font-semibold [overflow-wrap:anywhere]">{r.name}</div>
            <div className="small muted [overflow-wrap:anywhere]">{r.email}{r.rated === 0 && ", chưa chấm gói nào"}{!r.invited && ", đã thôi giao"}</div>
          </div>
          <div className="results-progress">
            <span className="small">{r.rated}/{r.assigned}</span>
            <progress aria-label={`Tiến độ ${r.email}`} value={r.rated} max={r.assigned || 1} />
          </div>
          {r.number && r.module_rated > 0 && <button type="button" className="link-action"
            onClick={() => void download(`raters/${r.number}.csv`, `rater-${r.number}.csv`)}>Tải CSV mô-đun</button>}
        </li>)}
      </ul>
    )}
    {(results.has_media_ratings || results.has_judge) && <div className="mt-3 flex flex-wrap gap-2">
      {results.has_media_ratings && <button type="button" className="ui-button"
        onClick={() => void download("media.csv", "flashcard-video.csv")}>Tải CSV flashcard và video</button>}
      {results.has_judge && <button type="button" className="ui-button"
        onClick={() => void download("judge.jsonl", "judge.jsonl")}>Tải kết quả mô hình chấm</button>}
    </div>}
    {results.unpaired.length > 0 && <p className="mt-3 small muted">
      Không ghép được cặp cho {results.unpaired.join(", ")}: các gói này không vào phép so sánh RAG và không RAG.
    </p>}

    <h3 className="results-subtitle">Theo từng gói</h3>
    {rated.length === 0 ? <p className="small muted">Chưa có gói nào được chấm.</p>
      : rated.map((item) => <ResultItem key={item.id} item={item} />)}
  </section>;
}

// One packet as a card: its code and name, opened by the arrow. Inside, a row per criterion with
// each rater's score and comment side by side, then what they flagged as made up and their notes.
function ResultItem({ item }: { item: Results["items"][number] }) {
  const [open, setOpen] = useState(false);
  const body = `result-${item.id}`;
  // A flashcard set or video says what it is by its code (F, V); a module's group is what the code hides.
  const media = item.group === "flashcards" || item.group === "video";
  return <div className="result-item" data-open={open}>
    <div className="result-head">
      <span className="font-semibold">{item.anon_code}</span>
      {!media && <span className="result-group" data-group={item.group}>{GROUP_LABEL[item.group] ?? item.group}</span>}
      <span className="min-w-0 flex-1 truncate">{item.topic}</span>
      <span className="small muted">{item.ratings.length} lượt chấm</span>
      <button type="button" className="icon-button" aria-expanded={open} aria-controls={body}
        aria-label={`${open ? "Thu gọn" : "Xem"} ${item.anon_code}`} onClick={() => setOpen(!open)}>
        <ChevronDown aria-hidden="true" className="result-chevron" />
      </button>
    </div>
    {open && <div id={body} className="result-table">
      <table>
        <thead>
          <tr><th rowSpan={2}>Tiêu chí</th>{item.ratings.map((r, i) => <th key={i} colSpan={2} className="result-rater-head">{r.rater}</th>)}</tr>
          <tr>{item.ratings.map((_, i) => [<th key={`s${i}`} className="result-score result-rater-head">Điểm</th>, <th key={`n${i}`}>Nhận xét</th>])}</tr>
        </thead>
        <tbody>
          {item.criteria.map((c) => <tr key={c}>
            <th scope="row">{CRITERION_LABEL[c] ?? c}</th>
            {item.ratings.map((r, i) => [
              <td key={`s${i}`} className="result-score result-rater-head">{r.scores[c]?.score ?? ""}</td>,
              <td key={`n${i}`}>{r.scores[c]?.justification ?? ""}</td>,
            ])}
          </tr>)}
          {item.ratings.some((r) => r.quiz_flaws?.length) && <tr>
            <th scope="row">Lỗi câu hỏi</th>
            {item.ratings.map((r, i) => <td key={i} colSpan={2} className="result-rater-head">
              {!r.quiz_flaws?.some((q) => q.flaws.length) ? <span className="muted">Không có</span> : <ul className="result-findings">
                {r.quiz_flaws.filter((q) => q.flaws.length).map((q) => <li key={q.question}>
                  <span className="result-field">{q.question === r.quiz_flaws.length ? "Bài luyện tập" : `Câu ${q.question}`}</span>
                  <span>{q.flaws.map((f) => FLAW_LABEL[f] ?? f).join("; ")}</span>
                </li>)}
              </ul>}
            </td>)}
          </tr>}
          <tr>
            <th scope="row">Thông tin bịa</th>
            {item.ratings.map((r, i) => <td key={i} colSpan={2} className="result-rater-head">
              {r.findings.length === 0 ? <span className="muted">Không có</span> : <ul className="result-findings">
                {r.findings.map((f, j) => <li key={j}>
                  <span className="result-field">{FIELD_LABEL[f.field] ?? f.field}</span>
                  <span>{f.claim}</span>
                  {f.reason && <span className="muted">Lý do: {f.reason}</span>}
                </li>)}
              </ul>}
            </td>)}
          </tr>
          <tr>
            <th scope="row">Ghi chú</th>
            {item.ratings.map((r, i) => <td key={i} colSpan={2} className="result-rater-head">{r.notes || <span className="muted">Không có</span>}</td>)}
          </tr>
        </tbody>
      </table>
    </div>}
  </div>;
}
