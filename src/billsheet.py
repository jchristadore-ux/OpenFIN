"""The monthly bill statement: every obligation, by category, as PDF and XLSX.

The question this answers is the plain household one - "what are our bills each
month, and what do they add up to?" - and it answers it from bills.json alone,
with no balance, no forecast and no advice.

Two things make it more than a list.

First, a bill is not a monthly figure just because it is paid regularly. The
mortgage is biweekly, so it lands twice in most months and three times in two
months a year; groceries and fuel are weekly, so a month holds four or five;
water is quarterly, sewer annual, and the lacrosse instalments land on four
specific dates. Multiplying a weekly figure by four, or dividing an annual one
by twelve and leaving it there, gets the yearly total wrong and every
individual month wrong as well. So every figure here is built by expanding the
real occurrence dates over twelve calendar months and adding up what actually
falls in each - the same occurrence maths the forecast uses, from bills.py.

Second, seasonal bills are not one number. Gas runs $54 in August and $551 in
March; electric inverts. Where bills.json carries a month-by-month profile this
report uses it, so the monthly columns show the real shape of the year and the
average is an average of twelve honest months rather than twelve copies of one
guess.

The "monthly average" column throughout is therefore the twelve-month total
divided by twelve - a true run rate, not a frequency multiplier.
"""

from __future__ import annotations

import argparse
import calendar
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import xlsxwrite as X
from bills import load_items, money, next_due, occurrences, parse_date
from fincal import FREQUENT_DRAW, OCCURRENCES_PER_YEAR, expected_amount
from pdfwrite import A4_LANDSCAPE, A4_PORTRAIT, HELV, HELV_BOLD
from report import (AMBER, BAND_GREEN, BAND_HEAD, GREEN, INK, MUTED, NAVY,
                    RED, RULE, STRIPE, WHITE, Col, Layout, m)

ROOT = Path(__file__).resolve().parent.parent

MONTHS = 12

# ---------------------------------------------------------------------------
# categories
# ---------------------------------------------------------------------------
#
# Categories are grouped by what the money is FOR, not by who is owed. A store
# card is a credit card whatever was bought with it; the gas utility heats the
# house and the fuel line fills the cars, and merging them under one word
# "gas" - the one thing a household sheet always does - hides a $3,000 a year
# difference in opposite seasons. A bill may also carry its own "category" in
# bills.json, which wins over this table.

CATEGORY_ORDER = [
    "Housing",
    "Utilities",
    "Insurance",
    "Transportation",
    "Groceries",
    "Medical & dental",
    "Kids & activities",
    "Loans",
    "Credit cards & store accounts",
    "Taxes",
    "Subscriptions",
    "Miscellaneous",
]

CATEGORY_OF = {
    "mortgage": "Housing",

    "electric": "Utilities",
    "electric-deferred-2026-09": "Utilities",
    "gas-utility": "Utilities",
    "clinton-water": "Utilities",
    "sewer": "Utilities",
    "sewer-deferred-2026-09": "Utilities",
    "trash": "Utilities",
    "trash-2027-q1": "Utilities",
    "culligan": "Utilities",
    "internet": "Utilities",
    "xfinity-mobile": "Utilities",
    "tmobile-lia": "Utilities",

    "auto-insurance": "Insurance",

    "car-payment": "Transportation",
    "ccu-car-loan": "Transportation",
    "fuel": "Transportation",

    "groceries": "Groceries",

    "loew-patel-braces": "Medical & dental",

    "trilogy-lacrosse": "Kids & activities",
    "lacrosse-2627-inst-1": "Kids & activities",
    "lacrosse-2627-inst-2": "Kids & activities",
    "lacrosse-2627-inst-3": "Kids & activities",
    "lacrosse-2627-inst-4": "Kids & activities",

    "upstart-debt-consolidation": "Loans",
    "upstart-past-bills": "Loans",
    "upstart-past-bills-step2": "Loans",
    "onemain-loan": "Loans",
    "upgrade-basement": "Loans",
    "affirm": "Loans",
    "affirm-large": "Loans",
    "pnc": "Loans",

    "capital-one": "Credit cards & store accounts",
    "chase-amazon": "Credit cards & store accounts",
    "chase-united": "Credit cards & store accounts",
    "barclay-oldnavy": "Credit cards & store accounts",
    "comenity": "Credit cards & store accounts",
    "lowes": "Credit cards & store accounts",
    "home-depot": "Credit cards & store accounts",
    "tjmaxx": "Credit cards & store accounts",

    "irs-federal": "Taxes",
    "irs-state": "Taxes",

    "netflix": "Subscriptions",
}

# Used only for a bill that is in neither the file nor the table above, so a
# newly added entry lands somewhere sensible instead of silently in the
# residual bucket.
KEYWORD_CATEGORY = [
    (("mortgage", "rent", "hoa"), "Housing"),
    (("electric", "gas", "water", "sewer", "trash", "internet", "mobile",
      "phone", "wifi", "cable"), "Utilities"),
    (("insurance",), "Insurance"),
    (("car", "auto", "fuel", "gasoline", "toll"), "Transportation"),
    (("grocer", "food"), "Groceries"),
    (("dental", "braces", "medical", "doctor", "ortho"), "Medical & dental"),
    (("lacrosse", "school", "camp", "sport"), "Kids & activities"),
    (("loan", "affirm", "upstart", "upgrade"), "Loans"),
    (("card", "visa", "amex", "chase", "capital"),
     "Credit cards & store accounts"),
    (("irs", "tax"), "Taxes"),
    (("netflix", "hulu", "spotify", "subscription"), "Subscriptions"),
]

# Short forms, used only where a column is too narrow for the full name to be
# anything but an ellipsis - the twelve-month grid in the PDF.
SHORT_CATEGORY = {
    "Credit cards & store accounts": "Credit cards",
    "Medical & dental": "Medical",
    "Kids & activities": "Kids",
    "Transportation": "Transport",
    "Subscriptions": "Subs",
}


