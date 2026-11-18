import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getLearnFlashcardSet, type LearnFlashcardSet } from "../api";
import { LearnerShell } from "../components/LearnerShell";

// Keyed by the set, so that moving to another one starts again on its first card instead of
// carrying over the old index, the flipped side or a failed load.
export function LearnFlashcardsPage() {
  const { courseId, setId } = useParams();
  return <LearnFlashcardsStudy key={setId} courseId={courseId} setId={setId} />;
}

function LearnFlashcardsStudy({ courseId, setId }: { courseId?: string; setId?: string }) {
  const [set, setSet] = useState<LearnFlashcardSet | null>(null);
  const [failed, setFailed] = useState(false);
  const [index, setIndex] = useState(0);
  const [flipped, setFlipped] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!setId) return;
    getLearnFlashcardSet(setId).then(
      (loaded) => !cancelled && setSet(loaded),
      () => !cancelled && setFailed(true),
    );
    return () => {
      cancelled = true;
    };
  }, [setId]);

  const back = <Link to={`/learn/${courseId}`} className="link-action">Về trang học</Link>;
  const card = set?.cards[index];
  const go = (to: number) => {
    // A card is read front first, so the back left open on the last one is closed again.
    setIndex(to);
    setFlipped(false);
  };

  return (
    <LearnerShell>
      <p className="learn-back">{back}</p>
      {failed ? (
        <p role="alert" className="ui-alert">Không tải được bộ thẻ. Bộ thẻ có thể đã được ẩn.</p>
      ) : !set || !card ? (
        <p role="status" className="muted">{set ? "Bộ thẻ này chưa có thẻ nào." : "Đang tải…"}</p>
      ) : (
        <>
          <h1 className="page-title">{set.focus ?? "Bộ thẻ"}</h1>
          {/* A button, so that space and enter flip it and a screen reader announces it as one. */}
          <button type="button" className="learn-card" onClick={() => setFlipped((value) => !value)}>
            <span className="learn-card-side">{flipped ? "Mặt sau" : "Mặt trước"}</span>
            <span className="learn-card-text">{flipped ? card.back : card.front}</span>
            {flipped && card.sources.length > 0 && (
              <span className="learn-card-sources">
                {card.sources.map((source) => (
                  <span key={source}>Nguồn: {source}</span>
                ))}
              </span>
            )}
          </button>
          <p className="muted learn-hint">Bấm vào thẻ hoặc nhấn phím cách để lật.</p>
          <div className="learn-nav">
            <button type="button" className="ui-button" disabled={index === 0} onClick={() => go(index - 1)}>
              Thẻ trước
            </button>
            <span aria-live="polite">Thẻ {index + 1}/{set.cards.length}</span>
            <button
              type="button" className="ui-button"
              disabled={index === set.cards.length - 1} onClick={() => go(index + 1)}
            >
              Thẻ sau
            </button>
          </div>
        </>
      )}
    </LearnerShell>
  );
}
