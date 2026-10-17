import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Markdown from "react-markdown";
import { AppShell } from "../components/AppShell";
import { Tabs } from "../components/Tabs";
import { RatingForm } from "../components/RatingForm";
import {
  myStudies,
  myStudyItems,
  saveRating,
  type MyStudy,
  type BlindStudy,
  type BlindItem,
  type PacketQuestion,
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
    <AppShell title="Nghiên cứu được mời">
      {error && <p role="alert">Không tải được danh sách nghiên cứu.</p>}
      {!studies && !error && <p>Đang tải…</p>}
      {studies?.length === 0 && <p>Bạn chưa được mời chấm nghiên cứu nào.</p>}
      <ul className="space-y-4">
        {studies?.map((study) => (
          <li key={study.id} className="rounded border bg-white p-4">
            <Link to={`/studies/${study.id}`} className="font-semibold text-terracotta">
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
            {explanation && i === question.correct_index && <strong> (Đáp án đúng)</strong>}
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
function Packet({ item }: { item: BlindItem }) {
  const p = item.packet;
  const [tab, setTab] = useState("sources");
  const tabs = [
    { id: "sources", label: `Đoạn nguồn (${p.source_chunks.length})` },
    { id: "objectives", label: "Mục tiêu" }, { id: "content", label: "Nội dung" },
    { id: "slides", label: "Slide" }, { id: "examples", label: "Ví dụ" },
    { id: "practice", label: "Luyện tập" }, { id: "quiz", label: "Quiz" },
    { id: "answers", label: "Giải thích đáp án" },
  ];
  return (
    <article className="packet ui-card">
      <Tabs tabs={tabs} selected={tab} onSelect={setTab} label="Thành phần gói chấm">
      {tab === "sources" && <section>
        <h2 className="sr-only">Tài liệu nguồn</h2>
        {p.source_chunks.map((source, i) => (
          <p className="source-passage whitespace-pre-wrap" key={i}>
            {source}
          </p>
        ))}
      </section>}
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
            components={{
              a: ({ children }) => <span>{children}</span>,
              img: ({ alt }) => <span>{alt}</span>,
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
    <AppShell title={item ? `Chấm mù · ${item.anon_code}` : "Chấm mù"}>
      {error ? (
        <p role="alert" className="mt-4">
          Không tải được nghiên cứu. Lời mời có thể đã bị thu hồi.
        </p>
      ) : !data ? (
        <p>Đang tải…</p>
      ) : (
        <>
          <div className="rating-context">
            <div><div className="small muted mb-2"><Link to="/studies">Nghiên cứu được mời</Link> / {data.study.name}</div>
              {item && <p>{item.packet.topic}</p>}
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
                {i.rating && <><span aria-hidden="true" className="success-text mr-2">✓</span><span className="sr-only"> · Đã chấm</span></>}
              </Link>
            ))}
          </nav>
          {item ? (
            <>
              <div className="rating-columns">
              <Packet key={item.id} item={item} />
              {data.study.status === "closed" ? (
                <p>Nghiên cứu đã đóng.</p>
              ) : (
                <RatingForm
                  key={item.id}
                  code={item.anon_code}
                  bloomLevel={item.packet.bloom_level}
                  rating={item.rating}
                  onSave={async (values) => {
                    const rating = await saveRating(studyId!, item.id, values);
                    setData((current) => {
                      if (!current) return current;
                      const items = current.items.map((i) =>
                        i.id === item.id ? { ...i, rating } : i,
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
