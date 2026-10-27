"""Đọc lại công thức của một trang PDF từ ảnh trang, khi lớp chữ không dựng lại được.

pdf_layout dựng công thức từ vị trí ký tự và nét vẽ, đủ cho phần lớn trang. Trang còn dấu hiệu vỡ
(suspect_formula_lines) mới được gửi cho một model đọc ảnh qua gateway, nên số lần gọi tỷ lệ với
số trang hỏng chứ không với số trang. Model chép lại cả trang, công thức viết bằng LaTeX.

Chữ thường của trang thì lớp chữ đã đúng, kể cả dấu tiếng Việt, còn model đọc ảnh có thể đổi một
dấu hay bỏ một dòng. Bản chép chỉ được nhận khi gần như mọi từ của lớp chữ còn nguyên trong đó và
nó không thêm câu chữ nào lớp chữ không có; nếu không, trang giữ bản dựng lại và có cảnh báo.
"""

import re
import unicodedata

from app.config import settings
from app.llm.gateway import complete_with_image, using_model

PROMPT = """This is one page of a lecture. Below is the text layer of the same page, in reading \
order, where the prose is correct but the formulas are garbled.

Transcribe the page line by line in reading order. Copy every sentence exactly as it is in the \
text layer, with the same words and the same Vietnamese diacritics, including the header and the \
footer. Write every formula in LaTeX between $ and $, read from the image. Do not translate, \
explain, summarise or add anything that is not on the page. Return only the transcription.

Text layer:
{text}"""

# Phần từ của lớp chữ phải có mặt trong bản chép, và phần từ mới tối đa mà bản chép được thêm.
_MIN_RECALL = 0.9
_MAX_NOVEL = 0.1
_WORD = re.compile(r"[^\W\d_]{2,}")
# Tên lệnh và môi trường LaTeX (\frac, \sqrt, \begin{cases}) không phải chữ của trang.
_LATEX_COMMAND = re.compile(r"\\(?:begin|end)\{\w+\*?\}|\\[a-zA-Z]+")


def _words(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFC", _LATEX_COMMAND.sub(" ", text)))


def _faithful(transcription: str, text_layer: str) -> bool:
    known = set(_words(text_layer))
    written = _words(transcription)
    if not known or not written:
        return False
    # Hàm sơ cấp (lim, sin, ln) được viết bằng lệnh LaTeX nên không còn là chữ trong bản chép;
    # chúng cũng không phải câu chữ cần giữ.
    commands = {c[1:] for c in _LATEX_COMMAND.findall(transcription)}
    expected = known - commands
    recall = len(expected & set(written)) / max(len(expected), 1)
    novel = sum(1 for w in written if w not in known) / len(written)
    return recall >= _MIN_RECALL and novel <= _MAX_NOVEL


def repair_page(png: bytes, text_layer: str) -> str | None:
    """Bản chép của trang, hoặc None khi model trả về một bản không trung thành với lớp chữ.
    Lỗi gọi model (mạng, hạn mức) được ném ra để nơi gọi ghi cảnh báo."""
    with using_model(settings.formula_vision_model):
        transcription = complete_with_image(PROMPT.format(text=text_layer), png, temperature=0)
    transcription = unicodedata.normalize("NFC", (transcription or "").strip())
    # Model hay bọc câu trả lời trong khối mã dù được dặn chỉ trả bản chép.
    transcription = re.sub(r"^```\w*\n|\n```$", "", transcription).strip()
    return transcription if _faithful(transcription, text_layer) else None
