"""Create a study on a running deployment and load exported packet files into it, as they are.

    ADMIN_EMAIL=... ADMIN_PASSWORD=... uv run python scripts/import_study_packets.py \
        https://<host>/api ../docs/measurements/module-study/packets --name "Rubric module RAG vs không RAG"

No model is called: the packets are the files the claim measurements were made on
(docs/measurements/2026-11-07-module-study-pairs.md). Raters are invited afterwards on the
admin page. Only the packet, its pair, group and provenance are sent; the exported reference
and claim citations stay on disk.
"""
import argparse
import json
import os
from pathlib import Path

import httpx

RUBRIC_VERSION = "docs/rubric.md 2026-11-07"
FIELDS = ("pair_id", "group", "generator_model", "generation_models", "packet")


def packets(folder: Path) -> list[dict]:
    return [{k: data[k] for k in FIELDS if k in data}
            for data in (json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json")))]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("api")
    parser.add_argument("folder", type=Path)
    parser.add_argument("--name", required=True)
    args = parser.parse_args(argv)
    with httpx.Client(base_url=args.api.rstrip("/"), timeout=60) as client:
        token = client.post("/auth/login", json={"email": os.environ["ADMIN_EMAIL"],
                                                 "password": os.environ["ADMIN_PASSWORD"]}).raise_for_status()
        client.headers["Authorization"] = f"Bearer {token.json()['access_token']}"
        study = client.post("/studies", json={"name": args.name, "rubric_version": RUBRIC_VERSION}).raise_for_status().json()
        items = client.post(f"/studies/{study['id']}/items",
                            json={"imported_packets": packets(args.folder)}).raise_for_status().json()
    print(f"Study {study['id']}: {len(items)} packets loaded")


if __name__ == "__main__":
    main()
