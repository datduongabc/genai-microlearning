"""devops/embedder bakes the model ECS serves; compose fetches it at start. Both must give the same vectors."""

import ast
import json
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
EMBEDDER = REPO_ROOT / "devops" / "embedder"


def _constants() -> dict:
    tree = ast.parse((EMBEDDER / "prepare_model.py").read_text(encoding="utf-8"))
    return {t.id: ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
            for t in node.targets if isinstance(t, ast.Name)}


def _args(command: list[str]) -> dict[str, str]:
    return {command[i]: command[i + 1] for i in range(len(command) - 1) if command[i].startswith("--")}


def test_the_image_bakes_the_model_and_revision_compose_serves():
    """chunks.embedding was written by compose's model: a different export or revision in ECS would give vectors
    that compare with none of them, and search would return noise without an error."""
    compose = yaml.safe_load((REPO_ROOT / "devops" / "docker-compose.yml").read_text(encoding="utf-8"))
    served = _args(compose["services"]["embedder"]["command"])
    baked = _constants()

    assert (baked["MODEL_ID"], baked["REVISION"]) == (served["--model-id"], served["--revision"])


def test_the_image_serves_with_the_arguments_compose_uses():
    compose = yaml.safe_load((REPO_ROOT / "devops" / "docker-compose.yml").read_text(encoding="utf-8"))
    served = _args(compose["services"]["embedder"]["command"])
    cmd = re.search(r"^CMD\s+(\[.*?\])\s*$", (EMBEDDER / "Dockerfile").read_text(encoding="utf-8").replace("\\\n", ""),
                    re.MULTILINE | re.DOTALL)
    image = _args(json.loads(cmd.group(1)))

    for flag in ("--pooling", "--max-batch-tokens", "--max-client-batch-size"):
        assert image[flag] == served[flag], flag
    assert image["--model-id"] == "/model"
    assert "--api-key" not in image, "the key comes from API_KEY at run time, never the image"


def test_every_baked_file_is_checked():
    for name, digest in _constants()["FILES"].items():
        assert re.fullmatch(r"[0-9a-f]{64}", digest), name
