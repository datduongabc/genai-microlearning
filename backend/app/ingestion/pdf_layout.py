"""Lecture-slide text, font decoding, and the measured DSA/OS layout repairs.

The corpus still has fragmented code, superscripts and two fraction slides.
Keep those repairs; avoid the unused radical, bracket and limit-operator machinery.
"""

import bisect
import re
import statistics
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

# Phông ký hiệu trang trí: gần như luôn là dấu đầu dòng, còn mã gốc của nó không mang nghĩa
# gì khi đọc thành chữ.
_DINGBAT_FONTS = ("wingdings", "webdings", "monotypesorts", "zapfdingbats", "dingbats")
# Phông của ký hiệu toán: khối chứa chúng được phép nối với khối bên cạnh cùng hàng, vì đó
# là các phần của cùng một công thức.
_MATH_FONTS = ("symbol", "cmmi", "cmsy", "cmex", "cmr", "mt extra", "mt-extra", "cambria math", "math")


# rawdict mặc định kèm cả dữ liệu ảnh của trang, chậm gấp trăm lần trên slide nhiều hình mà ở
# đây chỉ cần chữ.
_TEXT_FLAGS = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES


def _is_private(c: str) -> bool:
    return 0xE000 <= ord(c) <= 0xF8FF


def map_font_text(text: str, font: str) -> str:
    """Decode Symbol and dingbat glyphs used by the retained lecture slides."""
    name = font.lower()
    if any(f in name for f in _DINGBAT_FONTS):
        return "".join("•" if _is_private(c) or not c.isspace() else c for c in text)
    base = name.split(",")[0].split("-")[0].removesuffix("mt")
    is_symbol = base == "symbol"
    out = []
    for c in text:
        if is_symbol and ord(c) < 0x100:
            c = chr(0xF000 + ord(c))
        if is_symbol and 0xF020 <= ord(c) <= 0xF0FF:
            out.append(_SYMBOL.get(ord(c) - 0xF000, chr(ord(c) - 0xF000)))
        else:
            out.append(c)
    return "".join(out)


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
    # A fraction crosses the row it belongs to rather than sharing its baseline.
    tall: bool = False
    # Tệp có ghi dấu cách ngay trước mảnh này: khoảng hở hình học giữa chữ nghiêng hay chữ
    # đậm không đủ tin để đoán ranh giới từ.
    space_before: bool = False
    # "^" cho chỉ số trên, "_" cho chỉ số dưới, rỗng cho chữ ở dòng chính.
    script: str = ""

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


def _strokes(page: pymupdf.Page) -> list[_Rule]:
    """Horizontal bars only: fractions occur on two slides in the retained corpus."""
    rules = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                p, q = item[1], item[2]
                if abs(p.y - q.y) < 0.8 and abs(q.x - p.x) > 3:
                    rules.append(_Rule(min(p.x, q.x), max(p.x, q.x), (p.y + q.y) / 2))
            elif item[0] == "re":
                r = item[1]
                if r.height < 1.5 and r.width > 3:
                    rules.append(_Rule(r.x0, r.x1, (r.y0 + r.y1) / 2))
    return rules


def _block_is_mathlike(block: dict) -> bool:
    """Khối có chữ của phông ký hiệu toán. Chỉ dựa vào phông chứ không vào độ dài dòng: số dòng
    của đoạn mã, dấu ngoặc nhọn hay số trang cũng là dòng rất ngắn, và coi chúng là công thức
    thì chú thích đè lên mã nguồn bị trộn chữ vào nhau."""
    spans = [s for line in block["lines"] for s in line["spans"]]
    if any(any(f in s["font"].lower() for f in _MATH_FONTS) for s in spans):
        return True
    # MathType soạn bằng Arial hay Times thì không có phông toán nào; dấu hiệu còn lại là các
    # biến in nghiêng đứng riêng lẻ (x, y, dx). Mã nguồn dùng phông đơn cách không nghiêng, chú
    # thích là chữ thường, nên không bị bắt nhầm.
    italic_fragments = sum(
        1 for s in spans
        if 0 < len(_span_text(s).strip()) <= 3 and (s["flags"] & pymupdf.TEXT_FONT_ITALIC or "italic" in s["font"].lower())
    )
    return italic_fragments >= 2


