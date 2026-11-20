import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { AppShell } from "../components/AppShell";
import { Tabs } from "../components/Tabs";
import { RatingForm } from "../components/RatingForm";
import { modelMarkdownProps } from "../lib/markdownMath";
import { notifyFailed } from "../lib/notify";
import { apiErrorMessage } from "../api/client";
import { formatSize } from "../lib/fileSize";
import { useRenderedSource } from "../lib/useRenderedSource";
import {
  downloadPacketReference,
  getPacketVideo,
  myStudies,
  myStudyItems,
  saveRating,
  type MyStudy,
  type BlindStudy,
  type BlindItem,
  type PacketQuestion,
  type FlashcardPacket,
  type ModulePacket,
  type VideoPacket,
} from "../api/studies";

export function StudiesPage() {
  const [studies, setStudies] = useState<MyStudy[] | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    myStudies()
      .then((data) => {
        if (active) setStudies(data);
      })
      .catch(() => {
        if (active) setError(true);
      });
    return () => {
      active = false;
    };
  }, []);
  return (
    <AppShell title="Nghiên cứu">
      {error && <p role="alert">Không tải được danh sách nghiên cứu.</p>}
      {!studies && !error && <p>Đang tải…</p>}
      {studies?.length === 0 && <p>Bạn chưa được mời chấm nghiên cứu nào.</p>}
      <ul className="space-y-4">
        {studies?.map((study) => (
          <li key={study.id} className="ui-card p-4">
            <Link to={`/studies/${study.id}`} className="font-semibold text-accent">
              {study.name}
            </Link>
            <p>
              Đã chấm {study.rated}/{study.total}
            </p>
          </li>
        ))}
      </ul>
    </AppShell>
  );
}

function Question({ question, explanation = false }: { question: PacketQuestion; explanation?: boolean }) {
  return (
    <div className="my-4">
      <p className="font-semibold">{question.stem}</p>
      <ol className="ml-6 list-[upper-alpha]">
        {question.choices.map((choice, i) => (
          <li key={i}>
            {choice}
            {explanation && i === question.correct_index && <> <Check aria-label="Đáp án đúng" className="inline size-4 align-[-2px]" /></>}
          </li>
        ))}
      </ol>
      {explanation && <p className="mt-2">
        <strong>Lời giải thích: </strong>
        {question.explanation}
      </p>}
    </div>
  );
}
function Packet({ studyId, item }: { studyId: string; item: BlindItem }) {
  if (item.kind === "flashcards") return <FlashcardsPacketView packet={item.packet} />;
  if (item.kind === "video") return <VideoPacketView studyId={studyId} itemId={item.id} packet={item.packet} />;
  return <ModulePacketView packet={item.packet} />;
}

function FlashcardsPacketView({ packet }: { packet: FlashcardPacket }) {
  return (
    <article className="packet ui-card">
      <section>
        <h2>Flashcard <span className="count">{packet.cards.length}</span></h2>
        <ol className="packet-cards">
          {packet.cards.map((card, i) => <li key={i}>
            <p className="font-semibold">{card.front}</p>
            <p>{card.back}</p>
          </li>)}
        </ol>
      </section>
    </article>
  );
}

function VideoPacketView({ studyId, itemId, packet }: { studyId: string; itemId: string; packet: VideoPacket }) {
  const [tab, setTab] = useState(packet.has_video ? "video" : "scenes");
  const tabs = [...(packet.has_video ? [{ id: "video", label: "Video" }] : []), { id: "scenes", label: "Các cảnh" }];
  return (
    <article className="packet ui-card">
      <Tabs tabs={tabs} selected={tab} onSelect={setTab} label="Thành phần gói chấm">
      {tab === "video" && <section><h2 className="sr-only">Video</h2><PacketVideo studyId={studyId} itemId={itemId} /></section>}
      {tab === "scenes" && <section>
        <h2 className="sr-only">Các cảnh</h2>
        {packet.scenes.map((scene, i) => <div key={i} className="mt-3">
          <h3>{i + 1}. {scene.title}</h3>
          {scene.bullets.length > 0 && <ul className="ml-5 list-disc">{scene.bullets.map((b, j) => <li key={j}>{b}</li>)}</ul>}
          <p className="small muted mt-1 whitespace-pre-line">{scene.narration}</p>
        </div>)}
      </section>}
      </Tabs>
    </article>
  );
}

