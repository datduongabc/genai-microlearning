"""Audio preparation, parallel ASR and original-timeline citations without provider calls."""
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
import openai
import pytest
from litellm.exceptions import RateLimitError

from app.ingestion import document_readers as readers


def test_windows_cover_duration_with_only_small_overlap():
    assert readers._audio_windows(60) == [(0, 60)]
    assert readers._audio_windows(1201) == [(0, 600), (598, 602), (1198, 3)]
    assert readers._audio_windows(1200) == [(0, 600), (598, 602)]


def test_merge_offsets_and_removes_only_temporally_overlapping_prefix():
    units = readers._merge_audio_segments([
        (0, 600, [{"start": 595, "end": 600, "text": "Welcome to machine learning."}]),
        (598, 602, [
            {"start": 0, "end": 5, "text": " Machine learning, continues today."},
            {"start": 10, "end": 12, "text": "machine learning"},
        ]),
    ])
    assert [(u.text, u.locator) for u in units] == [
        ("Welcome to machine learning.", {"start_ms": 595000, "end_ms": 600000}),
        ("continues today.", {"start_ms": 600000, "end_ms": 603000}),
        ("machine learning", {"start_ms": 608000, "end_ms": 610000}),
    ]


def test_merge_deduplicates_across_segments_and_clamps_padding():
    units = readers._merge_audio_segments([
        (0, 600, [{"start": 598, "end": 600, "text": "one two three"}]),
        (598, 3, [
            {"start": 0, "end": 1, "text": "one two"},
            {"start": 1, "end": 5, "text": "three four"},
        ]),
    ])
    assert [u.text for u in units] == ["one two three", "four"]
    assert units[-1].locator == {"start_ms": 600000, "end_ms": 601000}


def _fake_ffmpeg(command, **kwargs):
    Path(command[-1]).write_bytes(b"mp3")


@pytest.mark.parametrize("extension", ["mp3", "wav", "mp4"])
def test_every_format_is_normalized_before_upload(tmp_path, extension):
    path = tmp_path / f"lecture.{extension}"
    path.write_bytes(b"input")
    with patch.object(readers, "_duration_seconds", return_value=60), patch.object(
        readers.subprocess, "run", side_effect=_fake_ffmpeg
    ) as ffmpeg, patch.object(readers, "transcribe_audio", return_value=[
        {"start": 1, "end": 2, "text": "speech"}
    ]) as transcribe:
        units = readers.read_audio_video(str(path))
    command = ffmpeg.call_args.args[0]
    for option, value in [("-ac", "1"), ("-ar", "16000"), ("-b:a", "32k")]:
        assert command[command.index(option) + 1] == value
    assert transcribe.call_args.args[0] != str(path)
    assert units[0].locator == {"start_ms": 1000, "end_ms": 2000}
    assert not Path(transcribe.call_args.args[0]).exists()


def test_parallel_segments_are_bounded_and_results_stay_in_order(tmp_path):
    path = tmp_path / "lecture.mp4"
    path.write_bytes(b"input")
    lock, release, started = threading.Lock(), threading.Event(), threading.Event()
    active = peak = 0
    def transcribe(piece):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                started.set()
        assert release.wait(5)
        with lock:
            active -= 1
        return [{"start": 3, "end": 4, "text": Path(piece).stem}]
    with (
        ThreadPoolExecutor(max_workers=2) as pool,
        ThreadPoolExecutor(max_workers=1) as caller,
        patch.object(readers, "_ASR_EXECUTOR", pool),
        patch.object(readers, "_duration_seconds", return_value=1801),
        patch.object(readers.subprocess, "run", side_effect=_fake_ffmpeg),
        patch.object(readers, "transcribe_audio", side_effect=transcribe),
    ):
        future = caller.submit(readers.read_audio_video, str(path))
        try:
            assert started.wait(5)
        finally:
            release.set()
        units = future.result(timeout=5)
    assert peak == 2
    assert [u.locator['start_ms'] for u in units] == [3000, 601000, 1201000]
    assert [u.text for u in units] == ["part000", "part001", "part002"]


def _rate_limit(retry_after="2"):
    return RateLimitError("quota", "groq", "transcribe", response=httpx.Response(
        429, headers={"retry-after": retry_after}
    ))


@pytest.mark.parametrize("error", [
    _rate_limit(),
    openai.RateLimitError("quota", response=httpx.Response(429, headers={"retry-after": "2"},
        request=httpx.Request("POST", "http://x")), body=None),
    openai.APIConnectionError(request=httpx.Request("POST", "http://x")),
])
def test_failed_segment_retries_without_resending_successful_segments(error):
    failed = threading.Event()
    with patch.object(readers, "transcribe_audio", side_effect=[error, [
        {"start": 0, "end": 2, "text": "speech"}
    ]]), patch.object(failed, "wait", return_value=False) as sleep:
        result = readers._transcribe_piece("part.mp3", 598, readers.time.monotonic() + 600, failed)
    assert result[0]['text'] == 'speech'
    assert sleep.call_count == 1


