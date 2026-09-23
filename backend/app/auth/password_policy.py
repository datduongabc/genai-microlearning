from app.auth.common_passwords import COMMON_PASSWORDS
from app.auth.security import MAX_PASSWORD_BYTES

MIN_PASSWORD_LENGTH = 8
# Below this a password is a handful of repeated characters ("aaaaaaaa", "abababab").
MIN_DISTINCT_CHARACTERS = 4


def check_password(password: str, *, email: str, name: str) -> str | None:
    """Why the password is refused, worded for the person choosing it, or None when it
    is acceptable.

    Length and the blocklist rather than composition rules ("one capital, one symbol"):
    those push people to "Password1!", which is on every attacker's list, while a plain
    passphrase of the same length is far harder to guess."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự."
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return (
            f"Mật khẩu tối đa {MAX_PASSWORD_BYTES} byte (mỗi chữ tiếng Việt có dấu chiếm 2 đến 3 byte), "
            "hãy chọn mật khẩu ngắn hơn."
        )
    lowered = password.casefold()
    if lowered in COMMON_PASSWORDS:
        return "Mật khẩu này quá phổ biến và dễ bị đoán, hãy chọn mật khẩu khác."
    if len(set(lowered)) < MIN_DISTINCT_CHARACTERS:
        return "Mật khẩu quá đơn giản, hãy dùng nhiều ký tự khác nhau hơn."
    own = {
        email.casefold(),
        email.casefold().split("@")[0],
        name.casefold(),
        name.casefold().replace(" ", ""),
    }
    if lowered in own:
        return "Mật khẩu không được trùng với email hoặc họ tên của bạn."
    return None
