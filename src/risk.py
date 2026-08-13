"""The risk engine: find the problems, and know which ones have already been said.

Seven risk types, in descending order of how much they matter:

  SECURED_PAYMENT   a mortgage or car payment looks unpayable
  NEGATIVE_BALANCE  the projection crosses zero
  LARGE_PAYMENT     a big obligation lands with too little behind it
  INSUFFICIENT_CASH the balance survives but drops under the safe floor
  INCOME_TIMING     the month works, the order does not
  FUTURE_CRUNCH     fine now, a combination bites later
  DISCRETIONARY     nothing free to spend this week

Deduplication is a first-class concern, not a nicety. An engine that fires on
every run trains the household to ignore it, at which point the whole system is
decorative. A risk is re-sent only when it gets materially worse, moves in time,
or comes back after resolving.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from bills import money
from fincal import Calendar, week_end
from forecast import Discretionary, Forecast

SECURED_PAYMENT = "secured_payment"
NEGATIVE_BALANCE = "negative_balance"
LARGE_PAYMENT = "large_payment"
INSUFFICIENT_CASH = "insufficient_cash"
INCOME_TIMING = "income_timing"
FUTURE_CRUNCH = "future_crunch"
DISCRETIONARY = "discretionary"

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# Re-notify when the money moves by more than this, or the date by more than
# this many days. Below these thresholds a change is noise.
MATERIAL_AMOUNT = Decimal("100.00")
MATERIAL_DAYS = 2


@dataclass
class Risk:
    id: str                     # stable across runs; the dedup key
    type: str
    severity: str               # critical | high | medium | low
    title: str
    detail: str
    amount: Decimal             # shortfall or the sum at stake
    when: date | None           # when it bites
    recommendation: str = ""

    def to_state(self) -> dict:
        return {
            "type": self.type,
            "severity": self.severity,
            "amount": str(self.amount),
            "when": self.when.isoformat() if self.when else None,
        }


@dataclass
class RiskReport:
    risks: list[Risk] = field(default_factory=list)
    to_notify: list[Risk] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)

    @property
    def worst(self) -> Risk | None:
        if not self.risks:
            return None
        return sorted(self.risks, key=lambda r: SEVERITY_ORDER[r.severity])[0]

    @property
    def clear(self) -> bool:
        return not self.risks


# --------------------------------------------------------------------------
# detection
# --------------------------------------------------------------------------

def detect(
    forecast: Forecast,
    calendar: Calendar,
    discretionary: Discretionary | None,
    *,
    minimum_safe_balance: Decimal,
    large_payment_threshold: Decimal,
    today: date,
) -> list[Risk]:
    """Scan a forecast and return every risk it contains."""
    risks: list[Risk] = []
    zero = money(0)

    negative_days = forecast.days_below(zero)
    first_negative = negative_days[0] if negative_days else None
    low_day = forecast.minimum_day

    # -- 1. a secured payment that the balance cannot actually cover ---------
    #
    # The test is per-payment, not "anything after the first negative day". The
    # account can dip below zero on the 18th and be comfortably funded again by
    # the 24th when payroll lands; calling that a mortgage risk is a false alarm,
    # and false alarms on the most severe alert in the system are how the whole
    # thing gets ignored. Only flag when THIS day cannot fund THIS payment.
    seen_secured: set[str] = set()
    for d in forecast.days:
        for e in d.bills:
            if not e.secured or e.item_id in seen_secured:
                continue
            available = d.opening + money(
                sum((i.amount for i in d.income), money(0))
            )
            if available >= e.amount:
                continue
            seen_secured.add(e.item_id)
            risks.append(
                Risk(
                    id=f"{SECURED_PAYMENT}:{e.item_id}:{e.day.isoformat()}",
                    type=SECURED_PAYMENT,
                    severity="critical",
                    title=f"{e.name} may not clear",
                    detail=(
                        f"{e.name} of {_m(e.amount)} is due {_d(e.day)} with only "
                        f"{_m(available)} available that day."
                    ),
                    amount=money(e.amount - max(available, zero)),
                    when=e.day,
                )
            )

    # -- 2. the account goes negative ---------------------------------------
    if first_negative:
        risks.append(
            Risk(
                id=f"{NEGATIVE_BALANCE}:{first_negative.day.isoformat()}",
                type=NEGATIVE_BALANCE,
                severity="critical",
                title=f"Balance goes negative {_d(first_negative.day)}",
                detail=(
                    f"Projected {_m(first_negative.closing)} on {_d(first_negative.day)}. "
                    f"Lowest point is {_m(forecast.minimum_balance)} on "
                    f"{_d(low_day.day)}." if low_day else ""
                ),
                amount=forecast.shortfall,
                when=first_negative.day,
            )
        )

    # -- 3. a large payment lands with too little behind it ------------------
    seen_large: set[str] = set()
    for d in forecast.days:
        for e in d.bills:
            if e.amount < large_payment_threshold or e.item_id in seen_large:
                continue
            if d.opening >= e.amount:
                continue
            seen_large.add(e.item_id)
            risks.append(
                Risk(
                    id=f"{LARGE_PAYMENT}:{e.item_id}:{e.day.isoformat()}",
                    type=LARGE_PAYMENT,
                    severity="high",
                    title=f"{e.name} {_m(e.amount)} may be short",
                    detail=(
                        f"{e.name} of {_m(e.amount)} is due {_d(e.day)} against a "
                        f"projected opening balance of {_m(d.opening)}."
                    ),
                    amount=money(e.amount - max(d.opening, zero)),
                    when=e.day,
                )
            )

    # -- 4. survives, but dips under the safe floor -------------------------
    if not first_negative and minimum_safe_balance > 0:
        below = forecast.first_day_below(minimum_safe_balance)
        if below:
            risks.append(
                Risk(
                    id=f"{INSUFFICIENT_CASH}:{below.day.isoformat()}",
                    type=INSUFFICIENT_CASH,
                    severity="medium",
                    title=f"Cash dips below {_m(minimum_safe_balance)}",
                    detail=(
                        f"Projected {_m(below.closing)} on {_d(below.day)}, under the "
                        f"{_m(minimum_safe_balance)} safe floor. Stays positive."
                    ),
                    amount=money(minimum_safe_balance - below.closing),
                    when=below.day,
                )
            )

    # -- 5. income timing: enough money, wrong order ------------------------
    if first_negative:
        horizon_end = forecast.days[-1].day
        total_in = calendar.total_in(today, horizon_end)
        total_out = calendar.total_out(today, horizon_end)
        if total_in >= total_out:
            nxt = calendar.next_income_after(first_negative.day)
            risks.append(
                Risk(
                    id=f"{INCOME_TIMING}:{first_negative.day.isoformat()}",
                    type=INCOME_TIMING,
                    severity="high",
                    title="Timing problem, not a shortfall",
                    detail=(
                        f"Income of {_m(total_in)} covers {_m(total_out)} of bills across "
                        f"the window, but the balance still dips to "
                        f"{_m(forecast.minimum_balance)} on "
                        f"{_d(low_day.day) if low_day else '?'} because money goes out "
                        f"before it comes in."
                        + (
                            f" Next income is {nxt.name} {_m(nxt.amount)} on {_d(nxt.day)}."
                            if nxt
                            else ""
                        )
                    ),
                    amount=forecast.shortfall,
                    when=first_negative.day,
                )
            )

    # -- 6. fine now, a crunch later ----------------------------------------
    # Only meaningful with a floor set. This warns about a day that is still
    # positive but under the safety line, and with the line at 0 there is no
    # such day — anything below it is an overdraft, which is risk 1's job and
    # excluded here by `not first_negative` anyway. Guarded explicitly so that
    # is a decision rather than an accident of the arithmetic.
    if low_day and not first_negative and minimum_safe_balance > 0:
        week = week_end(today)
        eow = forecast.closing_on(week)
        if eow is not None and eow > minimum_safe_balance and low_day.day > week:
            if low_day.closing < minimum_safe_balance:
                risks.append(
                    Risk(
                        id=f"{FUTURE_CRUNCH}:{low_day.day.isoformat()}",
                        type=FUTURE_CRUNCH,
                        severity="medium",
                        title=f"Future crunch on {_d(low_day.day)}",
                        detail=(
                            f"This week ends at {_m(eow)}, which is fine, but the "
                            f"balance falls to {_m(low_day.closing)} by "
                            f"{_d(low_day.day)}."
                        ),
                        amount=money(minimum_safe_balance - low_day.closing),
                        when=low_day.day,
                    )
                )

    # -- 7. nothing free to spend -------------------------------------------
    # Negative headroom means the bills alone do not fit, before a penny of
    # everyday spending. Nothing is assumed here: the figure is the low point of
    # a curve carrying income and bills only.
    if discretionary is not None and discretionary.headroom < 0:
        risks.append(
            Risk(
                id=f"{DISCRETIONARY}:{discretionary.headroom_day.isoformat()}"
                   if discretionary.headroom_day
                   else f"{DISCRETIONARY}:{discretionary.window_end.isoformat()}",
                type=DISCRETIONARY,
                severity="medium",
                title=f"Nothing available to spend: {_m(discretionary.headroom)}",
                detail=(
                    f"Over the next {discretionary.window_days} days the balance "
                    f"reaches {_m(discretionary.headroom)}"
                    + (f" on {_d(discretionary.headroom_day)}"
                       if discretionary.headroom_day else "")
                    + ". That is bills and income only — no everyday spending is "
                    "counted in it, so anything spent makes it worse by that much."
                ),
                amount=money(-discretionary.headroom),
                when=discretionary.headroom_day or discretionary.window_end,
            )
        )

    return sorted(risks, key=lambda r: (SEVERITY_ORDER[r.severity], r.when or date.max))


# --------------------------------------------------------------------------
# deduplication
# --------------------------------------------------------------------------

def _material_change(old: dict, new: Risk) -> bool:
    if old.get("severity") != new.severity:
        return SEVERITY_ORDER[new.severity] < SEVERITY_ORDER.get(old["severity"], 9)
    try:
        if abs(money(old.get("amount", "0")) - new.amount) > MATERIAL_AMOUNT:
            return True
    except Exception:                                            # noqa: BLE001
        return True
    old_when = old.get("when")
    if old_when and new.when:
        if abs((date.fromisoformat(old_when) - new.when).days) > MATERIAL_DAYS:
            return True
    return False


def triage(
    risks: list[Risk],
    known: dict,
    *,
    now: datetime,
    reminder_days: int = 3,
) -> tuple[list[Risk], list[str], dict]:
    """Split risks into notify / stay-quiet, and return the state to persist.

    Re-notify when: new, materially worse, moved in time, or a reminder is due.
    """
    to_notify: list[Risk] = []
    state = {}

    for r in risks:
        prev = known.get(r.id)
        entry = r.to_state()
        if prev is None:
            entry["first_seen"] = now.isoformat()
            entry["notified_at"] = now.isoformat()
            to_notify.append(r)
        else:
            entry["first_seen"] = prev.get("first_seen", now.isoformat())
            last = prev.get("notified_at")
            due_reminder = False
            if last:
                try:
                    due_reminder = (
                        now - datetime.fromisoformat(last)
                    ).days >= reminder_days
                except ValueError:
                    due_reminder = True
            if _material_change(prev, r) or due_reminder:
                entry["notified_at"] = now.isoformat()
                to_notify.append(r)
            else:
                entry["notified_at"] = last
        entry["last_seen"] = now.isoformat()
        state[r.id] = entry

    resolved = [rid for rid in known if rid not in state]
    return to_notify, resolved, state


def load_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("risks", {})
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(path: Path, risks: dict, extra: dict | None = None) -> None:
    payload = {"risks": risks}
    if extra:
        payload.update(extra)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------

def _m(v: Decimal) -> str:
    v = money(v)
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def _d(d: date) -> str:
    return d.strftime("%a %-d %b")
