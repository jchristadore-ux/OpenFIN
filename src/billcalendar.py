"""The bill calendar: what is due, on which date, and what each month totals.

This is the plain list. One line per payment, in date order, with a total at
the foot of every month. No categories, no analysis, no commentary - those
live in the bill statement (src/billsheet.py) and the cash-flow audit
(src/report.py).

The dates and amounts come from the same two places everything else in this
project uses: occurrence expansion from bills.py, and fincal.expected_amount
for what a variable or seasonal bill is carried at. So a line here always
agrees with the same bill in every other OpenFIN output.
"""

from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import xlsxwrite as X
from bills import load_items, money, occurrences
from fincal import expected_amount
from pdfwrite import A4_PORTRAIT, HELV, HELV_BOLD
from report import BAND_HEAD, INK, MUTED, NAVY, RULE, STRIPE, Col, Layout, m

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Due:
    day: date
    name: str
    amount: Decimal
    autopay: bool


@dataclass
class Month:
    first: date
    dues: list[Due]

    @property
    def total(self) -> Decimal:
        return money(sum((d.amount for d in self.dues), money(0)))

    @property
    def label(self) -> str:
        return self.first.strftime("%B %Y")


def month_starts(first: date, n: int) -> list[date]:
    out, y, mo = [], first.year, first.month
    for _ in range(n):
        out.append(date(y, mo, 1))
        mo += 1
        if mo == 13:
            y, mo = y + 1, 1
    return out


def month_end(first_of_month: date) -> date:
    last = calendar.monthrange(first_of_month.year, first_of_month.month)[1]
    return date(first_of_month.year, first_of_month.month, last)


def build(bills_path: Path, first: date, n: int = 12) -> list[Month]:
    """Every payment in the window, grouped by month and sorted by date.

    Two bills falling on the same day stay as two lines, largest first. They
    are two payments, and a household writing them off a list needs to see
    both.
    """
    items = load_items(bills_path, "bills")
    months: list[Month] = []
    for start in month_starts(first, n):
        end = month_end(start)
        dues: list[Due] = []
        for b in items:
            for day in occurrences(b, start, end):
                dues.append(Due(day=day, name=b["name"],
                                amount=expected_amount(b, day),
                                autopay=bool(b.get("autopay"))))
        dues.sort(key=lambda d: (d.day, -d.amount, d.name))
        months.append(Month(first=start, dues=dues))
    return months


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

class Doc(Layout):
    FOOT_TEXT = "OpenFIN bill calendar - dates and amounts from bills.json"

    def save(self, path: Path) -> int:
        total = len(self.pdf.pages)
        for i, pg in enumerate(self.pdf.pages, start=1):
            pg.line(self.MARGIN, pg.height - self.FOOT + 8,
                    pg.width - self.MARGIN, pg.height - self.FOOT + 8,
                    color=RULE)
            pg.text(self.MARGIN, pg.height - self.FOOT + 21, self.FOOT_TEXT,
                    size=7, color=MUTED)
            pg.text_right(pg.width - self.MARGIN, pg.height - self.FOOT + 21,
                          f"Page {i} of {total}", size=7.5, color=MUTED)
        self.pdf.save(path, title=self.title)
        return total


def write_pdf(months: list[Month], out: Path) -> int:
    grand = money(sum((mo.total for mo in months), money(0)))
    window = f"{months[0].label} to {months[-1].label}"
    L = Doc("Bills by date", window)

    L.cover([
        ("Window", f"{window} ({len(months)} months)"),
        ("Payments listed", f"{sum(len(mo.dues) for mo in months)}"),
        ("Average month", m(money(grand / len(months)))),
        ("Total", m(grand)),
        ("Generated", datetime.now().strftime("%d %b %Y %H:%M")),
    ])

    L.h2("Monthly totals")
    rows = [[mo.label, str(len(mo.dues)), m(mo.total)] for mo in months]
    rows.append(["TOTAL", str(sum(len(mo.dues) for mo in months)), m(grand)])
    L.table(
        [Col("Month", 170), Col("Payments", 70, "r"),
         Col("Total", 100, "r", HELV_BOLD)],
        rows, size=9,
        row_fill=lambda i, r: BAND_HEAD if i == len(rows) - 1 else None,
        cell_color=lambda i, j, r: NAVY if i == len(rows) - 1 else None)

    cols = [Col("Date", 78), Col("Bill", 250), Col("Amount", 80, "r",
                                                   HELV_BOLD)]
    L._section = ""
    for mo in months:
        # Months flow one after another rather than each starting a page: a
        # month of bills is about forty lines, so a page per month would waste
        # half of every sheet and double the length of the thing to read.
        L.need(120)
        L.y += 6
        L.page.rect(L.MARGIN, L.y - 4, L.usable, 20, BAND_HEAD)
        L.page.text(L.MARGIN + 8, L.y + 10, mo.label, size=11,
                    font=HELV_BOLD, color=NAVY)
        L.page.text_right(L.right - 8, L.y + 10,
                          f"{len(mo.dues)} payments   {m(mo.total)}",
                          size=10, font=HELV_BOLD, color=NAVY)
        L.y += 24
        body = [[d.day.strftime("%a %-d"), d.name, m(d.amount)]
                for d in mo.dues]
        body.append(["", f"{mo.label} total", m(mo.total)])
        L.table(cols, body, size=9,
                row_fill=lambda i, r, n=len(body): (BAND_HEAD if i == n - 1
                                                    else None),
                cell_color=lambda i, j, r, n=len(body): (NAVY if i == n - 1
                                                         else None))

    return L.save(out)


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

