"""The single authoritative run. Everything else in this project is a library.

Two modes, and the difference between them is the whole design:

  daily  Someone entered today's balance. Every number is anchored to a real
         figure, so the full daily check goes out.

  watch  Nobody entered anything. Runs on a schedule, projects from the last
         known balance and rebuilds the snapshot, so the app's Risk tile is
         current twice a day whether or not anyone opened it. It can also email
         an alert, but that is off: `risk_emails_enabled` is false, and the
         daily summary is the only email that sends.

`daily` refuses to send when the balance is not from today. A financial email
built on a stale balance is worse than no email — it reads as authoritative and
is quietly wrong.

    python src/engine.py daily --balance 4382.17
    python src/engine.py watch
    python src/engine.py daily --balance 100 --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import fincal
import forecast as fc
import messaging
import notify
import recommend
import risk as riskmod
from bills import BillError, load_items, money

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
STATE = ROOT / "state.json"
SNAPSHOT = ROOT / "snapshot.json"
LOGS = ROOT / "logs"


class EngineError(RuntimeError):
    """The run cannot produce an honest answer."""


# --------------------------------------------------------------------------
# config and state
# --------------------------------------------------------------------------

@dataclass
class Settings:
    timezone: str = "America/New_York"
    forecast_days: int = 60
    minimum_safe_balance: Decimal = money(500)
    large_payment_threshold: Decimal = money(750)
    daily_discretionary_allowance: Decimal = money(0)
    discretionary_lookahead_days: int = 14
    spending_window_days: int = 30
    alert_reminder_days: int = 3
    balance_max_age_hours: int = 20
    risk_emails_enabled: bool = False


def load_settings(path: Path = CONFIG) -> Settings:
    raw = json.loads(path.read_text(encoding="utf-8"))
    s = Settings()
    s.timezone = raw.get("timezone", s.timezone)
    s.forecast_days = int(raw.get("forecast_days", raw.get("projection_days", s.forecast_days)))
    for key in (
        "minimum_safe_balance",
        "large_payment_threshold",
        "daily_discretionary_allowance",
    ):
        if raw.get(key) is not None:
            setattr(s, key, money(raw[key]))
    s.discretionary_lookahead_days = int(
        raw.get("discretionary_lookahead_days", s.discretionary_lookahead_days)
    )
    s.spending_window_days = int(
        raw.get("spending_window_days", s.spending_window_days)
    )
    s.alert_reminder_days = int(raw.get("alert_reminder_days", s.alert_reminder_days))
    s.balance_max_age_hours = int(
        raw.get("balance_max_age_hours", s.balance_max_age_hours)
    )
    s.risk_emails_enabled = bool(
        raw.get("risk_emails_enabled", s.risk_emails_enabled)
    )
    return s


def load_state(path: Path = STATE) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict, path: Path = STATE) -> None:
    path.write_text(
        json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def record_balance(state: dict, amount: Decimal, when: datetime) -> dict:
    """Store a balance entry. History is appended, never overwritten."""
    state["balance"] = {
        "amount": str(money(amount)),
        "entered_at": when.isoformat(),
        "date": when.date().isoformat(),
    }
    hist = state.setdefault("balance_history", [])
    hist.append(dict(state["balance"]))
    del hist[:-400]
    return state


def load_deferrals(state: dict, today: date) -> set[tuple[str, date]]:
    """Deferrals the household has marked, as (bill_id, date).

    Anything dated before today is dropped on read rather than pruned on write.
    A deferral is a decision about one occurrence; once that date has passed the
    decision is spent, and carrying it forward would silently suppress the next
    occurrence of the same bill.
    """
    out: set[tuple[str, date]] = set()
    for d in state.get("deferrals", []):
        try:
            when = date.fromisoformat(d["date"])
        except (KeyError, TypeError, ValueError):
            continue
        if when >= today and d.get("bill_id"):
            out.add((d["bill_id"], when))
    return out


def record_deferrals(state: dict, items: list[dict], when: datetime) -> dict:
    """Replace the deferral set with what the app just sent.

    A full replace, not a merge: the app sends the complete set of ticked boxes,
    so unticking one has to be able to remove it.
    """
    clean = []
    for it in items:
        try:
            d = date.fromisoformat(str(it["date"]))
        except (KeyError, TypeError, ValueError):
            continue
        bid = str(it.get("bill_id", ""))
        if bid and d >= when.date():
            clean.append({"bill_id": bid, "date": d.isoformat(),
                          "marked_at": when.isoformat()})
    state["deferrals"] = clean
    return state


def balance_is_fresh(state: dict, now: datetime, max_age_hours: int) -> bool:
    b = state.get("balance")
    if not b:
        return False
    try:
        entered = datetime.fromisoformat(b["entered_at"])
    except (KeyError, ValueError):
        return False
    if entered.tzinfo is None:
        entered = entered.replace(tzinfo=now.tzinfo)
    if entered.date() != now.date():
        return False
    return (now - entered) <= timedelta(hours=max_age_hours)


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

@dataclass
class Result:
    today: date
    balance: Decimal
    balance_is_fresh: bool
    forecast: fc.Forecast
    discretionary: fc.Discretionary
    calendar: fincal.Calendar
    risks: list
    to_notify: list
    resolved: list
    plan: recommend.Plan
    upcoming: list


def analyse(
    settings: Settings, state: dict, now: datetime, *, notify: bool = False
) -> Result:
    """Load everything, build the calendar, forecast, and score the risks.

    `notify` says whether this run will actually send alert emails. It defaults
    to False because most runs do not: the daily check folds risks into the
    summary and `defer` sends nothing at all. Only the scheduled watch sends
    alerts, and only when they are switched on in config.

    It matters because the dedup state is written here. Marking a risk notified
    on a run that sends nothing buys three days of silence for a message nobody
    got — which is what used to happen every time a balance was entered.
    """
    today = now.date()

    try:
        bills = load_items(ROOT / "bills.json", "bills")
        income = load_items(ROOT / "income.json", "income")
    except BillError as exc:
        raise EngineError(f"financial data will not load: {exc}") from exc

    horizon = today + timedelta(days=settings.forecast_days + 30)
    deferrals = load_deferrals(state, today)
    calendar = fincal.build(bills, income, today, horizon, deferrals)

    b = state.get("balance") or {}
    try:
        balance = money(b["amount"])
    except (KeyError, InvalidOperation):
        raise EngineError(
            "no balance has ever been entered — cannot project without a starting point"
        ) from None

    fresh = balance_is_fresh(state, now, settings.balance_max_age_hours)

    # Bills and income only. The allowance is 0 in config and must stay there:
    # a curve that charges assumed spending cannot answer how much spending is
    # affordable, because it has already spent it.
    projection = fc.run(
        calendar,
        opening_balance=balance,
        start=today,
        days=settings.forecast_days,
        allowance=settings.daily_discretionary_allowance,
    )
    disc = fc.available(
        calendar,
        balance=balance,
        today=today,
        projection=projection,
        window_days=settings.spending_window_days,
    )

    risks = riskmod.detect(
        projection,
        calendar,
        disc,
        minimum_safe_balance=settings.minimum_safe_balance,
        large_payment_threshold=settings.large_payment_threshold,
        today=today,
    )
    to_notify, resolved, new_state = riskmod.triage(
        risks,
        state.get("risks", {}),
        now=now,
        reminder_days=settings.alert_reminder_days,
        notify=notify,
    )
    state["risks"] = new_state

    plan = recommend.build_plan(projection)

    # "Upcoming" is deliberately the non-weekly stuff: one-off and annual
    # obligations far enough out that they are easy to forget and big enough to
    # hurt. Weekly noise belongs in THIS WEEK, not here.
    #
    # Deferred occurrences are included and marked, not filtered out. Deferring
    # moves an obligation, it does not settle it, and a list of what is coming
    # that quietly omits the bill you postponed is exactly the kind of flattering
    # half-truth this project refuses to produce.
    week = fincal.week_end(today)
    upcoming = [
        e
        for e in calendar.bills_between(
            week + timedelta(days=1), horizon, include_deferred=True
        )
        if e.priority_tier <= 4 and e.amount >= money(100)
    ][:10]

    return Result(
        today=today,
        balance=balance,
        balance_is_fresh=fresh,
        forecast=projection,
        discretionary=disc,
        calendar=calendar,
        risks=risks,
        to_notify=to_notify,
        resolved=resolved,
        plan=plan,
        upcoming=upcoming,
    )


# --------------------------------------------------------------------------
# snapshot for the dashboard
# --------------------------------------------------------------------------

def _editable(today: date, file: str, key: str) -> list[dict]:
    """Every entry the Edit screens show, in the order it next falls due.

    A year and a bit of lookahead, so an annual item gets a real date rather
    than sorting to the bottom with everything else that has none. Inactive
    entries have no occurrences by definition and sort last, which is where they
    belong: they are kept for history, not for editing.
    """
    from bills import occurrences

    horizon = today + timedelta(days=400)
    out = []
    for b in load_items(ROOT / file, key):
        when = occurrences(b, today, horizon)
        out.append({
            "id": b["id"], "name": b["name"], "amount": str(money(b["amount"])),
            "due_day": b.get("due_day"), "frequency": b["frequency"],
            "next_date": when[0].isoformat() if when else None,
            "tier": b.get("priority_tier", 5), "active": bool(b.get("active", True)),
            "variable": bool(b.get("variable", False)),
            "needs_review": bool(b.get("needs_review", False)),
            "source": b.get("source") or "",
            "note": (b.get("note") or "")[:300],
        })
    out.sort(key=lambda x: (
        not x["active"],
        x["next_date"] or "9999-12-31",
        x["name"].lower(),
    ))
    return out


def write_snapshot(r: Result, settings: Settings, now: datetime) -> dict:
    """The dashboard reads this and computes nothing. One source of truth."""
    f = r.forecast
    low = f.minimum_day
    snap = {
        "generated_at": now.isoformat(),
        "today": r.today.isoformat(),
        "balance": str(r.balance),
        "balance_is_fresh": r.balance_is_fresh,
        "balance_entered_at": (r.balance_is_fresh and now.isoformat()) or None,
        "end_of_day": str(f.end_of_day),
        "end_of_week": str(f.end_of_week),
        "end_of_month": str(f.end_of_month),
        "week_end": r.discretionary.week_end.isoformat(),
        "minimum_balance": str(f.minimum_balance),
        "minimum_date": low.day.isoformat() if low else None,
        "shortfall": str(f.shortfall),
        "allowance": str(settings.allowance) if hasattr(settings, "allowance") else str(
            settings.daily_discretionary_allowance
        ),
        "discretionary": {
            "safe": str(r.discretionary.headroom),
            "explain": [[lbl, str(val)] for lbl, val in r.discretionary.explain()],
            # The two figures the card shows. Both are facts about the curve:
            # nothing is assumed, held back, or charged on the household's behalf.
            "headroom": str(r.discretionary.headroom),
            "headroom_day": (r.discretionary.headroom_day.isoformat()
                             if r.discretionary.headroom_day else None),
            "per_day": str(r.discretionary.per_day),
            "per_day_day": (r.discretionary.per_day_day.isoformat()
                            if r.discretionary.per_day_day else None),
            "window_days": r.discretionary.window_days,
            "window_end": r.discretionary.window_end.isoformat(),
            # Raw inputs, so the app can recompute both as boxes are ticked
            # without a round trip. `curve` above carries the projection, and
            # deferring a bill adds its amount back to every day from its date
            # onward — which is all the app needs to redo this arithmetic.
            "components": {
                "balance": str(r.balance),
                "window_end": r.discretionary.window_end.isoformat(),
            },
        },
        "today_bills": [
            {"name": e.name, "amount": str(e.amount), "tier": e.priority_tier,
             "autopay": e.autopay, "deferred": e.deferred}
            for e in r.calendar.bills_between(
                r.today, r.today, include_deferred=True
            )
        ],
        "today_income": [
            {"name": e.name, "amount": str(e.amount)}
            for e in (f.days[0].income if f.days else [])
        ],
        "this_week": [
            {"date": e.day.isoformat(), "name": e.name, "amount": str(e.amount),
             "direction": e.direction, "tier": e.priority_tier,
             "deferred": e.deferred}
            for e in r.calendar.between(r.today, r.discretionary.week_end)
        ],
        "upcoming": [
            {"date": e.day.isoformat(), "name": e.name, "amount": str(e.amount),
             "tier": e.priority_tier, "deferred": e.deferred}
            for e in r.upcoming
        ],
        "curve": [
            {"date": d.day.isoformat(), "closing": str(d.closing)}
            for d in f.days[:60]
        ],
        "risks": [
            {"id": x.id, "type": x.type, "severity": x.severity, "title": x.title,
             "detail": x.detail, "amount": str(x.amount),
             "when": x.when.isoformat() if x.when else None}
            for x in r.risks
        ],
        "plan": {
            "summary": r.plan.summary,
            "shortfall": str(r.plan.shortfall),
            "problem_day": r.plan.problem_day.isoformat() if r.plan.problem_day else None,
            "candidates": [
                {"name": c.event.name, "amount": str(c.frees),
                 "date": c.event.day.isoformat(), "tier": c.event.priority_tier}
                for c in r.plan.candidates
            ],
            "covered": r.plan.covered,
        },
        # Everything inside the discretionary horizon, so the app can recompute
        # "safe to spend" instantly as boxes are ticked instead of waiting on a
        # round trip. The browser reproduces the same arithmetic; the engine
        # remains the authority once the choice is saved.
        "deferral_window": [
            {"id": e.item_id, "name": e.name, "date": e.day.isoformat(),
             "amount": str(e.amount), "tier": e.priority_tier,
             "deferrable": e.deferrable, "secured": e.secured,
             "deferred": e.deferred,
             "in_week": e.day <= r.discretionary.week_end}
            for e in r.calendar.bills_between(
                r.today,
                r.discretionary.week_end + timedelta(
                    days=settings.discretionary_lookahead_days),
                include_deferred=True,
            )
        ],
        "deferred_total": str(
            r.calendar.deferred_total(
                r.today,
                r.discretionary.week_end + timedelta(
                    days=settings.discretionary_lookahead_days),
            )
        ),
        "lookahead_days": settings.discretionary_lookahead_days,
        # The full editable model, so the app's Edit screen renders from the
        # same snapshot as everything else rather than fetching bills.json and
        # re-implementing what counts as active.
        #
        # Ordered by when each one next lands. Someone opening this screen is
        # looking for the bill they just got a notice about, and the only thing
        # they reliably know about it is roughly when it is due — not its
        # priority tier, which is a modelling decision, and not its size.
        # Frequency is why due_day cannot be sorted on directly: a biweekly
        # mortgage has no day of the month at all.
        "bills": _editable(r.today, "bills.json", "bills"),
        # Income the same way, so the app can show what is already counted
        # before someone adds another one. Double-counting a paycheque is
        # invisible in the output — the forecast simply gets better — so the
        # list has to be in front of them at the moment they are adding to it.
        "income": _editable(r.today, "income.json", "income"),
        "settings": {
            "minimum_safe_balance": str(settings.minimum_safe_balance),
            "daily_discretionary_allowance": str(settings.daily_discretionary_allowance),
            "forecast_days": settings.forecast_days,
        },
    }
    SNAPSHOT.write_text(
        json.dumps(snap, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return snap


# --------------------------------------------------------------------------
# entry points
# --------------------------------------------------------------------------

def run_daily(balance: Decimal | None, *, dry_run: bool) -> int:
    settings = load_settings()
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    state = load_state()

    if balance is not None:
        state = record_balance(state, balance, now)
        # A dry run previews; it does not record. Persisting here would mark a
        # balance as "entered today" that the household never actually entered.
        if dry_run:
            r = analyse(load_settings(), state, now)
            print(notify.daily_text(r.today, r.balance, r.forecast, r.discretionary,
                                    r.risks, r.plan, r.upcoming))
            return 0

    if not balance_is_fresh(state, now, settings.balance_max_age_hours):
        print(
            "No balance entered today. The daily check is not sent — every figure "
            "in it would be anchored to a stale number.\n"
            "Enter today's balance to trigger it."
        )
        save_state(state)
        return 0

    r = analyse(settings, state, now)
    snap = write_snapshot(r, settings, now)

    subject = notify.daily_subject(r.forecast, r.discretionary, r.risks)
    text = notify.daily_text(
        r.today, r.balance, r.forecast, r.discretionary, r.risks, r.plan, r.upcoming
    )
    html = notify.daily_html(
        r.today, r.balance, r.forecast, r.discretionary, r.risks, r.plan, r.upcoming
    )
    sent = messaging.send_mail(subject, text, html, dry_run=dry_run)

    state["last_daily_sent"] = now.isoformat()
    save_state(state)
    _log(snap, r.today)
    print(f"Daily check {'previewed' if dry_run else 'sent to ' + ', '.join(sent)}.")
    return 0


def run_defer(items_json: str, *, dry_run: bool) -> int:
    """Record the deferral set the app sent, then rebuild and re-alert.

    No balance is required: deferring is a decision about what leaves the
    account, not a new reading of it.
    """
    settings = load_settings()
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    state = load_state()

    try:
        items = json.loads(items_json)
    except json.JSONDecodeError as exc:
        raise EngineError(f"deferrals are not valid JSON: {exc}") from exc
    if not isinstance(items, list) or len(items) > 200:
        raise EngineError("deferrals must be a list of at most 200 items")

    state = record_deferrals(state, items, now)
    r = analyse(settings, state, now)
    write_snapshot(r, settings, now)

    if not dry_run:
        save_state(state)
    n = len(state.get("deferrals", []))
    print(
        f"{n} deferral(s) recorded. Safe to spend is now "
        f"{notify.m(r.discretionary.safe)}; "
        f"lowest projected {notify.m(r.forecast.minimum_balance)}."
    )
    return 0


def run_watch(*, dry_run: bool) -> int:
    """Scheduled risk sweep. Never needs a balance entered today.

    The sweep runs whatever `risk_emails_enabled` says. What the setting turns
    off is the email, not the detection: the snapshot is rebuilt on every run
    and committed, so the app's Risk tile is current twice a day regardless.
    The daily summary also carries the risk list, so nothing is only ever
    knowable from an alert.
    """
    settings = load_settings()
    tz = ZoneInfo(settings.timezone)
    now = datetime.now(tz)
    state = load_state()

    r = analyse(settings, state, now, notify=settings.risk_emails_enabled)
    write_snapshot(r, settings, now)

    if not settings.risk_emails_enabled:
        save_state(state)
        print(
            f"{len(r.risks)} risk(s) found; risk emails are off, so nothing was "
            f"sent. The snapshot is updated and the app shows them."
        )
        return 0

    if not r.to_notify:
        save_state(state)
        print(
            f"{len(r.risks)} risk(s) known, none new or materially worse. "
            f"Nothing sent."
        )
        return 0

    for item in r.to_notify:
        plan = r.plan if item.type in (
            riskmod.NEGATIVE_BALANCE,
            riskmod.SECURED_PAYMENT,
            riskmod.LARGE_PAYMENT,
            riskmod.INCOME_TIMING,
        ) else None
        messaging.send_mail(
            notify.alert_subject(item),
            notify.alert_text(item, plan, r.balance_is_fresh),
            notify.alert_html(item, plan, r.balance_is_fresh),
            dry_run=dry_run,
        )

    save_state(state)
    print(f"{len(r.to_notify)} alert(s) {'previewed' if dry_run else 'sent'}.")
    return 0


def _log(snapshot: dict, day: date) -> None:
    LOGS.mkdir(exist_ok=True)
    (LOGS / f"{day.isoformat()}.json").write_text(
        json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="OpenFIN financial engine")
    p.add_argument("mode", choices=["daily", "watch", "defer"])
    p.add_argument("--balance", help="today's actual bank balance")
    p.add_argument("--items", help="JSON list of {bill_id, date} deferrals")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    bal = None
    if args.balance:
        cleaned = args.balance.replace("$", "").replace(",", "").strip()
        try:
            bal = money(cleaned)
        except (InvalidOperation, ValueError):
            print(f"'{args.balance}' is not a number.", file=sys.stderr)
            return 2

    try:
        if args.mode == "daily":
            return run_daily(bal, dry_run=args.dry_run)
        if args.mode == "defer":
            return run_defer(args.items or "[]", dry_run=args.dry_run)
        return run_watch(dry_run=args.dry_run)
    except EngineError as exc:
        # Financial software does not fail silently and does not invent numbers.
        print(f"ENGINE ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
