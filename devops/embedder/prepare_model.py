"""Fetch the pinned Qwen3 embedding files TEI's ONNX loader reads, and check every byte.

The vectors already stored in chunks.embedding can only be compared with vectors of this exact
export, so a file that differs fails the build instead of being served. The checksums are those
of the files TEI fetched into the compose cache (do-an_embedder_models) for the same revision.
"""

import hashlib
import shutil
from pathlib import Path
from urllib.request import urlopen

MODEL_ID = "onnx-community/Qwen3-Embedding-0.6B-ONNX"
REVISION = "c25a394dd583836952667c12f008335071b3f43d"
# Only what TEI reads: the other ONNX exports in the repository (fp16, int8, q4...) are skipped.
FILES = {
    "config.json": "66a10929782f3c9a3cd5dec90e2a95c60e05736134a63cd54479eeae80bed175",
    "tokenizer.json": "def76fb086971c7867b829c23a26261e38d9d74e02139253b38aeb9df8b4b50a",
    "tokenizer_config.json": "977648852447cb6587327ff3205b0a84cf2fc9f05621d6c8e88a497caafab2e1",
    "onnx/model.onnx": "bf27b2f3f9ef9c32ca337d75b361fa99439deaeaefe82e4701b2dbd8439197cc",
    "onnx/model.onnx_data": "f0a61604465929a27e68aa6217c8c89ec6186572f0209fdb7711adda48a9b9a9",
}


def prepare(directory: Path) -> None:
    for name, expected in FILES.items():
        target = directory / name
        target.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{name}",
                     timeout=300) as response, target.open("wb") as output:
            shutil.copyfileobj(response, output, length=1 << 20)
        with target.open("rb") as written:
            actual = hashlib.file_digest(written, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"{name}: sha256 {actual}, expected {expected}")


if __name__ == "__main__":
    prepare(Path("/model"))
