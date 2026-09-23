from app.auth.security import MAX_PASSWORD_BYTES

MIN_PASSWORD_LENGTH = 8


def check_password(password: str, *, email: str, name: str) -> str | None:
    """Vì sao mật khẩu bị từ chối, viết cho chính người đang đặt nó, hoặc None khi chấp nhận.

    Chỉ còn độ dài, không còn quy tắc đoán mật khẩu yếu: những quy tắc đó đẩy người dùng
    tới các mật khẩu kiểu Password1! vốn nằm trong mọi danh sách của kẻ tấn công, trong khi
    một câu dài bình thường lại khó đoán hơn nhiều.

    Giới hạn 72 byte không phải chính sách mà là ràng buộc của bcrypt: nó chỉ đọc 72 byte
    đầu, nên nếu không chặn ở đây thì hai mật khẩu khác nhau sẽ cùng mở được một tài khoản.
    Hai tham số email và name giữ lại trong chữ ký để nơi gọi không phải đổi.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Mật khẩu phải có ít nhất {MIN_PASSWORD_LENGTH} ký tự."
    if len(password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        return (
            f"Mật khẩu tối đa {MAX_PASSWORD_BYTES} byte (mỗi chữ tiếng Việt có dấu chiếm 2 đến 3 byte), "
            "hãy chọn mật khẩu ngắn hơn."
        )
    return None