TIER_NAMES = {
    1: "1 - critical / secured",
    2: "2 - essential",
    3: "3 - required debt",
    4: "4 - important scheduled",
    5: "5 - discretionary",
}


def category_of(bill: dict) -> str:
    if bill.get("category"):
        return str(bill["category"])
    if bill["id"] in CATEGORY_OF:
        return CATEGORY_OF[bill["id"]]
    hay = f"{bill['id']} {bill.get('name', '')}".lower()
    for words, cat in KEYWORD_CATEGORY:
        if any(w in hay for w in words):
            return cat
    return "Miscellaneous"


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------

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


def frequency_label(b: dict) -> str:
    """How it recurs, in the terms a person would use, with the date that
    anchors it - a bare 'monthly' is exactly the ambiguity that makes a bill
    sheet unusable for planning."""
    f = b["frequency"]
    if f == "monthly":
        return f"Monthly, {ordinal(b['due_day'])}"
    if f == "semimonthly":
        return f"Twice monthly, {ordinal(b['due_day'])} and {ordinal(min(b['due_day'] + 15, 31))}"
    if f == "biweekly":
        return "Every 2 weeks (26 a year)"
    if f == "weekly":
        return "Weekly (52 a year)"
    if f == "quarterly":
        return f"Quarterly from {parse_date(b['due_date']).strftime('%-d %b %Y')}"
    if f == "annual":
        return f"Annual, {parse_date(b['due_date']).strftime('%-d %b')}"
    if f == "once":
        return f"One-off, {parse_date(b['due_date']).strftime('%-d %b %Y')}"
    return f


def basis_of(b: dict) -> str:
    """Which rule produced the figure used for this bill.

    Printing this is not decoration. Three different rules are in play, they
    disagree by thousands of dollars a year, and a reader who cannot see which
    one applied to a line cannot tell a fixed obligation from a deliberately
    conservative estimate.
    """
    if b.get("monthly_expected"):
        return "Seasonal profile"
    if not b.get("variable"):
        return "Fixed amount"
    if OCCURRENCES_PER_YEAR.get(b["frequency"], 0) >= FREQUENT_DRAW:
        return "Average (frequent draw)"
    if b.get("observed_max") is not None:
        return "Worst case (observed max)"
    return "Average (variable)"


def ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


@dataclass
class Row:
    """One bill, expanded across the window."""

    bill: dict
    category: str
    months: list[Decimal]
    hits: list[int]                     # occurrences per month
    next_due: date | None

    @property
    def id(self) -> str:
        return self.bill["id"]

    @property
    def name(self) -> str:
        return self.bill["name"]

    @property
    def active(self) -> bool:
        return bool(self.bill.get("active", True))

    @property
    def total(self) -> Decimal:
        return money(sum(self.months, money(0)))

    @property
    def per_month(self) -> Decimal:
        return money(self.total / MONTHS)

    @property
    def tier(self) -> int:
        return int(self.bill.get("priority_tier", 5))

    @property
    def occurrences(self) -> int:
        return sum(self.hits)

    @property
    def basis(self) -> str:
        return basis_of(self.bill)

    @property
    def typical(self) -> Decimal:
        """The window total at the bill's own stated amount.

        Only meaningful where the worst-case rule lifted the figure above that
        amount; for a seasonal profile the stated amount is one month's value
        and multiplying it out would be nonsense, so `padding` is confined to
        the worst-case bills and the rest report zero."""
        return money(money(self.bill["amount"]) * self.occurrences)

    @property
    def padding(self) -> Decimal:
        if self.basis != "Worst case (observed max)":
            return money(0)
        return money(self.total - self.typical)


@dataclass
class Model:
    today: date
    months: list[date]
    rows: list[Row]
    inactive: list[Row]
    source: Path

    # ---- aggregates -------------------------------------------------------

    @property
    def categories(self) -> list[str]:
        present = {r.category for r in self.rows}
        ordered = [c for c in CATEGORY_ORDER if c in present]
        return ordered + sorted(present - set(ordered))

    def in_category(self, cat: str) -> list[Row]:
        return sorted((r for r in self.rows if r.category == cat),
                      key=lambda r: -r.total)

    def category_months(self, cat: str) -> list[Decimal]:
        return [money(sum((r.months[i] for r in self.in_category(cat)), money(0)))
                for i in range(len(self.months))]

    def month_totals(self) -> list[Decimal]:
        return [money(sum((r.months[i] for r in self.rows), money(0)))
                for i in range(len(self.months))]

    @property
    def total(self) -> Decimal:
        return money(sum((r.total for r in self.rows), money(0)))

    @property
    def per_month(self) -> Decimal:
        return money(self.total / MONTHS)

    def ids_total(self, ids: set[str]) -> Decimal:
        return money(sum((r.total for r in self.rows if r.id in ids), money(0)))

    def cat_total(self, cat: str) -> Decimal:
        return money(sum((r.total for r in self.in_category(cat)), money(0)))

    @property
    def padded(self) -> list[Row]:
        return sorted((r for r in self.rows if r.padding > 0),
                      key=lambda r: -r.padding)

    @property
    def padding(self) -> Decimal:
        return money(sum((r.padding for r in self.rows), money(0)))

    def tier_totals(self) -> list[tuple[int, int, Decimal]]:
        out = []
        for t in sorted(TIER_NAMES):
            rs = [r for r in self.rows if r.tier == t]
            if rs:
                out.append((t, len(rs), money(sum((r.total for r in rs), money(0)))))
        return out


def build(bills_path: Path, first: date, n: int = MONTHS) -> Model:
    items = load_items(bills_path, "bills")
    months = month_starts(first, n)
    today = date.today()

    def expand(b: dict) -> Row:
        per_month, hits = [], []
        for ms in months:
            days = occurrences(b, ms, month_end(ms))
            hits.append(len(days))
            per_month.append(
                money(sum((expected_amount(b, d) for d in days), money(0))))
        return Row(bill=b, category=category_of(b), months=per_month, hits=hits,
                   next_due=next_due(b, today) if b.get("active", True) else None)

    rows = [expand(b) for b in items if b.get("active", True)]
    dead = [expand(b) for b in items if not b.get("active", True)]
    return Model(today=today, months=months, rows=rows, inactive=dead,
                 source=bills_path)


