"""Audio preparation and timestamped transcription without provider calls."""
import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from litellm.exceptions import RateLimitError

from app.ingestion import document_readers as readers


def test_small_mp3_skips_ffmpeg_and_keeps_segment_timestamps(tmp_path):
    path = tmp_path / "lecture.mp3"
    path.write_bytes(b"audio")
    segments = [
        {"start": 750, "end": 760, "text": " A "},
        {"start": 760, "end": 785, "text": "  "},
        {"start": 785, "end": 820, "text": "C"},
    ]
    with patch.object(readers.subprocess, "run") as ffmpeg, patch.object(
        readers, "transcribe_audio", return_value=segments
    ) as transcribe:
        units = readers.read_audio_video(str(path))
    ffmpeg.assert_not_called()
    transcribe.assert_called_once_with(str(path))
    assert [(u.text, u.locator) for u in units] == [
        ("A", {"start_ms": 750000, "end_ms": 760000}),
        ("C", {"start_ms": 785000, "end_ms": 820000}),
    ]

    from app.api.modules import citation_label
    assert citation_label("lecture.mp3", json.dumps(units[0].locator)) == "lecture.mp3 · 12:30–12:40"


def _rate_limit(retry_after=None):
    headers = {"retry-after": retry_after} if retry_after else None
    return RateLimitError("quota", "groq", "transcribe", response=httpx.Response(429, headers=headers))


def test_rate_limited_piece_waits_and_retries_only_that_piece(tmp_path):
    path = tmp_path / "lecture.mp3"
    path.write_bytes(b"audio")
    segment = [{"start": 0, "end": 2, "text": "speech"}]
    with patch.object(
        readers, "transcribe_audio", side_effect=[_rate_limit("120"), segment]
    ) as transcribe, patch.object(readers.time, "sleep") as sleep:
        units = readers.read_audio_video(str(path))
    assert transcribe.call_count == 2
    sleep.assert_called_once_with(120.0)
    assert [u.locator for u in units] == [{"start_ms": 0, "end_ms": 2000}]


def test_exhausted_rate_limit_reports_quota(tmp_path):
    path = tmp_path / "lecture.mp3"
    path.write_bytes(b"audio")
    with patch.object(readers, "transcribe_audio", side_effect=_rate_limit()), patch.object(
        readers.time, "sleep"
    ) as sleep, pytest.raises(ValueError, match="han muc audio Groq"):
        readers.read_audio_video(str(path))
    assert sleep.call_count == readers._RATE_LIMIT_ATTEMPTS - 1
    sleep.assert_called_with(60)


@pytest.mark.parametrize("extension", ["wav", "mp4"])
def test_compression_is_mono_16khz_low_bitrate(tmp_path, extension):
    path = tmp_path / f"lecture.{extension}"
    path.write_bytes(b"input")
    commands = []
    def ffmpeg(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"mp3")
    with patch.object(readers.subprocess, "run", side_effect=ffmpeg), patch.object(
        readers, "transcribe_audio", return_value=[{"start": 0, "end": 2, "text": "speech"}]
    ):
        assert readers.read_audio_video(str(path))[0].locator == {"start_ms": 0, "end_ms": 2000}
    command = commands[0]
    assert "-vn" in command
    for option, value in [("-ac", "1"), ("-ar", "16000"), ("-b:a", "32k")]:
        assert command[command.index(option) + 1] == value


def test_oversized_audio_splits_and_offsets_segments(tmp_path):
    path = tmp_path / "large.mp3"
    with path.open("wb") as f:
        f.truncate(24_000_001)
    def ffmpeg(command, **kwargs):
        if "-f" not in command:
            with open(command[-1], "wb") as f:
                f.truncate(24_000_001)
        else:
            assert command[command.index("-f") + 1] == "segment"
            for i in range(2):
                Path(command[-1] % i).write_bytes(b"part")
            manifest = command[command.index("-segment_list") + 1]
            Path(manifest).write_text("part000.mp3,0.000000,5400.036000\npart001.mp3,5400.036000,5410.000000\n")
    with patch.object(readers.subprocess, "run", side_effect=ffmpeg), patch.object(
        readers, "transcribe_audio", return_value=[{"start": 1, "end": 10, "text": "speech"}]
    ) as transcribe:
        units = readers.read_audio_video(str(path))
    assert transcribe.call_count == 2
    assert units[0].locator == {"start_ms": 1000, "end_ms": 10000}
    assert units[1].locator == {"start_ms": 5401036, "end_ms": 5410036}
    assert not Path(transcribe.call_args.args[0]).exists()


def test_empty_transcript_is_rejected(tmp_path):
    path = tmp_path / "silence.mp3"
    path.write_bytes(b"audio")
    with patch.object(readers, "transcribe_audio", return_value=[]), pytest.raises(ValueError, match="rong"):
        readers.read_audio_video(str(path))
