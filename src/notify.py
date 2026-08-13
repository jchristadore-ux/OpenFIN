"""Email composition. Two kinds of message, and they are not the same message.

  daily  — the full financial check. Only ever sent after a balance is entered,
           because every number in it is anchored to that balance.
  alert  — a risk fired by the engine. Sent whether or not a balance was entered
           today, because a mortgage bouncing next Tuesday is true regardless of
           whether anyone opened the app this morning.

Both are written for a phone screen held in one hand: short lines, money right
where the eye lands, no horizontal scrolling.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape

from bills import money
from forecast import Discretionary, Forecast
from recommend import Plan
from risk import Risk

BRAND = "OpenFIN"


def m(v: Decimal) -> str:
    v = money(v)
    return f"-${abs(v):,.2f}" if v < 0 else f"${v:,.2f}"


def d(day: date) -> str:
    return day.strftime("%a %-d %b")


# --------------------------------------------------------------------------
# daily check
# --------------------------------------------------------------------------

def daily_subject(forecast: Forecast, disc: Discretionary, risks: list[Risk]) -> str:
    if risks and risks[0].severity == "critical":
        return f"{BRAND}: ACTION NEEDED — {risks[0].title}"
    if disc.per_day < 0:
        return f"{BRAND}: nothing available to spend ({m(disc.headroom)})"
    return f"{BRAND}: {m(disc.per_day)} a day available"


def daily_text(
    today: date,
    balance: Decimal,
    forecast: Forecast,
    disc: Discretionary,
    risks: list[Risk],
    plan: Plan | None,
    upcoming: list,
) -> str:
    L: list[str] = []
    L.append(f"{BRAND.upper()} — DAILY FINANCIAL CHECK")
    L.append(d(today))
    L.append("")
    L.append(f"Balance entered: {m(balance)}")
    L.append("")

    day0 = forecast.days[0] if forecast.days else None
    L.append("TODAY")
    if day0 and day0.bills:
        for e in day0.bills:
            L.append(f"  {e.name} — {m(e.amount)}")
    else:
        L.append("  No bills due today.")
    if day0 and day0.income:
        for e in day0.income:
            L.append(f"  + {e.name} {m(e.amount)}")
    L.append(f"  End of day: {m(forecast.end_of_day)}")
    L.append("")

    L.append(f"THIS WEEK (to {d(disc.week_end)})")
    L.append(f"  Bills:  {m(disc.bills_this_week)}")
    L.append(f"  Income: {m(disc.income_this_week)}")
    L.append(f"  End of week: {m(forecast.end_of_week)}")
    L.append("")

    L.append(f"AVAILABLE TO SPEND (next {disc.window_days} days)")
    L.append(f"  {m(disc.per_day)} a day, to {d(disc.window_end)}")
    L.append(f"  {m(disc.headroom)} in total before a day goes below zero")
    if disc.headroom_day:
        L.append(f"  Limited by {d(disc.headroom_day)}.")
    if disc.headroom < 0:
        L.append("  The bills alone do not fit. Nothing is free.")
    L.append("")
    L.append("  Bills and income only. No everyday spending is assumed, and")
    L.append("  nothing is held back as a buffer.")
    L.append("")
    L.append("  How that is worked out:")
    for label, value in disc.explain():
        L.append(f"    {label:<38} {m(value):>12}")
    L.append("")

    if upcoming:
        L.append("UPCOMING")
        for e in upcoming[:6]:
            # A deferred occurrence stays on the list and says so. It is still
            # owed; only its date has moved.
            mark = "  (DEFERRED — still owed)" if getattr(e, "deferred", False) else ""
            L.append(f"  {d(e.day)}  {e.name} — {m(e.amount)}{mark}")
        L.append("")

    L.append("FORECAST")
    low = forecast.minimum_day
    if low:
        L.append(f"  Lowest projected balance: {m(low.closing)} on {d(low.day)}")
    L.append(f"  End of month: {m(forecast.end_of_month)}")
    L.append("")

    L.append("RISK")
    if not risks:
        L.append("  No material financial risks detected.")
    else:
        for r in risks[:4]:
            L.append(f"  [{r.severity.upper()}] {r.title}")
            L.append(f"    {r.detail}")
        if plan and plan.problem_day:
            L.append("")
            L.append("  What could be done:")
            L.append(f"    {plan.summary}")
    L.append("")
    L.append("Figures are projections from the balance entered above.")
    return "\n".join(L)


def daily_html(
    today: date,
    balance: Decimal,
    forecast: Forecast,
    disc: Discretionary,
    risks: list[Risk],
    plan: Plan | None,
    upcoming: list,
) -> str:
    day0 = forecast.days[0] if forecast.days else None
    low = forecast.minimum_day
    neg = disc.safe < 0
    accent = "#00A98F"   # OpenFIN teal, darkened for text contrast
    navy = "#0A2540"
    danger = "#b91c1c"

    def row(label: str, value: str, bold: bool = False, color: str = "#0f172a") -> str:
        w = "600" if bold else "400"
        return (
            f'<tr><td style="padding:6px 0;color:#475569;font-size:14px">{escape(label)}</td>'
            f'<td style="padding:6px 0;text-align:right;font-weight:{w};'
            f'color:{color};font-size:14px;white-space:nowrap">{escape(value)}</td></tr>'
        )

    def card(title: str, inner: str, tone: str = accent) -> str:
        return (
            f'<div style="background:#fff;border:1px solid #e2e8f0;border-left:4px solid {tone};'
            f'border-radius:10px;padding:16px;margin:0 0 14px">'
            f'<div style="font-size:11px;letter-spacing:.12em;text-transform:uppercase;'
            f'color:#64748b;font-weight:700;margin-bottom:10px">{escape(title)}</div>'
            f"{inner}</div>"
        )

    parts: list[str] = []

    bills_html = "".join(row(e.name, m(e.amount)) for e in (day0.bills if day0 else []))
    inc_html = "".join(
        row("+ " + e.name, m(e.amount), color=accent) for e in (day0.income if day0 else [])
    )
    if not bills_html and not inc_html:
        bills_html = row("No bills due today", "—")
    parts.append(
        card(
            "Today",
            f"<table style='width:100%;border-collapse:collapse'>{bills_html}{inc_html}"
            f"{row('End of day', m(forecast.end_of_day), bold=True)}</table>",
        )
    )

    parts.append(
        card(
            f"This week — to {d(disc.week_end)}",
            "<table style='width:100%;border-collapse:collapse'>"
            + row("Bills", m(disc.bills_this_week))
            + row("Income", m(disc.income_this_week))
            + row("End of week", m(forecast.end_of_week), bold=True)
            + "</table>",
        )
    )

    explain = "".join(row(lbl, m(val)) for lbl, val in disc.explain()[:-1])
    parts.append(
        card(
            f"Available to spend — next {disc.window_days} days",
            f'<div style="font-size:34px;font-weight:700;color:{danger if neg else accent};'
            f'margin:2px 0 2px">{escape(m(disc.per_day))} a day</div>'
            + '<div style="font-size:13px;color:#4A5568;margin-bottom:12px">'
            + escape(f"{m(disc.headroom)} in total to {d(disc.window_end)}")
            + (escape(f", limited by {d(disc.headroom_day)}") if disc.headroom_day else "")
            + ". Bills and income only — no everyday spending assumed, no buffer held back."
            + "</div>"
            + (
                '<div style="font-size:13px;color:#b91c1c;margin-bottom:10px">'
                "The bills alone do not fit. Nothing is free.</div>"
                if neg
                else ""
            )
            + "<table style='width:100%;border-collapse:collapse'>"
            + explain
            + "</table>",
            danger if neg else accent,
        )
    )

    if upcoming:
        parts.append(
            card(
                "Upcoming",
                "<table style='width:100%;border-collapse:collapse'>"
                + "".join(
                    row(
                        f"{d(e.day)}  {e.name}"
                        + (" — deferred, still owed"
                           if getattr(e, "deferred", False) else ""),
                        m(e.amount),
                    )
                    for e in upcoming[:6]
                )
                + "</table>",
            )
        )

    parts.append(
        card(
            "Forecast",
            "<table style='width:100%;border-collapse:collapse'>"
            + (row("Lowest projected", f"{m(low.closing)} on {d(low.day)}") if low else "")
            + row("End of month", m(forecast.end_of_month))
            + "</table>",
        )
    )

    if not risks:
        risk_inner = '<div style="font-size:14px;color:#15803d">No material financial risks detected.</div>'
        tone = accent
    else:
        blocks = []
        for r in risks[:4]:
            blocks.append(
                f'<div style="margin-bottom:12px"><div style="font-weight:700;'
                f'color:{danger};font-size:14px">{escape(r.title)}</div>'
                f'<div style="font-size:13px;color:#475569;margin-top:3px">'
                f"{escape(r.detail)}</div></div>"
            )
        if plan and plan.problem_day:
            blocks.append(
                f'<div style="background:#f8fafc;border-radius:8px;padding:10px;'
                f'font-size:13px;color:#0f172a"><strong>What could be done:</strong><br>'
                f"{escape(plan.summary)}</div>"
            )
        risk_inner = "".join(blocks)
        tone = danger

    parts.append(card("Risk", risk_inner, tone))

    return (
        '<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,'
        'Arial,sans-serif;background:#f1f5f9;padding:18px;max-width:520px;margin:0 auto">'
        f'<div style="font-size:22px;font-weight:800;color:{navy};margin-bottom:2px">'
        f"{BRAND}</div>"
        f'<div style="font-size:13px;color:#64748b;margin-bottom:4px">Daily financial check — {d(today)}</div>'
        f'<div style="font-size:15px;color:#0f172a;margin-bottom:16px">'
        f"Balance entered: <strong>{escape(m(balance))}</strong></div>"
        + "".join(parts)
        + '<div style="font-size:11px;color:#94a3b8;margin-top:6px">'
        "Projections from the balance entered above.</div></div>"
    )


# --------------------------------------------------------------------------
# risk alerts
# --------------------------------------------------------------------------

ICON = {"critical": "[!]", "high": "[!]", "medium": "[*]", "low": "[.]"}


def alert_subject(risk: Risk) -> str:
    prefix = "URGENT" if risk.severity == "critical" else "WARNING"
    return f"{BRAND} {prefix}: {risk.title}"


def alert_text(risk: Risk, plan: Plan | None, balance_known: bool) -> str:
    L = [f"{BRAND.upper()} — {risk.severity.upper()} ALERT", "", risk.title, ""]
    L.append(risk.detail)
    L.append("")
    if risk.when:
        L.append(f"When:     {d(risk.when)}")
    L.append(f"Amount:   {m(risk.amount)}")
    L.append("")
    if plan and plan.problem_day:
        L.append("WHAT COULD BE DONE")
        L.append(f"  {plan.summary}")
        if plan.protected:
            names = ", ".join(sorted({e.name for e in plan.protected}))
            L.append(f"  Not deferrable: {names}")
        L.append("")
    if not balance_known:
        L.append(
            "No balance was entered today, so this is based on the last known\n"
            "position and the scheduled calendar. Enter today's balance for an\n"
            "exact figure."
        )
    return "\n".join(L)


def alert_html(risk: Risk, plan: Plan | None, balance_known: bool) -> str:
    danger = "#b91c1c" if risk.severity in ("critical", "high") else "#b45309"
    body = (
        f'<div style="font-family:-apple-system,BlinkMacSystemFont,Segoe UI,Helvetica,'
        f'Arial,sans-serif;background:#f1f5f9;padding:18px;max-width:520px;margin:0 auto">'
        f'<div style="font-size:22px;font-weight:800;color:#0A2540">{BRAND}</div>'
        f'<div style="background:#fff;border:1px solid #e2e8f0;border-left:4px solid {danger};'
        f'border-radius:10px;padding:16px;margin-top:12px">'
        f'<div style="font-size:11px;letter-spacing:.12em;text-transform:uppercase;'
        f'color:{danger};font-weight:700">{escape(risk.severity)} alert</div>'
        f'<div style="font-size:19px;font-weight:700;color:#0f172a;margin:8px 0">'
        f"{escape(risk.title)}</div>"
        f'<div style="font-size:14px;color:#334155;line-height:1.5">{escape(risk.detail)}</div>'
        f'<table style="width:100%;border-collapse:collapse;margin-top:12px">'
        + (
            f'<tr><td style="padding:5px 0;color:#475569;font-size:14px">When</td>'
            f'<td style="padding:5px 0;text-align:right;font-weight:600;font-size:14px">'
            f"{escape(d(risk.when))}</td></tr>"
            if risk.when
            else ""
        )
        + f'<tr><td style="padding:5px 0;color:#475569;font-size:14px">Amount</td>'
        f'<td style="padding:5px 0;text-align:right;font-weight:600;font-size:14px">'
        f"{escape(m(risk.amount))}</td></tr></table>"
    )
    if plan and plan.problem_day:
        body += (
            f'<div style="background:#f8fafc;border-radius:8px;padding:12px;margin-top:12px;'
            f'font-size:13px;color:#0f172a"><strong>What could be done</strong><br>'
            f"{escape(plan.summary)}</div>"
        )
    if not balance_known:
        body += (
            '<div style="font-size:12px;color:#64748b;margin-top:12px">'
            "No balance was entered today — this uses the last known position and "
            "the scheduled calendar.</div>"
        )
    return body + "</div></div>"
