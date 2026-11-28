"""What is taken out of a document as it is read, and, as important, what lecture text keeps."""

import pytest

from app.ingestion.redaction import EMAIL, KEY, PASSWORD, PHONE, redact


@pytest.mark.parametrize(("text", "label"), [
    ("Liên hệ: nguyen.van.a@hcmut.edu.vn để nộp bài", EMAIL),
    ("Gọi 0912 345 678 nếu cần", PHONE),
    ("Hotline 0912.345.678.", PHONE),
    ("SĐT: +84 912 345 678", PHONE),
    ("Văn phòng khoa: 028 3864 7256", PHONE),
    ("Call +1 415 555 0100 for support", PHONE),
    ("OPENROUTER_API_KEY=sk-or-v1-0123456789abcdef0123456789abcdef", KEY),
    ("aws_access_key_id = AKIAIOSFODNN7EXAMPLE", KEY),
    ("key: AIzaSyA1234567890abcdefghijklmnopqrstuv", KEY),
    ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789", KEY),
    ("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U", KEY),
    ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----", KEY),
    ('password = "hunter22"', PASSWORD),
    ("Mật khẩu: Abc@12345", PASSWORD),
    ("DB_PASSWORD=s3cr3t!pass", PASSWORD),
])
def test_secrets_and_contact_details_are_replaced(text, label):
    redacted, count = redact(text)
    assert label in redacted
    assert count >= 1


def test_only_the_value_of_a_password_goes():
    assert redact("mật khẩu: Abc@12345 rồi đăng nhập")[0] == f"mật khẩu: {PASSWORD} rồi đăng nhập"


@pytest.mark.parametrize("text", [
    "x = 10; y = 0.5 * x + 3",
    "Năm 2024 có 365 ngày; trang 12 / 33",
    "0 1 2 3 4 5 6 7 8 9",
    "0.5 0.25 0.125 0.0625 0.03125",
    "Số pi ≈ 3.14159265358979",
    "MSSV 2012345 nộp bài lúc 23:59",
    "f(x) = 0123456789 mod 7",
    "Password: a secret string the user remembers",
    "The token is split by the lexer",
    "Hash SHA-256 của tệp là 64 ký tự hex",
    "IPv4 192.168.100.200 và mặt nạ 255.255.255.0",
    "Một tiến trình có PID 012345678 trong ví dụ",
])
def test_lecture_text_is_kept(text):
    assert redact(text) == (text, 0)
