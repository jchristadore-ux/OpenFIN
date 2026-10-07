"""A minimal XLSX writer, standard library only.

The same reasoning as pdfwrite.py applies here: this project has no runtime
dependencies, GitHub Actions runs stdlib Python against JSON files in the
repository, and pulling in openpyxl to emit one workbook would be the largest
dependency in the codebase. An .xlsx file is a zip of XML parts, so it is
written directly.

Only what a financial workbook actually needs is here: inline strings (so no
shared-string table has to be kept consistent), numbers, formulas, a deduped
style registry covering fonts, fills, borders and number formats, column
widths, frozen panes, merged cells and autofilter.

Rows and columns are 1-based everywhere, because that is how a spreadsheet
refers to itself and translating at every call site is how off-by-one bugs get
in.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from xml.sax.saxutils import escape

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_CORE_REL = (NS_PKG_REL + "/metadata/core-properties")
CT = "application/vnd.openxmlformats-officedocument.spreadsheetml"

# Excel rejects a file with a control character in a string faster than it
# explains why, so anything outside the legal XML 1.0 range is dropped.
_LEGAL = {0x09, 0x0A, 0x0D}


def clean(s: str) -> str:
    return "".join(
        ch for ch in str(s)
        if ord(ch) in _LEGAL or 0x20 <= ord(ch) <= 0xD7FF
        or 0xE000 <= ord(ch) <= 0xFFFD
    )


def col_letter(col: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA."""
    if col < 1:
        raise ValueError(f"column {col} is not 1-based")
    out = ""
    while col:
        col, rem = divmod(col - 1, 26)
        out = chr(65 + rem) + out
    return out


def ref(row: int, col: int) -> str:
    return f"{col_letter(col)}{row}"


def span(r1: int, c1: int, r2: int, c2: int) -> str:
    return f"{ref(r1, c1)}:{ref(r2, c2)}"


@dataclass(frozen=True)
class Formula:
    """A live formula. `value` is the cached result Excel shows before it
    recalculates; the workbook also asks for a full recalculation on load, so a
    missing or stale cache is corrected the moment the file is opened."""

    text: str
    value: float | Decimal | None = None


# --------------------------------------------------------------------------
# styles
# --------------------------------------------------------------------------

