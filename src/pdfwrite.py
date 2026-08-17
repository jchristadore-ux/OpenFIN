"""A minimal PDF writer, standard library only.

This project has no runtime and no dependencies: GitHub Actions runs stdlib
Python against JSON files in the repository. Adding reportlab or a headless
browser to produce one report would be the largest dependency in the codebase,
and it would have to keep working inside Actions forever. So the report is
written directly.

Only what a financial report actually needs is here: the two Helvetica faces
from the standard 14 (so nothing is embedded), rectangles, lines, and text.
The widths table is the real content — text measurement is what makes the
difference between a table that fits and one with columns sitting on top of
each other, and it is the one thing that cannot be eyeballed.

Coordinates are given from the TOP-LEFT of the page in points, because that is
how a page is laid out when you are reading it. PDF's own origin is
bottom-left, and the flip happens once, at serialisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Page sizes, in points (1/72").
A4_PORTRAIT = (595.28, 841.89)
A4_LANDSCAPE = (841.89, 595.28)
LETTER_PORTRAIT = (612.0, 792.0)
LETTER_LANDSCAPE = (792.0, 612.0)

HELV = "Helvetica"
HELV_BOLD = "Helvetica-Bold"

# Adobe's standard AFM advance widths, in 1/1000 em, for the printable ASCII
# range. Everything this report emits is inside it.
_HELV_W = {
    " ": 278, "!": 278, '"': 355, "#": 556, "$": 556, "%": 889, "&": 667,
    "'": 191, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
    ".": 278, "/": 278, "0": 556, "1": 556, "2": 556, "3": 556, "4": 556,
    "5": 556, "6": 556, "7": 556, "8": 556, "9": 556, ":": 278, ";": 278,
    "<": 584, "=": 584, ">": 584, "?": 556, "@": 1015, "A": 667, "B": 667,
    "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 722, "I": 278,
    "J": 500, "K": 667, "L": 556, "M": 833, "N": 722, "O": 778, "P": 667,
    "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "W": 944,
    "X": 667, "Y": 667, "Z": 611, "[": 278, "\\": 278, "]": 278, "^": 469,
    "_": 556, "`": 333, "a": 556, "b": 556, "c": 500, "d": 556, "e": 556,
    "f": 278, "g": 556, "h": 556, "i": 222, "j": 222, "k": 500, "l": 222,
    "m": 833, "n": 556, "o": 556, "p": 556, "q": 556, "r": 333, "s": 500,
    "t": 278, "u": 556, "v": 500, "w": 722, "x": 500, "y": 500, "z": 500,
    "{": 334, "|": 260, "}": 334, "~": 584,
}
_HELV_BOLD_W = {
    " ": 278, "!": 333, '"': 474, "#": 556, "$": 556, "%": 889, "&": 722,
    "'": 238, "(": 333, ")": 333, "*": 389, "+": 584, ",": 278, "-": 333,
    ".": 278, "/": 278, "0": 556, "1": 556, "2": 556, "3": 556, "4": 556,
    "5": 556, "6": 556, "7": 556, "8": 556, "9": 556, ":": 333, ";": 333,
    "<": 584, "=": 584, ">": 584, "?": 611, "@": 975, "A": 722, "B": 722,
    "C": 722, "D": 722, "E": 667, "F": 611, "G": 778, "H": 722, "I": 278,
    "J": 556, "K": 722, "L": 611, "M": 833, "N": 722, "O": 778, "P": 667,
    "Q": 778, "R": 722, "S": 667, "T": 611, "U": 722, "V": 667, "W": 944,
    "X": 667, "Y": 667, "Z": 611, "[": 333, "\\": 278, "]": 333, "^": 584,
    "_": 556, "`": 333, "a": 556, "b": 611, "c": 556, "d": 611, "e": 556,
    "f": 333, "g": 611, "h": 611, "i": 278, "j": 278, "k": 556, "l": 278,
    "m": 889, "n": 611, "o": 611, "p": 611, "q": 611, "r": 389, "s": 556,
    "t": 333, "u": 611, "v": 556, "w": 778, "x": 556, "y": 556, "z": 500,
    "{": 389, "|": 280, "}": 389, "~": 584,
}
_WIDTHS = {HELV: _HELV_W, HELV_BOLD: _HELV_BOLD_W}

# Typographic characters this report uses, folded to WinAnsi equivalents. The
# alternative is a garbled glyph in a financial document, which is worse than a
# plain hyphen.
_FOLD = {
    "—": "-", "–": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "•": "-", "…": "...",
    " ": " ", "→": "->", "×": "x", "≥": ">=",
    "≤": "<=", "£": "GBP ", "€": "EUR ",
}


def sanitise(s: str) -> str:
    """Fold to characters the width table and WinAnsiEncoding both know."""
    out = []
    for ch in str(s):
        ch = _FOLD.get(ch, ch)
        for c in ch:
            out.append(c if 32 <= ord(c) < 127 else "?")
    return "".join(out)


def text_width(s: str, font: str = HELV, size: float = 10.0) -> float:
    """Width of `s` in points. The basis of every layout decision here."""
    table = _WIDTHS.get(font, _HELV_W)
    return sum(table.get(c, 556) for c in sanitise(s)) * size / 1000.0


def fit(s: str, width: float, font: str = HELV, size: float = 10.0) -> str:
    """Shorten with an ellipsis until it fits. Never returns something wider."""
    s = sanitise(s)
    if text_width(s, font, size) <= width:
        return s
    ell = "..."
    ew = text_width(ell, font, size)
    if ew > width:
        return ""
    out = ""
    for ch in s:
        if text_width(out + ch, font, size) + ew > width:
            break
        out += ch
    return out + ell


def wrap(s: str, width: float, font: str = HELV, size: float = 10.0) -> list[str]:
    """Greedy word wrap. A word longer than the column is broken rather than
    allowed to run past it."""
    words = sanitise(s).split()
    if not words:
        return [""]
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if text_width(trial, font, size) <= width:
            cur = trial
            continue
        if cur:
            lines.append(cur)
        while text_width(w, font, size) > width:
            piece = ""
            for ch in w:
                if text_width(piece + ch, font, size) > width:
                    break
                piece += ch
            if not piece:
                break
            lines.append(piece)
            w = w[len(piece):]
        cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _esc(s: str) -> str:
    return (sanitise(s).replace("\\", r"\\")
            .replace("(", r"\(").replace(")", r"\)"))


@dataclass
class Page:
    """One page. `ops` accumulates raw content-stream operators."""

    width: float
    height: float
    ops: list[str] = field(default_factory=list)

    # ---- drawing, all measured from the top-left ---------------------------

    def text(self, x: float, y: float, s: str, *, size: float = 10.0,
             font: str = HELV, color: tuple[float, float, float] = (0, 0, 0)) -> None:
        """Draw `s` with its BASELINE at `y` points below the top edge."""
        if s is None or s == "":
            return
        r, g, b = color
        fk = "F2" if font == HELV_BOLD else "F1"
        self.ops.append(
            f"BT {r:.3f} {g:.3f} {b:.3f} rg /{fk} {size:.2f} Tf "
            f"1 0 0 1 {x:.2f} {self.height - y:.2f} Tm ({_esc(s)}) Tj ET"
        )

    def text_right(self, x: float, y: float, s: str, *, size: float = 10.0,
                   font: str = HELV,
                   color: tuple[float, float, float] = (0, 0, 0)) -> None:
        """Right-align at `x`. Money columns are unreadable any other way."""
        self.text(x - text_width(s, font, size), y, s,
                  size=size, font=font, color=color)

    def rect(self, x: float, y: float, w: float, h: float,
             fill: tuple[float, float, float]) -> None:
        r, g, b = fill
        self.ops.append(
            f"{r:.3f} {g:.3f} {b:.3f} rg "
            f"{x:.2f} {self.height - y - h:.2f} {w:.2f} {h:.2f} re f"
        )

    def line(self, x1: float, y1: float, x2: float, y2: float, *,
             width: float = 0.5,
             color: tuple[float, float, float] = (0.8, 0.8, 0.8)) -> None:
        r, g, b = color
        self.ops.append(
            f"{r:.3f} {g:.3f} {b:.3f} RG {width:.2f} w "
            f"{x1:.2f} {self.height - y1:.2f} m {x2:.2f} {self.height - y2:.2f} l S"
        )

    def stream(self) -> bytes:
        return "\n".join(self.ops).encode("latin-1", "replace")


class PDF:
    """A document. Add pages, then `save`."""

    def __init__(self) -> None:
        self.pages: list[Page] = []

    def add_page(self, size: tuple[float, float] = A4_PORTRAIT) -> Page:
        p = Page(width=size[0], height=size[1])
        self.pages.append(p)
        return p

    # ---- serialisation -----------------------------------------------------

    def to_bytes(self, title: str = "Report") -> bytes:
        objs: list[bytes] = []

        def add(body: bytes) -> int:
            objs.append(body)
            return len(objs)          # 1-based object number

        font_n = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>"
        )
        font_b = add(
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
            b"/Encoding /WinAnsiEncoding >>"
        )
        # Reserve the Pages object number so each Page can name its parent.
        pages_n = add(b"")

        kids: list[int] = []
        for pg in self.pages:
            data = pg.stream()
            content_n = add(
                b"<< /Length " + str(len(data)).encode() + b" >>\nstream\n"
                + data + b"\nendstream"
            )
            page_n = add(
                f"<< /Type /Page /Parent {pages_n} 0 R /MediaBox "
                f"[0 0 {pg.width:.2f} {pg.height:.2f}] /Resources << /Font << "
                f"/F1 {font_n} 0 R /F2 {font_b} 0 R >> >> /Contents "
                f"{content_n} 0 R >>".encode()
            )
            kids.append(page_n)

        objs[pages_n - 1] = (
            "<< /Type /Pages /Count " + str(len(kids)) + " /Kids ["
            + " ".join(f"{k} 0 R" for k in kids) + "] >>"
        ).encode()

        info_n = add(
            b"<< /Title (" + _esc(title).encode("latin-1", "replace")
            + b") /Producer (OpenFIN) >>"
        )
        catalog_n = add(f"<< /Type /Catalog /Pages {pages_n} 0 R >>".encode())

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0] * (len(objs) + 1)
        for i, body in enumerate(objs, start=1):
            offsets[i] = len(out)
            out += str(i).encode() + b" 0 obj\n" + body + b"\nendobj\n"

        xref_at = len(out)
        out += f"xref\n0 {len(objs) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for i in range(1, len(objs) + 1):
            out += f"{offsets[i]:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {len(objs) + 1} /Root {catalog_n} 0 R "
            f"/Info {info_n} 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
        ).encode()
        return bytes(out)

    def save(self, path, title: str = "Report") -> None:
        with open(path, "wb") as fh:
            fh.write(self.to_bytes(title))