def _regions(blocks: list[dict]) -> list[list[int]]:
    """Gom các khối thuộc cùng một công thức: khung chồng lên nhau, hoặc cùng hàng và sát nhau
    khi ít nhất một khối mang ký hiệu toán. Hai cột văn bản thường không bao giờ bị gom."""
    parent = list(range(len(blocks)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    boxes = [b["bbox"] for b in blocks]
    mathlike = [_block_is_mathlike(b) for b in blocks]
    sizes = [max((s["size"] for line in b["lines"] for s in line["spans"]), default=10) for b in blocks]
    for i, a in enumerate(boxes):
        for j in range(i + 1, len(boxes)):
            b = boxes[j]
            touching = _overlap(a[0] - 3, a[2] + 3, b[0], b[2]) > 0 and _overlap(a[1] - 3, a[3] + 3, b[1], b[3]) > 0
            same_row = _overlap(a[1], a[3], b[1], b[3]) >= 0.5 * min(a[3] - a[1], b[3] - b[1])
            gap = max(b[0] - a[2], a[0] - b[2])
            near = same_row and gap < max(sizes[i], sizes[j]) and (mathlike[i] or mathlike[j])
            if (touching or near) and (mathlike[i] or mathlike[j]):
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
    for piece in sorted(pieces, key=lambda p: p.x0):
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


def _small_pieces(pieces: list[_Piece]) -> set[int]:
    """Find indices by comparing font size with nearby text."""
    reference = [p for p in pieces if not p.tall]
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
        if p.size < 0.8 * biggest:
            small.add(id(p))
    return small


def _rows(pieces: list[_Piece]) -> list[list[_Piece]]:
    """Xếp mảnh thành hàng. Chữ cỡ chính quyết định hàng; chữ nhỏ hơn nằm sát bên một hàng là
    chỉ số của hàng đó; phân số thuộc hàng mà chúng cắt qua giữa."""
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
            neighbour = min(r["pieces"], key=lambda q: min(abs(p.x0 - q.x1), abs(q.x0 - p.x1)))
            touching = _overlap(p.y0, p.y1, r["y0"], r["y1"]) > 0
            adjacent = touching and min(abs(p.x0 - neighbour.x1), abs(neighbour.x0 - p.x1)) < 0.8 * size
            if not adjacent:
                continue
            score = (0 if r["main"] else 1, abs((r["y0"] + r["y1"]) / 2 - p.cy))
            if best is None or score < best[0]:
                best = (score, r)
        if best is None:
            new_row(p)
            rows[-1]["main"] = False
            continue
        _, row = best
        shift = row["baseline"] - p.baseline
        if shift > 0.2 * row["size"]:
            p.script = "^"
        elif shift < -0.1 * row["size"]:
            p.script = "_"
        row["pieces"].append(p)
    rows.sort(key=lambda r: r["y0"])
    return [r["pieces"] for r in rows]


def _group(pieces: list[_Piece]) -> str:
    text = " ".join(_join(row).strip() for row in _rows(pieces))
    # Một ký hiệu đơn hay một lũy thừa đơn đứng được một mình; mọi thứ khác cần ngoặc để
    # "a+b/c+d" không bị đọc nhầm thành a + b/c + d.
    return text if re.fullmatch(r"[\w.]+(\^[\w.]+)?", text) else f"({text})"


def _is_fraction(rule: _Rule, above: list[_Piece], below: list[_Piece], size: float) -> bool:
    """Tử và mẫu canh giữa gạch phân số, và gạch không dài hơn vế rộng hơn bao nhiêu. Đường kẻ
    bảng, gạch chân hay nét chỉ của hộp chú thích thì dài hơn hẳn chữ nằm cạnh nó, hoặc lệch hẳn
    sang một bên."""
    # A table header or prose row is not a numerator, even when centred over a rule.
    if any(re.search(r"[^\W\d_]{4,}", p.text) for p in above + below):
        return False
    width = rule.x1 - rule.x0
    centre = (rule.x0 + rule.x1) / 2
    widest = 0.0
    for side in (above, below):
        left, right = min(p.x0 for p in side), max(p.x1 for p in side)
        if abs((left + right) / 2 - centre) > 0.25 * width + 0.3 * size:
            return False
        widest = max(widest, right - left)
    return width <= widest + 2 * size


def _structures(pieces: list[_Piece], rules: list[_Rule]) -> list[_Piece]:
    """Phân số, gạch ngắn trước: một phân số lồng trong phân số khác được dựng
    xong rồi mới thành một mảnh của tử hay mẫu bên ngoài."""
    for rule in sorted(rules, key=lambda r: r.x1 - r.x0):
        within = [p for p in pieces if rule.x0 - 1 <= p.cx <= rule.x1 + 1]
        if not within:
            continue
        size = statistics.median(p.size for p in within)
        below = [p for p in within if rule.y < p.cy <= rule.y + 1.3 * size and p.y0 >= rule.y - 0.3 * size]
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


def _span_pieces(span: dict) -> list[_Piece]:
    """Tách một span thành từng từ theo vị trí ký tự. PyMuPDF gộp chữ cùng phông cùng cỡ vào
    một span ngay cả khi chúng nằm ở hai tầng khác nhau, như số 1 của tử số 1/a với chữ
    "arctan" ở dòng chính, nên không thể coi cả span là một mảnh."""
    size = span["size"]
    pieces: list[_Piece] = []
    current: list[dict] = []
    space = False

    def flush() -> None:
        nonlocal current, space
        if current:
            text = map_font_text("".join(c["c"] for c in current), span["font"])
            if text.strip():
                pieces.append(_Piece(
                    min(c["bbox"][0] for c in current), min(c["bbox"][1] for c in current),
                    max(c["bbox"][2] for c in current), max(c["bbox"][3] for c in current),
                    current[0]["origin"][1], size, text.strip(), space_before=space,
                ))
                space = False
        current = []

    for ch in span["chars"]:
        if ch["c"].isspace():
            flush()
            space = True
            continue
        if current and (
            abs(ch["origin"][1] - current[-1]["origin"][1]) > 0.1 * size
            or ch["bbox"][0] - current[-1]["bbox"][2] > 0.25 * size
        ):
            flush()
        current.append(ch)
    flush()
    return pieces


def _line_text(spans: list[dict]) -> str:
    """Một dòng PyMuPDF bình thường: chỉ đổi mã phông và đánh dấu chỉ số trên/dưới nằm trong dòng."""
    visible = [s for s in spans if _span_text(s).strip()]
    if not visible:
        return ""
    base = max(visible, key=lambda s: s["size"])
    out = []
    for s in spans:
        text = map_font_text(_span_text(s), s["font"])
        if s is not base and text.strip() and s["size"] < 0.8 * base["size"] and len(text.strip()) <= 4:
            shift = base["origin"][1] - s["origin"][1]
            if shift > 0.2 * base["size"]:
                text = "^" + text.strip()
            elif shift < -0.1 * base["size"]:
                text = "_" + text.strip()
        out.append(text)
    return "".join(out).rstrip()


def page_lines(page: pymupdf.Page) -> list[str]:
    """Các dòng chữ của trang theo thứ tự đọc, công thức đã được dựng lại thành một dòng."""
    blocks = [b for b in page.get_text("rawdict", flags=_TEXT_FLAGS)["blocks"] if b["type"] == 0 and b["lines"]]
    if not blocks:
        return []
    rules = _strokes(page) if any(_block_is_mathlike(b) for b in blocks) else []
    out: list[str] = []
    previous = None
    for region in _regions(blocks):
        lines = [line for i in region for line in blocks[i]["lines"]]
        boxes = [line["bbox"] for line in lines]
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        main_size = statistics.median(s["size"] for line in lines for s in line["spans"])
        # A fraction bar can extend slightly beyond its numerator and denominator.
        region_rules = [
            r for r in rules
            if _overlap(r.x0, r.x1, x0, x1) >= 0.5 * (r.x1 - r.x0)
            and y0 - 0.6 * main_size <= r.y <= y1 + 0.3 * main_size
        ]
        # Dòng cùng hàng mà không có ký hiệu toán nào là bố cục có chủ ý (chân trang Beamer ba ô,
        # bảng hai cột) chứ không phải công thức vỡ: trộn chúng lại thì chữ các ô đan vào nhau.
        math = any(_block_is_mathlike(blocks[i]) for i in region)
        has_code = any(s["flags"] & pymupdf.TEXT_FONT_MONOSPACED for line in lines for s in line["spans"])
        has_bullets = any(_line_text(line["spans"]).strip() in {"•", "▪", "❖"} for line in lines)
        if (not math or has_bullets or (has_code and not region_rules)
                or (not _is_shattered(boxes) and not region_rules)):
            for line in lines:
                text = _line_text(line["spans"])
                # Chữ vẽ hai lần chồng khít (hiệu ứng bóng của tiêu đề slide) chỉ giữ một lần.
                if not text:
                    continue
                box = line["bbox"]
                base = max(line["spans"], key=lambda s: s["size"])
                baseline, size = base["origin"][1], base["size"]
                if previous:
                    old_text, old_box, old_baseline, old_size = previous
                    if (old_text == text and _overlap(old_box[1], old_box[3], box[1], box[3]) > 0
                            and _overlap(old_box[0], old_box[2], box[0], box[2])
                            > 0.8 * min(old_box[2] - old_box[0], box[2] - box[0])):
                        continue
                    gap = box[0] - old_box[2]
                    if (abs(baseline - old_baseline) < 0.15 * max(size, old_size)
                            and 0 <= gap < max(size, old_size)):
                        out[-1] += (" " if gap > 0.12 * max(size, old_size) else "") + text
                        box = (old_box[0], min(old_box[1], box[1]), box[2], max(old_box[3], box[3]))
                        previous = (out[-1], box, baseline, size)
                        continue
                out.append(text)
                previous = (text, box, baseline, size)
            continue
        previous = None
        pieces: list[_Piece] = []
        for line in lines:
            for s in line["spans"]:
                for piece in _span_pieces(s):
                    # Chữ vẽ hai lần chồng khít chỉ giữ một lần.
                    if any(q.text == piece.text and _overlap(q.x0, q.x1, piece.x0, piece.x1) > 0.8 * (piece.x1 - piece.x0)
                           and _overlap(q.y0, q.y1, piece.y0, piece.y1) > 0.8 * piece.height for q in pieces):
                        continue
                    pieces.append(piece)
        pieces = _structures(pieces, region_rules)
        for row in _rows(pieces):
            text = _join(row).strip()
            if text:
                out.append(text)
    return out


def title_candidates(page: pymupdf.Page) -> list[str]:
    """Các dòng to hơn hẳn chữ thân bài, dòng to nhất trước. Trên slide, tiêu đề là dòng cỡ chữ
    lớn nhất chứ không phải dòng đầu tiên: PyMuPDF hay trả chân trang hoặc số trang ra trước."""
    blocks = [b for b in page.get_text("rawdict", flags=_TEXT_FLAGS)["blocks"] if b["type"] == 0 and b["lines"]]
    if not blocks:
        return []

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
            text = _line_text(line["spans"])
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