# ---------------------------------------------------------------------------
# the headline cross-cuts
# ---------------------------------------------------------------------------
#
# The categories answer "where does it go"; these answer the questions actually
# asked of a bill sheet. "Everything else" is a residual - the grand total less
# the named lines - so the block always adds up to the total and nothing can
# quietly fall out of it.

HEADLINE_IDS = {
    "Mortgage": {"mortgage"},
    "Utilities (all)": None,                    # filled from the category
    "Gas - heating (Elizabethtown)": {"gas-utility"},
    "Electric (JCP&L)": {"electric", "electric-deferred-2026-09"},
    "Water, sewer and trash": {"clinton-water", "sewer", "sewer-deferred-2026-09",
                               "trash", "trash-2027-q1"},
    "Phone, internet and TV": {"internet", "xfinity-mobile", "tmobile-lia"},
    "Fuel - vehicles": {"fuel"},
    "Groceries": {"groceries"},
}


def headline_rows(mod: Model) -> list[tuple[str, Decimal, Decimal, bool]]:
    """(label, per month, per year, is_subtotal_of_something_above)."""
    named: set[str] = set()
    out: list[tuple[str, Decimal, Decimal, bool]] = []

    def add(label: str, total: Decimal, *, sub: bool = False,
            claim: set[str] | None = None) -> None:
        if claim:
            named.update(claim)
        out.append((label, money(total / MONTHS), money(total), sub))

    add("Mortgage", mod.ids_total({"mortgage"}), claim={"mortgage"})

    util_ids = {r.id for r in mod.in_category("Utilities")}
    add("Utilities - all", mod.cat_total("Utilities"), claim=util_ids)
    grouped: set[str] = set()
    for label in ("Gas - heating (Elizabethtown)", "Electric (JCP&L)",
                  "Water, sewer and trash", "Phone, internet and TV"):
        ids = HEADLINE_IDS[label] & util_ids
        grouped |= ids
        add("   " + label, mod.ids_total(ids), sub=True)
    # Whatever is left inside utilities, so the indented block reconciles to
    # the line above it instead of quietly falling short of it.
    other = util_ids - grouped
    if other:
        names = ", ".join(sorted(r.name for r in mod.rows if r.id in other))
        add(f"   Other utilities ({names})", mod.ids_total(other), sub=True)

    add("Fuel - vehicles", mod.ids_total({"fuel"}), claim={"fuel"})
    add("Groceries", mod.ids_total({"groceries"}), claim={"groceries"})

    for cat in ("Transportation", "Insurance", "Loans",
                "Credit cards & store accounts", "Taxes", "Medical & dental",
                "Kids & activities", "Subscriptions"):
        ids = {r.id for r in mod.in_category(cat)} - named
        if not ids:
            continue
        label = {"Transportation": "Car payments (Kia, CCU)"}.get(cat, cat)
        add(label, mod.ids_total(ids), claim=ids)

    rest = {r.id for r in mod.rows} - named
    label = ("Everything else - miscellaneous" if rest else
             "Everything else - miscellaneous (none: every bill is named above)")
    add(label, mod.ids_total(rest), claim=rest)
    return out


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

class Doc(Layout):
    """The report layout, with this document's own footer."""

    FOOT_TEXT = ("OpenFIN monthly bill statement - every figure expanded from "
                 "the occurrence dates in bills.json")

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


def mshort(v: Decimal) -> str:
    """$1,234 - the monthly grid has twelve money columns and cents in all of
    them costs the width that keeps the columns apart."""
    v = money(v)
    return "-" if v == 0 else (f"-${abs(v):,.0f}" if v < 0 else f"${v:,.0f}")


