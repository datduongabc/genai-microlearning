"""Chữ nằm trong hình của slide và trang PDF: sơ đồ lớp UML, một trang chữ chụp thành ảnh, bảng
dán dưới dạng hình. Lớp chữ của tệp không có những chữ này, nên truy hồi và sinh nội dung không
bao giờ thấy chúng nếu không đọc ảnh.

Chỉ hình có thể mang nội dung mới được đọc: hình đủ lớn trên trang, và không lặp lại trên nhiều
trang như logo hay hình nền của mẫu slide. Ảnh chụp, tranh minh họa không có chữ thì OCR trả về
vài ký tự rời với độ tin cậy thấp và bị bỏ, nên chúng không thêm rác vào bài.
"""

import io
import logging
import re
from concurrent.futures import ThreadPoolExecutor

import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)

# Phần diện tích trang tối thiểu: biểu tượng, logo, mũi tên trang trí đều nhỏ hơn.
MIN_FIGURE_AREA = 0.04
# Cùng một ảnh trên chừng này trang trở lên là một phần của mẫu slide, không phải nội dung.
REPEATED_FIGURE_PAGES = 3
# Ảnh có chữ thật khi có ít nhất ba từ đọc chắc chắn và độ tin cậy trung bình của cả ảnh từ 60:
# sơ đồ, mã nguồn, bảng chụp đạt 64-93 trên bộ tài liệu chuẩn, còn ảnh chụp màn hình rối, logo
# hay trục số của đồ thị chỉ 43-56 và cho ra chữ vô nghĩa.
_CONFIDENT = 60
_MIN_CONFIDENT_WORDS = 3
# Trong một ảnh đã có chữ, một dòng vẫn được giữ khi độ tin cậy thấp hơn: nét nối của sơ đồ dính
# vào cuối một tên phương thức kéo độ tin cậy của cả từ xuống, mà tên đó vẫn đúng.
_MIN_WORD_CONFIDENCE = 20
_MIN_LINE_CONFIDENCE = 35
# Đường kẻ, mũi tên của sơ đồ bị đọc thành một chuỗi gạch dính vào chữ ("float[2]e-------").
# Đầu chấm tròn của nét nối hay bị đọc thành "e" hoặc "®".
_RULE = re.compile(r"[-~_—–.]+")
_CONNECTOR = re.compile(r"(?:[eo®•°]|[\W_])?[-~_—–=.'\"“”‘’`]{3,}\W*$")
# Tesseract là tiến trình riêng, nên vài ảnh đọc song song thật sự (ảnh backend đặt
# OMP_THREAD_LIMIT=1 để các tiến trình không tranh nhau CPU).
_WORKERS = 4
_WORD = re.compile(r"[^\W\d_]{2,}")


def is_content_figure(area_ratio: float, pages_with_image: int) -> bool:
    return area_ratio >= MIN_FIGURE_AREA and pages_with_image < REPEATED_FIGURE_PAGES


def _prepare(blob: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(blob))
    image.load()
    # Sơ đồ PNG nền trong suốt có chữ đen: bỏ kênh alpha thẳng thì nền thành đen và mất hết chữ.
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, "white")
        background.paste(image, mask=image.getchannel("A"))
        image = background
    else:
        image = image.convert("RGB")
    # Chữ nhỏ trong ảnh thu nhỏ trên slide: Tesseract đọc tốt nhất khi chữ cao khoảng 30 điểm ảnh.
    if image.width < 1000:
        scale = 1000 / image.width
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.LANCZOS)
    return image


def ocr_figure(blob: bytes) -> str:
    """Các dòng chữ đọc được trong ảnh, hoặc chuỗi rỗng nếu ảnh không có chữ đáng tin."""
    data = pytesseract.image_to_data(_prepare(blob), lang="vie+eng", output_type=pytesseract.Output.DICT)
    lines: dict[tuple[int, int, int], list[tuple[str, float]]] = {}
    for i, word in enumerate(data["text"]):
        confidence = float(data["conf"][i])
        if word.strip() and confidence >= 0:
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append((word.strip(), confidence))
    every = [c for words in lines.values() for _, c in words]
    confident = sum(1 for words in lines.values() for w, c in words if c >= _CONFIDENT and _WORD.search(w))
    if confident < _MIN_CONFIDENT_WORDS or sum(every) / len(every) < _CONFIDENT:
        return ""
    kept = []
    for words in lines.values():
        words = [(_CONNECTOR.sub("", w), c) for w, c in words if c >= _MIN_WORD_CONFIDENCE]
        words = [(w, c) for w, c in words if w and not _RULE.fullmatch(w)]
        text = " ".join(w for w, _ in words)
        if words and sum(c for _, c in words) / len(words) >= _MIN_LINE_CONFIDENCE and re.search(r"\w", text):
            kept.append(text)
    return "\n".join(kept)


def ocr_figures(blobs: list[bytes]) -> list[str]:
    """OCR từng ảnh; ảnh lỗi (định dạng không mở được, Tesseract hỏng) cho chuỗi rỗng, vì thiếu
    chữ của một hình không được làm hỏng cả tài liệu."""

    def one(blob: bytes) -> str:
        try:
            return ocr_figure(blob)
        except Exception as error:
            logger.warning(f"[figures] OCR failed on an image: {error}")
            return ""

    if not blobs:
        return []
    with ThreadPoolExecutor(max_workers=_WORKERS) as pool:
        return list(pool.map(one, blobs))


def new_text(figure_text: str, existing_text: str) -> bool:
    """Hình có thêm chữ mà lớp chữ của trang chưa có. Ảnh nằm dưới một khung chữ (hình nền của
    tiêu đề) thì OCR đọc lại đúng những chữ đã có, thêm vào chỉ làm trùng."""
    known = {w.lower() for w in _WORD.findall(existing_text)}
    words = [w.lower() for w in _WORD.findall(figure_text)]
    novel = [w for w in words if w not in known]
    return len(novel) >= 3 and len(novel) >= 0.3 * len(words)
