import { useCallback, useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { getLearnVideo, videoFileBlob, type LearnVideo } from "../api";
import { LearnerShell } from "../components/LearnerShell";
import { formatClock } from "../lib/duration";

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

function Watch({ video }: { video: LearnVideo }) {
  const player = useRef<HTMLVideoElement>(null);
  const [fileUrl, setFileUrl] = useState(video.file_url);
  const [blobSrc, setBlobSrc] = useState<string | null>(null);
  const [current, setCurrent] = useState(0);
  const [failed, setFailed] = useState(false);
  // Where to go on and whether to play once a fresh link has loaded.
  const resume = useRef<{ at: number; playing: boolean } | null>(null);
  const renewed = useRef(false);

  // Without S3 the file is an API path that needs the token, so it comes down as a blob.
  const viaApi = fileUrl.startsWith("/");
  const src = viaApi ? blobSrc : fileUrl;
  useEffect(() => {
    if (!viaApi) return;
    let gone = false;
    let created: string | null = null;
    videoFileBlob(fileUrl).then(
      (blob) => {
        if (gone) return;
        created = URL.createObjectURL(blob);
        setBlobSrc(created);
      },
      () => !gone && setFailed(true),
    );
    return () => {
      gone = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [fileUrl, viaApi]);

  const play = (element: HTMLVideoElement) => {
    // A browser may refuse to play without a gesture; the controls are right there then.
    Promise.resolve(element.play()).catch(() => undefined);
  };

  // The link lasts a few minutes, so a player left open fails when it is used again. One new link, and
  // the learner is back where they were; a file that still fails is not asked for again.
  const renew = useCallback(async () => {
    const element = player.current;
    if (renewed.current || !element) {
      setFailed(true);
      return;
    }
    renewed.current = true;
    resume.current = { at: element.currentTime, playing: !element.paused };
    try {
      const fresh = await getLearnVideo(video.id);
      setFileUrl(fresh.file_url);
    } catch {
      setFailed(true);
    }
  }, [video.id]);

  const loaded = () => {
    const element = player.current;
    if (!element || !resume.current) return;
    element.currentTime = resume.current.at;
    // Resumes playing only if it was; a paused video stays paused where it was.
    if (resume.current.playing) play(element);
    resume.current = null;
  };

  const timeUpdate = () => {
    const time = player.current?.currentTime ?? 0;
    let index = 0;
    video.scenes.forEach((scene, i) => {
      if (scene.start_seconds <= time) index = i;
    });
    setCurrent(index);
  };

  const seek = (index: number) => {
    const element = player.current;
    if (!element) return;
    element.currentTime = video.scenes[index].start_seconds;
    setCurrent(index);
    play(element);
  };

  return (
    <>
      <h1 className="page-title">{video.title ?? "Bài giảng"}</h1>
      {failed ? (
        <p role="alert" className="ui-alert">Không phát được video. Hãy tải lại trang.</p>
      ) : (
        <video
          ref={player}
          className="learn-video"
          aria-label={video.title ?? "Bài giảng"}
          src={src ?? undefined}
          controls
          preload="metadata"
          playsInline
          onTimeUpdate={timeUpdate}
          onLoadedMetadata={loaded}
          // Only a link expires; a blob that fails to play would fail again.
          onError={src && !src.startsWith("blob:") ? renew : () => setFailed(true)}
        />
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
                {scene.sources.map((source) => (
                  <span key={source} className="learn-scene-source">Nguồn: {source}</span>
                ))}
              </button>
            </li>
          ))}
        </ol>
      )}
    </>
  );
}