class Styles:
    """A deduping registry. `register()` returns the cellXfs index for a
    combination of font, fill, border, number format and alignment, creating
    each underlying part only once."""

    def __init__(self) -> None:
        self._numfmts: dict[str, int] = {}
        self._fonts: dict[tuple, int] = {}
        self._fills: dict[tuple, int] = {}
        self._borders: dict[tuple, int] = {}
        self._xfs: dict[tuple, int] = {}
        self.numfmts: list[tuple[int, str]] = []
        self.fonts: list[tuple] = []
        self.fills: list[tuple] = []
        self.borders: list[tuple] = []
        self.xfs: list[tuple] = []

        # Indices 0 and 1 of <fills> are reserved by the format: none and
        # gray125. Writing anything else there silently shifts every fill.
        self._fills[("none", None)] = 0
        self.fills.append(("none", None))
        self._fills[("gray125", None)] = 1
        self.fills.append(("gray125", None))

        self.default = self.register()

    def _numfmt(self, code: str | None) -> int:
        if not code:
            return 0
        if code not in self._numfmts:
            self._numfmts[code] = 164 + len(self._numfmts)
            self.numfmts.append((self._numfmts[code], code))
        return self._numfmts[code]

    def _font(self, bold: bool, italic: bool, size: float, color: str | None,
              name: str) -> int:
        key = (bold, italic, float(size), color, name)
        if key not in self._fonts:
            self._fonts[key] = len(self.fonts)
            self.fonts.append(key)
        return self._fonts[key]

    def _fill(self, color: str | None) -> int:
        key = ("solid", color) if color else ("none", None)
        if key not in self._fills:
            self._fills[key] = len(self.fills)
            self.fills.append(key)
        return self._fills[key]

    def _border(self, top: str | None, bottom: str | None) -> int:
        key = (top, bottom)
        if key not in self._borders:
            self._borders[key] = len(self.borders)
            self.borders.append(key)
        return self._borders[key]

    def register(self, *, bold: bool = False, italic: bool = False,
                 size: float = 11, color: str | None = None,
                 name: str = "Calibri", fill: str | None = None,
                 fmt: str | None = None, align: str | None = None,
                 valign: str | None = None, wrap: bool = False,
                 indent: int = 0, top: str | None = None,
                 bottom: str | None = None) -> int:
        key = (self._font(bold, italic, size, color, name), self._fill(fill),
               self._border(top, bottom), self._numfmt(fmt), align, valign,
               wrap, indent)
        if key not in self._xfs:
            self._xfs[key] = len(self.xfs)
            self.xfs.append(key)
        return self._xfs[key]

    def xml(self) -> str:
        out = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<styleSheet xmlns="{NS_MAIN}">']

        out.append(f'<numFmts count="{len(self.numfmts)}">')
        for nid, code in self.numfmts:
            out.append(f'<numFmt numFmtId="{nid}" formatCode="{escape(code)}"/>')
        out.append("</numFmts>")

        out.append(f'<fonts count="{len(self.fonts)}">')
        for bold, italic, size, color, name in self.fonts:
            bits = [f'<sz val="{size:g}"/>', f'<name val="{escape(name)}"/>',
                    '<family val="2"/>']
            if bold:
                bits.insert(0, "<b/>")
            if italic:
                bits.insert(0, "<i/>")
            if color:
                bits.append(f'<color rgb="FF{color}"/>')
            out.append("<font>" + "".join(bits) + "</font>")
        out.append("</fonts>")

        out.append(f'<fills count="{len(self.fills)}">')
        for kind, color in self.fills:
            if kind == "solid" and color:
                out.append(f'<fill><patternFill patternType="solid">'
                           f'<fgColor rgb="FF{color}"/><bgColor indexed="64"/>'
                           f'</patternFill></fill>')
            else:
                out.append(f'<fill><patternFill patternType="{kind}"/></fill>')
        out.append("</fills>")

        out.append(f'<borders count="{len(self.borders)}">')
        for top, bottom in self.borders:
            t = (f'<top style="thin"><color rgb="FF{top}"/></top>'
                 if top else "<top/>")
            b = (f'<bottom style="{"medium" if bottom == "MEDIUM" else "thin"}">'
                 f'<color rgb="FF{"000000" if bottom == "MEDIUM" else bottom}"/>'
                 f'</bottom>' if bottom else "<bottom/>")
            out.append(f"<border><left/><right/>{t}{b}<diagonal/></border>")
        out.append("</borders>")

        out.append('<cellStyleXfs count="1">'
                   '<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
                   '</cellStyleXfs>')

        out.append(f'<cellXfs count="{len(self.xfs)}">')
        for fid, fillid, bid, nid, align, valign, wrap, indent in self.xfs:
            attrs = (f'numFmtId="{nid}" fontId="{fid}" fillId="{fillid}" '
                     f'borderId="{bid}" xfId="0"')
            if nid:
                attrs += ' applyNumberFormat="1"'
            if fillid > 1:
                attrs += ' applyFill="1"'
            if bid:
                attrs += ' applyBorder="1"'
            if align or valign or wrap or indent:
                a = []
                if align:
                    a.append(f'horizontal="{align}"')
                if valign:
                    a.append(f'vertical="{valign}"')
                if wrap:
                    a.append('wrapText="1"')
                if indent:
                    a.append(f'indent="{indent}"')
                out.append(f'<xf {attrs} applyAlignment="1">'
                           f'<alignment {" ".join(a)}/></xf>')
            else:
                out.append(f"<xf {attrs}/>")
        out.append("</cellXfs>")

        out.append('<cellStyles count="1">'
                   '<cellStyle name="Normal" xfId="0" builtinId="0"/>'
                   '</cellStyles></styleSheet>')
        return "".join(out)


# --------------------------------------------------------------------------
# sheets
# --------------------------------------------------------------------------

