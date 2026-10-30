"""Dựng lại dòng chữ của một trang PDF từ vị trí các mảnh chữ và nét vẽ.

PyMuPDF trả chữ theo khối mà trình soạn thảo đã ghi vào tệp. Với văn bản thường thì khối
chính là đoạn văn, nhưng một công thức MathType bị ghi thành một khối cho mỗi phông (mọi chữ
nghiêng một khối, mọi ký hiệu Symbol một khối khác) chồng lên cùng một vùng, còn gạch phân
số và dấu căn là nét vẽ chứ không phải chữ. Đọc theo khối thì mỗi ký hiệu nằm một dòng, cả
trang giải tích thành một cột ký tự rời mà truy hồi lẫn LLM đều không hiểu.

Ở đây công thức được dựng lại theo hình học: mảnh nào cùng hàng thì nối, mảnh nhỏ lệch lên
là chỉ số trên, mảnh nằm trên và dưới một gạch ngang là tử và mẫu. Văn bản thường không bị
động tới: một vùng chỉ được dựng lại khi có dấu hiệu vỡ.
"""

import bisect
import re
import statistics
import unicodedata
from dataclasses import dataclass

import pymupdf

# Adobe Symbol encoding sang Unicode. Phông Symbol ghi ký hiệu bằng mã của chính nó (0x61 là
# alpha, 0xF2 là dấu tích phân), còn PDF xuất từ Office thường đẩy mã đó vào vùng riêng
# U+F000 + mã, nên cả hai dạng đều phải đổi.
_SYMBOL = {
    0x20: " ", 0x21: "!", 0x22: "∀", 0x23: "#", 0x24: "∃", 0x25: "%", 0x26: "&", 0x27: "∋", 0x28: "(",
    0x29: ")", 0x2A: "∗", 0x2B: "+", 0x2C: ",", 0x2D: "−", 0x2E: ".", 0x2F: "/", 0x3A: ":", 0x3B: ";",
    0x3C: "<", 0x3D: "=", 0x3E: ">", 0x3F: "?", 0x40: "≅", 0x41: "Α", 0x42: "Β", 0x43: "Χ", 0x44: "Δ",
    0x45: "Ε", 0x46: "Φ", 0x47: "Γ", 0x48: "Η", 0x49: "Ι", 0x4A: "ϑ", 0x4B: "Κ", 0x4C: "Λ", 0x4D: "Μ",
    0x4E: "Ν", 0x4F: "Ο", 0x50: "Π", 0x51: "Θ", 0x52: "Ρ", 0x53: "Σ", 0x54: "Τ", 0x55: "Υ", 0x56: "ς",
    0x57: "Ω", 0x58: "Ξ", 0x59: "Ψ", 0x5A: "Ζ", 0x5B: "[", 0x5C: "∴", 0x5D: "]", 0x5E: "⊥", 0x5F: "_",
    0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x65: "ε", 0x66: "φ", 0x67: "γ", 0x68: "η", 0x69: "ι",
    0x6A: "ϕ", 0x6B: "κ", 0x6C: "λ", 0x6D: "μ", 0x6E: "ν", 0x6F: "ο", 0x70: "π", 0x71: "θ", 0x72: "ρ",
    0x73: "σ", 0x74: "τ", 0x75: "υ", 0x76: "ϖ", 0x77: "ω", 0x78: "ξ", 0x79: "ψ", 0x7A: "ζ", 0x7B: "{",
    0x7C: "|", 0x7D: "}", 0x7E: "∼", 0xA0: "€", 0xA1: "ϒ", 0xA2: "′", 0xA3: "≤", 0xA4: "⁄", 0xA5: "∞",
    0xA6: "ƒ", 0xA7: "♣", 0xA8: "♦", 0xA9: "♥", 0xAA: "♠", 0xAB: "↔", 0xAC: "←", 0xAD: "↑", 0xAE: "→",
    0xAF: "↓", 0xB0: "°", 0xB1: "±", 0xB2: "″", 0xB3: "≥", 0xB4: "×", 0xB5: "∝", 0xB6: "∂", 0xB7: "•",
    0xB8: "÷", 0xB9: "≠", 0xBA: "≡", 0xBB: "≈", 0xBC: "…", 0xBD: "|", 0xBF: "↵", 0xC0: "ℵ", 0xC1: "ℑ",
    0xC2: "ℜ", 0xC3: "℘", 0xC4: "⊗", 0xC5: "⊕", 0xC6: "∅", 0xC7: "∩", 0xC8: "∪", 0xC9: "⊃", 0xCA: "⊇",
    0xCB: "⊄", 0xCC: "⊂", 0xCD: "⊆", 0xCE: "∈", 0xCF: "∉", 0xD0: "∠", 0xD1: "∇", 0xD2: "®", 0xD3: "©",
    0xD4: "™", 0xD5: "∏", 0xD6: "√", 0xD7: "⋅", 0xD8: "¬", 0xD9: "∧", 0xDA: "∨", 0xDB: "⇔", 0xDC: "⇐",
    0xDD: "⇑", 0xDE: "⇒", 0xDF: "⇓", 0xE0: "◊", 0xE1: "〈", 0xE2: "®", 0xE3: "©", 0xE4: "™", 0xE5: "∑",
    0xF1: "〉", 0xF2: "∫", 0xF3: "∫",
    # Nét nối của dấu căn, mũi tên và tích phân nhiều tầng: chỉ là phần kéo dài của ký hiệu
    # đã có, giữ lại thì thành rác giữa dòng.
    0x60: "", 0xBE: "", 0xF4: "", 0xF5: "",
}
_SYMBOL.update({c: chr(c) for c in range(0x30, 0x3A)})

# Mảnh ngoặc lớn (đỉnh, thân, đáy) của phông Symbol, và cùng các mảnh đó trong vùng mã riêng
# của Adobe U+F8EB-U+F8FE mà TeX (CMEX) dùng. Mỗi mảnh đổi thành đúng dấu ngoặc; các mảnh
# chồng nhau được gộp lại thành một dấu ở bước dựng hàng.
_BRACKET_PIECES = {}
for _code, _char in zip(range(0xE6, 0xF0), "((([[[{{{{"):
    _BRACKET_PIECES[chr(0xF000 + _code)] = _char
for _code, _char in zip(range(0xF6, 0xFF), ")))]]]}}}"):
    _BRACKET_PIECES[chr(0xF000 + _code)] = _char
for _code, _char in zip(range(0xF8EB, 0xF8FF), "((([[[{{{|)))]]]}}}"):
    _BRACKET_PIECES[chr(_code)] = _char

