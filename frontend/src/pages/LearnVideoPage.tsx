import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getLearnVideo, type LearnVideo } from "../api";
import { LearnerShell } from "../components/LearnerShell";
import { formatClock } from "../lib/duration";
import { useRenderedSource } from "../lib/useRenderedSource";

// Keyed by the video, so that moving to another one drops the old player, its position and a failed load.
export function LearnVideoPage() {
  const { courseId, videoId } = useParams();
  return <LearnVideoWatch key={videoId} courseId={courseId} videoId={videoId} />;
}

function LearnVideoWatch({ courseId, videoId }: { courseId?: string; videoId?: string }) {
  const [video, setVideo] = useState<LearnVideo | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    if (!videoId) return;
    getLearnVideo(videoId).then(
      (loaded) => !cancelled && setVideo(loaded),
      () => !cancelled && setFailed(true),
    );
    return () => {
      cancelled = true;
    };
  }, [videoId]);

  return (
    <LearnerShell>
      <p className="learn-back"><Link to={`/learn/${courseId}`} className="link-action">Về trang học</Link></p>
      {failed ? (
        <p role="alert" className="ui-alert">Không tải được video. Video có thể đã được ẩn.</p>
      ) : !video ? (
        <p role="status" className="muted">Đang tải...</p>
      ) : (
        <Watch video={video} />
      )}
    </LearnerShell>
  );
}

// The link lasts a few minutes, so a player left open fails when it is used again: one new link, and
// the learner is back where they were.
const MAX_LINK_RENEWALS = 1;

function Watch({ video: first }: { video: LearnVideo }) {
  const player = useRef<HTMLVideoElement>(null);
  const [current, setCurrent] = useState(0);
  const [updated, setUpdated] = useState(false);
  // Where to go on once a fresh link has loaded, and the scene that was playing.
  const resume = useRef<{ at: number; playing: boolean; scene: number } | null>(null);
  const shownVersion = useRef(first.rendered_version);
  const currentScene = useRef(0);

  const { src, fresh, failed, onError, loaded } = useRenderedSource(
    () => getLearnVideo(first.id).then((v) => ({ ...v, url: v.file_url })),
    {
      initial: { ...first, url: first.file_url },
      maxRenewals: MAX_LINK_RENEWALS,
      beforeRenew: () => {
        const element = player.current;
        if (element) resume.current = { at: element.currentTime, playing: !element.paused, scene: currentScene.current };
      },
    },
  );
  // The new link comes with the scenes of its render, which may be a newer one than the file shown.
  const video = fresh ?? first;

  const play = (element: HTMLVideoElement) => {
    // A browser may refuse to play without a gesture; the controls are right there then.
    Promise.resolve(element.play()).catch(() => undefined);
  };

  const metadataLoaded = () => {
    loaded();
    const element = player.current;
    const back = resume.current;
    if (!element || !back) return;
    resume.current = null;
    if (video.rendered_version !== shownVersion.current) {
      // Another file: the old time means nothing in it. Start at the scene that was playing, or the first.
      shownVersion.current = video.rendered_version;
      const scene = video.scenes[back.scene] ?? video.scenes[0];
      element.currentTime = scene?.start_seconds ?? 0;
      setCurrent(video.scenes[back.scene] ? back.scene : 0);
      setUpdated(true);
    } else {
      element.currentTime = back.at;
    }
    // Resumes playing only if it was; a paused video stays paused where it was.
    if (back.playing) play(element);
  };

  const timeUpdate = () => {
    const time = player.current?.currentTime ?? 0;
    let index = 0;
    video.scenes.forEach((scene, i) => {
      if (scene.start_seconds <= time) index = i;
    });
    currentScene.current = index;
    setCurrent(index);
  };

  const seek = (index: number) => {
    const element = player.current;
    if (!element) return;
    element.currentTime = video.scenes[index].start_seconds;
    currentScene.current = index;
    setCurrent(index);
    play(element);
  };

  return (
    <>
      <h1 className="page-title">{video.title ?? "Bài giảng"}</h1>
      {updated && <p role="status" className="muted">Giảng viên vừa cập nhật video.</p>}
      {failed ? (
        <p role="alert" className="ui-alert">Không phát được video. Hãy tải lại trang.</p>
      ) : (
        <video
          ref={player}
          className="video-player learn-video"
          aria-label={video.title ?? "Bài giảng"}
          src={src ?? undefined}
          controls
          preload="metadata"
          playsInline
          onTimeUpdate={timeUpdate}
          onLoadedMetadata={metadataLoaded}
          onError={onError}
        />
      )}
      {video.scenes.length > 0 && video.source_document && (
        <p className="learn-scenes-source">Nguồn: {video.source_document}</p>
      )}
      {video.scenes.length > 0 && (
        <ol className="learn-scenes" aria-label="Các cảnh">
          {video.scenes.map((scene, i) => (
            <li key={i}>
              {/* A button, so that enter and space seek and a screen reader announces it as one. */}
              <button
                type="button" className="learn-scene" onClick={() => seek(i)}
                aria-current={i === current ? "true" : undefined}
              >
                <span className="learn-scene-time">{formatClock(scene.start_seconds)}</span>
                <span className="learn-scene-title">{scene.title}</span>
                {/* One line: a line per source made a scene of five sources 290 px tall at 390 wide. */}
                {scene.sources.length > 0 && (
                  <span className="learn-scene-source">Nguồn: {scene.sources.join("; ")}</span>
                )}
              </button>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}
