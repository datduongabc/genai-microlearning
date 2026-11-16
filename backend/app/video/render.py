"""The render job: a voice and a slide for each scene, assembled by ffmpeg into the MP4 learners watch.

Each scene is read by one TTS call, so a scene that fails costs only that scene's call again. A
scene whose narration and voice model are unchanged since its voice was made keeps that voice, so
an edit to one scene pays for one voice. The voices are saved as soon as they are made, before the
video is assembled: they are paid for, and a render that fails afterwards keeps them for the next.

The measured length is checked before anything is assembled: the estimate from the narration's
length (app/generation/video_script.py) can be wrong for a slow voice, and the 8-minute cap is on
the video. Over it, the job fails and the script stays as it is, for the lecturer to shorten.

Learners keep the video they had until the new one is stored and its key committed (Review Focus
1): the old file is deleted only then, and a render that fails partway leaves `video_key`,
`rendered_version` and the old file as they were.

Unlike the pictures job, the render keeps the queue's retries, since a retry is what recovers a
Chromium or ffmpeg failure. The price: when storing or committing the voices just made fails, they
are deleted with the run, and the retry pays for them again.
"""

import io
import logging
import math
import subprocess
import tempfile
import uuid
import wave
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.db import WorkerSessionLocal
from app.generation.media_common import values_kept_across_commits
from app.generation.video_script import MAX_SECONDS, Scene, normalize_narration, prepare_for_render
from app.jobs.queue import LostJob, hold
from app.llm.media import VOICES, MediaError, synthesize_speech, wait_before_retry
from app.models import Job, JobStatus, MediaStatus, Video
from app.storage import media_store, staging_dir
from app.video.media_jobs import (
    SceneFailure,
    delete_files,
    delete_unnamed_files,
    refusal_reason,
    scene_failure_message,
    scene_file_keys,
    store_bytes,
    store_file,
)
from app.video.slides import SlideRenderer
from app.video.sources import scene_source_labels

logger = logging.getLogger(__name__)

# From PATH: the backend image installs Debian's ffmpeg (backend/Dockerfile).
FFMPEG = "ffmpeg"
SIZE = (1280, 720)
FPS = 24
# Silence after each scene's voice, so that one slide's last word does not run into the next slide.
SCENE_PAD_SECONDS = 0.6
# HeyGen speaks at 44.1 kHz and Gemini at 24 kHz; the video's one audio track takes the higher.
AUDIO_RATE = 44_100
AUDIO_BITRATE = "128k"
# A reply without audio is often a one-off; a second try within the job is cheaper for the lecturer
# than a failed job, and a third is not worth the wait.
VOICE_ATTEMPTS = 2
# Voices made at once: a video has 8 to 10 scenes of 5 to 15 seconds' synthesis each, and more at
# once would only meet the provider's rate limit.
PARALLEL_VOICES = 3
# x264 starts 1.5 threads a core it sees, each with frames of its own: on a 20-core host that took
# 550 MB for one video, against 350 MB with two, at the same speed on the worker's one vCPU.
FFMPEG_THREADS = 2
# A 5-minute video encodes in about 35 seconds on one vCPU (the worker's); far past that, ffmpeg is stuck.
FFMPEG_TIMEOUT_SECONDS = 15 * 60
_SAMPLE_WIDTH = 2  # Speech.pcm is 16-bit mono


def _token() -> str:
    """Each run's files get names no other run uses, even for identical audio: a run deletes the
    files it stored when it fails, and must never delete one that a scene still plays."""
    return uuid.uuid4().hex[:12]


def voice_key(video_id: uuid.UUID, index: int) -> str:
    return f"videos/{video_id}/voice-{index}-{_token()}.wav"


def video_key(video_id: uuid.UUID, version: int) -> str:
    return f"videos/{video_id}/video-{version}-{_token()}.mp4"


