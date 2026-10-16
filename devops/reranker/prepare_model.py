"""Prepare Jina's pinned official INT8 graph for TEI's local ONNX loader."""

import hashlib
import json
import shutil
from pathlib import Path
from urllib.request import urlopen

MODEL_ID = "jinaai/jina-reranker-v2-base-multilingual"
REVISION = "9cfeff2df7d40d1b78e75e5e9cebec92a99813c9"
GRAPH_SHA256 = "c5220cf8fe023f8aa0ed2a3eb787d4451a7f17cf53f6b787e35718dd4b8815c3"


def prepare(directory: Path) -> None:
    # CC-BY-NC-4.0: Jina AI weights are for non-commercial research (this thesis).
    for remote, local in (
        ("config.json", "config.json"),
        ("tokenizer.json", "tokenizer.json"),
        ("tokenizer_config.json", "tokenizer_config.json"),
        ("special_tokens_map.json", "special_tokens_map.json"),
        ("README.md", "README.md"),
        ("onnx/model_quantized.onnx", "onnx/model.onnx"),
    ):
        target = directory / local
        target.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(f"https://huggingface.co/{MODEL_ID}/resolve/{REVISION}/{remote}",
                     timeout=300) as response, target.open("wb") as output:
            shutil.copyfileobj(response, output)
    with (directory / "onnx/model.onnx").open("rb") as graph:
        if hashlib.file_digest(graph, "sha256").hexdigest() != GRAPH_SHA256:
            raise ValueError("Jina INT8 graph checksum mismatch")
    # Official metadata omits model_type; TEI needs it to choose the ONNX loader.
    config_path = directory / "config.json"
    config = json.loads(config_path.read_text())
    config["model_type"] = "xlm-roberta"
    config_path.write_text(json.dumps(config, indent=2) + "\n")


if __name__ == "__main__":
    prepare(Path("/model"))