@dataclass
class Sheet:
    name: str
    cells: dict[int, dict[int, tuple]] = field(default_factory=dict)
    widths: dict[int, float] = field(default_factory=dict)
    heights: dict[int, float] = field(default_factory=dict)
    merges: list[str] = field(default_factory=list)
    _freeze: tuple[int, int] | None = None
    _filter: str | None = None
    gridlines: bool = False
    cursor: int = 0                     # last row written by append()

    # ---- writing ----------------------------------------------------------

    def set(self, row: int, col: int, value=None, style: int = 0) -> None:
        self.cells.setdefault(row, {})[col] = (value, style)
        self.cursor = max(self.cursor, row)

    def append(self, values: list, styles: list[int] | int = 0,
               *, row: int | None = None, first_col: int = 1) -> int:
        """Write a row of cells and return the row number used."""
        r = self.cursor + 1 if row is None else row
        for i, v in enumerate(values):
            s = styles[i] if isinstance(styles, list) else styles
            self.set(r, first_col + i, v, s)
        self.cursor = max(self.cursor, r)
        return r

    def blank(self, n: int = 1) -> None:
        self.cursor += n

    def width(self, col: int, w: float) -> None:
        self.widths[col] = w

    def height(self, row: int, h: float) -> None:
        self.heights[row] = h

    def freeze(self, rows: int = 0, cols: int = 0) -> None:
        self._freeze = (rows, cols)

    def autofilter(self, r1: int, c1: int, r2: int, c2: int) -> None:
        self._filter = span(r1, c1, r2, c2)

    def merge(self, r1: int, c1: int, r2: int, c2: int) -> None:
        self.merges.append(span(r1, c1, r2, c2))

    # ---- serialisation ----------------------------------------------------

    def _cell_xml(self, r: int, c: int, value, style: int) -> str:
        at = f'r="{ref(r, c)}"' + (f' s="{style}"' if style else "")
        if value is None or value == "":
            return f"<c {at}/>"
        if isinstance(value, Formula):
            cached = "" if value.value is None else f"<v>{value.value}</v>"
            return f"<c {at}><f>{escape(value.text)}</f>{cached}</c>"
        if isinstance(value, bool):
            return f'<c {at} t="b"><v>{int(value)}</v></c>'
        if isinstance(value, Decimal):
            return f"<c {at}><v>{value}</v></c>"
        if isinstance(value, (int, float)):
            return f"<c {at}><v>{value!r}</v></c>"
        return (f'<c {at} t="inlineStr"><is><t xml:space="preserve">'
                f"{escape(clean(value))}</t></is></c>")

    def xml(self) -> str:
        rows = sorted(self.cells)
        max_col = max((max(cs) for cs in self.cells.values()), default=1)
        dim = span(1, 1, rows[-1] if rows else 1, max_col)

        grid = "" if self.gridlines else ' showGridLines="0"'
        out = [f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
               f'<worksheet xmlns="{NS_MAIN}" xmlns:r="{NS_REL}">'
               f'<dimension ref="{dim}"/>'
               f'<sheetViews><sheetView workbookViewId="0"{grid}>']
        if self._freeze and (self._freeze[0] or self._freeze[1]):
            fr, fc = self._freeze
            parts = []
            if fc:
                parts.append(f'xSplit="{fc}"')
            if fr:
                parts.append(f'ySplit="{fr}"')
            pane = "bottomRight" if fr and fc else ("bottomLeft" if fr else "topRight")
            out.append(f'<pane {" ".join(parts)} '
                       f'topLeftCell="{ref(fr + 1, fc + 1)}" '
                       f'activePane="{pane}" state="frozen"/>')
        out.append("</sheetView></sheetViews>"
                   '<sheetFormatPr defaultRowHeight="15"/>')

        if self.widths:
            out.append("<cols>")
            for col in sorted(self.widths):
                out.append(f'<col min="{col}" max="{col}" '
                           f'width="{self.widths[col]:.2f}" customWidth="1"/>')
            out.append("</cols>")

        out.append("<sheetData>")
        for r in rows:
            h = self.heights.get(r)
            attr = f' ht="{h:.2f}" customHeight="1"' if h else ""
            out.append(f'<row r="{r}"{attr}>')
            for c in sorted(self.cells[r]):
                value, style = self.cells[r][c]
                out.append(self._cell_xml(r, c, value, style))
            out.append("</row>")
        out.append("</sheetData>")

        # Schema order: autoFilter precedes mergeCells. Swapping them is the
        # kind of thing Excel reports only as "unreadable content".
        if self._filter:
            out.append(f'<autoFilter ref="{self._filter}"/>')
        if self.merges:
            out.append(f'<mergeCells count="{len(self.merges)}">')
            for m in self.merges:
                out.append(f'<mergeCell ref="{m}"/>')
            out.append("</mergeCells>")

        out.append('<pageMargins left="0.5" right="0.5" top="0.6" bottom="0.6"'
                   ' header="0.3" footer="0.3"/>')
        out.append("</worksheet>")
        return "".join(out)


