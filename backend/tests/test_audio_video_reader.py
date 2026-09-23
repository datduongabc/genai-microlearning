"""Test logic phan doan/paragraph-splitting cua read_audio_video() -- mock ca
_transcode_to_mp3 (can ffmpeg, khong co tren host chay test, chi co trong
Docker image) va transcribe_audio (goi Gemini that, ton quota) de tach rieng
LOGIC (verify duoc, chay nhanh, khong phu thuoc moi truong) khoi phan phu
thuoc external that (verify rieng qua Docker/live_llm, khong lap lai o day)."""

import os
from unittest.mock import patch

import pytest

from app.ingestion.document_readers import read_audio_video


def _fake_transcode(input_path: str, out_dir: str) -> str:
    out_path = os.path.join(out_dir, "audio.mp3")
    with open(out_path, "wb") as f:
        f.write(b"fake mp3 bytes -- ffmpeg is mocked out in this test")
    return out_path


def test_read_audio_video_splits_transcript_into_paragraphs(tmp_path):
    fake_input = tmp_path / "lecture.mp3"
    fake_input.write_bytes(b"not real audio, transcoding is mocked")

    with (
        patch("app.ingestion.document_readers._transcode_to_mp3", side_effect=_fake_transcode),
        patch(
            "app.ingestion.document_readers.transcribe_audio",
            return_value="Doan mot ve khai niem A.\n\nDoan hai ve khai niem B, khac A.",
        ),
    ):
        units = read_audio_video(str(fake_input))

    assert len(units) == 2
    assert units[0].text == "Doan mot ve khai niem A."
    assert units[0].locator == {"paragraph_index": 0}
    assert units[1].text == "Doan hai ve khai niem B, khac A."
    assert units[1].locator == {"paragraph_index": 1}


def test_read_audio_video_raises_on_empty_transcript(tmp_path):
    fake_input = tmp_path / "silence.mp3"
    fake_input.write_bytes(b"not real audio, transcoding is mocked")

    with (
        patch("app.ingestion.document_readers._transcode_to_mp3", side_effect=_fake_transcode),
        patch("app.ingestion.document_readers.transcribe_audio", return_value="   "),
        pytest.raises(ValueError, match="rong"),
    ):
        read_audio_video(str(fake_input))