def as_wav(pcm: bytes, sample_rate: int) -> bytes:
    """The voice as stored: WAV, the PCM the model returned with a 44-byte header. Lossless, so the
    video's AAC is the only lossy step; the header keeps the sample rate, which differs by model,
    and the exact length; ffmpeg and every browser read it. About 2.6 MB a 30-second scene at
    44.1 kHz, a few cents a year of storage for a whole video."""
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(_SAMPLE_WIDTH)
        w.setframerate(sample_rate)
        # A stray odd byte is half a sample; dropping it keeps every frame whole.
        w.writeframes(pcm[:len(pcm) - len(pcm) % _SAMPLE_WIDTH])
    return out.getvalue()


def wav_seconds(data: bytes) -> float:
    with wave.open(io.BytesIO(data)) as w:
        return w.getnframes() / w.getframerate()


def segment_seconds(voice_seconds: float) -> float:
    """How long a scene is on screen: its voice, the pad, rounded up to a whole frame so that the
    picture and the sound of every scene end together and nothing drifts over ten scenes."""
    return math.ceil(round((voice_seconds + SCENE_PAD_SECONDS) * FPS, 6)) / FPS


def voice_input(spoken: str) -> str:
    """The text the voice is sent: the normalized narration's sentences, one after the other."""
    return " ".join(spoken.split("\n"))


@dataclass
class _Voice:
    index: int
    spoken: str  # the normalized narration it reads, as stored in voice_text_used
    wav: bytes
    seconds: float
    served_model: str | None = None
    cost: float | None = None
    key: str | None = None


def _speak(alias: str, index: int, spoken: str) -> _Voice | SceneFailure:
    """One scene's voice, or why there is none. Never raises: a scene that fails must not cost the
    other scenes the voices already paid for."""
    label = VOICES[alias].label if alias in VOICES else alias
    reason = ""
    for attempt in range(1, VOICE_ATTEMPTS + 1):
        try:
            speech = synthesize_speech(alias, voice_input(spoken))
            wav = as_wav(speech.pcm, speech.sample_rate)
            seconds = wav_seconds(wav)
        except MediaError as busy:
            reason = f"model {label} không trả về âm thanh"
            if attempt < VOICE_ATTEMPTS:
                wait_before_retry(busy, attempt)
            continue
        except ValueError as refusal:
            # A refusal (no provider that keeps data_collection deny, a text it rejects) repeats on
            # every try; the lecturer needs the model and its words to choose another.
            return SceneFailure(index, refusal_reason(label, refusal, "giọng"))
        except Exception:
            logger.exception(f"[video_render] scene {index}: speech failed unexpectedly")
            return SceneFailure(index, f"lỗi hệ thống khi tạo giọng bằng model {label}")
        if seconds <= 0:
            reason = f"model {label} trả về âm thanh rỗng"
            continue
        return _Voice(index, spoken, wav, seconds, speech.served_model, speech.cost)
    return SceneFailure(index, reason)


def _read_stored(key: str) -> bytes | None:
    try:
        with media_store().local_copy(key) as path:
            return Path(path).read_bytes()
    except Exception:
        logger.warning(f"[video_render] could not read {key}", exc_info=True)
        return None


def _reused_voice(index: int, scene: Scene, spoken: str, alias: str) -> _Voice | None:
    if not scene.voice_key or scene.voice_text_used != spoken or scene.voice_alias_used != alias:
        return None
    wav = _read_stored(scene.voice_key)
    try:
        seconds = wav_seconds(wav) if wav is not None else 0.0
    except (wave.Error, EOFError):
        logger.warning(f"[video_render] {scene.voice_key} is not a readable WAV", exc_info=True)
        seconds = 0.0
    # A voice whose file is gone or broken is made again: a video cannot be assembled without it.
    return _Voice(index, spoken, wav, seconds, key=scene.voice_key) if seconds > 0 else None


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:.1f}".replace(".", ",")


def _render_slides(scenes: Sequence[Scene], images: dict[int, bytes], labels: Sequence[str], lang: str,
                   work: Path) -> list[Path]:
    """Every slide in one Chromium, which costs a second to start and a tenth of one a slide.

    In a thread of its own: Playwright's sync API refuses to start in a thread that already runs an
    event loop (app/video/slides.py, render_slide)."""
    def run() -> list[Path]:
        paths = []
        with SlideRenderer() as renderer:
            for i, scene in enumerate(scenes):
                path = work / f"slide-{i}.png"
                path.write_bytes(renderer.render(scene, images.get(i), labels[i], SIZE, lang))
                paths.append(path)
        return paths

    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(run).result()