# Phông CMEX của TeX không kèm bảng chuyển mã: PyMuPDF trả nguyên mã glyph, nên dấu tích phân ra
# chữ "R", dấu căn ra "q", ngoặc lớn ra ký tự điều khiển. Chỉ đổi chữ cái, chữ số và ký tự điều
# khiển: không glyph CMEX nào có bảng chuyển mã ra những ký tự đó, còn ngoặc tròn, ngoặc vuông hay
# gạch dọc thì tệp có kèm bảng chuyển mã cũng ra đúng các ký tự ấy, đổi nữa là sai.
_CMEX_DELIMITERS = {
    **dict(zip(range(0x10), "()[]⌊⌋⌈⌉{}⟨⟩|‖/\\")),
    **dict(zip(range(0x10, 0x20), "()()[]⌊⌋⌈⌉{}⟨⟩/\\")),
    **dict(zip(range(0x21, 0x28), ")[]⌊⌋⌈⌉")),
    **dict(zip(range(0x30, 0x46), "()[][][]{}{}{}{|()()⟨⟩")),
    **dict(zip(range(0x68, 0x70), "[]⌊⌋⌈⌉{}")),
    0x77: "‖",
}
_CMEX_SYMBOLS = {
    **dict(zip(range(0x46, 0x62), "⊔⊔∮∮⊙⊙⊕⊕⊗⊗∑∏∫⋃⋂⨄⋀⋁∑∏∫⋃⋂⨄⋀⋁∐∐")),
    0x70: "√", 0x71: "√", 0x72: "√", 0x73: "√", 0x74: "√",
    # Mũ, sóng, thân dấu căn, đầu mũi tên và mảnh ngoặc nhọn nằm ngang: phần kéo dài của ký hiệu khác.
    **{c: "" for c in (*range(0x62, 0x68), 0x75, 0x76, *range(0x78, 0x7B))},
}
_CMEX_MAPPED = {*range(0x20), *range(0x21, 0x28), *range(0x30, 0x5B), *range(0x5E, 0x7B)}
# Glyph CMEX treo dưới điểm gốc, sâu hơn hẳn một dòng chữ, mà khung PyMuPDF trả chỉ cao bằng cỡ
# chữ: ngoặc bao quanh một phân số trông như nằm trên dòng của tử số. Độ sâu (theo em) lấy từ
# bảng số đo của cmex10: big, Big, bigg, Bigg; toán tử lớn dạng chữ và dạng trưng bày.
_CMEX_DEPTH = {
    **dict.fromkeys(range(0x10), 1.16), 0x10: 1.76, 0x11: 1.76, **dict.fromkeys(range(0x12, 0x20), 2.36),
    **dict.fromkeys(range(0x20, 0x30), 2.96), **dict.fromkeys(range(0x30, 0x44), 1.76), 0x44: 2.36, 0x45: 2.36,
    **dict.fromkeys((*range(0x46, 0x50, 2), *range(0x50, 0x58), 0x60), 1.0),
    **dict.fromkeys((*range(0x47, 0x50, 2), *range(0x58, 0x60), 0x61), 1.36),
    0x52: 1.07, 0x5A: 2.18, **dict.fromkeys(range(0x68, 0x70), 1.76),
    0x70: 1.16, 0x71: 1.76, 0x72: 2.36, 0x73: 2.96,
}
# Chữ bảng đen của AMS (msbm): R là tập số thực chứ không phải chữ R.
_BLACKBOARD = dict(zip("CNPQRZ", "ℂℕℙℚℝℤ"))

# Phông ký hiệu trang trí: gần như luôn là dấu đầu dòng, còn mã gốc của nó không mang nghĩa
# gì khi đọc thành chữ.
_DINGBAT_FONTS = ("wingdings", "webdings", "monotypesorts", "zapfdingbats", "dingbats")
# Phông của ký hiệu toán: khối chứa chúng được phép nối với khối bên cạnh cùng hàng, vì đó
# là các phần của cùng một công thức.
_MATH_FONTS = ("symbol", "cmmi", "cmsy", "cmex", "cmr", "msam", "msbm", "mt extra", "mt-extra", "cambria math",
               "math")
# Phông chỉ chứa ký hiệu toán. cmr là phông chữ thân bài của LaTeX nên không làm một dòng thành
# dòng công thức.
_MATH_SYMBOL_FONTS = tuple(f for f in _MATH_FONTS if f != "cmr")


# Toán tử có giới hạn đặt ngay dưới hoặc trên nó. Chữ thường không bao giờ có: chỉ số của dòng
# phía trên nằm sát dưới một từ ở dòng dưới là chuyện thường gặp trên slide.
_LIMIT_OPERATORS = {"lim", "∑", "∏", "∫", "max", "min", "sup", "inf", "Σ", "Π"}


# Bảng mã VNI: dấu thanh và dấu mũ là một ký tự Latin-1 đứng ngay sau nguyên âm ("eá" là ế,
# "oâ" là ô), vài chữ riêng dùng một ký tự đơn ("ö" là ư, "ñ" là đ). Tệp có kèm bảng chuyển mã
# thì PyMuPDF đã ra đúng Unicode; tệp không kèm thì ra chuỗi này, và cả trang thành rác.
_COMBINING = {"sac": "́", "huyen": "̀", "hoi": "̉", "nga": "̃", "nang": "̣"}
_VNI_TONES = {"ù": "sac", "ø": "huyen", "û": "hoi", "õ": "nga", "ï": "nang"}
_VNI_CIRCUMFLEX = {"â": None, "á": "sac", "à": "huyen", "å": "hoi", "ã": "nga", "ä": "nang"}
_VNI_BREVE = {"ê": None, "é": "sac", "è": "huyen", "ú": "hoi", "ü": "nga", "ë": "nang"}
# Chữ đứng một mình: ơ, ư, và nguyên âm i, y mang dấu thanh đã gói sẵn trong một ký tự.
_VNI_SINGLE = {"ô": "ơ", "ö": "ư", "ñ": "đ", "í": "í", "ì": "ì", "æ": "ỉ", "ó": "ĩ", "ò": "ị", "î": "ỵ"}
# Ký tự không bao giờ có trong tiếng Việt Unicode: gặp chúng trong phông VNI nghĩa là chữ chưa
# được chuyển mã, còn không gặp thì PyMuPDF đã chuyển đúng và không được động vào.
_VNI_ONLY = set("øûïåäëüñöæîØÛÏÅÄËÜÑÖÆÎ")


