"""Apply bill edits made in the app.

Edits arrive as JSON from the Worker, already shape-validated there. This module
does not trust that: the Worker is a different codebase on a different host, and
the thing being written is the household's financial model. Everything is
re-checked here, and anything that fails is rejected loudly rather than applied
partially.

Three rules, in order of how much they matter:

  * Nothing is ever deleted. Deactivating sets active=false and keeps the entry,
    because the history is keyed to the id.
  * A large change is applied but flagged. A tenfold typo is far more likely
    than a tenfold renegotiation, and the daily email is where that gets caught.
  * Either every edit applies or none do. A half-written bills.json is worse
    than a rejected one.

    python src/apply_edits.py '[{"id":"netflix","amount":"21.31"}]'
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from bills import BillError, load_items, money, save_items, validate

ROOT = Path(__file__).resolve().parent.parent
BILLS = ROOT / "bills.json"

# A change beyond this ratio is applied but flagged for review.
REVIEW_RATIO = Decimal("0.40")
# Nothing legitimate in this household is this big; treat it as a typo.
ABSURD = Decimal("10000")


class EditError(ValueError):
    """An edit could not be applied honestly."""


def parse_edits(raw: str) -> list[dict]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise EditError(f"edits are not valid JSON: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("edits", [])
    if not isinstance(data, list) or not data:
        raise EditError("edits must be a non-empty list")
    if len(data) > 60:
        raise EditError(f"{len(data)} edits at once looks wrong; refusing")
    return data


def apply(edits: list[dict], path: Path = BILLS, *, now: datetime | None = None) -> list[str]:
    """Apply every edit, or none. Returns a human-readable change log."""
    now = now or datetime.now(ZoneInfo("America/New_York"))
    items = load_items(path, "bills")
    by_id = {b["id"]: b for b in items}

    staged: list[tuple[dict, dict, list[str]]] = []

    for edit in edits:
        bid = str(edit.get("id", ""))
        target = by_id.get(bid)
        if target is None:
            raise EditError(f"no bill with id {bid!r}")

        proposed = dict(target)
        notes: list[str] = []
        flag = False

        if "amount" in edit:
            try:
                new = money(str(edit["amount"]).replace("$", "").replace(",", ""))
            except (InvalidOperation, ValueError) as exc:
                raise EditError(f"{bid}: {edit['amount']!r} is not an amount") from exc
            if new < 0:
                raise EditError(f"{bid}: amount cannot be negative")
            if new > ABSURD:
                raise EditError(f"{bid}: ${new:,.2f} is implausibly large; refusing")
            old = money(target.get("amount", 0))
            if new != old:
                notes.append(f"amount {old} -> {new}")
                if old > 0 and abs(new - old) / old > REVIEW_RATIO:
                    flag = True
                proposed["amount"] = float(new)

        if "due_day" in edit:
            try:
                day = int(edit["due_day"])
            except (TypeError, ValueError) as exc:
                raise EditError(f"{bid}: due_day must be a whole number") from exc
            if not 1 <= day <= 31:
                raise EditError(f"{bid}: due_day {day} is not between 1 and 31")
            if day != target.get("due_day"):
                notes.append(f"due_day {target.get('due_day')} -> {day}")
                proposed["due_day"] = day

        if "active" in edit:
            active = bool(edit["active"])
            if active != target.get("active", True):
                notes.append("reactivated" if active else "deactivated")
                proposed["active"] = active

        if not notes:
            continue

        proposed["last_updated"] = now.isoformat()
        proposed["source"] = "app"
        if flag:
            proposed["needs_review"] = True
            notes.append("FLAGGED: more than 40% change")
        proposed["note"] = (
            f"Edited in the app {now:%d %b %Y}: {'; '.join(notes)}. "
            f"Previous note: {target.get('note', '(none)')}"
        )[:900]

        # Reject before anything is written, not after.
        validate(proposed)
        staged.append((target, proposed, notes))

    if not staged:
        return []

    for target, proposed, _ in staged:
        target.clear()
        target.update(proposed)

    save_items(path, "bills", items)
    return [f"{p['name']}: {'; '.join(n)}" for _, p, n in staged]


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: apply_edits.py '<json edits>'", file=sys.stderr)
        return 2
    try:
        changes = apply(parse_edits(argv[1]))
    except (EditError, BillError) as exc:
        print(f"EDIT REJECTED: {exc}", file=sys.stderr)
        return 1
    if not changes:
        print("No effective changes.")
        return 0
    print(f"Applied {len(changes)} change(s):")
    for c in changes:
        print(f"  {c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
