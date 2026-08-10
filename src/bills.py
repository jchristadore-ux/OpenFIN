"""Bill and income occurrence maths.

Everything here is about one question: on which calendar dates does a given
recurring item fall? The projection module asks it for every day in the
window, so it has to be cheap, and it has to be exactly right — a bill landing
on the wrong day moves the low point.

Money is Decimal throughout. Floats are never used for dollar amounts; 0.1 +
0.2 is not 0.3 and a cash-flow projection is a long chain of additions.
"""

from __future__ import annotations

import calendar
import json
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

CENT = Decimal("0.01")

VALID_FREQUENCIES = {
    "monthly",
    "weekly",
    "biweekly",
    "semimonthly",
    "annual",
    "once",
}


class BillError(ValueError):
    """An entry in bills.json or income.json is not usable."""


# --------------------------------------------------------------------------
# money
# --------------------------------------------------------------------------

def money(value) -> Decimal:
    """Coerce anything numeric to a 2dp Decimal, rounding half up.

    Accepts str, int, float, or Decimal. Floats are converted via str() so
    that 236.0 does not become 235.99999999999997 on the way in.
    """
    if isinstance(value, Decimal):
        d = value
    elif isinstance(value, float):
        d = Decimal(str(value))
    else:
        d = Decimal(str(value))
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def fmt_money(value: Decimal) -> str:
    """$1,234.56 / -$1,234.56 — the form used in the SMS."""
    d = money(value)
    sign = "-" if d < 0 else ""
    return f"{sign}${abs(d):,.2f}"


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------

def _unwrap(data, key: str):
    """Accept either a bare JSON array or {"_comments": [...], "<key>": [...]}.

    The wrapper exists so the file can carry its own documentation at the top;
    a plain array still works for anyone who prefers it.
    """
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get(key, [])
    raise BillError(f"expected a list or an object with a {key!r} key")


def load_items(path: Path, key: str) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        items = _unwrap(json.load(fh), key)
    for item in items:
        validate(item)
    return items


def save_items(path: Path, key: str, items: list[dict]) -> None:
    """Write back, preserving the documentation wrapper if the file had one."""
    existing = {}
    if path.exists():
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if isinstance(loaded, dict):
            existing = {k: v for k, v in loaded.items() if k.startswith("_")}

    payload = {**existing, key: items} if existing else items
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def validate(item: dict) -> None:
    """Reject an entry the occurrence maths could not honestly interpret.

    Better a loud failure at load time than a bill that silently never fires.
    """
    name = item.get("name") or item.get("id") or "<unnamed>"
    freq = item.get("frequency")

    if not item.get("id"):
        raise BillError(f"{name}: missing id")
    if freq not in VALID_FREQUENCIES:
        raise BillError(f"{name}: frequency {freq!r} is not one of {sorted(VALID_FREQUENCIES)}")

    try:
        money(item.get("amount"))
    except Exception as exc:                                    # noqa: BLE001
        raise BillError(f"{name}: amount {item.get('amount')!r} is not a number") from exc

    if freq in {"monthly", "semimonthly"}:
        day = item.get("due_day")
        if not isinstance(day, int) or not 1 <= day <= 31:
            raise BillError(f"{name}: {freq} needs due_day between 1 and 31, got {day!r}")

    if freq in {"weekly", "biweekly"}:
        if not item.get("anchor_date"):
            raise BillError(f"{name}: {freq} needs an anchor_date")
        parse_date(item["anchor_date"], f"{name}.anchor_date")

    if freq in {"once", "annual"}:
        if not item.get("due_date"):
            raise BillError(f"{name}: {freq} needs a due_date")
        parse_date(item["due_date"], f"{name}.due_date")


def parse_date(value: str, label: str = "date") -> date:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError as exc:
        raise BillError(f"{label}: {value!r} is not an ISO date (YYYY-MM-DD)") from exc


# --------------------------------------------------------------------------
# occurrence maths
# --------------------------------------------------------------------------

def clamp_day(year: int, month: int, day: int) -> date:
    """Day-of-month, clamped to the last day of a short month.

    A bill due on the 31st falls on 28 February in a normal year and 29 in a
    leap year. This is the rule stated in the data model, and it is the one
    most billers actually follow.
    """
    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(day, last))


def _month_iter(start: date, end: date):
    """Yield (year, month) for every month touched by the window."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def occurrences(item: dict, start: date, end: date) -> list[date]:
    """Every date in [start, end] on which this item falls.

    Weekend handling: none. A due date that lands on a Saturday is counted on
    the Saturday. Shifting it to Monday would make the projection optimistic
    on exactly the days that matter most.
    """
    if not item.get("active", True):
        return []
    if start > end:
        return []

    freq = item["frequency"]
    hits: list[date] = []

    if freq == "monthly":
        for y, m in _month_iter(start, end):
            hits.append(clamp_day(y, m, item["due_day"]))

    elif freq == "semimonthly":
        # Two payments a month: the due day, and fifteen days later. Both
        # clamp to the end of the month, so a 20th/35th pair becomes 20th and
        # last-day rather than spilling into the next month.
        for y, m in _month_iter(start, end):
            first = clamp_day(y, m, item["due_day"])
            second = clamp_day(y, m, item["due_day"] + 15)
            hits.append(first)
            if second != first:
                hits.append(second)

    elif freq in {"weekly", "biweekly"}:
        step = 7 if freq == "weekly" else 14
        anchor = parse_date(item["anchor_date"])
        # Walk forward or backward from the anchor onto the window in one
        # step rather than looping — anchors can be years away.
        delta = (start - anchor).days
        n = delta // step
        cursor = anchor + timedelta(days=n * step)
        while cursor < start:
            cursor += timedelta(days=step)
        while cursor <= end:
            hits.append(cursor)
            cursor += timedelta(days=step)

    elif freq == "annual":
        due = parse_date(item["due_date"])
        for year in range(start.year, end.year + 1):
            hits.append(clamp_day(year, due.month, due.day))

    elif freq == "once":
        hits.append(parse_date(item["due_date"]))

    else:                                                       # pragma: no cover
        raise BillError(f"unhandled frequency {freq!r}")

    return sorted({d for d in hits if start <= d <= end})


def next_due(item: dict, on_or_after: date, horizon_days: int = 400) -> date | None:
    """The item's next occurrence, or None if it has none in the horizon."""
    hits = occurrences(item, on_or_after, on_or_after + timedelta(days=horizon_days))
    return hits[0] if hits else None


def due_on(items: list[dict], day: date) -> list[dict]:
    """Every active item falling exactly on `day`."""
    return [i for i in items if occurrences(i, day, day)]