def _vni_table() -> dict[str, str]:
    def compose(base: str, *marks: str) -> str:
        return unicodedata.normalize("NFC", base + "".join(marks))

    table: dict[str, str] = {}
    for lower in (False, True):
        def case(s: str) -> str:
            return s.lower() if lower else s.upper()

        for vowel in "aeiouy":
            for mark, tone in _VNI_TONES.items():
                table[case(vowel + mark)] = case(compose(vowel, _COMBINING[tone]))
        for vowel in "aeo":
            for mark, tone in _VNI_CIRCUMFLEX.items():
                table[case(vowel + mark)] = case(compose(vowel, "̂", *([_COMBINING[tone]] if tone else [])))
        for mark, tone in _VNI_BREVE.items():
            table[case("a" + mark)] = case(compose("a", "̆", *([_COMBINING[tone]] if tone else [])))
        for vowel, horn in (("ô", "o"), ("ö", "u")):
            for mark, tone in _VNI_TONES.items():
                table[case(vowel + mark)] = case(compose(horn, "̛", _COMBINING[tone]))
        for single, target in _VNI_SINGLE.items():
            table[case(single)] = case(target)
    return table


_VNI = _vni_table()
# Cặp hai ký tự phải khớp trước ký tự đơn trong cùng một lượt: "ôù" là ớ, còn đổi "ô" thành ơ
# trước thì không còn nhận ra cặp đó nữa.
_VNI_RE = re.compile("|".join(sorted(map(re.escape, _VNI), key=len, reverse=True)))


def vni_to_unicode(text: str) -> str:
    return _VNI_RE.sub(lambda m: _VNI[m.group(0)], text)


def _vni_fonts(blocks: list[dict]) -> set[str]:
    fonts = set()
    for block in blocks:
        for line in block["lines"]:
            for span in line["spans"]:
                if span["font"].lower().startswith("vni") and _VNI_ONLY & set(_span_text(span)):
                    fonts.add(span["font"])
    return fonts


# rawdict mặc định kèm cả dữ liệu ảnh của trang, chậm gấp trăm lần trên slide nhiều hình mà ở
# đây chỉ cần chữ.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES


def _is_private(c: str) -> bool:
    return 0xE000 <= ord(c) <= 0xF8FF


def map_font_text(text: str, font: str, vni_fonts: frozenset[str] = frozenset()) -> tuple[str, bool]:
    """Đổi mã của phông ký hiệu và phông VNI sang Unicode. Trả thêm cờ cho biết đây có phải mảnh
    ngoặc lớn."""
    if font in vni_fonts:
        return vni_to_unicode(text), False
    name = font.lower()
    if any(f in name for f in _DINGBAT_FONTS):
        return "".join("•" if _is_private(c) or not c.isspace() else c for c in text), False
    base = name.split(",")[0].split("-")[0].removesuffix("mt")
    is_symbol = base == "symbol"
    is_cmex, is_blackboard = base.startswith("cmex"), base.startswith("msbm")
    out, bracket = [], False
    for c in text:
        if is_cmex and ord(c) in _CMEX_MAPPED:
            if ord(c) in _CMEX_DELIMITERS:
                out.append(_CMEX_DELIMITERS[ord(c)])
                bracket = True
            else:
                out.append(_CMEX_SYMBOLS[ord(c)])
            continue
        if is_blackboard and c in _BLACKBOARD:
            out.append(_BLACKBOARD[c])
            continue
        if is_symbol and ord(c) < 0x100:
            c = chr(0xF000 + ord(c))
        if c in _BRACKET_PIECES:
            out.append(_BRACKET_PIECES[c])
            bracket = True
        elif is_symbol and 0xF020 <= ord(c) <= 0xF0FF:
            out.append(_SYMBOL.get(ord(c) - 0xF000, chr(ord(c) - 0xF000)))
        else:
            out.append(c)
    return "".join(out), bracket


def _overlap(a0: float, a1: float, b0: float, b1: float) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


@dataclass
class _Piece:
    x0: float
    y0: float
    x1: float
    y1: float
    baseline: float
    size: float
    text: str
    # Ngoặc lớn, gạch dọc và phân số cao hơn một dòng chữ: chúng thuộc hàng nào chúng cắt
    # ngang qua giữa, chứ không xét theo mức chồng lên như chữ thường.
    tall: bool = False
    # Tệp có ghi dấu cách ngay trước mảnh này: khoảng hở hình học giữa chữ nghiêng hay chữ
    # đậm không đủ tin để đoán ranh giới từ.
    space_before: bool = False
    # "^" cho chỉ số trên, "_" cho chỉ số dưới, rỗng cho chữ ở dòng chính.
    script: str = ""
    # Vị trí dùng để xếp thứ tự thay cho x0: giới hạn "n→∞" canh giữa dưới "lim" nên bắt đầu
    # lệch trái hơn chữ lim, mà phải được đọc ra sau nó.
    anchor: float | None = None

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


@dataclass
class _Rule:
    x0: float
    x1: float
    y: float
    radical: bool = False


def _strokes(page: pymupdf.Page) -> tuple[list[_Rule], list[tuple[float, float, float]]]:
    """Gạch ngang (phân số, vạch trên của dấu căn) và gạch dọc (trị tuyệt đối) trên trang."""
    rules: list[_Rule] = []
    verticals: list[tuple[float, float, float]] = []
    diagonals = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                p, q = item[1], item[2]
                if abs(p.y - q.y) < 0.8 and abs(q.x - p.x) > 3:
                    rules.append(_Rule(min(p.x, q.x), max(p.x, q.x), (p.y + q.y) / 2))
                elif abs(p.x - q.x) < 0.8 and abs(q.y - p.y) > 6:
                    verticals.append((p.x, min(p.y, q.y), max(p.y, q.y)))
                else:
                    diagonals.append((p, q))
            elif item[0] == "re":
                r = item[1]
                if r.height < 1.5 and r.width > 3:
                    rules.append(_Rule(r.x0, r.x1, (r.y0 + r.y1) / 2))
    # Vạch ngang mà đầu trái chạm một nét chéo đi xuống là vạch trên của dấu căn, không phải
    # gạch phân số: nhầm thì dòng ngay trên dấu căn bị kéo xuống làm tử số.
    for rule in rules:
        for p, q in diagonals:
            for top, bottom in ((p, q), (q, p)):
                if abs(top.x - rule.x0) < 1.5 and abs(top.y - rule.y) < 1.5 and bottom.y > top.y + 3:
                    rule.radical = True
    # MathType vẽ một dấu căn hai ba lần (nét viền, mảng tô) lệch nhau vài điểm: không gộp thì
    # mỗi bản thành một dấu căn lồng vào bản kia.
    unique: list[_Rule] = []
    for rule in sorted(rules, key=lambda r: (r.y, r.x0)):
        twin = next((u for u in unique if abs(u.y - rule.y) < 2.5 and _overlap(u.x0, u.x1, rule.x0, rule.x1)
                     >= 0.8 * min(u.x1 - u.x0, rule.x1 - rule.x0)), None)
        if twin is not None:
            twin.radical = twin.radical or rule.radical
            continue
        unique.append(rule)
    return unique, verticals