function PacketVideo({ studyId, itemId }: { studyId: string; itemId: string }) {
  // An S3 link lasts minutes, so a player that fails to load asks for a new one, twice at most.
  const { src, failed, onError, loaded } = useRenderedSource(() => getPacketVideo(studyId, itemId), { maxRenewals: 2 });
  if (failed) return <p className="text-sm danger-text">Không tải được video. Hãy tải lại trang.</p>;
  return <video aria-label="Video" src={src ?? undefined} controls preload="metadata" onError={onError}
    onLoadedMetadata={loaded} className="video-player" />;
}

function ModulePacketView({ packet: p }: { packet: ModulePacket }) {
  // No tab of source passages: raters check against the reference file, the document itself.
  const [tab, setTab] = useState("objectives");
  const tabs = [
    { id: "objectives", label: "Mục tiêu" }, { id: "content", label: "Nội dung" },
    { id: "slides", label: "Slide" }, { id: "examples", label: "Ví dụ" },
    { id: "practice", label: "Luyện tập" }, { id: "quiz", label: "Quiz" },
    { id: "answers", label: "Giải thích đáp án" },
  ];
  return (
    <article className="packet ui-card">
      <Tabs tabs={tabs} selected={tab} onSelect={setTab} label="Thành phần gói chấm">
      {tab === "objectives" && <section>
        <h2 className="font-semibold">Mục tiêu học tập</h2>
        <p>{p.learning_objective}</p>
        <ul className="ml-5 list-disc">
          {p.sub_objectives.map((objective, i) => (
            <li key={i}>{objective}</li>
          ))}
        </ul>
      </section>}
      {tab === "content" && <section>
        <h2 className="font-semibold">Nội dung</h2>
        <div className="space-y-3 break-words">
          <Markdown
            skipHtml
            {...modelMarkdownProps}
            components={{
              a: ({ children }) => <span>{children}</span>,
            }}
          >
            {p.content_markdown}
          </Markdown>
        </div>
      </section>}
      {tab === "slides" && <section>
        <h2 className="font-semibold">Slides</h2>
        {p.slides.map((slide, i) => (
          <div key={i} className="mt-3">
            <h3>{slide.title}</h3>
            <ul className="ml-5 list-disc">
              {slide.bullets.map((bullet, j) => (
                <li key={j}>{bullet}</li>
              ))}
            </ul>
          </div>
        ))}
      </section>}
      {tab === "examples" && <section>
        <h2 className="font-semibold">Ví dụ</h2>
        {p.examples.map((example, i) => (
          <div key={i} className="mt-3">
            <p>{example.context}</p>
            <p>{example.analysis}</p>
          </div>
        ))}
      </section>}
      {tab === "quiz" && <section>
        <h2 className="font-semibold">Quiz</h2>
        {p.quiz.map((question, i) => (
          <Question key={i} question={question} />
        ))}
      </section>}
      {tab === "practice" && <section>
        <h2 className="font-semibold">Bài luyện tập</h2>
        <Question question={p.practice} />
      </section>}
      {tab === "answers" && <section><h2 className="font-semibold">Giải thích đáp án</h2>
        <h3>Luyện tập</h3><Question question={p.practice} explanation />
        <h3>Quiz</h3>{p.quiz.map((question, i) => <Question key={i} question={question} explanation />)}
      </section>}
      </Tabs>
    </article>
  );
}