class Workbook:
    """A workbook. Add sheets, register styles, then `save`."""

    def __init__(self) -> None:
        self.sheets: list[Sheet] = []
        self.styles = Styles()

    def sheet(self, name: str) -> Sheet:
        # Excel's own limits: 31 characters, and none of []:*?/\
        safe = clean(name)[:31]
        for ch in "[]:*?/\\":
            safe = safe.replace(ch, "-")
        s = Sheet(name=safe)
        self.sheets.append(s)
        return s

    def style(self, **kw) -> int:
        return self.styles.register(**kw)

    # ---- package parts ----------------------------------------------------

    def _content_types(self) -> str:
        parts = [
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            f'content-types">'
            f'<Default Extension="rels" ContentType="application/'
            f'vnd.openxmlformats-package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{CT}.sheet.main+xml"/>'
            f'<Override PartName="/xl/styles.xml" ContentType="{CT}.styles+xml"/>'
        ]
        for i in range(1, len(self.sheets) + 1):
            parts.append(f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
                         f'ContentType="{CT}.worksheet+xml"/>')
        parts.append(
            '<Override PartName="/docProps/core.xml" ContentType="application/'
            'vnd.openxmlformats-package.core-properties+xml"/>'
            '<Override PartName="/docProps/app.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.extended-properties+xml"/>'
            "</Types>")
        return "".join(parts)

    def _workbook(self) -> str:
        sheets = "".join(
            f'<sheet name="{escape(s.name)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, s in enumerate(self.sheets, start=1)
        )
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_REL}">'
                f'<workbookPr/><sheets>{sheets}</sheets>'
                f'<calcPr calcId="124519" fullCalcOnLoad="1"/></workbook>')

    def _workbook_rels(self) -> str:
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="{NS_REL}/worksheet" '
            f'Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(self.sheets) + 1)
        )
        n = len(self.sheets) + 1
        rels += (f'<Relationship Id="rId{n}" Type="{NS_REL}/styles" '
                 f'Target="styles.xml"/>')
        return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<Relationships xmlns="{NS_PKG_REL}">{rels}</Relationships>')

    def save(self, path, title: str = "Workbook",
             creator: str = "OpenFIN") -> None:
        stamp = datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")
        root_rels = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Relationships xmlns="{NS_PKG_REL}">'
            f'<Relationship Id="rId1" Type="{NS_REL}/officeDocument" '
            f'Target="xl/workbook.xml"/>'
            f'<Relationship Id="rId2" Type="{NS_CORE_REL}" '
            f'Target="docProps/core.xml"/>'
            f'<Relationship Id="rId3" Type="{NS_REL}/extended-properties" '
            f'Target="docProps/app.xml"/></Relationships>'
        )
        core = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<cp:coreProperties '
            f'xmlns:cp="http://schemas.openxmlformats.org/package/2006/'
            f'metadata/core-properties" '
            f'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            f'xmlns:dcterms="http://purl.org/dc/terms/" '
            f'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{escape(clean(title))}</dc:title>"
            f"<dc:creator>{escape(creator)}</dc:creator>"
            f"<cp:lastModifiedBy>{escape(creator)}</cp:lastModifiedBy>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{stamp}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{stamp}</dcterms:modified>'
            f"</cp:coreProperties>"
        )
        app = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<Properties xmlns="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/extended-properties" '
            f'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/'
            f'docPropsVTypes"><Application>OpenFIN</Application>'
            f"<Company>{escape(creator)}</Company></Properties>"
        )

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml", self._content_types())
            z.writestr("_rels/.rels", root_rels)
            z.writestr("docProps/core.xml", core)
            z.writestr("docProps/app.xml", app)
            z.writestr("xl/workbook.xml", self._workbook())
            z.writestr("xl/_rels/workbook.xml.rels", self._workbook_rels())
            z.writestr("xl/styles.xml", self.styles.xml())
            for i, s in enumerate(self.sheets, start=1):
                z.writestr(f"xl/worksheets/sheet{i}.xml", s.xml())