def _block_is_mathlike(block: dict) -> bool:
    """Khối có chữ của phông ký hiệu toán. Chỉ dựa vào phông chứ không vào độ dài dòng: số dòng
    của đoạn mã, dấu ngoặc nhọn hay số trang cũng là dòng rất ngắn, và coi chúng là công thức
    thì chú thích đè lên mã nguồn bị trộn chữ vào nhau."""
    spans = [s for line in block["lines"] for s in line["spans"]]
    if any(any(f in s["font"].lower() for f in _MATH_FONTS) for s in spans):
        return True
    # Công thức soạn bằng Arial hay Times thì không có phông toán nào; dấu hiệu còn lại là các
    # biến in nghiêng đứng riêng lẻ (x, y, dx). Mã nguồn dùng phông đơn cách, kể cả chú thích
    # nghiêng như "//" hay "of", nên không được tính.
    italic_fragments = sum(
        1 for s in spans
        if 0 < len(_span_text(s).strip()) <= 3 and not s["flags"] & pymupdf.TEXT_FONT_MONOSPACED
        and (s["flags"] & pymupdf.TEXT_FONT_ITALIC or "italic" in s["font"].lower())
    )
    return italic_fragments >= 2


def _block_box(block: dict) -> tuple[float, float, float, float]:
    """Khung của khối, kéo xuống tới đáy thật của glyph CMEX treo dưới điểm gốc."""
    x0, y0, x1, y1 = block["bbox"]
    for line in block["lines"]:
        for span in line["spans"]:
            if span["font"].lower().startswith("cmex"):
                for c in span["chars"]:
                    y1 = max(y1, c["origin"][1] + _CMEX_DEPTH.get(ord(c["c"]), 0) * span["size"])
    return x0, y0, x1, y1