def write_pdf(mod: Model, out: Path) -> int:
    first, last = mod.months[0], month_end(mod.months[-1])
    window = f"{first.strftime('%B %Y')} to {last.strftime('%B %Y')}"
    L = Doc("Monthly bills", f"{window} - {len(mod.rows)} active obligations")

    totals = mod.month_totals()
    hi = max(range(len(totals)), key=lambda i: totals[i])
    lo = min(range(len(totals)), key=lambda i: totals[i])

    L.cover([
        ("Window", f"{window} ({MONTHS} calendar months)"),
        ("Active bills", f"{len(mod.rows)}"
         + (f"  ({len(mod.inactive)} inactive, listed but not counted)"
            if mod.inactive else "")),
        ("Average month", m(mod.per_month)),
        ("Heaviest month",
         f"{m(totals[hi])} in {mod.months[hi].strftime('%B %Y')}"),
        ("Lightest month",
         f"{m(totals[lo])} in {mod.months[lo].strftime('%B %Y')}"),
        ("Twelve-month total", m(mod.total)),
        ("Of which worst-case margin",
         f"{m(money(mod.padding / MONTHS))} a month on {len(mod.padded)} "
         f"variable bills"),
        ("Source", f"{mod.source.name}, read {mod.today.isoformat()}"),
        ("Generated", datetime.now().strftime("%d %b %Y %H:%M")),
    ])

    # ---- 1. what it comes to ---------------------------------------------
    L.h1("1. What the bills come to")

    L.stat_row([
        ("Average month", m(mod.per_month), NAVY),
        ("Heaviest month", m(totals[hi]), RED),
        ("Lightest month", m(totals[lo]), GREEN),
        ("Twelve-month total", m(mod.total), INK),
    ])

    L.para(
        f"Across {window} the {len(mod.rows)} active obligations in "
        f"{mod.source.name} come to {m(mod.total)}, an average of "
        f"{m(mod.per_month)} a month. The months are not alike: "
        f"{mod.months[hi].strftime('%B')} carries {m(totals[hi])} and "
        f"{mod.months[lo].strftime('%B')} {m(totals[lo])}, a spread of "
        f"{m(totals[hi] - totals[lo])}. That spread is the point of the "
        f"month-by-month tables in sections 3 and 4 - a single monthly figure "
        f"is right on average and wrong in almost every individual month.")

    if mod.padding > 0:
        usual = money(mod.per_month - mod.padding / MONTHS)
        L.callout(
            f"{m(money(mod.padding / MONTHS))} of that month is deliberate "
            f"headroom, not a bill anyone sends",
            f"{len(mod.padded)} variable bills are carried at the highest "
            f"amount ever seen rather than the usual one, because a bill that "
            f"lands monthly or less often arrives once and has to be covered "
            f"at its worst. Culligan is the clearest case: {m(40.39)} most "
            f"months, {m(307.70)} when the salt delivery lands, and it is the "
            f"{m(307.70)} that is budgeted. At the usual amounts these same "
            f"bills would run {m(usual)} a month instead of "
            f"{m(mod.per_month)}. Neither figure is wrong - plan against "
            f"{m(mod.per_month)}, expect to spend nearer {m(usual)}. Section 5 "
            f"lists every bill this applies to.",
            tone=AMBER, band=BAND_HEAD)

    L.h2("The lines usually asked for")
    rows = []
    for label, per_mo, per_yr, sub in headline_rows(mod):
        share = (per_yr / mod.total * 100) if mod.total else Decimal(0)
        rows.append([label, m(per_mo), m(per_yr), f"{share:.1f}%"])
    rows.append(["TOTAL - everything", m(mod.per_month), m(mod.total), "100.0%"])

    heads = {i for i, (lab, *_rest) in enumerate(headline_rows(mod))
             if not lab.startswith("   ")}

    def hl_fill(i, row):
        if i == len(rows) - 1:
            return BAND_HEAD
        return None if i in heads else STRIPE

    def hl_ink(i, j, row):
        if i == len(rows) - 1:
            return NAVY
        return MUTED if i not in heads else INK

    L.table(
        [Col("", 250), Col("Per month", 100, "r", HELV_BOLD),
         Col("Per year", 110, "r"), Col("Share", 60, "r")],
        rows, row_fill=hl_fill, cell_color=hl_ink,
        note="Indented lines are part of the utilities line above them, not "
             "additions to it. \"Everything else\" is the residual - the grand "
             "total less every line named above - so the block adds to the "
             "total exactly.")

    L.h2("By category")
    crows = []
    for cat in mod.categories:
        tot = mod.cat_total(cat)
        crows.append([
            cat, str(len(mod.in_category(cat))), m(money(tot / MONTHS)), m(tot),
            f"{(tot / mod.total * 100) if mod.total else 0:.1f}%",
        ])
    crows.append(["TOTAL", str(len(mod.rows)), m(mod.per_month), m(mod.total),
                  "100.0%"])
    L.table(
        [Col("Category", 200), Col("Bills", 50, "r"),
         Col("Per month", 100, "r", HELV_BOLD), Col("Per year", 110, "r"),
         Col("Share", 60, "r")],
        crows,
        row_fill=lambda i, r: BAND_HEAD if i == len(crows) - 1 else None,
        cell_color=lambda i, j, r: NAVY if i == len(crows) - 1 else None)

    L.h2("By priority tier")
    trows = [[TIER_NAMES[t], str(n), m(money(tot / MONTHS)), m(tot),
              f"{(tot / mod.total * 100) if mod.total else 0:.1f}%"]
             for t, n, tot in mod.tier_totals()]
    L.table(
        [Col("Tier", 200), Col("Bills", 50, "r"),
         Col("Per month", 100, "r", HELV_BOLD), Col("Per year", 110, "r"),
         Col("Share", 60, "r")],
        trows,
        note="Tiers come from bills.json and drive deferral advice elsewhere in "
             "the system; they are reproduced here so the discretionary share "
             "of the month is visible at a glance.")

    # ---- 2. every bill ----------------------------------------------------
    L.h1("2. Every bill, by category")
    L.para(
        "Per month is the twelve-month total divided by twelve, so a quarterly "
        "or annual bill shows its true monthly cost rather than the amount of "
        "one instalment. Occurrences is how many times the bill actually lands "
        f"in the {MONTHS} months - the figure that makes a biweekly mortgage or "
        "a weekly shop add up correctly.")

    cols = [Col("Bill", 150, wrap_cells=True), Col("Amount", 62, "r"),
            Col("How it recurs", 132), Col("Next due", 58),
            Col("Occ.", 30, "r"), Col("Per month", 68, "r", HELV_BOLD),
            Col("Per year", 72, "r")]

    for cat in mod.categories:
        rs = mod.in_category(cat)
        tot = mod.cat_total(cat)
        L.h2(f"{cat}  -  {m(money(tot / MONTHS))} a month, {m(tot)} a year")
        body = [[
            r.name + (" *" if r.bill.get("variable") else ""),
            m(r.bill["amount"]),
            frequency_label(r.bill),
            r.next_due.strftime("%-d %b") if r.next_due else "-",
            str(r.occurrences),
            m(r.per_month),
            m(r.total),
        ] for r in rs]
        body.append([f"{cat} subtotal", "", "", "", "",
                     m(money(tot / MONTHS)), m(tot)])
        L.table(cols, body,
                row_fill=lambda i, row, n=len(body): (BAND_HEAD if i == n - 1
                                                      else None),
                cell_color=lambda i, j, row, n=len(body): (NAVY if i == n - 1
                                                           else None))

    L.para("A bill showing zero occurrences is still active in the file, but "
           "its own window closes or opens outside these twelve months - a "
           "loan in its final instalments, or one that a successor entry takes "
           "over. It is listed rather than hidden so the handover between the "
           "two entries is visible.", size=8, color=MUTED)
    L.para("* marks a bill whose amount moves month to month. For those, the "
           "figure shown is what the engine forecasts: a month-by-month profile "
           "where bills.json holds one, otherwise the observed maximum for "
           "bills that land monthly or less often, and the historical mean for "
           "frequent draws like the weekly shop, which average out.",
           size=8, color=MUTED)

    # ---- 3. month by month, by category -----------------------------------
    L.h1("3. Month by month, by category", size=A4_LANDSCAPE)
    L.para(
        "The shape of the year. Seasonal utilities, quarterly water, the annual "
        "sewer bill and the four lacrosse instalments all land in particular "
        "months, and this is where they are visible.")

    mcols = [Col("Category", 120)] + [
        Col(d.strftime("%b %y"), 47, "r") for d in mod.months
    ] + [Col("Year", 60, "r", HELV_BOLD)]

    mrows = []
    for cat in mod.categories:
        vals = mod.category_months(cat)
        mrows.append([cat] + [mshort(v) for v in vals] + [m(mod.cat_total(cat))])
    mrows.append(["TOTAL"] + [mshort(v) for v in totals] + [m(mod.total)])

    L.table(
        mcols, mrows, size=7.6,
        row_fill=lambda i, r: BAND_HEAD if i == len(mrows) - 1 else None,
        cell_color=lambda i, j, r: NAVY if i == len(mrows) - 1 else None,
        page_size=A4_LANDSCAPE,
        note="Rounded to the dollar to keep twelve money columns legible; the "
             "year column and the workbook carry the cents.")

    L.h2("Month totals against the average")
    brows = []
    for i, d in enumerate(mod.months):
        delta = totals[i] - mod.per_month
        brows.append([d.strftime("%B %Y"), m(totals[i]),
                      ("+" if delta >= 0 else "") + m(delta),
                      "heaviest" if i == hi else ("lightest" if i == lo else "")])
    L.table(
        [Col("Month", 120), Col("Total", 90, "r", HELV_BOLD),
         Col("vs average month", 110, "r"), Col("", 80)],
        brows, size=8.2,
        cell_color=lambda i, j, r: (RED if j == 2 and r[2].startswith("+")
                                    else (GREEN if j == 2 else None)),
        page_size=A4_LANDSCAPE)

    # ---- 4. month by month, every bill ------------------------------------
    L.h1("4. Month by month, every bill", size=A4_LANDSCAPE)
    dcols = [Col("Bill", 108, wrap_cells=True), Col("Category", 88)] + [
        Col(d.strftime("%b %y"), 43, "r") for d in mod.months
    ] + [Col("Year", 56, "r", HELV_BOLD)]

    drows = []
    for cat in mod.categories:
        for r in mod.in_category(cat):
            drows.append([r.name, SHORT_CATEGORY.get(cat, cat)]
                         + [mshort(v) for v in r.months]
                         + [m(r.total)])
    drows.append(["TOTAL", ""] + [mshort(v) for v in totals] + [m(mod.total)])

    L.table(
        dcols, drows, size=7.0,
        row_fill=lambda i, r: BAND_HEAD if i == len(drows) - 1 else None,
        cell_color=lambda i, j, r: NAVY if i == len(drows) - 1 else None,
        page_size=A4_LANDSCAPE,
        note="A dash means the bill does not land in that month at all.")

    # ---- 5. what is not counted -------------------------------------------
    L.h1("5. What is not counted, and what to check")

    if mod.inactive:
        L.h2("Inactive - held in the file, excluded from every figure above")
        L.table(
            [Col("Bill", 150), Col("Amount", 70, "r"), Col("How it recurred", 120),
             Col("Why it is inactive", 170, wrap_cells=True)],
            [[r.name, m(r.bill["amount"]), frequency_label(r.bill),
              r.bill.get("note") or "marked inactive"] for r in mod.inactive],
            size=8.2)

    flagged = [r for r in mod.rows if r.bill.get("needs_review")]
    if flagged:
        L.h2("Flagged for review")
        L.table(
            [Col("Bill", 150), Col("Per month", 80, "r"),
             Col("What is flagged", 280, wrap_cells=True)],
            [[r.name, m(r.per_month), r.bill.get("note") or "needs_review is set"]
             for r in flagged], size=8.2)

    low = [r for r in mod.rows
           if r.bill.get("confidence") in {"low", "medium"}
           or (r.bill.get("variable") and not r.bill.get("monthly_expected"))]
    if low:
        L.h2("Amounts that are estimates, not fixed figures")
        L.table(
            [Col("Bill", 150), Col("Per month", 80, "r"), Col("Confidence", 70),
             Col("Range seen in the statements", 190, wrap_cells=True)],
            [[r.name, m(r.per_month), r.bill.get("confidence", "high"),
              (f"{m(r.bill.get('observed_min', r.bill['amount']))} - "
               f"{m(r.bill['observed_max'])}"
               if r.bill.get("observed_max") is not None else
               (r.bill.get("note") or "no range recorded"))]
             for r in low], size=8.2)

    if mod.padded:
        L.h2("Where the figure used is the worst case, not the usual amount")
        L.table(
            [Col("Bill", 128, wrap_cells=True), Col("Usual", 62, "r"),
             Col("Worst case", 70, "r"), Col("Occ.", 28, "r"),
             Col("Per month used", 74, "r", HELV_BOLD),
             Col("At usual", 66, "r"), Col("Difference a year", 76, "r")],
            [[r.name, m(r.bill["amount"]), m(r.bill["observed_max"]),
              str(r.occurrences), m(r.per_month),
              m(money(r.typical / MONTHS)), m(r.padding)]
             for r in mod.padded]
            + [["TOTAL margin", "", "", "", m(money(mod.padding / MONTHS)),
                "", m(mod.padding)]],
            size=8.2,
            row_fill=lambda i, row, n=len(mod.padded) + 1: (BAND_HEAD
                                                            if i == n - 1 else None),
            cell_color=lambda i, j, row, n=len(mod.padded) + 1: (NAVY
                                                                 if i == n - 1 else None),
            note="This rule is the forecasting engine's, not this report's, and "
                 "it is applied only to bills landing monthly or less often. "
                 "Frequent draws such as the weekly shop use the historical "
                 "mean instead, because applying a worst week to every week of "
                 "the year compounds into a figure no household ever spends.")

    L.h2("Method")
    L.bullet("Every figure is built by expanding each bill's real occurrence "
             "dates across the twelve calendar months and adding up what falls "
             "in each one. Nothing is annualised by multiplying a weekly or "
             "biweekly amount by a rounded factor.")
    L.bullet("A due date that falls on a weekend is counted on that date. "
             "Nothing is shifted to the next working day, because shifting it "
             "would make the month look lighter than it is.")
    L.bullet("Seasonal bills use the month-by-month profile recorded in "
             "bills.json where there is one, so gas is heavy in winter and "
             "electric in summer rather than flat all year.")
    L.bullet("This report is a statement of obligations only. It carries no "
             "balance, no income and no forecast - the cash-flow audit is the "
             "report for those.")

    return L.save(out)


