"""Add an income source from the app.

An overstated income is the most dangerous edit this system accepts. Every other
mistake makes the forecast pessimistic, which costs nothing but nerves; an
income that is not really coming raises safe-to-spend, lowers the projected
curve's floor, and silences the risks that would otherwise have warned about
exactly the day it fails to arrive. This module is written on that basis.

  * The app sends a name, an amount, how often, and one date. Everything else —
    the id, the frequency plumbing, the note — is derived here, so a phone can
    never write a field it does not understand.
  * The date means what the frequency needs it to mean, and only one of the
    three date fields is ever set. A monthly income takes its day of the month
    from that date; a weekly one anchors to it; a one-off falls on it.
  * A new entry is marked `needs_review`. It came from a phone rather than from
    a statement, so it has not been reconciled against anything yet, and the
    daily email is where that gets noticed.
  * Nothing is overwritten. Adding is only ever an append; removing one is a
    deactivation handled by apply_edits, same as a bill.

    python src/add_income.py '{"name":"Side work","amount":"250",
                               "frequency":"weekly","date":"2026-08-21"}'
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from bills import BillError, load_items, money, parse_date, save_items, validate

ROOT = Path(__file__).resolve().parent.parent
INCOME = ROOT / "income.json"

# Which date field each frequency actually reads. The app asks one question —
# "when is it / when is the next one" — and this decides what that answer means.
DATE_FIELD = {
    "once": "due_date",
    "annual": "due_date",
    "quarterly": "due_date",
    "weekly": "anchor_date",
    "biweekly": "anchor_date",
    "semimonthly": "due_day",
    "monthly": "due_day",
}

# No household in this project earns this in one deposit. Treat it as a typo —
# a stray zero here would swamp every figure the app shows.
ABSURD = Decimal("100000")


class IncomeError(ValueError):
    """The income could not be added honestly."""


def slug(name: str, taken: set[str]) -> str:
    """A stable id from the name, unique against what already exists.

    Ids are permanent: notes, history and any future statement matching are
    keyed to them, so a collision has to become a new id rather than silently
    join two different incomes together.
    """
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "income"
    if not base[0].isalnum():
        base = "i" + base
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def parse(raw: str | dict) -> dict:
    """Read what the app sent. Rejects loudly; never guesses a missing field."""
    data = raw if isinstance(raw, dict) else None
    if data is None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IncomeError(f"income is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise IncomeError("income must be a single JSON object")

    name = " ".join(str(data.get("name", "")).split())[:60]
    if len(name) < 2:
        raise IncomeError("give the income a name")

    try:
        amount = money(str(data.get("amount", "")).replace("$", "").replace(",", ""))
    except (InvalidOperation, ValueError) as exc:
        raise IncomeError(f"{data.get('amount')!r} is not an amount") from exc
    if amount <= 0:
        raise IncomeError("an income has to be more than zero")
    if amount > ABSURD:
        raise IncomeError(f"${amount:,.2f} is implausibly large; refusing")

    freq = str(data.get("frequency", "")).strip().lower()
    if freq not in DATE_FIELD:
        raise IncomeError(
            f"{freq!r} is not a frequency. Use one of {', '.join(sorted(DATE_FIELD))}"
        )

    when = parse_date(str(data.get("date", "")), "date")

    return {"name": name, "amount": amount, "frequency": freq, "date": when}


def build(spec: dict, taken: set[str], now: datetime) -> dict:
    """Turn the app's four answers into an entry the calendar can read."""
    field = DATE_FIELD[spec["frequency"]]
    item = {
        "id": slug(spec["name"], taken),
        "name": spec["name"],
        "amount": float(spec["amount"]),
        "frequency": spec["frequency"],
        "due_day": spec["date"].day if field == "due_day" else None,
        "due_date": spec["date"].isoformat() if field == "due_date" else None,
        "anchor_date": spec["date"].isoformat() if field == "anchor_date" else None,
        "match_keywords": [],
        "active": True,
        # It came from a phone, not from a statement. Nothing has confirmed this
        # money arrives, and the forecast is about to start counting on it.
        "needs_review": True,
        "note": (
            f"Added in the app {now:%d %b %Y}: {spec['frequency']}, "
            f"{'from' if field == 'anchor_date' else 'first on'} "
            f"{spec['date']:%d %b %Y}. Not yet seen on a statement."
        ),
        "last_updated": now.isoformat(),
        "source": "app",
    }
    validate(item)
    return item


def add(spec: dict, path: Path = INCOME, *, now: datetime | None = None) -> dict:
    """Append one income source. Returns the entry as written."""
    now = now or datetime.now(ZoneInfo("America/New_York"))
    items = load_items(path, "income")

    # Adding the same thing twice is a double-counted paycheque, and the effect
    # is invisible: the forecast simply gets better. Cheap to catch here.
    for existing in items:
        if (
            existing.get("active", True)
            and str(existing.get("name", "")).lower() == spec["name"].lower()
            and existing.get("frequency") == spec["frequency"]
            and money(existing.get("amount", 0)) == spec["amount"]
        ):
            raise IncomeError(
                f"{spec['name']} already exists at {spec['frequency']} "
                f"${spec['amount']:,.2f}. Adding it again would count it twice."
            )

    item = build(spec, {b["id"] for b in items}, now)
    items.append(item)
    save_items(path, "income", items)
    return item


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: add_income.py '<json>'", file=sys.stderr)
        return 2
    try:
        item = add(parse(argv[1]))
    except (IncomeError, BillError) as exc:
        print(f"INCOME REJECTED: {exc}", file=sys.stderr)
        return 1
    print(
        f"Added {item['name']}: ${item['amount']:,.2f} {item['frequency']} "
        f"(id {item['id']}). Flagged for review."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