def test_exhausted_segment_is_visible_and_no_partial_transcript_is_returned(tmp_path):
    path = tmp_path / "lecture.mp4"
    path.write_bytes(b"input")
    def transcribe(piece):
        if '001' in piece:
            raise _rate_limit("900")
        return [{"start": 0, "end": 2, "text": "speech"}]
    with patch.object(readers, "_duration_seconds", return_value=700), patch.object(
        readers.subprocess, "run", side_effect=_fake_ffmpeg
    ), patch.object(readers, "transcribe_audio", side_effect=transcribe), pytest.raises(
        ValueError, match="09:58.*han muc audio Groq"
    ):
        readers.read_audio_video(str(path))


def test_empty_transcript_is_rejected(tmp_path):
    path = tmp_path / "silence.mp3"
    path.write_bytes(b"input")
    with patch.object(readers, "_duration_seconds", return_value=60), patch.object(
        readers.subprocess, "run", side_effect=_fake_ffmpeg
    ), patch.object(readers, "transcribe_audio", return_value=[]), pytest.raises(ValueError, match="rong"):
        readers.read_audio_video(str(path))


@pytest.mark.parametrize('duration', [7200.5, float('nan'), 0])
def test_invalid_duration_refused_before_transcode_or_spending_quota(tmp_path, duration):
    path = tmp_path / "long.mp3"
    path.write_bytes(b"input")
    with patch.object(readers, "_duration_seconds", return_value=duration), patch.object(
        readers.subprocess, "run"
    ) as ffmpeg, patch.object(readers, "transcribe_audio") as transcribe, pytest.raises(ValueError):
        readers.read_audio_video(str(path))
    ffmpeg.assert_not_called()
    transcribe.assert_not_called()


def test_queued_segment_does_not_upload_after_document_deadline():
    with patch.object(readers, "transcribe_audio") as transcribe, pytest.raises(
        ValueError, match="09:58.*TimeoutError"
    ):
        readers._transcribe_piece("part.mp3", 598, readers.time.monotonic() - 1, threading.Event())
    transcribe.assert_not_called()


def test_retry_does_not_repeat_a_successful_sibling(tmp_path):
    from collections import Counter
    path = tmp_path / "lecture.mp4"
    path.write_bytes(b"input")
    calls = Counter()
    lock = threading.Lock()
    def transcribe(piece):
        name = Path(piece).stem
        with lock:
            calls[name] += 1
            attempt = calls[name]
        if name == 'part001' and attempt == 1:
            raise _rate_limit('1')
        return [{"start": 3, "end": 4, "text": name}]
    with patch.object(readers, "_duration_seconds", return_value=700), patch.object(
        readers.subprocess, "run", side_effect=_fake_ffmpeg
    ), patch.object(readers, "transcribe_audio", side_effect=transcribe):
        units = readers.read_audio_video(str(path))
    assert calls == {'part000': 1, 'part001': 2}
    assert [u.text for u in units] == ['part000', 'part001']


def test_permanently_failed_segment_stops_after_three_attempts():
    failed = threading.Event()
    with patch.object(readers, "transcribe_audio", side_effect=_rate_limit('1')) as transcribe, patch.object(
        failed, "wait", return_value=False
    ), pytest.raises(ValueError, match="09:58.*han muc audio Groq"):
        readers._transcribe_piece('part.mp3', 598, readers.time.monotonic() + 600, failed)
    assert transcribe.call_count == 3


def test_normalization_shares_the_bounded_pool(tmp_path):
    path = tmp_path / 'lecture.mp4'
    path.write_bytes(b'input')
    started, release, lock = threading.Event(), threading.Event(), threading.Lock()
    active = peak = 0
    def ffmpeg(command, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == 2:
                started.set()
        assert release.wait(5)
        Path(command[-1]).write_bytes(b'mp3')
        with lock:
            active -= 1
    with (
        ThreadPoolExecutor(max_workers=2) as pool,
        ThreadPoolExecutor(max_workers=1) as caller,
        patch.object(readers, '_ASR_EXECUTOR', pool),
        patch.object(readers, '_duration_seconds', return_value=700),
        patch.object(readers.subprocess, 'run', side_effect=ffmpeg),
        patch.object(readers, 'transcribe_audio', return_value=[{'start': 3, 'end': 4, 'text': 'speech'}]),
    ):
        future = caller.submit(readers.read_audio_video, str(path))
        try:
            assert started.wait(2)
        finally:
            release.set()
        assert len(future.result(timeout=5)) == 2
    assert peak == 2


def test_failed_window_preparation_is_terminal_before_its_upload(tmp_path):
    import subprocess
    with patch.object(readers.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'ffmpeg')), patch.object(
        readers, 'transcribe_audio'
    ) as transcribe, pytest.raises(ValueError, match='09:58.*ffmpeg'):
        readers._transcribe_window('input.mp4', str(tmp_path), 1, 598, 602, readers.time.monotonic() + 600,
                                   threading.Event())
    transcribe.assert_not_called()