# ---------------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------------

MONEY = "$#,##0.00"
MONEY0 = "$#,##0"
PCT = "0.0%"


def write_xlsx(mod: Model, out: Path) -> None:
    wb = X.Workbook()
    F = X.Formula

    title = wb.style(bold=True, size=18, color="0F2F52")
    sub = wb.style(size=10, color="6B7280")
    head = wb.style(bold=True, color="FFFFFF", fill="16365C", align="center",
                    valign="center", wrap=True)
    head_l = wb.style(bold=True, color="FFFFFF", fill="16365C", valign="center",
                      wrap=True)
    sect = wb.style(bold=True, size=12, color="0F2F52", bottom="BFC5CC")
    txt = wb.style()
    txt_i = wb.style(indent=1, color="6B7280")
    note = wb.style(size=9, color="6B7280", wrap=True, valign="top")
    bold = wb.style(bold=True)
    cash = wb.style(fmt=MONEY)
    cash_b = wb.style(bold=True, fmt=MONEY)
    cash_i = wb.style(fmt=MONEY, color="6B7280")
    cash0 = wb.style(fmt=MONEY0)
    pct = wb.style(fmt=PCT)
    pct_b = wb.style(bold=True, fmt=PCT)
    tot_l = wb.style(bold=True, fill="DCE6F1", top="16365C")
    tot_m = wb.style(bold=True, fmt=MONEY, fill="DCE6F1", top="16365C")
    tot_p = wb.style(bold=True, fmt=PCT, fill="DCE6F1", top="16365C")
    cat_l = wb.style(bold=True, fill="EEF2F7")
    cat_m = wb.style(bold=True, fmt=MONEY, fill="EEF2F7")
    num = wb.style(align="center")
    dt = wb.style(align="center", color="6B7280")

    totals = mod.month_totals()
    window = (f"{mod.months[0].strftime('%B %Y')} to "
              f"{month_end(mod.months[-1]).strftime('%B %Y')}")

    # ---- Summary ----------------------------------------------------------
    s = wb.sheet("Summary")
    for c, w in enumerate([34, 15, 15, 11, 44], start=1):
        s.width(c, w)
    s.append(["Monthly bills"], title)
    s.height(1, 26)
    s.append([f"{window} - {len(mod.rows)} active obligations from "
              f"{mod.source.name}, read {mod.today.isoformat()}"], sub)
    s.append([f"Generated {datetime.now().strftime('%d %b %Y %H:%M')}. "
              f"Per month = twelve-month total / 12."], sub)
    s.blank()

    s.append(["Headline"], sect)
    hdr = s.append(["", "Per month", "Per year", "Share", ""],
                   [head_l, head, head, head, head])
    s.height(hdr, 28)
    first_data = hdr + 1
    for label, per_mo, per_yr, is_sub in headline_rows(mod):
        s.append([label.strip() if is_sub else label, per_mo, per_yr,
                  float(per_yr / mod.total) if mod.total else 0],
                 [txt_i if is_sub else bold,
                  cash_i if is_sub else cash_b,
                  cash_i if is_sub else cash, pct])
    last_data = s.cursor
    s.append(["TOTAL - everything", mod.per_month, mod.total, 1.0],
             [tot_l, tot_m, tot_m, tot_p])
    if mod.padding > 0:
        s.append([f"of which worst-case margin on {len(mod.padded)} variable "
                  f"bills", money(mod.padding / MONTHS), mod.padding,
                  float(mod.padding / mod.total) if mod.total else 0],
                 [txt_i, cash_i, cash_i, pct])
        s.append(["the same bills at their usual amounts",
                  money(mod.per_month - mod.padding / MONTHS),
                  money(mod.total - mod.padding), ""],
                 [txt_i, cash_i, cash_i, pct])
    s.append(["Indented lines are part of the utilities line above them, not "
              "additions to it. \"Everything else\" is the residual, so the "
              "block adds to the total exactly."], note)
    s.blank()

    s.append(["By category"], sect)
    hdr = s.append(["Category", "Bills", "Per month", "Per year", "Share"],
                   [head_l, head, head, head, head])
    s.height(hdr, 28)
    cat_first = hdr + 1
    for cat in mod.categories:
        tot = mod.cat_total(cat)
        s.append([cat, len(mod.in_category(cat)), money(tot / MONTHS), tot,
                  float(tot / mod.total) if mod.total else 0],
                 [txt, num, cash_b, cash, pct])
    cat_last = s.cursor
    r = s.cursor + 1
    s.append(["TOTAL", F(f"SUM(B{cat_first}:B{cat_last})", len(mod.rows)),
              F(f"SUM(C{cat_first}:C{cat_last})", float(mod.per_month)),
              F(f"SUM(D{cat_first}:D{cat_last})", float(mod.total)), 1.0],
             [tot_l, wb.style(bold=True, align="center", fill="DCE6F1",
                              top="16365C"), tot_m, tot_m, tot_p])
    s.blank()

    s.append(["By priority tier"], sect)
    hdr = s.append(["Tier", "Bills", "Per month", "Per year", "Share"],
                   [head_l, head, head, head, head])
    s.height(hdr, 28)
    for t, n, tot in mod.tier_totals():
        s.append([TIER_NAMES[t], n, money(tot / MONTHS), tot,
                  float(tot / mod.total) if mod.total else 0],
                 [txt, num, cash_b, cash, pct])
    s.blank()

    s.append(["Month by month"], sect)
    hdr = s.append(["Month", "Total", "vs average month", "", ""],
                   [head_l, head, head, head, head])
    s.height(hdr, 28)
    for i, d in enumerate(mod.months):
        s.append([d.strftime("%B %Y"), totals[i], totals[i] - mod.per_month,
                  "heaviest" if totals[i] == max(totals) else
                  ("lightest" if totals[i] == min(totals) else "")],
                 [txt, cash_b, cash, dt])
    s.freeze(rows=3)

    # ---- Bills ------------------------------------------------------------
    b = wb.sheet("Bills")
    widths = [30, 26, 12, 26, 24, 12, 11, 14, 14, 16, 7, 9, 9, 9, 11, 13, 13,
              46]
    for c, w in enumerate(widths, start=1):
        b.width(c, w)
    b.append(["Every active bill"], title)
    b.height(1, 24)
    b.append([f"{window}. Per month = twelve-month total / 12; occurrences is "
              f"how many times the bill actually lands in the window."], sub)
    hdr = b.append(
        ["Bill", "Category", "Amount", "How it recurs", "Figure used",
         "Next due", "Occurrences", "Per month", "Per year",
         "Worst-case margin", "Tier", "Autopay", "Variable", "Secured",
         "Deferrable", "Confidence", "Observed range", "Note"],
        [head_l, head_l, head, head_l, head_l, head, head, head, head, head,
         head, head, head, head, head, head, head_l, head_l])
    b.height(hdr, 42)
    first = hdr + 1
    for cat in mod.categories:
        for r in mod.in_category(cat):
            bill = r.bill
            rng = ("" if bill.get("observed_max") is None else
                   f"{m(bill.get('observed_min', bill['amount']))} - "
                   f"{m(bill['observed_max'])}")
            b.append([
                r.name, cat, money(bill["amount"]), frequency_label(bill),
                r.basis, r.next_due.isoformat() if r.next_due else "",
                r.occurrences, r.per_month, r.total,
                r.padding if r.padding else None, r.tier,
                "yes" if bill.get("autopay") else "",
                "yes" if bill.get("variable") else "",
                "yes" if bill.get("secured") else "",
                "yes" if bill.get("deferrable") else "",
                bill.get("confidence", "high"), rng, bill.get("note") or "",
            ], [txt, txt, cash, txt, txt, dt, num, cash_b, cash, cash_i, num,
                dt, dt, dt, dt, dt, txt, note])
    last = b.cursor
    b.append(["TOTAL", "", "", "", "", "",
              F(f"SUM(G{first}:G{last})", sum(r.occurrences for r in mod.rows)),
              F(f"SUM(H{first}:H{last})", float(mod.per_month)),
              F(f"SUM(I{first}:I{last})", float(mod.total)),
              F(f"SUM(J{first}:J{last})", float(mod.padding))],
             [tot_l, tot_l, tot_l, tot_l, tot_l, tot_l,
              wb.style(bold=True, align="center", fill="DCE6F1", top="16365C"),
              tot_m, tot_m, tot_m])
    b.freeze(rows=hdr, cols=1)
    b.autofilter(hdr, 1, last, len(widths))

    # ---- Monthly detail ---------------------------------------------------
    d = wb.sheet("By month")
    d.width(1, 30)
    d.width(2, 26)
    for c in range(3, 3 + len(mod.months)):
        d.width(c, 11)
    d.width(3 + len(mod.months), 13)
    d.append(["Every bill, month by month"], title)
    d.height(1, 24)
    d.append([f"{window}. A blank means the bill does not land in that month."],
             sub)
    hdr = d.append(["Bill", "Category"]
                   + [x.strftime("%b %Y") for x in mod.months] + ["Year"],
                   [head_l, head_l] + [head] * len(mod.months) + [head])
    d.height(hdr, 28)
    first = hdr + 1
    last_col = 2 + len(mod.months)
    for cat in mod.categories:
        for r in mod.in_category(cat):
            vals = [(v if v else None) for v in r.months]
            d.append([r.name, cat] + vals
                     + [F(f"SUM({X.span(d.cursor + 1, 3, d.cursor + 1, last_col)})",
                          float(r.total))],
                     [txt, txt] + [cash0] * len(mod.months) + [cash_b])
    last = d.cursor
    cells = ["TOTAL", ""]
    styles = [tot_l, tot_l]
    for c in range(3, last_col + 2):
        col = X.col_letter(c)
        cells.append(F(f"SUM({col}{first}:{col}{last})",
                       float(totals[c - 3]) if c <= last_col else float(mod.total)))
        styles.append(tot_m)
    d.append(cells, styles)
    d.freeze(rows=hdr, cols=2)

    # ---- Category by month ------------------------------------------------
    c_ = wb.sheet("Category by month")
    c_.width(1, 30)
    for c in range(2, 2 + len(mod.months)):
        c_.width(c, 12)
    c_.width(2 + len(mod.months), 14)
    c_.width(3 + len(mod.months), 10)
    c_.append(["Category by month"], title)
    c_.height(1, 24)
    c_.append([f"{window}."], sub)
    hdr = c_.append(["Category"] + [x.strftime("%b %Y") for x in mod.months]
                    + ["Year", "Share"],
                    [head_l] + [head] * len(mod.months) + [head, head])
    c_.height(hdr, 28)
    first = hdr + 1
    last_col = 1 + len(mod.months)
    for cat in mod.categories:
        vals = mod.category_months(cat)
        rnum = c_.cursor + 1
        c_.append([cat] + [(v if v else None) for v in vals]
                  + [F(f"SUM({X.span(rnum, 2, rnum, last_col)})",
                       float(mod.cat_total(cat))),
                     float(mod.cat_total(cat) / mod.total) if mod.total else 0],
                  [cat_l] + [cash] * len(mod.months) + [cat_m, pct_b])
    last = c_.cursor
    cells, styles = ["TOTAL"], [tot_l]
    for c in range(2, last_col + 2):
        col = X.col_letter(c)
        cells.append(F(f"SUM({col}{first}:{col}{last})",
                       float(totals[c - 2]) if c <= last_col else float(mod.total)))
        styles.append(tot_m)
    cells.append(1.0)
    styles.append(tot_p)
    c_.append(cells, styles)
    c_.freeze(rows=hdr, cols=1)

    # ---- Excluded and flagged --------------------------------------------
    n = wb.sheet("Excluded and flagged")
    for c, w in enumerate([30, 14, 26, 14, 70], start=1):
        n.width(c, w)
    n.append(["What is not counted, and what to check"], title)
    n.height(1, 24)

    n.blank()
    n.append(["Inactive - held in bills.json, excluded from every figure"], sect)
    hdr = n.append(["Bill", "Amount", "How it recurred", "", "Why"],
                   [head_l, head, head_l, head, head_l])
    n.height(hdr, 24)
    if mod.inactive:
        for r in mod.inactive:
            n.append([r.name, money(r.bill["amount"]), frequency_label(r.bill),
                      "", r.bill.get("note") or "marked inactive"],
                     [txt, cash, txt, txt, note])
    else:
        n.append(["None"], txt)

    n.blank()
    n.append(["Flagged for review in bills.json"], sect)
    hdr = n.append(["Bill", "Per month", "", "", "What is flagged"],
                   [head_l, head, head, head, head_l])
    n.height(hdr, 24)
    flagged = [r for r in mod.rows if r.bill.get("needs_review")]
    if flagged:
        for r in flagged:
            n.append([r.name, r.per_month, "", "",
                      r.bill.get("note") or "needs_review is set"],
                     [txt, cash_b, txt, txt, note])
    else:
        n.append(["None"], txt)

    n.blank()
    n.append(["Amounts that are estimates, not fixed figures"], sect)
    hdr = n.append(["Bill", "Per month", "Confidence", "Range seen", "Note"],
                   [head_l, head, head, head, head_l])
    n.height(hdr, 24)
    est = [r for r in mod.rows
           if r.bill.get("confidence") in {"low", "medium"}
           or (r.bill.get("variable") and not r.bill.get("monthly_expected"))]
    for r in est:
        rng = ("" if r.bill.get("observed_max") is None else
               f"{m(r.bill.get('observed_min', r.bill['amount']))} - "
               f"{m(r.bill['observed_max'])}")
        n.append([r.name, r.per_month, r.bill.get("confidence", "high"), rng,
                  r.bill.get("note") or ""], [txt, cash_b, dt, txt, note])

    n.blank()
    n.append(["Where the figure used is the worst case, not the usual amount"],
             sect)
    hdr = n.append(["Bill", "Usual amount", "Worst case used",
                    "Per month at usual", "Difference a year"],
                   [head_l, head, head, head, head])
    n.height(hdr, 24)
    for r in mod.padded:
        n.append([r.name, money(r.bill["amount"]), money(r.bill["observed_max"]),
                  money(r.typical / MONTHS), r.padding],
                 [txt, cash, cash_b, cash, cash_b])
    if mod.padded:
        n.append(["TOTAL", "", "", money(mod.padding / MONTHS), mod.padding],
                 [tot_l, tot_l, tot_l, tot_m, tot_m])

    n.blank()
    n.append(["Method"], sect)
    for line in [
        "Every figure is built by expanding each bill's real occurrence dates "
        "across the twelve calendar months and adding up what falls in each "
        "one - no weekly or biweekly amount is annualised by a rounded factor.",
        "A due date falling on a weekend is counted on that date; nothing is "
        "shifted to the next working day.",
        "Seasonal bills use the month-by-month profile recorded in bills.json "
        "where there is one, so gas is heavy in winter and electric in summer.",
        "Variable bills without a profile are forecast at the observed maximum "
        "when they land monthly or less often, and at the historical mean for "
        "frequent draws such as the weekly shop, which average out.",
        "This workbook is a statement of obligations only: no balance, no "
        "income and no forecast.",
    ]:
        n.append([line], note)
        n.merge(n.cursor, 1, n.cursor, 5)
        n.height(n.cursor, 30)

    wb.save(out, title="OpenFIN monthly bills")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Monthly bill statement as PDF and Excel")
    ap.add_argument("--bills", default=str(ROOT / "bills.json"))
    ap.add_argument("--out-pdf", default=str(ROOT / "monthly-bills.pdf"))
    ap.add_argument("--out-xlsx", default=str(ROOT / "monthly-bills.xlsx"))
    ap.add_argument("--start", help="first month, YYYY-MM; defaults to this month")
    ap.add_argument("--months", type=int, default=MONTHS)
    args = ap.parse_args(argv)

    if args.start:
        y, mo = (int(x) for x in args.start.split("-")[:2])
        first = date(y, mo, 1)
    else:
        today = date.today()
        first = date(today.year, today.month, 1)

    mod = build(Path(args.bills), first, args.months)

    pdf_path, xlsx_path = Path(args.out_pdf), Path(args.out_xlsx)
    pages = write_pdf(mod, pdf_path)
    write_xlsx(mod, xlsx_path)

    print(f"{pdf_path.name}: {pages} pages")
    print(f"{xlsx_path.name}: 5 sheets")
    print(f"{len(mod.rows)} active bills, {m(mod.per_month)} a month, "
          f"{m(mod.total)} over {args.months} months")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
