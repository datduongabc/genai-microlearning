"""Nhúng lại mọi chunk chưa có vector, sau khi một migration xoá vector cũ vì đổi model nhúng
(như 0005) hoặc sau khi một job dừng giữa chừng. Dừng giữa chừng thì chạy lại là làm tiếp.

    docker exec do-an-backend uv run python scripts/reembed_chunks.py
"""

import sys

from app.db import WorkerSessionLocal
from app.ingestion.reembed import reembed_missing


def main() -> None:
    # Chạy bằng vai trò của worker: nó đọc được chunk của mọi môn, còn vai trò của request chỉ thấy
    # chunk của người đang đăng nhập.
    with WorkerSessionLocal() as db:
        done = reembed_missing(db)
    print(f"đã nhúng lại {done} chunk")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
