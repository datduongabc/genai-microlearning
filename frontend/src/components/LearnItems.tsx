import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { formatClock } from "../lib/duration";
import { listLearnItems, type LearnItems as Items } from "../api";

// "giao 16/11": two items of one focus differ only by when the lecturer handed them out.
function deliveredText(iso: string) {
  const at = new Date(iso);
  const two = (n: number) => String(n).padStart(2, "0");
  return `giao ${two(at.getDate())}/${two(at.getMonth() + 1)}`;
}

function duration(seconds: number | null) {
  return seconds == null ? null : formatClock(seconds);
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
  if (!items) return <p role="status" className="muted learn-items">Đang tải...</p>;

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
            {sets.map((set) => (
              <li key={set.id}>
                <Link to={`/learn/${courseId}/flashcards/${set.id}`} className="learn-link">
                  <span>{set.focus ?? "Bộ thẻ"}</span>
                  <span className="muted">{set.card_count} thẻ · {deliveredText(set.published_at)}</span>
                </Link>
              </li>
            ))}
            {videos.map((video) => (
              <li key={video.id}>
                <Link to={`/learn/${courseId}/videos/${video.id}`} className="learn-link">
                  <span>Video: {video.focus ?? "Bài giảng"}</span>
                  <span className="muted">
                    {[duration(video.duration_seconds), deliveredText(video.published_at)].filter(Boolean).join(" · ")}
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </section>
  );
}
