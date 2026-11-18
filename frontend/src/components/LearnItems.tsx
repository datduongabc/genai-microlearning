import { useEffect, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { formatClock } from "../lib/duration";
import { listLearnItems, type LearnItems as Items } from "../api";

// Two items of one focus differ only by when the lecturer handed them out, so each row ends with that day.
function dayMonth(iso: string) {
  const at = new Date(iso);
  const two = (n: number) => String(n).padStart(2, "0");
  return `${two(at.getDate())}/${two(at.getMonth() + 1)}`;
}

function Delivered({ iso }: { iso: string }) {
  return <time dateTime={iso} className="learn-date">Giao {dayMonth(iso)}</time>;
}

const CARDS_ICON = <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="3" y="7" width="14" height="14" rx="2" /><path d="M7 3h12a2 2 0 0 1 2 2v12" /></svg>;
const PLAY_ICON = <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5.5v13a1 1 0 0 0 1.5.86l10.5-6.5a1 1 0 0 0 0-1.72L9.5 4.64A1 1 0 0 0 8 5.5z" /></svg>;

function duration(seconds: number | null) {
  return seconds == null ? null : formatClock(seconds);
}

// Lecturers often hand out a newer take on the same focus. The newest leads, the earlier ones fold
// under it, so a student opens the right one first and can still find the others.
function byFocus<T extends { focus: string | null; published_at: string }>(list: T[]) {
  const versions = new Map<string, T[]>();
  for (const item of list) {
    const key = (item.focus ?? "").trim().toLowerCase();
    versions.set(key, [...(versions.get(key) ?? []), item]);
  }
  return [...versions.values()].map((all) => {
    const [latest, ...older] = [...all].sort((x, y) => y.published_at.localeCompare(x.published_at));
    return { latest, older };
  });
}

function Earlier({ count, children }: { count: number; children: ReactNode }) {
  if (count === 0) return null;
  return <details className="learn-older">
    <summary>{count} bản giao trước</summary>
    <ul className="learn-list">{children}</ul>
  </details>;
}

function SetLink({ courseId, set }: { courseId: string; set: Items["flashcard_sets"][number] }) {
  return <Link to={`/learn/${courseId}/flashcards/${set.id}`} className="learn-link"
    aria-label={`Bộ thẻ ${set.focus ?? ""}`.trim() + `, ${set.card_count} thẻ, giao ${dayMonth(set.published_at)}`}>
    <span className="learn-icon" aria-hidden="true">{CARDS_ICON}</span>
    <span className="learn-title">{set.focus ?? "Bộ thẻ"}</span>
    <span className="learn-meta"><span>{set.card_count} thẻ</span><Delivered iso={set.published_at} /></span>
  </Link>;
}
function VideoLink({ courseId, video }: { courseId: string; video: Items["videos"][number] }) {
  return <Link to={`/learn/${courseId}/videos/${video.id}`} className="learn-link"
    aria-label={[`Video ${video.focus ?? "bài giảng"}`, duration(video.duration_seconds), `giao ${dayMonth(video.published_at)}`].filter(Boolean).join(", ")}>
    <span className="learn-icon" aria-hidden="true">{PLAY_ICON}</span>
    <span className="learn-title">{video.focus ?? "Bài giảng"}</span>
    <span className="learn-meta"><span>{duration(video.duration_seconds)}</span><Delivered iso={video.published_at} /></span>
  </Link>;
}

// What the lecturer published for the course, grouped by the document it was made from.
export function LearnItems({ courseId }: { courseId: string }) {
  const [items, setItems] = useState<Items | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    listLearnItems(courseId).then(
      (loaded) => !cancelled && setItems(loaded),
      () => !cancelled && setFailed(true),
    );
    return () => {
      cancelled = true;
    };
  }, [courseId]);

  // The chat below does not depend on this list, so a failure here is a line, not a blocked page.
  if (failed) return <p role="alert" className="ui-alert learn-items">Không tải được danh sách bộ thẻ và video.</p>;
  if (!items) return <p role="status" className="muted learn-items">Đang tải…</p>;

  const groups = new Map<string, { filename: string; sets: Items["flashcard_sets"]; videos: Items["videos"] }>();
  const group = (document: { id: string; filename: string }) => {
    if (!groups.has(document.id)) groups.set(document.id, { filename: document.filename, sets: [], videos: [] });
    return groups.get(document.id)!;
  };
  items.flashcard_sets.forEach((set) => group(set.document).sets.push(set));
  items.videos.forEach((video) => group(video.document).videos.push(video));

  if (groups.size === 0) return <p className="muted learn-items">Giảng viên chưa giao bộ thẻ hay video nào.</p>;

  return (
    <section className="learn-items" aria-label="Bộ thẻ và video">
      {[...groups].map(([id, { filename, sets, videos }]) => (
        <div key={id} className="learn-group ui-card">
          <h2 className="learn-group-title">{filename}</h2>
          <ul className="learn-list">
            {byFocus(sets).map(({ latest, older }) => (
              <li key={latest.id}>
                <SetLink courseId={courseId} set={latest} />
                <Earlier count={older.length}>{older.map((set) => <li key={set.id}><SetLink courseId={courseId} set={set} /></li>)}</Earlier>
              </li>
            ))}
            {byFocus(videos).map(({ latest, older }) => (
              <li key={latest.id}>
                <VideoLink courseId={courseId} video={latest} />
                <Earlier count={older.length}>{older.map((video) => <li key={video.id}><VideoLink courseId={courseId} video={video} /></li>)}</Earlier>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