def test_overlap_before_a_short_trailing_utterance_is_not_repeated():
    units = readers._merge_audio_segments([
        (0, 600, [{'start': 596, 'end': 599.64, 'text': 'And Y is the output.'},
                  {'start': 599.64, 'end': 600, 'text': 'Yeah.'}]),
        (598, 602, [{'start': 0, 'end': 1.66, 'text': 'is the output.'},
                    {'start': 3, 'end': 6, 'text': 'This is the target variable.'}]),
    ])
    assert [u.text for u in units] == ['And Y is the output.', 'Yeah.', 'This is the target variable.']


def test_crossing_overlap_prefix_can_match_before_a_truncated_trailing_word():
    units = readers._merge_audio_segments([
        (0, 600, [{'start': 597.36, 'end': 600, 'text': "And then I'll describe this in..."}]),
        (598, 602, [{'start': 0, 'end': 2.86, 'text': "I'll describe this informally for now,"}]),
    ])
    assert units[-1].text == 'informally for now,'
    assert units[-1].locator == {'start_ms': 600000, 'end_ms': 600860}


def test_unmatched_overlap_keeps_text_in_absolute_timestamp_order():
    units = readers._merge_audio_segments([
        (0, 600, [{'start': 599, 'end': 600, 'text': 'earlier ending'}]),
        (598, 602, [{'start': 0, 'end': 4, 'text': 'different wording'}]),
    ])
    assert [u.locator['start_ms'] for u in units] == sorted(u.locator['start_ms'] for u in units)
    assert {u.text for u in units} == {'earlier ending', 'different wording'}


def test_common_phrase_earlier_in_the_previous_segment_is_not_trimmed():
    units = readers._merge_audio_segments([
        (0, 600, [{'start': 580, 'end': 600,
                   'text': 'chúng ta có thể thấy rằng mô hình này hoạt động tốt'}]),
        (598, 602, [{'start': 1, 'end': 4, 'text': 'chúng ta sẽ xem ví dụ tiếp theo'}]),
    ])
    assert units[-1].text == 'chúng ta sẽ xem ví dụ tiếp theo'
    assert units[-1].locator == {'start_ms': 599000, 'end_ms': 602000}


def test_failed_window_cancels_queued_siblings_before_upload(tmp_path):
    path = tmp_path / 'lecture.mp4'
    path.write_bytes(b'input')
    uploaded = []
    def transcribe(piece):
        uploaded.append(Path(piece).stem)
        raise openai.BadRequestError('bad audio', response=httpx.Response(
            400, request=httpx.Request('POST', 'http://x')), body=None)
    with (
        ThreadPoolExecutor(max_workers=1) as pool,
        patch.object(readers, '_ASR_EXECUTOR', pool),
        patch.object(readers, '_duration_seconds', return_value=3600),
        patch.object(readers.subprocess, 'run', side_effect=_fake_ffmpeg),
        patch.object(readers, 'transcribe_audio', side_effect=transcribe),
        pytest.raises(ValueError, match='00:00.*BadRequestError'),
    ):
        readers.read_audio_video(str(path))
    assert uploaded == ['part000']


def test_sibling_sleeping_on_backoff_never_reuploads_after_document_failure(tmp_path):
    path = tmp_path / 'lecture.mp4'
    path.write_bytes(b'input')
    uploaded, backing_off = [], threading.Event()
    def transcribe(piece):
        name = Path(piece).stem
        uploaded.append(name)
        if name == 'part000':
            backing_off.set()
            raise _rate_limit('30')
        assert backing_off.wait(5)
        raise openai.BadRequestError('bad audio', response=httpx.Response(
            400, request=httpx.Request('POST', 'http://x')), body=None)
    with (
        ThreadPoolExecutor(max_workers=2) as pool,
        ThreadPoolExecutor(max_workers=1) as caller,
        patch.object(readers, '_ASR_EXECUTOR', pool),
        patch.object(readers, '_duration_seconds', return_value=700),
        patch.object(readers.subprocess, 'run', side_effect=_fake_ffmpeg),
        patch.object(readers, 'transcribe_audio', side_effect=transcribe),
    ):
        future = caller.submit(readers.read_audio_video, str(path))
        with pytest.raises(ValueError, match='09:58.*BadRequestError'):
            future.result(timeout=5)
    assert sorted(uploaded) == ['part000', 'part001']
