"""Morning run: read the bank, project forward, send one text.

Failure posture: if the bank feed cannot be read, this does NOT go quiet and
it does NOT guess. It sends a text saying the data is stale, quotes the last
known balance with its timestamp, and exits non-zero so the Actions run goes
red. A message with silently-wrong numbers is worse than no message.

Run it by hand from the Actions tab with dry_run checked to see the exact SMS
without sending anything.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import bills as billsmod
import messaging
import projection
from bills import fmt_money, load_items, money
from simplefin_client import SimpleFinError, fetch_accounts, spendable_balance

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
BILLS = ROOT / "bills.json"
INCOME = ROOT / "income.json"
STATE = ROOT / "state.json"
LOGS = ROOT / "logs"

MAX_SMS_CHARS = 900
MAX_BILL_LINES = 5


# --------------------------------------------------------------------------
# config and state
# --------------------------------------------------------------------------

def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_state(state: dict) -> None:
    existing = load_json(STATE) if STATE.exists() else {}
    preserved = {k: v for k, v in existing.items() if k.startswith("_")}
    with open(STATE, "w", encoding="utf-8") as fh:
        json.dump({**preserved, **state}, fh, indent=2)
        fh.write("\n")


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


# --------------------------------------------------------------------------
# message formatting
# --------------------------------------------------------------------------

def short_money(value: Decimal) -> str:
    """Compact form for the tight-day list: -142, +41."""
    whole = int(money(value).to_integral_value(rounding="ROUND_HALF_UP"))
    return f"{'+' if whole >= 0 else '-'}{abs(whole)}"


def format_brief(proj: projection.Projection, config: dict) -> str:
    threshold = money(config["low_balance_threshold"])
    buffer_ = money(config["warning_buffer"])

    lines = [f"CASH {proj.today.strftime('%m/%d')}"]

    now_line = f"Now: {fmt_money(proj.balance_now)} (incl pending)"
    if proj.used_fallback_balance:
        now_line += " [bal-pending]"
    lines.append(now_line)

    lines.append(f"Bills today: {fmt_money(proj.bills_today_total)}")

    shown = sorted(proj.bills_today, key=lambda b: b.amount, reverse=True)
    overflow = 0
    if len(shown) > MAX_BILL_LINES:
        overflow = len(shown) - MAX_BILL_LINES
        shown = shown[:MAX_BILL_LINES]
    for bill in shown:
        suffix = " (PAID)" if bill.paid else ""
        lines.append(f" - {bill.name} {fmt_money(bill.amount)}{suffix}")
    if overflow:
        lines.append(f" (+{overflow} more)")

    lines.append(
        f"Disc spent: {fmt_money(proj.discretionary_spent_today)} of {fmt_money(proj.allowance)}"
    )
    lines.append(f"End of day: {fmt_money(proj.end_of_day)}")
    lines.append("")

    tight = proj.tight_days(buffer_)
    if tight:
        # The nearest tight day, not the deepest — it is the one you can still
        # do something about.
        nearest = tight[0]
        lines.append(f"WATCH: {nearest.day.strftime('%m/%d')} {fmt_money(nearest.closing)}")
        parts = [
            f"{d.day.strftime('%m/%d')} {short_money(d.closing)}"
            for d in proj.next_tight_days(buffer_, 3)
        ]
        lines.append("Next 3 tight: " + ", ".join(parts))
    else:
        last = proj.days[-1].day if proj.days else proj.today
        lines.append(f"Clear through {last.strftime('%m/%d')}")

    body = "\n".join(lines)

    # Belt and braces: if the bill list still pushed us over, drop it further.
    if len(body) > MAX_SMS_CHARS and len(proj.bills_today) > 1:
        trimmed = [ln for ln in lines if not ln.startswith(" - ")][:]
        body = "\n".join(trimmed)

    return body


def format_alert(day: projection.Day, balance_now: Decimal) -> str:
    lines = [f"ALERT: {day.day.strftime('%m/%d')} projects to {fmt_money(day.closing)}"]
    driver = projection.driver_for(day)
    if driver:
        name, amount = driver
        lines.append(f"Driver: {name} {fmt_money(amount)} on {day.day.strftime('%m/%d')}")
    lines.append(f"Balance today: {fmt_money(balance_now)}")
    return "\n".join(lines)


def format_stale(state: dict, reason: str) -> str:
    balance = state.get("last_known_balance")
    seen = state.get("last_known_balance_iso")
    lines = ["CASH DATA STALE — bank feed unavailable."]
    if balance is not None and seen:
        lines.append(f"Last known: {fmt_money(money(balance))} as of {seen[:16].replace('T', ' ')}")
    else:
        lines.append("No previous balance on record.")
    lines.append(f"Reason: {reason[:200]}")
    lines.append("Figures above are NOT current. Check the bank directly.")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main() -> int:
    dry_run = env_flag("DRY_RUN")
    force = env_flag("FORCE_SEND")

    config = load_json(CONFIG)
    state = load_json(STATE) if STATE.exists() else {}
    tz = ZoneInfo(config["timezone"])
    now_local = datetime.now(tz)
    today = now_local.date()
    provider = config["sms_provider"]

    # --- send-window gate -------------------------------------------------
    # Two cron entries fire each day (11:00 and 12:00 UTC). Exactly one of
    # them lands on the configured local hour, whichever side of the daylight
    # saving switch we are on. The other exits here.
    if not dry_run and not force:
        if now_local.hour != int(config["send_hour_local"]):
            print(
                f"Local time is {now_local:%H:%M %Z}; send_hour_local is "
                f"{config['send_hour_local']:02d}. Nothing to do."
            )
            return 0

        last_run = state.get("last_run_iso")
        if last_run:
            try:
                if datetime.fromisoformat(last_run).astimezone(tz).date() == today:
                    print(f"Already sent today ({last_run}). Not sending again.")
                    return 0
            except ValueError:
                print(f"WARNING: last_run_iso {last_run!r} is unparseable; ignoring it.")

    # --- read the bank ----------------------------------------------------
    access_url = os.environ.get("SIMPLEFIN_ACCESS_URL", "").strip()
    account_ids = [str(a) for a in config.get("account_ids", [])]
    lookback = today - timedelta(days=14)

    try:
        if not access_url:
            raise SimpleFinError("SIMPLEFIN_ACCESS_URL is not set")
        account_set = fetch_accounts(access_url, start_date=lookback, account_ids=account_ids)
        if not account_set.accounts:
            raise SimpleFinError("SimpleFIN returned no accounts")
        if account_set.errors:
            print("SimpleFIN reported errors: " + "; ".join(account_set.errors))
    except SimpleFinError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        body = format_stale(state, str(exc))
        try:
            messaging.send(body, provider, dry_run=dry_run)
        except messaging.MessagingError as send_exc:
            print(f"ERROR: could not even send the stale-data notice: {send_exc}", file=sys.stderr)
        return 1

    accounts = account_set.accounts
    if account_ids:
        accounts = [a for a in accounts if a.id in set(account_ids)]
        if not accounts:
            print(
                f"ERROR: config.account_ids {account_ids} matched none of "
                f"{[a.id for a in account_set.accounts]}",
                file=sys.stderr,
            )
            messaging.send(
                format_stale(state, "config.account_ids matches no account"),
                provider,
                dry_run=dry_run,
            )
            return 1

    balance_now, used_fallback = spendable_balance(accounts)
    transactions = [t for a in accounts for t in a.transactions]
    included = {a.id for a in accounts}

    # --- project ----------------------------------------------------------
    bill_list = load_items(BILLS, "bills")
    income_list = load_items(INCOME, "income")

    proj = projection.build(
        today=today,
        balance_now=balance_now,
        used_fallback_balance=used_fallback,
        bills=bill_list,
        income=income_list,
        transactions=transactions,
        projection_days=int(config["projection_days"]),
        daily_allowance=money(config["daily_discretionary_allowance"]),
        exclude_keywords=config.get("discretionary_exclude_keywords", []),
        included_accounts=included,
    )

    # --- send -------------------------------------------------------------
    body = format_brief(proj, config)
    print(f"Brief is {len(body)} characters.")
    messaging.send(body, provider, dry_run=dry_run)

    threshold = money(config["low_balance_threshold"])
    alerted = dict(state.get("alerted_days") or {})
    urgent = projection.new_or_worsened(proj, threshold, alerted)

    # At most ONE immediate alert per run, naming the nearest new or worsened
    # critical day. Once a household knows the date it first runs out, every
    # later negative day is a consequence of that one — texting all of them
    # would mean dozens of messages saying the same thing, and a phone that
    # buzzes forty times is a phone nobody reads.
    sent_alerts: list[projection.Day] = []
    if urgent:
        nearest = min(urgent, key=lambda d: d.day)
        messaging.send(format_alert(nearest, proj.balance_now), provider, dry_run=dry_run)
        sent_alerts.append(nearest)
        if len(urgent) > 1:
            print(f"{len(urgent)} critical days are new or worse; alerted on the nearest "
                  f"({nearest.day.isoformat()}) and recorded the rest.")

    # Record every critical day, alerted or not, so none of them re-alerts
    # tomorrow unless it drops by more than the re-alert threshold.
    for day in proj.critical_days(threshold):
        alerted[day.day.isoformat()] = str(day.closing)

    # Forget days that have passed or recovered, so the map cannot grow forever.
    still_critical = {d.day.isoformat() for d in proj.critical_days(threshold)}
    alerted = {k: v for k, v in alerted.items() if k in still_critical}

    # --- persist ----------------------------------------------------------
    snapshot = {
        "generated_at": now_local.isoformat(),
        "today": today.isoformat(),
        "balance_now": str(proj.balance_now),
        "balance_method": "balance-minus-pending" if used_fallback else "available-balance",
        "accounts": [
            {"id": a.id, "name": a.name, "balance": str(a.balance),
             "available_balance": str(a.available_balance) if a.available_balance is not None else None}
            for a in accounts
        ],
        "bills_today": [
            {"id": b.id, "name": b.name, "amount": str(b.amount), "paid": b.paid,
             "matched": b.matched_description}
            for b in proj.bills_today
        ],
        "discretionary_spent_today": str(proj.discretionary_spent_today),
        "end_of_day": str(proj.end_of_day),
        "projection": [
            {"date": d.day.isoformat(), "closing": str(d.closing),
             "bills": [[n, str(a)] for n, a in d.bills],
             "income": [[n, str(a)] for n, a in d.income]}
            for d in proj.days
        ],
        "message": body,
        "alerts_sent": [d.day.isoformat() for d in sent_alerts],
        "critical_days_known": sorted(alerted),
        "simplefin_errors": account_set.errors,
    }

    if not dry_run:
        LOGS.mkdir(parents=True, exist_ok=True)
        with open(LOGS / f"{today.isoformat()}.json", "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2)
            fh.write("\n")

        save_state(
            {
                "last_run_iso": now_local.isoformat(),
                "last_known_balance": str(proj.balance_now),
                "last_known_balance_iso": now_local.isoformat(),
                "alerted_days": alerted,
                "last_message_hash": hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }
        )
        print(f"Wrote logs/{today.isoformat()}.json and updated state.json.")
    else:
        print("Dry run: state.json and logs/ were not written.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