// The file the admin picked for this packet, for the rater to open in an app of their own.
function PacketReference({ studyId, item }: { studyId: string; item: BlindItem }) {
  const [busy, setBusy] = useState(false);
  if (!item.reference) return null;
  const { filename, size } = item.reference;
  return (
    <div className="packet-reference">
      <span>Tài liệu gốc: <strong className="[overflow-wrap:anywhere]">{filename}</strong> <span className="muted">· {formatSize(size)}</span></span>
      <button type="button" className="link-action" disabled={busy} onClick={async () => {
        setBusy(true);
        try { await downloadPacketReference(studyId, item.id); }
        catch (e) { notifyFailed(apiErrorMessage(e, "Không tải được tài liệu gốc.")); }
        finally { setBusy(false); }
      }}>{busy ? "Đang tải…" : "Tải xuống"}</button>
    </div>
  );
}

export function BlindRatingPage() {
  const { studyId, itemId } = useParams();
  const [data, setData] = useState<BlindStudy | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    let active = true;
    myStudyItems(studyId!)
      .then((result) => {
        if (active) {
          setData(result);
          setError(false);
        }
      })
      .catch(() => {
        if (active) setError(true);
      });
    return () => {
      active = false;
    };
  }, [studyId]);
  const item = data?.items.find((i) => i.id === itemId);
  return (
    <AppShell title={item ? `Chấm mù: ${item.anon_code}` : "Chấm mù"}>
      {error ? (
        <p role="alert" className="mt-4">
          Không tải được nghiên cứu. Lời mời có thể đã bị thu hồi.
        </p>
      ) : !data ? (
        <p>Đang tải…</p>
      ) : (
        <>
          <div className="rating-context">
            <div><nav aria-label="Đường dẫn" className="small muted mb-2"><Link to="/studies">Nghiên cứu</Link> / {data.study.name}{item && ` / ${item.anon_code}`}</nav>
              {item && <p>{item.packet.topic}</p>}
              {item && <PacketReference key={item.id} studyId={studyId!} item={item} />}
            </div>
            <div className="rating-progress"><span className="small muted">Đã chấm {data.rated}/{data.total}</span>
              <progress aria-label="Tiến độ chấm" value={data.rated} max={data.total || 1} />
            </div>
          </div>
          <nav aria-label="Gói chấm" className="rating-items">
            {data.items.map((i) => (
              <Link
                key={i.id}
                aria-current={i.id === itemId ? "page" : undefined}
                to={`/studies/${studyId}/items/${i.id}`}
                className="rating-item"
              >
                {i.anon_code}
                {i.rating && <><Check aria-hidden="true" className="success-text mr-2 inline size-4" /><span className="sr-only"> · Đã chấm</span></>}
              </Link>
            ))}
          </nav>
          {item ? (
            <>
              <div className="rating-columns">
              {/* Siblings need keys of their own: sharing item.id left the packet just left in place
                  beside the next one, so the page kept every packet visited. */}
              <Packet key={`packet-${item.id}`} studyId={studyId!} item={item} />
              {data.study.status === "closed" ? (
                <p>Nghiên cứu đã đóng.</p>
              ) : (
                <RatingForm
                  key={`rating-${item.id}`}
                  kind={item.kind}
                  code={item.anon_code}
                  bloomLevel={item.kind === "module" ? item.packet.bloom_level : undefined}
                  rating={item.rating}
                  onSave={async (values) => {
                    const rating = await saveRating(studyId!, item.id, values);
                    setData((current) => {
                      if (!current) return current;
                      const items = current.items.map((i) =>
                        i.id === item.id ? { ...i, rating } as BlindItem : i,
                      );
                      return { ...current, items, rated: items.filter((i) => i.rating).length };
                    });
                  }}
                />
              )}
              </div>
            </>
          ) : itemId ? (
            <p role="alert">Không tìm thấy gói chấm.</p>
          ) : (
            <p>{data.total ? "Chọn mã ẩn danh để bắt đầu chấm." : "Chưa có gói để chấm."}</p>
          )}
        </>
      )}
    </AppShell>
  );
}