def write_xlsx(months: list[Month], out: Path) -> None:
    wb = X.Workbook()
    F = X.Formula
    grand = money(sum((mo.total for mo in months), money(0)))

    title = wb.style(bold=True, size=16, color="0F2F52")
    sub = wb.style(size=10, color="6B7280")
    head = wb.style(bold=True, color="FFFFFF", fill="16365C")
    head_r = wb.style(bold=True, color="FFFFFF", fill="16365C", align="right")
    txt = wb.style()
    dt = wb.style()
    cash = wb.style(fmt="$#,##0.00")
    month_l = wb.style(bold=True, size=12, fill="DCE6F1")
    month_m = wb.style(bold=True, fmt="$#,##0.00", fill="DCE6F1")
    tot_l = wb.style(bold=True, fill="EEF2F7", top="16365C")
    tot_m = wb.style(bold=True, fmt="$#,##0.00", fill="EEF2F7", top="16365C")
    num = wb.style(align="center")

    # ---- Monthly totals ---------------------------------------------------
    s = wb.sheet("Monthly totals")
    for c, w in enumerate([22, 12, 16], start=1):
        s.width(c, w)
    s.append(["Monthly totals"], title)
    s.append([f"{months[0].label} to {months[-1].label}"], sub)
    hdr = s.append(["Month", "Payments", "Total"], [head, head_r, head_r])
    first = hdr + 1
    for mo in months:
        s.append([mo.label, len(mo.dues), mo.total], [txt, num, cash])
    last = s.cursor
    s.append(["TOTAL",
              F(f"SUM(B{first}:B{last})", sum(len(mo.dues) for mo in months)),
              F(f"SUM(C{first}:C{last})", float(grand))],
             [tot_l, wb.style(bold=True, align="center", fill="EEF2F7",
                              top="16365C"), tot_m])
    s.append([f"Average month: {m(money(grand / len(months)))}"], sub)
    s.freeze(rows=hdr)

    # ---- By date ----------------------------------------------------------
    d = wb.sheet("By date")
    for c, w in enumerate([14, 10, 40, 16], start=1):
        d.width(c, w)
    d.append(["Bills by date"], title)
    d.append([f"{months[0].label} to {months[-1].label}. Every payment, in "
              f"date order, with each month totalled."], sub)
    d.append(["Date", "Day", "Bill", "Amount"], [head, head, head, head_r])
    d.freeze(rows=d.cursor)

    for mo in months:
        d.blank()
        head_row = d.append([mo.label, "", "", mo.total],
                            [month_l, month_l, month_l, month_m])
        d.height(head_row, 20)
        block_first = d.cursor + 1
        for due in mo.dues:
            d.append([due.day.isoformat(), due.day.strftime("%a"), due.name,
                      due.amount], [dt, num, txt, cash])
        block_last = d.cursor
        d.append(["", "", f"{mo.label} total",
                  F(f"SUM(D{block_first}:D{block_last})", float(mo.total))],
                 [tot_l, tot_l, tot_l, tot_m])

    wb.save(out, title="OpenFIN bills by date")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Bills by date, month by month")
    ap.add_argument("--bills", default=str(ROOT / "bills.json"))
    ap.add_argument("--out-pdf", default=str(ROOT / "bills-by-date.pdf"))
    ap.add_argument("--out-xlsx", default=str(ROOT / "bills-by-date.xlsx"))
    ap.add_argument("--start", help="first month, YYYY-MM; defaults to this month")
    ap.add_argument("--months", type=int, default=12)
    args = ap.parse_args(argv)

    if args.start:
        y, mo = (int(x) for x in args.start.split("-")[:2])
        first = date(y, mo, 1)
    else:
        today = date.today()
        first = date(today.year, today.month, 1)

    months = build(Path(args.bills), first, args.months)
    pages = write_pdf(months, Path(args.out_pdf))
    write_xlsx(months, Path(args.out_xlsx))

    print(f"{Path(args.out_pdf).name}: {pages} pages")
    print(f"{Path(args.out_xlsx).name}: 2 sheets")
    for mo in months:
        print(f"  {mo.label:<16}{len(mo.dues):>3} payments  {m(mo.total):>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
