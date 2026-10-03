"""Tạo tài khoản admin, tài khoản đầu tiên của một hệ thống mới. Không có API công khai nào làm
việc này: admin chỉ được tạo từ lệnh này, trên máy chủ.

    docker exec -it -e PYTHONPATH=. do-an-backend uv run python scripts/create_admin.py admin@example.com --name "Admin"

Mật khẩu được hỏi tại chỗ (không hiện trên màn hình). Chạy không có terminal thì đọc biến môi
trường ADMIN_PASSWORD. Mật khẩu theo đúng chính sách của mọi tài khoản (app/auth/password_policy.py).
Từ tài khoản admin này, các giảng viên được tạo trên trang Quản trị.
"""

import argparse
import getpass
import os
import sys

from app.auth.accounts import AccountError, create_account
from app.db import RequestSessionLocal
from app.models import UserRole


def _password() -> str:
    from_env = os.environ.get("ADMIN_PASSWORD")
    if from_env is not None:
        return from_env
    if not sys.stdin.isatty():
        sys.exit("Không có terminal để hỏi mật khẩu: đặt biến môi trường ADMIN_PASSWORD.")
    first = getpass.getpass("Mật khẩu: ")
    if getpass.getpass("Nhập lại mật khẩu: ") != first:
        sys.exit("Hai lần nhập mật khẩu không giống nhau.")
    return first


def main() -> None:
    parser = argparse.ArgumentParser(description="Tạo tài khoản admin.")
    parser.add_argument("email")
    parser.add_argument("--name", default="Admin")
    args = parser.parse_args()

    # Vai trò của request (không phải chủ database): bảng users không có RLS, nên nó tạo được tài khoản.
    with RequestSessionLocal() as db:
        try:
            user = create_account(
                db, email=args.email, name=args.name, password=_password(), role=UserRole.admin
            )
        except AccountError as error:
            sys.exit(error.detail)
    print(f"đã tạo admin {user.email}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