def _assemble(slides: Sequence[Path], voices: Sequence[Path], seconds: Sequence[float], out: Path) -> None:
    """One encode of every scene, joined by ffmpeg's concat filter. Each scene is its slide held for
    its length and its voice resampled to AUDIO_RATE and padded to that length, so voices of
    different sample rates mix. One encode rather than a file per scene joined afterwards: AAC
    starts each file with a frame of priming, which leaves a click and a gap at every join."""
    count = len(slides)
    command = [FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-y"]
    for slide in slides:
        command += ["-framerate", str(FPS), "-i", str(slide)]
    for voice in voices:
        command += ["-i", str(voice)]
    graph = []
    for i, length in enumerate(seconds):
        # The slide is decoded once and its frame repeated (tpad). A looped image input decodes the
        # PNG again for every frame, which made a 5-minute video take twice as long on one vCPU.
        graph.append(f"[{i}:v]scale={SIZE[0]}:{SIZE[1]},setsar=1,format=yuv420p,"
                     f"tpad=stop_mode=clone:stop_duration={length:.6f},trim=end={length:.6f}[v{i}]")
        graph.append(f"[{count + i}:a]aresample={AUDIO_RATE},aformat=sample_fmts=fltp:channel_layouts=mono,"
                     f"apad=whole_dur={length:.6f},atrim=end={length:.6f}[a{i}]")
    graph.append("".join(f"[v{i}][a{i}]" for i in range(count)) + f"concat=n={count}:v=1:a=1[v][a]")
    command += [
        "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
        "-threads", str(FFMPEG_THREADS),
        # A still slide: stillimage tunes x264 for it, and the stream stays small. Medium rather than a
        # faster preset keeps the slide's text 2 dB sharper (PSNR 41.9 against 39.8 for veryfast).
        "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_RATE),
        # The index first, so a browser starts playing before the whole file has arrived.
        "-movflags", "+faststart", str(out),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_SECONDS, check=False)
    if result.returncode != 0:
        # Not a ValueError: the input was checked, so this is the system's failure, retried.
        raise RuntimeError(f"ffmpeg exited with {result.returncode}: {result.stderr[-500:]}")


def _fail_now(db: Session, video_id: uuid.UUID, error: str, before_save: Callable[[str | None], None] | None) -> None:
    if before_save is not None:
        before_save(error)
    current = db.get(Video, video_id, with_for_update=True, populate_existing=True)
    if current is not None:
        current.status = MediaStatus.failed
        current.error = error
    db.commit()


def _save_voices(
    db: Session, video_id: uuid.UUID, alias: str, lang: str, made: list[_Voice], error: str | None,
    hold_job: Callable[[], None] | None, before_save: Callable[[str | None], None] | None,
) -> bool:
    """Stores the voices just made and attaches each to its scene, with their cost; with `error`,
    also fails the video. False when the video is gone, its files then deleted."""
    stored: list[str] = []
    try:
        for v in made:
            v.key = voice_key(video_id, v.index)
            store_bytes(v.wav, v.key, stored)
        if error is not None:
            if before_save is not None:
                before_save(error)
        elif hold_job is not None:
            hold_job()
        # Read again, locked: the lecturer may have edited the scenes, or the document may have been
        # deleted, while the voices were made.
        current = db.get(Video, video_id, with_for_update=True, populate_existing=True)
        if current is None:
            # Nothing is left to render or to show a failure on, so the job ends done. The job row is
            # locked first, then the video's, the order the queue's error path uses too.
            if before_save is not None:
                before_save(None)
            db.commit()
            delete_files(stored, "video_render")
            return False
        now = [Scene.model_validate(s) for s in current.scenes]
        replaced: list[str] = []
        unused: list[str] = []
        for v in made:
            scene = now[v.index] if v.index < len(now) else None
            if (scene is None or current.voice_alias != alias
                    or normalize_narration(scene.narration, lang) != v.spoken):
                unused.append(v.key)
                continue
            if scene.voice_key and scene.voice_key != v.key:
                replaced.append(scene.voice_key)
            scene.voice_key, scene.voice_text_used, scene.voice_alias_used = v.key, v.spoken, alias
        voice = (current.cost or {}).get("voice") or {}
        current.scenes = [s.model_dump() for s in now]
        current.cost = {**(current.cost or {}), "voice": {
            "usd": (voice.get("usd") or 0.0) + sum(v.cost for v in made if v.cost is not None),
            "characters": (voice.get("characters") or 0) + sum(len(voice_input(v.spoken)) for v in made),
            # Speech replies carry no usage, so the cost is the model's price times the characters
            # (app/llm/media.py).
            "estimated": True,
        }}
        current.served_models = [*(current.served_models or []), *(
            {"requested_alias": alias, "routed_alias": alias, "served_model": v.served_model} for v in made)]
        if error is not None:
            current.status = MediaStatus.failed
            current.error = error
        db.commit()
    except BaseException:
        db.rollback()
        delete_files(stored, "video_render")
        raise
    # Only once the new keys are committed: until then the old voices are the scenes'.
    delete_files(replaced + unused, "video_render")
    return True


def render_video(
    db: Session, video_id: uuid.UUID, *,
    hold_job: Callable[[], None] | None = None,
    before_save: Callable[[str | None], None] | None = None,
) -> None:
    """Renders the video's scenes as they are now into a new MP4, and makes it the one learners see.

    `hold_job()` runs right before the voices are saved, and `before_save(error)` right before the
    final write, with the message the job fails with or None, each in the transaction that writes:
    the job queue checks there that its worker still holds the job, and `before_save` marks it done
    only when nothing failed (app/jobs/queue.py, hold). Raises ValueError with the message when the
    script cannot be rendered, a voice could not be made, or the video would run over MAX_SECONDS."""
    with values_kept_across_commits(db):
        video = db.get(Video, video_id)
        if video is None:
            raise ValueError(f"Video {video_id} not found")
        scenes = [Scene.model_validate(s) for s in video.scenes]
        problems = prepare_for_render(scenes) + lost_pictures(scenes)
        if problems:
            error = "Chưa dựng được video: " + "; ".join(problems) + "."
            _fail_now(db, video_id, error, before_save)
            raise ValueError(error)
        alias, lang, version = video.voice_alias, video.output_language, video.draft_version
        labels = scene_source_labels(db, video, scenes)
        # No transaction stays open while the voices are made and the video is encoded.
        db.commit()

        spoken = [normalize_narration(s.narration, lang) for s in scenes]
        with tempfile.TemporaryDirectory(dir=staging_dir(), prefix="render-") as work_dir:
            work = Path(work_dir)
            voices: dict[int, _Voice] = {}
            for i, scene in enumerate(scenes):
                reused = _reused_voice(i, scene, spoken[i], alias)
                if reused is not None:
                    voices[i] = reused
            targets = [i for i in range(len(scenes)) if i not in voices]
            if targets:
                with ThreadPoolExecutor(max_workers=min(PARALLEL_VOICES, len(targets))) as pool:
                    results = list(pool.map(lambda i: _speak(alias, i, spoken[i]), targets))
            else:
                results = []
            made = [r for r in results if isinstance(r, _Voice)]
            failures = [r for r in results if isinstance(r, SceneFailure)]
            voices.update({v.index: v for v in made})

            lengths = [segment_seconds(voices[i].seconds) for i in sorted(voices)]
            if failures:
                error = scene_failure_message(failures, scenes, "Không tạo được giọng cho ",
                                              "Hãy dựng lại; các cảnh đã có giọng không phải tạo lại.")
            elif sum(lengths) > MAX_SECONDS:
                error = (f"Video dài {_minutes(sum(lengths))} phút khi đọc thành tiếng, quá giới hạn "
                         f"{MAX_SECONDS // 60} phút. Lời thoại vẫn được giữ nguyên: hãy rút ngắn rồi dựng lại.")
            else:
                error = None
            if (made or error) and not _save_voices(db, video_id, alias, lang, made, error, hold_job, before_save):
                return
            if error is not None:
                raise ValueError(error)

            images = {i: _read_image(s.image_key) for i, s in enumerate(scenes)
                      if s.kind == "illustration" and s.image_key}
            slides = _render_slides(scenes, images, labels, lang, work)
            wavs = []
            for i in range(len(scenes)):
                path = work / f"voice-{i}.wav"
                path.write_bytes(voices[i].wav)
                wavs.append(path)
            out = work / "video.mp4"
            _assemble(slides, wavs, lengths, out)
            _publish(db, video_id, video_key(video_id, version), out, version, sum(lengths), before_save,
                     rendered_scenes(scenes, lengths))


def lost_pictures(scenes: Sequence[Scene]) -> list[str]:
    """A picture whose file is gone fails the render before any paid call, and for good: a retry
    would find it gone again, and only drawing it again brings it back."""
    store = media_store()
    return [f"Cảnh {i + 1}: hình minh họa bị mất, hãy vẽ lại" for i, s in enumerate(scenes)
            if s.kind == "illustration" and s.image_key and store.size(s.image_key) is None]


def _read_image(key: str) -> bytes:
    with media_store().local_copy(key) as path:
        return Path(path).read_bytes()


def rendered_scenes(scenes: Sequence[Scene], lengths: Sequence[float]) -> list[dict]:
    """What a learner needs of each scene to jump to it: scenes follow each other with no gap, so a
    scene starts where the lengths before it add up to."""
    out, start = [], 0.0
    for scene, length in zip(scenes, lengths, strict=True):
        out.append({"title": scene.title, "start_seconds": round(start, 3),
                    "citations": list(dict.fromkeys(scene.citations))})
        start += length
    return out


def _publish(db: Session, video_id: uuid.UUID, key: str, out: Path, version: int, seconds: float,
             before_save: Callable[[str | None], None] | None, scenes: list[dict]) -> None:
    stored: list[str] = []
    try:
        store_file(out, key, stored)
        if before_save is not None:
            before_save(None)
        current = db.get(Video, video_id, with_for_update=True, populate_existing=True)
        if current is None:
            db.commit()
            delete_files(stored, "video_render")
            return
        previous = current.video_key
        current.video_key = key
        # The version this render read: an edit made while it ran is a newer draft, still to render.
        current.rendered_version = version
        current.duration_seconds = round(seconds, 3)
        current.rendered_scenes = scenes
        current.status = MediaStatus.ready
        current.error = None
        db.commit()
    except BaseException:
        db.rollback()
        delete_files(stored, "video_render")
        raise
    # Only now: until the new key was committed, the old file is what learners were watching.
    if previous and previous != key:
        delete_files([previous], "video_render")


def run_video_render_step(job: Job) -> None:
    video_id = uuid.UUID(job.payload["video_id"])
    db = WorkerSessionLocal()

    def finish(error: str | None) -> None:
        held = hold(db, job)
        if error is None:
            held.status = JobStatus.done

    keys_at_start: set[str] = set()
    try:
        # Marked while the worker holds the job, so a worker that lost it cannot leave the video rendering.
        hold(db, job)
        video = db.get(Video, video_id)
        if video is None:
            raise ValueError(f"Video {video_id} not found")
        video.status = MediaStatus.rendering
        # Cleared so that the error the video shows at the end is this run's (queue.mark_target_failed).
        video.error = None
        keys_at_start = scene_file_keys(video.scenes)
        db.commit()
        render_video(db, video_id, hold_job=lambda: hold(db, job), before_save=finish)
    except LostJob:
        # Another worker runs the job now and may still read these files; its own end cleans up.
        keys_at_start = set()
        raise
    finally:
        if keys_at_start:
            delete_unnamed_files(db, video_id, keys_at_start, "video_render")
        db.close()
