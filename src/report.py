"""The cash-flow audit report: a dated ledger, an optimised schedule, a PDF.

Nothing about this household is written into this file. Every figure comes from
`bills.json`, `income.json`, `state.json` and `config.json` at run time, so the
same code produces next month's report from next month's data. That is the
difference between a report and a transcript, and it is the reason this exists
as a module rather than as a one-off script.

    python src/report.py --out cashflow-audit.pdf
    python src/report.py --out x.pdf --date 2026-08-16 --balance 2414.80

Sections follow the audit brief: executive summary, the schedule as it stands,
the schedule after optimisation, the recommended date changes, a risk calendar,
the shortfalls no date change can fix, the assumptions, and the method.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import fincal
import forecast as fc
import schedule as sch
from bills import load_items, money
from pdfwrite import (A4_LANDSCAPE, A4_PORTRAIT, HELV, HELV_BOLD, PDF, fit,
                      text_width, wrap)

ROOT = Path(__file__).resolve().parent.parent

# ---- palette ---------------------------------------------------------------

INK = (0.10, 0.11, 0.13)
MUTED = (0.42, 0.45, 0.50)
RULE = (0.84, 0.85, 0.87)
NAVY = (0.09, 0.24, 0.44)
RED = (0.70, 0.11, 0.11)
AMBER = (0.66, 0.42, 0.04)
GREEN = (0.09, 0.40, 0.23)
WHITE = (1, 1, 1)
BAND_RED = (0.99, 0.92, 0.92)
BAND_AMBER = (0.99, 0.96, 0.88)
BAND_GREEN = (0.93, 0.97, 0.94)
BAND_HEAD = (0.94, 0.95, 0.96)
STRIPE = (0.975, 0.978, 0.982)

# Risk bands. The boundaries are NOT invented for this report: zero is
# `minimum_safe_balance` and the upper line is `large_payment_threshold`, both
# read from config.json. A day below the latter cannot absorb a single large
# obligation, which is what makes it worth marking.
NEGATIVE, TIGHT, HEALTHY = "NEGATIVE", "TIGHT", "OK"


def m(v) -> str:
    v = money(v)
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def d_(x: date) -> str:
    return x.strftime("%a %-d %b")


def d_short(x: date) -> str:
    return x.strftime("%-d %b")


# ---------------------------------------------------------------------------
# layout engine
# ---------------------------------------------------------------------------

@dataclass
class Col:
    title: str
    width: float
    align: str = "l"          # l | r
    font: str = HELV
    wrap_cells: bool = False


class Layout:
    """A flowing document: headings, paragraphs and tables that break pages.

    The cursor `y` is measured down from the top of the page. Every routine
    that draws asks `need()` for room first, so nothing is ever clipped by the
    bottom margin and no caller has to think about pagination.
    """

    MARGIN = 42.0
    FOOT = 34.0

    def __init__(self, title: str, subtitle: str) -> None:
        self.pdf = PDF()
        self.title = title
        self.subtitle = subtitle
        self.page = None
        self.size = A4_PORTRAIT
        self.y = 0.0
        self._section = ""
        self.new_page(A4_PORTRAIT, first=True)

    # ---- pages ------------------------------------------------------------

    @property
    def right(self) -> float:
        return self.size[0] - self.MARGIN

    @property
    def usable(self) -> float:
        return self.size[0] - 2 * self.MARGIN

    @property
    def bottom(self) -> float:
        return self.size[1] - self.FOOT

    def new_page(self, size=None, *, first: bool = False) -> None:
        self.size = size or self.size
        self.page = self.pdf.add_page(self.size)
        if first:
            self.y = self.MARGIN
            return
        # Running head, so a printed page out of order still identifies itself.
        self.page.text(self.MARGIN, self.MARGIN - 12, self.title,
                       size=7.5, color=MUTED)
        if self._section:
            self.page.text_right(self.right, self.MARGIN - 12, self._section,
                                 size=7.5, color=MUTED)
        self.page.line(self.MARGIN, self.MARGIN - 8, self.right,
                       self.MARGIN - 8, color=RULE)
        self.y = self.MARGIN + 16

    def need(self, h: float, size=None) -> None:
        """Ensure `h` points of room, starting a new page if not."""
        if size is not None and size != self.size:
            self.new_page(size)
            return
        if self.y + h > self.bottom:
            self.new_page()

    # ---- blocks -----------------------------------------------------------

    def cover(self, lines: list[tuple[str, str]]) -> None:
        p = self.page
        p.rect(0, 0, self.size[0], 150, NAVY)
        p.text(self.MARGIN, 62, "OpenFIN", size=13, font=HELV_BOLD,
               color=(0.68, 0.78, 0.90))
        p.text(self.MARGIN, 96, self.title, size=24, font=HELV_BOLD, color=WHITE)
        p.text(self.MARGIN, 120, self.subtitle, size=11,
               color=(0.80, 0.86, 0.93))
        self.y = 186
        for label, value in lines:
            self.need(20)
            self.page.text(self.MARGIN, self.y, label, size=9.5, color=MUTED)
            self.page.text(self.MARGIN + 190, self.y, value, size=9.5,
                           font=HELV_BOLD, color=INK)
            self.y += 17
        self.y += 8

    def h1(self, text: str, size=None) -> None:
        """Start a section on a fresh page, in the orientation it asks for.

        Orientation is a per-section decision: prose is unreadable at a
        landscape measure, and a nine-column ledger is unreadable in portrait.
        Without this, a section silently inherited whatever the previous
        table happened to need.
        """
        self._section = text
        self.new_page(size or A4_PORTRAIT)
        self.page.rect(self.MARGIN, self.y - 10, 3, 17, NAVY)
        self.page.text(self.MARGIN + 11, self.y + 3, text, size=15,
                       font=HELV_BOLD, color=NAVY)
        self.y += 16
        self.page.line(self.MARGIN, self.y, self.right, self.y, color=RULE)
        self.y += 16

    def h2(self, text: str) -> None:
        self.need(34)
        self.y += 8
        self.page.text(self.MARGIN, self.y, text, size=11, font=HELV_BOLD,
                       color=INK)
        self.y += 14

    def para(self, text: str, *, size: float = 9.5, color=INK,
             gap: float = 7.0, font: str = HELV, indent: float = 0.0) -> None:
        for line in wrap(text, self.usable - indent, font, size):
            self.need(size + 4)
            self.page.text(self.MARGIN + indent, self.y + size * 0.8, line,
                           size=size, font=font, color=color)
            self.y += size + 3.2
        self.y += gap

    def bullet(self, text: str, *, size: float = 9.5, color=INK) -> None:
        lines = wrap(text, self.usable - 14, HELV, size)
        for i, line in enumerate(lines):
            self.need(size + 4)
            if i == 0:
                self.page.text(self.MARGIN + 3, self.y + size * 0.8, "-",
                               size=size, font=HELV_BOLD, color=NAVY)
            self.page.text(self.MARGIN + 14, self.y + size * 0.8, line,
                           size=size, color=color)
            self.y += size + 3.2
        self.y += 2

    def callout(self, title: str, body: str, tone=RED, band=BAND_RED) -> None:
        size = 9.5
        lines = wrap(body, self.usable - 24, HELV, size)
        h = 26 + len(lines) * (size + 3.2) + 8
        self.need(h)
        self.page.rect(self.MARGIN, self.y - 6, self.usable, h, band)
        self.page.rect(self.MARGIN, self.y - 6, 3, h, tone)
        self.page.text(self.MARGIN + 12, self.y + 10, title, size=10.5,
                       font=HELV_BOLD, color=tone)
        yy = self.y + 26
        for line in lines:
            self.page.text(self.MARGIN + 12, yy, line, size=size, color=INK)
            yy += size + 3.2
        self.y += h + 8

    def stat_row(self, stats: list[tuple[str, str, tuple]]) -> None:
        """A row of headline figures, evenly divided across the text column."""
        self.need(56)
        n = max(len(stats), 1)
        w = self.usable / n
        for i, (label, value, color) in enumerate(stats):
            x = self.MARGIN + i * w
            self.page.rect(x + 2, self.y - 4, w - 4, 48, STRIPE)
            self.page.text(x + 10, self.y + 12, fit(label, w - 20, HELV, 8),
                           size=8, color=MUTED)
            self.page.text(x + 10, self.y + 32, fit(value, w - 20, HELV_BOLD, 14),
                           size=14, font=HELV_BOLD, color=color)
        self.y += 60

    # ---- tables -----------------------------------------------------------

    def table(self, cols: list[Col], rows: list[list[str]], *,
              size: float = 8.5, row_fill=None, cell_color=None,
              page_size=None, note: str = "") -> None:
        """Draw a table, repeating the header on every page it spans.

        `row_fill(i, row) -> colour|None` shades a whole row; `cell_color(i, j,
        row) -> colour|None` colours one cell's text. Cells in a column marked
        `wrap_cells` wrap onto extra lines and the row grows to fit, which is
        what keeps a long list of bill names from running into the next column.
        """
        if page_size is not None and page_size != self.size:
            self.new_page(page_size)
        total = sum(c.width for c in cols)
        avail = self.usable
        if total > avail:                      # scale to fit, never overflow
            k = avail / total
            cols = [Col(c.title, c.width * k, c.align, c.font, c.wrap_cells)
                    for c in cols]
        head_h = size + 12

        def header() -> None:
            self.page.rect(self.MARGIN, self.y, avail, head_h, BAND_HEAD)
            x = self.MARGIN
            for c in cols:
                t = fit(c.title, c.width - 8, HELV_BOLD, size)
                if c.align == "r":
                    self.page.text_right(x + c.width - 4, self.y + size + 3, t,
                                         size=size, font=HELV_BOLD, color=INK)
                else:
                    self.page.text(x + 4, self.y + size + 3, t, size=size,
                                   font=HELV_BOLD, color=INK)
                x += c.width
            self.y += head_h
            self.page.line(self.MARGIN, self.y, self.MARGIN + avail, self.y,
                           color=RULE, width=0.7)

        self.need(head_h + 3 * (size + 8))
        header()

        for i, row in enumerate(rows):
            cells: list[list[str]] = []
            for j, c in enumerate(cols):
                v = str(row[j]) if j < len(row) else ""
                if c.wrap_cells:
                    cells.append(wrap(v, c.width - 8, c.font, size))
                else:
                    cells.append([fit(v, c.width - 8, c.font, size)])
            lines = max(len(cl) for cl in cells)
            rh = lines * (size + 2.6) + 6

            if self.y + rh > self.bottom:
                self.new_page()
                header()

            fill = row_fill(i, row) if row_fill else None
            if fill is None and i % 2 == 1:
                fill = STRIPE
            if fill:
                self.page.rect(self.MARGIN, self.y, avail, rh, fill)

            x = self.MARGIN
            for j, c in enumerate(cols):
                col = cell_color(i, j, row) if cell_color else None
                yy = self.y + size + 2
                for line in cells[j]:
                    if c.align == "r":
                        self.page.text_right(x + c.width - 4, yy, line,
                                             size=size, font=c.font,
                                             color=col or INK)
                    else:
                        self.page.text(x + 4, yy, line, size=size, font=c.font,
                                       color=col or INK)
                    yy += size + 2.6
                x += c.width
            self.y += rh
            self.page.line(self.MARGIN, self.y, self.MARGIN + avail, self.y,
                           color=(0.93, 0.94, 0.95), width=0.4)

        self.y += 6
        if note:
            self.para(note, size=7.8, color=MUTED, gap=8)

    # ---- finish -----------------------------------------------------------

    def save(self, path: Path) -> int:
        total = len(self.pdf.pages)
        for i, pg in enumerate(self.pdf.pages, start=1):
            pg.line(self.MARGIN, pg.height - self.FOOT + 8,
                    pg.width - self.MARGIN, pg.height - self.FOOT + 8,
                    color=RULE)
            pg.text(self.MARGIN, pg.height - self.FOOT + 21,
                    "OpenFIN cash-flow audit - figures derived from repository "
                    "data, not estimates", size=7, color=MUTED)
            pg.text_right(pg.width - self.MARGIN, pg.height - self.FOOT + 21,
                          f"Page {i} of {total}", size=7.5, color=MUTED)
        self.pdf.save(path, title=self.title)
        return total


# ---------------------------------------------------------------------------
# the analysis model
# ---------------------------------------------------------------------------

@dataclass
class Audit:
    today: date
    balance: Decimal
    balance_entered: str
    balance_date: str
    stale: bool
    settings: dict
    bills: list[dict]
    income: list[dict]
    calendar: fincal.Calendar
    deferrals: set
    plan: sch.SchedulePlan
    conditional: sch.SchedulePlan
    movabilities: dict
    assume_days: int = 7
    scenarios: list[tuple[str, str, Decimal, int]] = field(default_factory=list)
    unknowns: list[tuple[str, str]] = field(default_factory=list)
    integrity: list[tuple[str, str, str]] = field(default_factory=list)


def band(closing: Decimal, tight: Decimal) -> str:
    if closing < 0:
        return NEGATIVE
    return TIGHT if closing < tight else HEALTHY


def _load_state() -> dict:
    p = ROOT / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def build_audit(*, on: date | None = None, balance: Decimal | None = None,
                horizon_days: int | None = None,
                assume_days: int = 7) -> Audit:
    """Assemble everything the report needs, from the repository as it stands."""
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    state = _load_state()
    tz = ZoneInfo(cfg.get("timezone", "America/New_York"))

    bal_rec = state.get("balance") or {}
    bal_date = bal_rec.get("date") or ""
    # Anchor to the date the balance actually represents. Running from "today"
    # against a balance entered days ago silently skips every bill in between,
    # which is the quiet kind of wrong this project exists to avoid.
    today = on or (date.fromisoformat(bal_date) if bal_date
                   else datetime.now(tz).date())
    bal = money(balance if balance is not None else bal_rec.get("amount", 0))

    days = int(horizon_days or cfg.get("forecast_days", 60))
    bills = load_items(ROOT / "bills.json", "bills")
    income = load_items(ROOT / "income.json", "income")

    deferrals = {
        (x["bill_id"], date.fromisoformat(x["date"]))
        for x in state.get("deferrals", [])
        if x.get("bill_id") and x.get("date")
        and date.fromisoformat(x["date"]) >= today
    }
    cal = fincal.build(bills, income, today, today + timedelta(days=days + 60),
                       deferrals)
    mv = sch.build_movabilities(bills)

    plan = sch.optimise(cal, bal, today, days, mv)
    cond = sch.optimise(cal, bal, today, days, mv,
                        assume_unknown_days=assume_days)

    a = Audit(
        today=today, balance=bal,
        balance_entered=bal_rec.get("entered_at", "(none)"),
        balance_date=bal_date or "(none)",
        stale=(bal_date != datetime.now(tz).date().isoformat()),
        settings=cfg, bills=bills, income=income, calendar=cal,
        deferrals=deferrals, plan=plan, conditional=cond, movabilities=mv,
        assume_days=assume_days,
    )
    a.scenarios = _scenarios(a, days)
    a.unknowns = _unknowns(a)
    a.integrity = _integrity(a)
    return a


def _rerun(bills, income, a: Audit, days: int) -> fc.Forecast:
    cal = fincal.build(bills, income, a.today,
                       a.today + timedelta(days=days + 60), a.deferrals)
    return fc.run(cal, a.balance, a.today, days, money(0))


def _superseded(a: Audit) -> list[dict]:
    """Bills whose stated amount is a household correction that history
    contradicts, AND where that history is actually driving the forecast.

    Detected from the data, never from a list of names: the household set the
    amount and `observed_max` predates it. The second half of the test matters
    as much as the first — a weekly bill carries an `observed_max` too, but the
    frequent-draw rule already ignores it, so counting those here would inflate
    the finding with entries that change nothing. The test is therefore what the
    engine would actually forecast, not what the field says.
    """
    out = []
    for b in a.bills:
        if not b.get("active", True) or b.get("source") != "household":
            continue
        if b.get("observed_max") is None:
            continue
        when = fincal.occurrences(b, a.today, a.today + timedelta(days=420))
        used = fincal.expected_amount(b, when[0] if when else a.today)
        if used > money(b["amount"]):
            out.append(b)
    return out


def _scenarios(a: Audit, days: int) -> list[tuple[str, str, Decimal, int]]:
    """What-if sensitivities, each isolating one modelling decision."""
    import copy
    out = []
    base = a.plan.baseline
    out.append(("As configured today",
                "Every setting exactly as the live engine runs it.",
                base.minimum_balance, len(base.days_below(money(0)))))

    sup = _superseded(a)
    if sup:
        b2 = copy.deepcopy(a.bills)
        ids = {b["id"] for b in sup}
        for b in b2:
            if b["id"] in ids:
                b.pop("observed_max", None)
        f = _rerun(b2, a.income, a, days)
        out.append((
            f"Honour {len(sup)} household-corrected amount(s)",
            "Stop forecasting renegotiated bills at their pre-renegotiation "
            "observed maximum.",
            f.minimum_balance, len(f.days_below(money(0)))))

    # Every infrequent variable bill at its stated amount rather than its worst.
    b3 = copy.deepcopy(a.bills)
    for b in b3:
        b.pop("observed_max", None)
    f3 = _rerun(b3, a.income, a, days)
    out.append(("All variable bills at their stated amount",
                "Removes the forecast-at-worst rule entirely. The optimistic "
                "bound, shown only to bracket the answer.",
                f3.minimum_balance, len(f3.days_below(money(0)))))

    off = [i for i in a.income if not i.get("active", True)]
    if off:
        i2 = copy.deepcopy(a.income)
        for i in i2:
            i["active"] = True
        f4 = _rerun(a.bills, i2, a, days)
        names = ", ".join(i["name"] for i in off)
        out.append((f"Reactivate: {names}",
                    "Every income source currently switched off is counted "
                    "again, at its recorded amount and cadence.",
                    f4.minimum_balance, len(f4.days_below(money(0)))))
        if sup:
            f5 = _rerun(b2, i2, a, days)
            out.append(("Both corrections together",
                        "Household-corrected amounts honoured AND the inactive "
                        "income restored.",
                        f5.minimum_balance, len(f5.days_below(money(0)))))
    return out


def _unknowns(a: Audit) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    n_unknown = sum(1 for v in a.movabilities.values() if v.kind == sch.UNKNOWN)
    out.append((
        "Payment flexibility is not recorded anywhere",
        f"{n_unknown} active obligations are marked deferrable, but nothing in "
        f"bills.json records a due date separate from the payment date, a grace "
        f"period, or a late-fee term. Until those are supplied, no bill can be "
        f"moved later on evidence. This is the single largest gap between what "
        f"this report can prove and what it could recommend."))
    for i in a.income:
        if i.get("needs_review"):
            out.append((f"Income unverified: {i['name']}",
                        f"Flagged needs_review in income.json. "
                        f"{(i.get('note') or '')[:240]}"))
        if not i.get("active", True):
            out.append((f"Income switched off: {i['name']}",
                        f"{m(i['amount'])} {i.get('frequency')} is recorded but "
                        f"not counted. {(i.get('note') or '')[:240]}"))
    for b in a.bills:
        if b.get("active", True) and b.get("needs_review"):
            out.append((f"Bill needs review: {b['name']}",
                        (b.get("note") or "")[:260]))
    return out


def _integrity(a: Audit) -> list[tuple[str, str, str]]:
    """Discrepancies between what is stored and what is forecast."""
    out: list[tuple[str, str, str]] = []
    for b in a.bills:
        if not b.get("active", True):
            continue
        stated = money(b["amount"])
        used = fincal.expected_amount(b, a.today + timedelta(days=30))
        if used != stated and not b.get("monthly_expected"):
            tag = ("SUPERSEDED" if b.get("source") == "household"
                   else "FORECAST-AT-WORST")
            out.append((
                b["name"],
                f"stored {m(stated)}, forecast {m(used)} "
                f"({m(used - stated)} per occurrence)",
                tag))
    return out


# ---------------------------------------------------------------------------
# ledger rows
# ---------------------------------------------------------------------------

def ledger_rows(forecast: fc.Forecast, tight: Decimal,
                actions: dict[date, str] | None = None) -> list[list[str]]:
    actions = actions or {}
    rows = []
    for d in forecast.days:
        ins = "; ".join(f"{e.name} {m(e.amount)}" for e in d.income) or "-"
        outs = "; ".join(f"{e.name} {m(e.amount)}" for e in d.bills) or "-"
        rows.append([
            d.day.strftime("%a %d %b"),
            m(d.opening),
            m(d.income_total) if d.income else "-",
            ins if d.income else "-",
            outs,
            m(d.bills_total) if d.bills else "-",
            m(d.closing),
            band(d.closing, tight),
            actions.get(d.day, ""),
        ])
    return rows


LEDGER_COLS = [
    Col("Date", 62), Col("Opening", 58, "r"), Col("In", 54, "r"),
    Col("Money in", 96, wrap_cells=True), Col("Bills / expenses out", 210,
                                              wrap_cells=True),
    Col("Out", 58, "r"), Col("Closing", 60, "r"), Col("Risk", 40),
    Col("Action", 108, wrap_cells=True),
]


def _ledger_fill(tight: Decimal):
    def f(i, row):
        if row[7] == NEGATIVE:
            return BAND_RED
        if row[7] == TIGHT:
            return BAND_AMBER
        return None
    return f


def _ledger_ink(i, j, row):
    if j in (6, 7):
        if row[7] == NEGATIVE:
            return RED
        if row[7] == TIGHT:
            return AMBER
        return GREEN
    return None


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def render(a: Audit, out: Path) -> int:
    tight = money(a.settings.get("large_payment_threshold", 750))
    p = a.plan
    base, opt = p.baseline, p.optimised
    neg = base.days_below(money(0))
    horizon = base.days[-1].day if base.days else a.today

    L = Layout("Cash-flow audit and bill-date optimisation",
               f"{d_(a.today)} to {d_(horizon)} - {len(base.days)} days projected")

    # ---- cover ------------------------------------------------------------
    L.cover([
        ("Opening balance", f"{m(a.balance)}  (as entered {a.balance_date})"),
        ("Projection window", f"{d_(a.today)} - {d_(horizon)}"),
        ("Lowest projected balance", f"{m(base.minimum_balance)} on "
         f"{d_(base.minimum_day.day) if base.minimum_day else '-'}"),
        ("Days below zero", f"{len(neg)} of {len(base.days)}"),
        ("Bill-date changes the data supports", f"{len(p.moves)}"),
        ("Obligations modelled",
         f"{sum(1 for b in a.bills if b.get('active', True))} active bills, "
         f"{sum(1 for i in a.income if i.get('active', True))} income streams"),
        ("Generated", datetime.now().strftime("%d %b %Y %H:%M")),
    ])

    # ---- 1. executive summary --------------------------------------------
    L.h1("1. Executive summary")

    L.stat_row([
        ("Balance now", m(a.balance), INK),
        ("Lowest projected", m(base.minimum_balance),
         RED if base.minimum_balance < 0 else GREEN),
        ("Days negative", f"{len(neg)}/{len(base.days)}",
         RED if neg else GREEN),
        ("Moves available", str(len(p.moves)), AMBER if not p.moves else GREEN),
    ])

    if not p.moves and neg:
        L.callout(
            "Moving bill dates cannot fix this, and the report will not pretend it can",
            f"The projection goes below zero on {len(neg)} of {len(base.days)} days "
            f"and reaches {m(base.minimum_balance)}. No payment-date change is "
            f"recommended, for one reason: of the "
            f"{sum(1 for v in a.movabilities.values() if v.kind == sch.FIXED)} "
            f"obligations classified FIXED and the "
            f"{sum(1 for v in a.movabilities.values() if v.kind == sch.UNKNOWN)} "
            f"classified UNKNOWN, none has a recorded latest-safe payment date. "
            f"Section 4 lists exactly which ones to confirm and section 5 shows "
            f"what confirming them would buy. Section 6 shows why the largest "
            f"gap survives even then.")

    total_in = a.calendar.total_in(a.today, horizon)
    total_out = a.calendar.total_out(a.today, horizon)
    per_month = money((total_in - total_out) / max(len(base.days), 1) * 30)
    L.para(
        f"Across the {len(base.days)} days projected, {m(total_in)} of income is "
        f"set against {m(total_out)} of obligations. That is a net "
        f"{m(total_in - total_out)}, or about {m(per_month)} per 30 days. This is "
        f"the number that decides everything else in this report: where income "
        f"exceeds obligations, a cash-flow problem is a timing problem and moving "
        f"payment dates can solve it. Where it does not, no schedule solves it, "
        f"and the honest output is the size of the gap.")

    if total_in < total_out:
        L.callout(
            "This is a shortfall, not a timing problem",
            f"Obligations exceed income by {m(total_out - total_in)} over the "
            f"window ({m(per_month)} per 30 days). Rescheduling moves a deficit "
            f"around the calendar; it cannot remove one. Every bill could be paid "
            f"on its most convenient possible date and the account would still "
            f"end the window {m(total_out - total_in)} further behind. Sections 6 "
            f"and 7 deal with this directly.",
            tone=RED, band=BAND_RED)
    else:
        L.callout(
            "Income covers obligations over the window - the order is the problem",
            f"Income exceeds obligations by {m(total_in - total_out)} across the "
            f"window, so the difficulty is sequencing rather than sufficiency. "
            f"That is the case payment-date optimisation is built for.",
            tone=GREEN, band=BAND_GREEN)

    L.h2("The risk dates, in order")
    eps = sch._episodes(base, money(0))
    if eps:
        rows = []
        for i0, i1 in eps[:12]:
            dd = base.days[i0:i1 + 1]
            worst = min(dd, key=lambda x: x.closing)
            big = sorted(((e.name, e.amount) for x in dd for e in x.bills),
                         key=lambda r: -r[1])[:3]
            rows.append([
                f"{d_short(dd[0].day)} - {d_short(dd[-1].day)}",
                str(len(dd)),
                m(worst.closing),
                d_short(worst.day),
                ", ".join(f"{n} {m(v)}" for n, v in big) or "-",
            ])
        L.table([Col("Period", 96), Col("Days", 38, "r"),
                 Col("Worst balance", 76, "r"), Col("On", 52),
                 Col("Largest obligations in the period", 250, wrap_cells=True)],
                rows)
    else:
        L.para("No day in the window closes below zero.", color=GREEN)

    L.h2("What to do first")
    acts = []
    if a.stale:
        acts.append(f"The balance driving every figure here is dated "
                    f"{a.balance_date}. Enter today's balance before acting on "
                    f"the near dates.")
    for name, why, mn, nd in a.scenarios[1:]:
        delta = money(mn - base.minimum_balance)
        if delta > 0:
            acts.append(f"{name}: would lift the lowest projected balance by "
                        f"{m(delta)} to {m(mn)} and take negative days from "
                        f"{len(neg)} to {nd}. Confirm whether it is true.")
    n_unknown = sum(1 for v in a.movabilities.values() if v.kind == sch.UNKNOWN)
    if n_unknown:
        acts.append(f"Supply a latest-safe payment date for the {n_unknown} "
                    f"obligations listed in section 4. Section 5 shows the "
                    f"schedule that becomes available once they are known.")
    for x in acts:
        L.bullet(x)

    # ---- 2. starting position --------------------------------------------
    L.h1("2. Starting position and how it was derived")
    L.para(
        f"Every figure in this report runs forward from {m(a.balance)}, the most "
        f"recent balance recorded in state.json. It was entered on "
        f"{a.balance_entered[:19] or '(unknown)'} and represents "
        f"{a.balance_date}. It is a single figure typed in by the household, not "
        f"a bank feed: there is no connection to the bank in this system, so "
        f"pending transactions, uncleared cheques and holds are included only to "
        f"the extent the bank had already applied them when it was read.")
    if a.stale:
        L.callout(
            "The starting balance is not from today",
            f"It is dated {a.balance_date}. Bills dated between then and now are "
            f"projected as though still to come, and any spending since is not "
            f"reflected. The engine's own rule (balance_max_age_hours = "
            f"{a.settings.get('balance_max_age_hours')}) treats this as stale and "
            f"suppresses the daily email for exactly this reason. This report is "
            f"anchored to {a.today.isoformat()}, the date the balance represents, "
            f"so the ledger reconciles to it exactly.",
            tone=AMBER, band=BAND_AMBER)

    L.h2("Accounts, transfers and double counting")
    L.para(
        "The model holds one pooled checking balance. There is no second account, "
        "no savings sweep and no internal transfer in bills.json or income.json, "
        "so no transfer can be miscounted as income or as an expense. That is a "
        "genuine simplification rather than a verified fact about the household: "
        "if money is regularly moved to or from another account, none of it is "
        "modelled here. config.json does exclude transfer-like descriptions "
        "(transfer, xfer, zelle, venmo, cash app and others) when classifying "
        "spending, which is the one place the distinction is currently drawn.")
    L.para(
        "One double-count risk is real and worth stating. The balance was entered "
        f"at {a.balance_entered[11:16] or '??:??'} on {a.balance_date}, near the "
        f"end of that day, while the projection charges that day's bills in full. "
        f"Anything that had already cleared before it was typed in is therefore "
        f"subtracted twice. The engine has no way to detect this: it has no "
        f"transaction feed, only a number and a date.")

    # ---- 3. income --------------------------------------------------------
    L.h1("3. Income model")
    L.para(
        "Income is modelled on actual arrival dates, never as a monthly average. "
        "A weekly deposit lands on its weekday and is available that day; a "
        "biweekly one steps 14 days from its anchor. This matters because the "
        "whole question is what is in the account on a given morning.")
    rows = []
    for i in a.income:
        occ = fincal.occurrences(i, a.today, horizon) if i.get("active", True) else []
        rows.append([
            i["name"], m(i["amount"]), i.get("frequency", "-"),
            i.get("anchor_date") or i.get("due_date") or "-",
            "yes" if i.get("active", True) else "NO - switched off",
            str(len(occ)),
            m(money(i["amount"]) * len(occ)),
            "unverified" if i.get("needs_review") else "confirmed",
        ])
    L.table([Col("Source", 118, wrap_cells=True), Col("Amount", 62, "r"),
             Col("Cadence", 58), Col("Anchor", 62), Col("Counted", 86),
             Col("Times", 38, "r"), Col("Total in window", 74, "r"),
             Col("Status", 62)], rows,
            note=f"Totals cover {d_short(a.today)} to {d_short(horizon)}. "
                 f"'Times' is the number of deposits the calendar generates in "
                 f"that period.")
    off = [i for i in a.income if not i.get("active", True)]
    if off:
        L.callout(
            f"{len(off)} income source(s) are recorded but not counted",
            " ".join(
                f"{i['name']} ({m(i['amount'])} {i.get('frequency')}) is switched "
                f"off in income.json. Note on file: "
                f"{(i.get('note') or '(none)')[:300]}"
                for i in off),
            tone=AMBER, band=BAND_AMBER)

    # ---- 4. movability ----------------------------------------------------
    L.h1("4. Bill movability - which dates may legitimately change",
         A4_LANDSCAPE)
    L.para(
        "A payment date can only be moved if something establishes how late is "
        "still safe. Three classifications are used, and the rule for each is "
        "stated so it can be checked rather than trusted.")
    L.bullet("FIXED - secured against an asset, or recorded as non-deferrable. "
             "Never moved later by this report.")
    L.bullet("FLEXIBLE - a payment_window is recorded in bills.json giving the "
             "earliest and latest safe dates. Only these can be optimised on "
             "evidence.")
    L.bullet("UNKNOWN - USER CONFIRMATION REQUIRED - marked deferrable, but no "
             "due date, grace period or late-fee term is recorded, so how far it "
             "may slip is not derivable. Never moved later in section 5's "
             "baseline.")
    L.para(
        "Paying EARLIER is treated as always permissible, because paying ahead of "
        "schedule is never late. It is bounded at one cadence so a bill cannot be "
        "pulled on top of its own previous occurrence. Moving earlier is what "
        "needs no confirmation; moving later is what does.", gap=10)

    rows, fills = [], []
    for b in sorted(a.bills, key=lambda x: (x.get("priority_tier", 5), x["name"])):
        if not b.get("active", True):
            continue
        v = a.movabilities.get(b["id"])
        if v is None:
            continue
        nxt = fincal.occurrences(b, a.today, horizon)
        rows.append([
            b["name"],
            m(fincal.expected_amount(b, nxt[0] if nxt else a.today)),
            b.get("frequency", "-"),
            d_short(nxt[0]) if nxt else "-",
            str(b.get("priority_tier", 5)),
            v.kind,
            (d_short(nxt[0] + timedelta(days=v.earliest_days))
             if nxt else "-"),
            (d_short(nxt[0] + timedelta(days=v.latest_days))
             if nxt and v.latest_days else "not established"),
            v.reason,
        ])
        fills.append(v.kind)

    def mv_fill(i, row):
        return {sch.FIXED: None, sch.FLEXIBLE: BAND_GREEN,
                sch.UNKNOWN: BAND_AMBER}.get(fills[i])

    L.table([Col("Bill", 108, wrap_cells=True), Col("Forecast", 58, "r"),
             Col("Freq", 50), Col("Next due", 48), Col("Tier", 28, "r"),
             Col("Class", 62), Col("Earliest safe", 62),
             Col("Latest safe", 70), Col("Basis for the classification", 210,
                                         wrap_cells=True)],
            rows, page_size=A4_LANDSCAPE, row_fill=mv_fill,
            note="Amber rows are the confirmations that would unlock "
                 "optimisation. 'Latest safe' reads 'not established' wherever "
                 "the repository does not record one - that is a missing fact, "
                 "not a judgement that the bill cannot move.")

    # ---- 5. current and optimised schedules -------------------------------
    L.h1("5. Current cash-flow schedule, day by day", A4_LANDSCAPE)
    L.para(
        f"Every day from {d_(a.today)} to {d_(horizon)}. Opening balance, money "
        f"in, every obligation out, and the closing balance after each. Rows "
        f"shaded red close below zero; amber rows close below "
        f"{m(tight)}, the large_payment_threshold from config.json, meaning the "
        f"day could not absorb one large obligation.", gap=10)
    actions = {}
    for s in p.shortfalls:
        actions[s.worst_day] = f"Short {m(s.needed)} - see section 8"
    for mo in p.moves:
        actions[mo.from_day] = f"Move to {d_short(mo.to_day)}"
    L.table(LEDGER_COLS, ledger_rows(base, tight, actions),
            page_size=A4_LANDSCAPE, row_fill=_ledger_fill(tight),
            cell_color=_ledger_ink,
            note="Deferred occurrences are excluded from the outflow columns "
                 "because they do not leave the account on that date; they "
                 "remain owed and are listed in section 9.")

    L.h1("6. Optimised schedule", A4_LANDSCAPE)
    if p.moves:
        L.para(f"After the {len(p.moves)} date change(s) in section 7. Total "
               f"outflow is unchanged - every obligation is still paid in full, "
               f"once. Only the dates differ.", gap=10)
        L.table(LEDGER_COLS, ledger_rows(opt, tight), page_size=A4_LANDSCAPE,
                row_fill=_ledger_fill(tight), cell_color=_ledger_ink)
        L.h2("Before and after")
        L.table([Col("Measure", 220), Col("Current", 90, "r"),
                 Col("Optimised", 90, "r"), Col("Change", 90, "r")], [
            ["Lowest projected balance", m(base.minimum_balance),
             m(opt.minimum_balance), m(p.improvement)],
            ["Days closing below zero", str(p.negative_days_before),
             str(p.negative_days_after),
             str(p.negative_days_after - p.negative_days_before)],
            ["Bills moved", "-", str(len(p.moves)), str(len(p.moves))],
            ["Dollars rescheduled", "-", m(p.dollars_moved), m(p.dollars_moved)],
            ["Total outflow in window", m(total_out), m(total_out), m(0)],
        ])
    else:
        L.para(
            "There is no optimised schedule to show, because the repository "
            "does not support a single legitimate date change. Every obligation "
            "is either FIXED or UNKNOWN, and this report will not move a bill "
            "later on the basis that it technically could be paid later. What "
            "follows instead is the schedule that becomes available the moment "
            "those windows are confirmed.", gap=10)

        c = a.conditional
        L.h2(f"Conditional schedule - assumes every UNKNOWN bill may slip up to "
             f"{a.assume_days} days")
        L.callout(
            "Conditional, and not a recommendation",
            "The plan below exists to size the prize, not to be acted on. It "
            "assumes a uniform tolerance that nothing in the repository "
            "establishes. Confirm the individual windows in section 4 and this "
            "becomes a real schedule; until then it is arithmetic on an "
            "assumption, clearly labelled as one.",
            tone=AMBER, band=BAND_AMBER)
        if c.moves:
            L.table([Col("Bill", 120, wrap_cells=True), Col("Amount", 62, "r"),
                     Col("From", 62), Col("To", 62), Col("Days", 36, "r"),
                     Col("Tier", 32, "r"), Col("Confidence", 60)],
                    [[x.name, m(x.amount), d_short(x.from_day),
                      d_short(x.to_day), f"{x.days_moved:+d}", str(x.tier),
                      x.confidence] for x in c.moves])
            L.table([Col("Measure", 220), Col("Current", 90, "r"),
                     Col("Conditional", 90, "r"), Col("Change", 90, "r")], [
                ["Lowest projected balance", m(c.baseline_minimum),
                 m(c.optimised_minimum), m(c.improvement)],
                ["Days closing below zero", str(c.negative_days_before),
                 str(c.negative_days_after),
                 str(c.negative_days_after - c.negative_days_before)],
                ["Bills moved", "-", str(len(c.moves)), str(len(c.moves))],
                ["Dollars rescheduled", "-", m(c.dollars_moved),
                 m(c.dollars_moved)],
            ])
            L.para(
                f"Even on that assumption the lowest projected balance moves "
                f"from {m(c.baseline_minimum)} to {m(c.optimised_minimum)} and "
                f"{c.negative_days_after} days still close below zero. "
                f"Rescheduling is not the answer to this projection; it is at "
                f"best a partial mitigation of the earliest episodes.")

    # ---- 7. recommended changes -------------------------------------------
    L.h1("7. Recommended bill-date changes")
    if p.moves:
        for x in p.moves:
            L.h2(f"{x.name} - {m(x.amount)}")
            L.table([Col("Field", 150), Col("Value", 330, wrap_cells=True)], [
                ["Current payment date", d_(x.from_day)],
                ["Recommended date", d_(x.to_day)],
                ["Days moved", f"{x.days_moved:+d}"],
                ["Why", x.reason],
                ["Basis", x.basis],
                ["Late-payment risk", "None identified from recorded terms."],
                ["Confidence", x.confidence],
            ])
    else:
        L.para(
            "None. No bill in this repository has a recorded latest-safe payment "
            "date, so there is no change this report can recommend on evidence. "
            "Recommending one anyway would mean inventing the single fact that "
            "decides whether it is safe - the deadline. The actionable request "
            "is therefore the confirmation itself, listed below in the order the "
            "projection would benefit.", gap=10)
        want = []
        for x in a.conditional.moves:
            v = a.movabilities.get(x.item_id)
            want.append([x.name, m(x.amount), d_short(x.from_day),
                         f"{x.days_moved:+d} days",
                         "What is the actual due date, and what happens if it is "
                         "paid after it?"])
        if want:
            L.table([Col("Bill", 110, wrap_cells=True), Col("Amount", 62, "r"),
                     Col("Currently paid", 70), Col("Would need", 62),
                     Col("Question to answer", 200, wrap_cells=True)], want,
                    note="Answering these turns section 6's conditional "
                         "schedule into a set of real recommendations. Record "
                         "the answer in bills.json as a payment_window entry "
                         "and the engine will use it automatically.")

    # ---- 8. shortfalls ----------------------------------------------------
    L.h1("8. Shortfalls that no date change can fix")
    if p.shortfalls:
        L.para(
            "Each of these is a period where the account closes below zero and "
            "the obligations landing in it cannot legitimately be moved out of "
            "it. Moving dates redistributes a deficit; it does not fund one.",
            gap=10)
        for s in p.shortfalls:
            L.h2(f"{d_(s.start)} to {d_(s.end)} - short {m(s.needed)}")
            rows = [
                ["Worst day", d_(s.worst_day)],
                ["Projected balance", m(s.worst_balance)],
                ["Cash needed to hold at zero", m(s.needed)],
                ["Next income after it",
                 f"{d_(s.next_income_day)} for {m(s.next_income_amount)}"
                 if s.next_income_day else "none inside the window"],
                ["Largest obligations in the period",
                 "; ".join(f"{n} {m(v)} on {d_short(dd)}"
                           for n, v, dd in s.bills_responsible) or "-"],
                ["Why rescheduling does not solve it", s.why],
            ]
            L.table([Col("Field", 150), Col("Value", 330, wrap_cells=True)],
                    rows)
            L.para(
                "Options that do address it, in rough order of how little they "
                "cost: confirm whether any obligation in the period may "
                "legitimately be paid later; reduce discretionary spending in "
                "the days before it by the amount shown; bring forward or "
                "restore income; or arrange the cash from outside. This report "
                "does not recommend borrowing to close a modelled gap, because "
                "the gap is partly a modelling question and section 10 shows by "
                "how much.", size=9)
    else:
        L.para("None. Every projected day closes at or above zero after "
               "optimisation.", color=GREEN)

    # ---- 9. discretionary --------------------------------------------------
    L.h1("9. Committed obligations versus discretionary spending")
    disc = fc.available(a.calendar, a.balance, a.today, base,
                        window_days=int(a.settings.get("spending_window_days", 30)))
    L.para(
        f"Everything above counts committed obligations only. No everyday "
        f"spending is assumed anywhere in this report, which is deliberate: an "
        f"invented spending rate charged to the household is an assumption "
        f"wearing the costume of arithmetic. What the projection can carry is "
        f"derived from it instead.", gap=10)
    L.stat_row([
        (f"Headroom over {disc.window_days} days", m(disc.headroom),
         RED if disc.headroom < 0 else GREEN),
        ("Binding day", d_short(disc.headroom_day) if disc.headroom_day else "-",
         INK),
        ("Sustainable per day", m(disc.per_day),
         RED if disc.per_day < 0 else GREEN),
    ])
    if disc.headroom < 0:
        L.callout(
            "There is no discretionary capacity at all",
            f"Headroom is {m(disc.headroom)}, binding on "
            f"{d_(disc.headroom_day) if disc.headroom_day else '-'}. That figure "
            f"carries bills and income only. It is already negative before a "
            f"single dollar of groceries beyond the modelled weekly amount, "
            f"fuel, or anything else - so every additional dollar spent deepens "
            f"the worst day by exactly that dollar. Note that groceries and fuel "
            f"ARE modelled as committed weekly obligations here, at their "
            f"historical means, so they are not double counted in this figure.",
            tone=RED, band=BAND_RED)
    deferred = a.calendar.deferred_total(a.today, horizon)
    if a.deferrals or deferred > 0:
        L.h2("Deferred occurrences")
        L.para(
            f"{len(a.deferrals)} occurrence(s) totalling {m(deferred)} are marked "
            f"deferred and are excluded from the outflow above, because they do "
            f"not leave the account on that date. They are still owed. Note that "
            f"the engine drops a deferral once its date has passed and never "
            f"re-charges the money on a later day, so a deferred bill leaves the "
            f"balance curve permanently. Section 10 records this as a defect.")

    # ---- 10. data integrity -----------------------------------------------
    L.h1("10. Data integrity findings")
    L.para(
        "Differences between what the repository stores and what the engine "
        "forecasts. Neither is automatically wrong - forecasting a variable bill "
        "at its worst observed value is a deliberate rule - but each one is a "
        "place where the displayed model and the stored model disagree, and each "
        "moves the projection.", gap=10)
    if a.integrity:
        L.table([Col("Bill", 130, wrap_cells=True),
                 Col("Stored versus forecast", 220, wrap_cells=True),
                 Col("Classification", 110)],
                [[n, txt, tag] for n, txt, tag in a.integrity],
                row_fill=lambda i, r: BAND_AMBER if r[2] == "SUPERSEDED" else None,
                note="SUPERSEDED means the household set the current amount and "
                     "the engine is forecasting a higher pre-correction "
                     "observation over the top of it. FORECAST-AT-WORST is the "
                     "documented rule applying as intended - shown so its size "
                     "is visible.")
    L.h2("Sensitivity - what each modelling decision is worth")
    L.table([Col("Scenario", 180, wrap_cells=True),
             Col("What it changes", 200, wrap_cells=True),
             Col("Lowest balance", 78, "r"), Col("Days below zero", 66, "r")],
            [[n, w, m(mn), str(nd)] for n, w, mn, nd in a.scenarios],
            row_fill=lambda i, r: (
                BAND_GREEN if a.scenarios[i][2] >= 0
                else (None if i == 0 else BAND_AMBER)),
            note="Each row re-runs the whole projection with one decision "
                 "changed and everything else held constant. These are "
                 "diagnostics, not proposals - none of them has been applied to "
                 "the live model.")

    # ---- 11. assumptions ---------------------------------------------------
    L.h1("11. Assumptions and unknowns")
    L.para("Labelled honestly. Anything the repository does not establish is "
           "marked as needing confirmation rather than filled in.", gap=10)
    for title, body in a.unknowns:
        L.h2(title)
        L.para(body, size=9, gap=4)

    # ---- 12. methodology ---------------------------------------------------
    L.h1("12. Methodology")
    L.para(
        "The projection starts from the last recorded balance and walks forward "
        "one day at a time. For each day: opening balance, plus income landing "
        "that day, minus obligations landing that day, equals closing balance, "
        "which becomes the next day's opening. Income is ordered before "
        "outflows within a day, because assuming the reverse would flag a "
        "phantom shortfall on every payday. No allowance for everyday spending "
        "is charged. All money is handled as exact decimal, rounded half up; "
        "floating point is never used, because a projection is a long chain of "
        "additions.")
    L.para(
        "Occurrence dates come from one calendar module used by every other "
        "part of the system, so the report and the app cannot drift apart. "
        "Monthly bills clamp to the last day of a short month. Weekly and "
        "biweekly items step 7 or 14 days from an anchor date, which is what "
        "makes a biweekly mortgage produce 26 payments a year rather than 24. "
        "Nothing is shifted off a weekend: a due date landing on a Saturday is "
        "counted on the Saturday, because shifting it to Monday would make the "
        "projection optimistic on precisely the days that matter.")
    L.para(
        "Optimisation works on episodes. The first stretch of days closing below "
        "the floor is identified, and its deficit is the amount that must be "
        "kept out of it. Candidates are obligations landing on or before the end "
        "of that stretch whose recorded window permits landing after it. They "
        "are taken least-critical first and largest first, so the fewest moves "
        "close the gap, and any move the episode turns out not to need is then "
        "dropped. The curve is re-run and the next episode examined, until none "
        "remain or nothing legitimate is left to move.")
    L.para(
        "Moving a payment later is safe for the balance curve in a precise "
        "sense: shifting an obligation from day d to day t raises every day from "
        "d to t-1 by its amount and changes no day from t onward, because the "
        "money has left by then either way. So a later date never worsens any "
        "day. The entire cost of moving is the risk of paying late, which is "
        "why a recorded deadline is required before this report will propose it.")
    L.para(
        "Total outflow is identical before and after optimisation, and that is "
        "asserted in the test suite rather than assumed. A schedule that reduces "
        "what is owed is not a schedule.")

    return L.save(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="OpenFIN cash-flow audit report")
    ap.add_argument("--out", default="cashflow-audit.pdf")
    ap.add_argument("--date", help="anchor date; defaults to the balance's date")
    ap.add_argument("--balance", help="override the starting balance")
    ap.add_argument("--days", type=int, help="projection length")
    ap.add_argument("--assume-days", type=int, default=7,
                    help="conditional window for UNKNOWN bills (report only)")
    args = ap.parse_args(argv)

    a = build_audit(
        on=date.fromisoformat(args.date) if args.date else None,
        balance=money(args.balance.replace("$", "").replace(",", ""))
        if args.balance else None,
        horizon_days=args.days,
        assume_days=args.assume_days,
    )
    out = Path(args.out)
    pages = render(a, out)
    print(f"{out} written - {pages} pages.")
    print(f"  anchored {a.today} at {m(a.balance)}")
    print(f"  lowest projected {m(a.plan.baseline_minimum)}, "
          f"{a.plan.negative_days_before} day(s) below zero")
    print(f"  {len(a.plan.moves)} evidence-backed date change(s), "
          f"{len(a.plan.shortfalls)} unavoidable shortfall(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