def _regions(blocks: list[dict]) -> list[list[int]]:
    """Gom các khối thuộc cùng một công thức: khung chồng lên nhau, hoặc cùng hàng và sát nhau
    khi ít nhất một khối mang ký hiệu toán. Hai cột văn bản thường không bao giờ bị gom, và khối
    mã nguồn chỉ chạm chân trang hay chú thích thường thì giữ nguyên thứ tự của nó."""
    parent = list(range(len(blocks)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    boxes = [_block_box(b) for b in blocks]
    mathlike = [_block_is_mathlike(b) for b in blocks]
    code = [any(s["flags"] & pymupdf.TEXT_FONT_MONOSPACED for line in b["lines"] for s in line["spans"])
            for b in blocks]
    sizes = [max((s["size"] for line in b["lines"] for s in line["spans"]), default=10) for b in blocks]
    for i, a in enumerate(boxes):
        for j in range(i + 1, len(boxes)):
            b = boxes[j]
            touching = _overlap(a[0] - 3, a[2] + 3, b[0], b[2]) > 0 and _overlap(a[1] - 3, a[3] + 3, b[1], b[3]) > 0
            same_row = _overlap(a[1], a[3], b[1], b[3]) >= 0.5 * min(a[3] - a[1], b[3] - b[1])
            gap = max(b[0] - a[2], a[0] - b[2])
            near = same_row and gap < max(sizes[i], sizes[j]) and (mathlike[i] or mathlike[j])
            if near or (touching and (mathlike[i] or mathlike[j] or not (code[i] or code[j]))):
                parent[root(i)] = root(j)
    groups: dict[int, list[int]] = {}
    for i in range(len(blocks)):
        groups.setdefault(root(i), []).append(i)
    return sorted(groups.values(), key=min)


def _is_shattered(boxes: list[tuple]) -> bool:
    """Hai dòng PyMuPDF nằm trên cùng một hàng ngang: dấu hiệu vùng đã bị tách theo phông."""
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            h = min(a[3] - a[1], b[3] - b[1])
            if h > 0 and _overlap(a[1], a[3], b[1], b[3]) >= 0.5 * h and _overlap(a[0], a[2], b[0], b[2]) < 0.5 * h:
                return True
    return False


def _wrap(body: str) -> str:
    if len(body) == 1:
        return body
    depth = 0
    for i, c in enumerate(body):
        depth += (c == "(") - (c == ")")
        if depth == 0 and i < len(body) - 1:
            break
    else:
        if body.startswith("(") and body.endswith(")"):
            return body
    return f"({body})"


def _join(pieces: list[_Piece]) -> str:
    """Nối một hàng trái sang phải. Các mảnh chỉ số liền nhau là một cụm chỉ số: "^α", "^+",
    "^1" thành "^(α+1)", vì "x^α+1" đọc ra một biểu thức khác. Chỉ số dưới và trên chồng lên
    nhau (ion A với "n+" ở trên, "(dd)" ở dưới) viết dưới trước trên sau, như quy ước LaTeX,
    thay vì xen kẽ từng mảnh theo tọa độ."""
    tokens: list[list[_Piece]] = []
    for piece in sorted(pieces, key=lambda p: p.x0 if p.anchor is None else p.anchor):
        if tokens and piece.script and tokens[-1][0].script:
            tokens[-1].append(piece)
        else:
            tokens.append([piece])
    text, previous = "", None
    for group in tokens:
        first = group[0]
        if not first.script:
            if previous is not None and (
                first.space_before or first.x0 - previous.x1 > 0.12 * max(first.size, previous.size)
            ):
                text += " "
            text += first.text
        else:
            below = "".join(p.text for p in group if p.script == "_")
            above = "".join(p.text for p in group if p.script == "^")
            text += ("_" + _wrap(below) if below else "") + ("^" + _wrap(above) if above else "")
        previous = group[-1]
    return text


_BIG_OPERATOR_CHARS = set("∫∑∏∮Σ")


def _is_big_operator(text: str) -> bool:
    return bool(text) and set(text) <= _BIG_OPERATOR_CHARS


def _takes_limits(text: str) -> bool:
    return text in _LIMIT_OPERATORS or _is_big_operator(text)


def _small_pieces(pieces: list[_Piece]) -> set[int]:
    """Mảnh nhỏ hơn hẳn chữ ngay cạnh nó là ứng viên chỉ số. So với chữ lân cận chứ không với
    cỡ chữ chung của vùng: một vùng lớn gồm nhiều công thức có trung vị cỡ chữ thấp tới mức
    chỉ số "+∞" của tích phân trông như chữ thường. Dấu ∫, ∑ luôn to hơn chữ nên không được
    làm thước so, không thì chính chữ thường bên cạnh chúng bị coi là chỉ số."""
    reference = [p for p in pieces if not p.tall and not _is_big_operator(p.text)]
    operators = [p for p in pieces if not p.tall and _is_big_operator(p.text)]
    tall = [p for p in pieces if p.tall]
    reference.sort(key=lambda p: p.x0)
    starts = [p.x0 for p in reference]
    small: set[int] = set()
    for p in pieces:
        if p.tall:
            continue
        reach = 1.5 * p.size
        lo = bisect.bisect_left(starts, p.x0 - 4 * reach)
        biggest = p.size
        for q in reference[lo:]:
            if q.x0 > p.x1 + reach:
                break
            if q.x1 >= p.x0 - reach and _overlap(p.y0 - 0.3 * p.size, p.y1 + 0.3 * p.size, q.y0, q.y1) > 0:
                biggest = max(biggest, q.size)
        # Cận dưới dấu ∑ không chạm chữ thường nào, nên thước so của nó là chính toán tử.
        for o in operators:
            beside = _overlap(p.x0, p.x1, o.x0 - 0.5 * o.size, o.x1 + 0.5 * o.size) > 0
            below = 0 <= p.y0 - o.y1 + 0.35 * o.size < 0.9 * o.size
            above = 0 <= o.y0 - p.y1 + 0.35 * o.size < 0.9 * o.size
            if beside and (below or above):
                biggest = max(biggest, o.size)
        # Số mũ của (1/q)^n chỉ đứng cạnh ngoặc lớn, ở nửa trên của nó. Chữ bên trong ngoặc thì
        # trải qua giữa ngoặc, nên không bị so với cỡ của ngoặc.
        for q in tall:
            if q.x1 - 1 <= p.x0 <= q.x1 + reach and p.y1 <= q.cy:
                biggest = max(biggest, q.size)
        if p.size < 0.85 * biggest:
            small.add(id(p))
    return small


def _rows(pieces: list[_Piece]) -> list[list[_Piece]]:
    """Xếp mảnh thành hàng. Chữ cỡ chính quyết định hàng; chữ nhỏ hơn nằm sát bên một hàng là
    chỉ số của hàng đó; phân số và ngoặc thuộc hàng mà chúng cắt qua giữa."""
    small_ids = _small_pieces(pieces)
    normal = sorted((p for p in pieces if not p.tall and id(p) not in small_ids), key=lambda p: p.baseline)
    small = [p for p in pieces if not p.tall and id(p) in small_ids]
    tall = [p for p in pieces if p.tall]
    rows: list[dict] = []

    def new_row(p: _Piece) -> None:
        rows.append({"y0": p.y0, "y1": p.y1, "baseline": p.baseline, "size": p.size, "pieces": [p], "main": True})

    for p in normal:
        # Cùng đường chân chữ là cùng hàng, kể cả khi khung chữ của hai phông cao thấp khác nhau;
        # chỉ khi không có hàng nào như vậy mới xét độ chồng, và lấy hàng chồng nhiều nhất.
        same = [r for r in rows if abs(r["baseline"] - p.baseline) <= 0.15 * max(r["size"], p.size)]
        if same:
            row = min(same, key=lambda r: abs(r["baseline"] - p.baseline))
        else:
            overlapping = [
                (_overlap(p.y0, p.y1, r["y0"], r["y1"]) / min(p.height, r["y1"] - r["y0"]), r) for r in rows
            ]
            overlapping = [(ratio, r) for ratio, r in overlapping if ratio >= 0.5]
            if not overlapping:
                new_row(p)
                continue
            row = max(overlapping, key=lambda t: t[0])[1]
        row["pieces"].append(p)
        row["y0"], row["y1"] = min(row["y0"], p.y0), max(row["y1"], p.y1)
    for p in tall:
        crossing = [r for r in rows if p.y0 <= (r["y0"] + r["y1"]) / 2 <= p.y1]
        if crossing:
            min(crossing, key=lambda r: abs((r["y0"] + r["y1"]) / 2 - p.cy))["pieces"].append(p)
        else:
            new_row(p)
    # Trái sang phải: chữ "1" của "^(α+1)" chỉ nằm sát hàng chính qua α và dấu cộng đứng trước
    # nó, nên hai mảnh kia phải được gắn trước.
    for p in sorted(small, key=lambda p: p.x0):
        best = None
        for r in rows:
            size = r["size"]
            # Giới hạn của lim, ∑, ∫: nằm hẳn dưới hoặc trên toán tử, canh giữa theo nó nên có thể
            # rộng hơn và bắt đầu lệch trái hơn chính toán tử.
            base = next((q for q in r["pieces"] if not q.script and _takes_limits(q.text)
                         and _overlap(p.x0, p.x1, q.x0 - 0.5 * size, q.x1 + 0.5 * size) > 0
                         and (0 <= p.y0 - q.y1 + 0.35 * size < 0.9 * size or 0 <= q.y0 - p.y1 + 0.35 * size < 0.9 * size)),
                        None)
            neighbour = min(r["pieces"], key=lambda q: min(abs(p.x0 - q.x1), abs(q.x0 - p.x1)))
            # Số mũ của một ngoặc lớn nằm cao hơn cả dòng chữ, nhưng vẫn chạm vào chính ngoặc đó.
            touching = _overlap(p.y0, p.y1, r["y0"], r["y1"]) > 0 or (
                neighbour.tall and _overlap(p.y0, p.y1, neighbour.y0, neighbour.y1) > 0)
            adjacent = touching and min(abs(p.x0 - neighbour.x1), abs(neighbour.x0 - p.x1)) < 0.8 * size
            if base is None and not adjacent:
                continue
            # Giới hạn được ưu tiên hơn mọi quan hệ kề bên: chữ "n" dưới "lim" còn nằm sát dòng
            # văn bản ngay dưới nó, và gắn vào đó thì thành một chỉ số trên vô nghĩa.
            score = (0 if base is not None else 1, 0 if r["main"] else 1, abs((r["y0"] + r["y1"]) / 2 - p.cy))
            if best is None or score < best[0]:
                best = (score, r, base, neighbour)
        if best is None:
            new_row(p)
            rows[-1]["main"] = False
            continue
        _, row, base, neighbour = best
        if base is not None:
            p.script = "_" if p.cy > base.cy else "^"
            p.anchor = base.x1 + p.x0 * 1e-4
        else:
            shift = row["baseline"] - p.baseline
            if shift > 0.2 * row["size"]:
                p.script = "^"
            elif shift < -0.1 * row["size"]:
                p.script = "_"
            # Mảnh nối tiếp một giới hạn (dấu ∞ sau "n→") đi cùng giới hạn đó.
            if neighbour.anchor is not None and p.script == neighbour.script:
                p.anchor = neighbour.anchor + p.x0 * 1e-4
        row["pieces"].append(p)
    rows.sort(key=lambda r: r["y0"])
    return [r["pieces"] for r in rows]


def _group(pieces: list[_Piece]) -> str:
    text = " ".join(_join(row).strip() for row in _rows(pieces))
    # Một ký hiệu đơn hay một lũy thừa đơn đứng được một mình; mọi thứ khác cần ngoặc để
    # "a+b/c+d" không bị đọc nhầm thành a + b/c + d.
    return text if re.fullmatch(r"[\w.]+(\^[\w.]+)?", text) else f"({text})"


def _merge_brackets(pieces: list[_Piece]) -> list[_Piece]:
    brackets = sorted((p for p in pieces if p.tall and p.text in "()[]{}|"), key=lambda p: (round(p.x0), p.y0))
    rest = [p for p in pieces if not (p.tall and p.text in "()[]{}|")]
    groups: list[list[_Piece]] = []
    for p in brackets:
        last = groups[-1][-1] if groups else None
        if last and abs(last.x0 - p.x0) < 2 and p.y0 - last.y1 < 3 and last.text == p.text:
            groups[-1].append(p)
        else:
            groups.append([p])
    for g in groups:
        y0, y1 = min(p.y0 for p in g), max(p.y1 for p in g)
        rest.append(_Piece(min(p.x0 for p in g), y0, max(p.x1 for p in g), y1, (y0 + y1) / 2, g[0].size, g[0].text,
                           tall=True))
    return rest


def _is_fraction(rule: _Rule, above: list[_Piece], below: list[_Piece], size: float) -> bool:
    """Tử và mẫu canh giữa gạch phân số, và gạch không dài hơn vế rộng hơn bao nhiêu. Đường kẻ
    bảng, gạch chân hay nét chỉ của hộp chú thích thì dài hơn hẳn chữ nằm cạnh nó, hoặc lệch hẳn
    sang một bên."""
    width = rule.x1 - rule.x0
    centre = (rule.x0 + rule.x1) / 2
    widest = 0.0
    for side in (above, below):
        left, right = min(p.x0 for p in side), max(p.x1 for p in side)
        if abs((left + right) / 2 - centre) > 0.25 * width + 0.3 * size:
            return False
        widest = max(widest, right - left)
    return width <= widest + 2 * size


def _absolute_bars(verticals, box, size: float) -> list[tuple[float, float, float]]:
    """Gạch dọc của |x|: ngắn cỡ một hai dòng và luôn đi theo cặp ngang hàng nhau. Viền bảng thì
    dài, còn nét của ngoặc vẽ tay chỉ có một."""
    x0, y0, x1, y1 = box
    inside = [
        (x, ya, yb) for x, ya, yb in verticals
        if x0 - 2 <= x <= x1 + 2 and y0 - 2 <= ya and yb <= y1 + 2 and yb - ya <= 3 * size
    ]
    return [
        v for v in inside
        if any(w is not v and abs(w[0] - v[0]) > 1 and _overlap(v[1], v[2], w[1], w[2]) >= 0.7 * (v[2] - v[1])
               and abs(w[0] - v[0]) <= 15 * size for w in inside)
    ]


def _structures(pieces: list[_Piece], rules: list[_Rule]) -> list[_Piece]:
    """Dấu căn và phân số, gạch ngắn trước: một phân số lồng trong phân số khác được dựng
    xong rồi mới thành một mảnh của tử hay mẫu bên ngoài."""
    # TeX vẽ dấu căn là một glyph √ của phông rồi kéo vạch trên bằng một nét ngang bắt đầu ngay
    # mép phải glyph, không có nét chéo nào như MathType. Gạch phân số đứng ngay sau dấu căn lớn
    # cũng chạm mép glyph đó, nhưng nằm thấp hơn: vạch trên là nét cao nhất.
    signs: dict[int, _Piece] = {}
    for p in pieces:
        if p.text == "√":
            touching = [r for r in rules if abs(p.x1 - r.x0) < 1.5 and p.y0 - 1 <= r.y <= p.y1 + 1]
            if touching:
                signs[id(min(touching, key=lambda r: r.y))] = p
    for rule in sorted(rules, key=lambda r: r.x1 - r.x0):
        within = [p for p in pieces if rule.x0 - 1 <= p.cx <= rule.x1 + 1]
        if not within:
            continue
        size = statistics.median(p.size for p in within)
        below = [p for p in within if rule.y < p.cy <= rule.y + 1.3 * size and p.y0 >= rule.y - 0.3 * size]
        sign = signs.get(id(rule))
        if rule.radical or sign is not None:
            # Dưới dấu căn có thể là cả một phân số, cao hơn hẳn một dòng: nó thuộc dấu căn vì bắt
            # đầu ngay dưới vạch trên.
            below = [p for p in within if p.y0 >= rule.y - 0.3 * size
                     and (p.cy <= rule.y + 1.3 * size or p.y0 <= rule.y + 0.5 * size)]
            if not below:
                continue
            used = set(map(id, below)) | ({id(sign)} if sign is not None else set())
            pieces = [p for p in pieces if id(p) not in used]
            left = sign.x0 if sign is not None else rule.x0 - 0.6 * size
            pieces.append(_Piece(left, rule.y, rule.x1, max(p.y1 for p in below),
                                 statistics.median(p.baseline for p in below), size, "√" + _group(below)))
            continue
        above = [p for p in within if rule.y - 1.3 * size <= p.cy < rule.y and p.y1 <= rule.y + 0.3 * size]
        if not above or not below or not _is_fraction(rule, above, below, size):
            continue
        used = set(map(id, above + below))
        pieces = [p for p in pieces if id(p) not in used]
        pieces.append(_Piece(rule.x0, min(p.y0 for p in above), rule.x1, max(p.y1 for p in below),
                             rule.y + 0.3 * size, size, f"{_group(above)}/{_group(below)}",
                             tall=True))
    return pieces


def _span_text(span: dict) -> str:
    return "".join(c["c"] for c in span["chars"])


def _span_pieces(span: dict, vni_fonts: frozenset[str]) -> list[_Piece]:
    """Tách một span thành từng từ theo vị trí ký tự. PyMuPDF gộp chữ cùng phông cùng cỡ vào
    một span ngay cả khi chúng nằm ở hai tầng khác nhau, như số 1 của tử số 1/a với chữ
    "arctan" ở dòng chính, nên không thể coi cả span là một mảnh."""
    size = span["size"]
    pieces: list[_Piece] = []
    current: list[dict] = []
    space = False
    cmex = span["font"].lower().startswith("cmex")

    def flush() -> None:
        nonlocal current, space
        if current:
            text, bracket = map_font_text("".join(c["c"] for c in current), span["font"], vni_fonts)
            if text.strip():
                y0, y1 = min(c["bbox"][1] for c in current), max(c["bbox"][3] for c in current)
                baseline = current[0]["origin"][1]
                depths = [_CMEX_DEPTH[ord(c["c"])] for c in current if cmex and ord(c["c"]) in _CMEX_DEPTH]
                if depths:
                    y0, y1 = baseline - 0.04 * size, baseline + max(depths) * size
                    # TeX canh ký hiệu lớn giữa trục toán, cao hơn đường chân chữ khoảng một phần tư em.
                    baseline = (y0 + y1) / 2 + 0.25 * size
                pieces.append(_Piece(
                    min(c["bbox"][0] for c in current), y0, max(c["bbox"][2] for c in current), y1,
                    baseline, size, text.strip(), tall=bracket, space_before=space,
                ))
                space = False
        current = []

    for ch in span["chars"]:
        if ch["c"].isspace():
            flush()
            space = True
            continue
        is_bracket = map_font_text(ch["c"], span["font"])[1]
        if current and (
            is_bracket or map_font_text(current[-1]["c"], span["font"])[1]
            or abs(ch["origin"][1] - current[-1]["origin"][1]) > 0.1 * size
            or ch["bbox"][0] - current[-1]["bbox"][2] > 0.25 * size
        ):
            flush()
        current.append(ch)
    flush()
    return pieces


def _line_text(spans: list[dict], vni_fonts: frozenset[str]) -> str:
    """Một dòng PyMuPDF bình thường: chỉ đổi mã phông và đánh dấu chỉ số trên/dưới nằm trong dòng."""
    visible = [s for s in spans if _span_text(s).strip()]
    if not visible:
        return ""
    base = max(visible, key=lambda s: s["size"])
    out = []
    for s in spans:
        text = map_font_text(_span_text(s), s["font"], vni_fonts)[0]
        if s is not base and text.strip() and s["size"] < 0.8 * base["size"] and len(text.strip()) <= 4:
            shift = base["origin"][1] - s["origin"][1]
            if shift > 0.2 * base["size"]:
                text = "^" + text.strip()
            elif shift < -0.1 * base["size"]:
                text = "_" + text.strip()
        out.append(text)
    text = "".join(out).rstrip()
    # Chỉ thụt lề của mã nguồn mang nghĩa; khoảng trắng đầu dòng chữ thường bỏ đi.
    return text if any(s["flags"] & pymupdf.TEXT_FONT_MONOSPACED for s in visible) else text.lstrip()


def page_lines(
    page: pymupdf.Page, *, code_lines: set[int] | None = None, math_lines: set[int] | None = None,
) -> list[str]:
    """Các dòng chữ của trang theo thứ tự đọc, công thức đã được dựng lại thành một dòng. Chỉ số các
    dòng mã nguồn và các dòng có ký hiệu toán được ghi vào `code_lines` và `math_lines`."""
    blocks = [b for b in page.get_text("rawdict", flags=_TEXT_FLAGS)["blocks"] if b["type"] == 0 and b["lines"]]
    if not blocks:
        return []
    rules, verticals = _strokes(page) if any(_block_is_mathlike(b) for b in blocks) else ([], [])
    vni_fonts = frozenset(_vni_fonts(blocks))
    out: list[str] = []
    previous = None
    for region in _regions(blocks):
        lines = [line for i in region for line in blocks[i]["lines"]]
        boxes = [line["bbox"] for line in lines]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        main_size = statistics.median(s["size"] for line in lines for s in line["spans"])
        # Vạch trên của dấu căn nằm cao hơn chữ bên dưới nó, nên được nới lên một chút.
        # Gạch phân số và vạch trên dấu căn thường dài hơn chữ một chút ở hai đầu, còn vạch dấu căn
        # nằm cao hơn chữ bên dưới nó, nên nét chỉ cần nằm phần lớn trong vùng.
        region_rules = [
            r for r in rules
            if _overlap(r.x0, r.x1, x0, x1) >= 0.5 * (r.x1 - r.x0)
            and y0 - 0.6 * main_size <= r.y <= y1 + 0.3 * main_size
        ]
        # Dòng cùng hàng mà không có ký hiệu toán nào là bố cục có chủ ý (chân trang Beamer ba ô,
        # bảng hai cột) chứ không phải công thức vỡ: trộn chúng lại thì chữ các ô đan vào nhau.
        math = any(_block_is_mathlike(blocks[i]) for i in region)
        has_code = any(s["flags"] & pymupdf.TEXT_FONT_MONOSPACED for line in lines for s in line["spans"])
        owners = [i for i in region for _ in blocks[i]["lines"]]
        texts = [_line_text(line["spans"], vni_fonts) for line in lines]
        has_bullets = any(text.strip() in {"•", "▪", "❖"} for text in texts)
        if (not math or ((has_bullets or has_code) and not region_rules)
                or (not _is_shattered(boxes) and not region_rules)):
            for owner, line, text in zip(owners, lines, texts):
                # Chữ vẽ hai lần chồng khít (hiệu ứng bóng của tiêu đề slide) chỉ giữ một lần.
                if not text:
                    continue
                box = line["bbox"]
                base = max(line["spans"], key=lambda s: s["size"])
                baseline, size = base["origin"][1], base["size"]
                if previous:
                    old_text, old_box, old_baseline, old_size, old_owner = previous
                    if (old_text == text and _overlap(old_box[1], old_box[3], box[1], box[3]) > 0
                            and _overlap(old_box[0], old_box[2], box[0], box[2])
                            > 0.8 * min(old_box[2] - old_box[0], box[2] - box[0])):
                        continue
                    gap = box[0] - old_box[2]
                    em = max(size, old_size)
                    listing = owner == old_owner and any(
                        s["flags"] & pymupdf.TEXT_FONT_MONOSPACED for s in line["spans"]
                    ) and ((old_text.strip().isdigit() and gap < 4 * em) or text.lstrip().startswith("//"))
                    if abs(baseline - old_baseline) < 0.15 * em and 0 <= gap and (gap < em or listing):
                        out[-1] += (" " if gap > 0.12 * em else "") + text.lstrip()
                        box = (old_box[0], min(old_box[1], box[1]), box[2], max(old_box[3], box[3]))
                        previous = (out[-1], box, baseline, size, owner)
                        _mark(line["spans"], len(out) - 1, code_lines, math_lines)
                        continue
                out.append(text)
                _mark(line["spans"], len(out) - 1, code_lines, math_lines)
                previous = (text, box, baseline, size, owner)
            continue
        previous = None
        pieces: list[_Piece] = []
        for line in lines:
            for s in line["spans"]:
                for piece in _span_pieces(s, vni_fonts):
                    # Chữ vẽ hai lần chồng khít chỉ giữ một lần.
                    if any(q.text == piece.text and _overlap(q.x0, q.x1, piece.x0, piece.x1) > 0.8 * (piece.x1 - piece.x0)
                           and _overlap(q.y0, q.y1, piece.y0, piece.y1) > 0.8 * piece.height for q in pieces):
                        continue
                    pieces.append(piece)
        for x, ya, yb in _absolute_bars(verticals, (x0, y0, x1, y1), main_size):
            pieces.append(_Piece(x - 0.5, ya, x + 0.5, yb, (ya + yb) / 2, main_size, "|", tall=True))
        pieces = _structures(_merge_brackets(pieces), region_rules)
        symbols = _has_math_symbols([s for line in lines for s in line["spans"]])
        for row in _rows(pieces):
            text = _join(row).strip()
            if text:
                out.append(text)
                if math_lines is not None and symbols:
                    math_lines.add(len(out) - 1)
    return out


def _mark(spans: list[dict], index: int, code_lines: set[int] | None, math_lines: set[int] | None) -> None:
    if code_lines is not None and any(s["flags"] & pymupdf.TEXT_FONT_MONOSPACED for s in spans):
        code_lines.add(index)
    if math_lines is not None and _has_math_symbols(spans):
        math_lines.add(index)


def _has_math_symbols(spans: list[dict]) -> bool:
    return any(
        any(f in s["font"].lower() for f in _MATH_SYMBOL_FONTS) for s in spans if _span_text(s).strip()
    )


def title_candidates(page: pymupdf.Page) -> list[str]:
    """Các dòng to hơn hẳn chữ thân bài, dòng to nhất trước. Trên slide, tiêu đề là dòng cỡ chữ
    lớn nhất chứ không phải dòng đầu tiên: PyMuPDF hay trả chân trang hoặc số trang ra trước."""
    blocks = [b for b in page.get_text("rawdict", flags=_TEXT_FLAGS)["blocks"] if b["type"] == 0 and b["lines"]]
    if not blocks:
        return []
    vni_fonts = frozenset(_vni_fonts(blocks))

    def is_math(span: dict) -> bool:
        return any(f in span["font"].lower() for f in _MATH_FONTS)

    # Cỡ chữ thân bài là cỡ phổ biến nhất của chữ thường. Không lấy trung vị: slide ít chữ có tiêu
    # đề chiếm gần nửa số ký tự, kéo trung vị lên bằng cỡ tiêu đề phụ; còn trang nhiều công thức
    # có hàng trăm chỉ số nhỏ kéo nó xuống. Phông toán không được tính vì cùng lý do đó.
    weighted = [
        round(s["size"] * 2) / 2 for b in blocks for line in b["lines"] for s in line["spans"] if not is_math(s)
        for c in s["chars"] if c["c"].isalpha()
    ]
    body = statistics.mode(weighted) if weighted else 0
    found: list[tuple[float, float, float, str]] = []
    for block in blocks:
        for line in block["lines"]:
            text = _line_text(line["spans"], vni_fonts).strip()
            size = max(s["size"] for s in line["spans"])
            visible = [c for c in text if not c.isspace()]
            letters = sum(c.isalpha() for c in visible)
            # Tiêu đề là chữ: không mở đầu bằng dấu đầu dòng, không mang ký hiệu toán, không phải
            # một dòng công thức hay một câu dài.
            if (
                size >= 1.15 * body and letters >= 0.6 * len(visible) and len(text) <= 120
                and re.search(r"[^\W\d_]{3,}", text)
                and not text.startswith(("•", "-", "–", "▪", "◦", "o ", "*"))
                and not any(is_math(s) for s in line["spans"] if _span_text(s).strip())
            ):
                found.append((size, line["bbox"][1], line["bbox"][3], text))
    # Tiêu đề dài xuống dòng là nhiều dòng liền nhau cùng cỡ: nối lại thành một.
    found.sort(key=lambda f: (-round(f[0]), f[1]))
    merged: list[tuple[float, float, str]] = []
    for size, top, bottom, text in found:
        last = merged[-1] if merged else None
        if last and round(last[0]) == round(size) and 0 <= top - last[1] < 0.6 * size:
            merged[-1] = (size, bottom, f"{last[2]} {text}")
        else:
            merged.append((size, bottom, text))
    seen, out = set(), []
    for _, _, text in merged:
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out


# Dấu hiệu một công thức dựng lại chưa đúng: dấu ngoặc lệch số, cặp ngoặc rỗng (ngoặc lớn mất phần
# bên trong), toán tử treo ngay sát ngoặc hay chỉ số không có gì theo sau.
_EMPTY_GROUP = re.compile(r"[(\[]\s*[)\]]")
_DANGLING = re.compile(r"\(\s*[+=×·/](?!\s*∞)|[+−=×/]\s*\)|[\^_](?:\s|$)")
# "1)", "a)" mở đầu một mục liệt kê: dấu ngoặc đóng không có ngoặc mở là đúng chứ không phải lỗi.
_ENUMERATOR = re.compile(r"(^|;\s*)(\d{1,2}|[a-zA-Z])\)")
# Chữ Việt có dấu: một dòng có chúng là câu chữ, không phải mảnh công thức.
_VIETNAMESE = re.compile(r"[\u00C0-\u024F\u1E00-\u1EFF]")


def _balanced(text: str) -> bool:
    depth = 0
    for c in text:
        depth += (c in "([") - (c in ")]")
        if depth < 0:
            return False
    return depth == 0


def suspect_formula_lines(lines: list[str]) -> list[str]:
    """Các dòng công thức trông như dựng lại sai. Một mảnh ngắn rơi khỏi công thức của nó ("n+1",
    "x→0") có thể chỉ là phần cuối của một dòng dài xuống dòng, nên cần ít nhất hai mảnh như vậy
    mới tính; một dòng ngoặc lệch hay toán tử treo thì tự nó đã đủ."""
    broken, fragments = [], []
    for line in lines:
        text = _ENUMERATOR.sub(r"\1\2", line.strip())
        if not text:
            continue
        if _EMPTY_GROUP.search(text) or _DANGLING.search(text) or not _balanced(text):
            broken.append(line)
        elif (len(text) <= 10 and not re.search(r"[^\W\d_]{3,}", text) and not _VIETNAMESE.search(text)
              and not text.startswith(("=", "•")) and (len(text.replace(" ", "")) <= 3 or re.search(r"[+−→∞√∫]", text))
              and not re.fullmatch(r"[\d\s/.]+", text)):
            fragments.append(line)
    return broken + fragments if broken or len(fragments) >= 2 else []
