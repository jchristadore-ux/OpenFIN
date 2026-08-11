"""SMS delivery: Twilio, or a carrier email-to-SMS gateway.

Twilio, from the official API surface (verified against twilio-python's
generated client rather than recalled):

    POST https://api.twilio.com/2010-04-01/Accounts/{AccountSid}/Messages.json
    Auth:         HTTP Basic (AccountSid, AuthToken)
    Content-Type: application/x-www-form-urlencoded
    Body:         To, From, Body
    Success:      201 Created
    Errors:       either the legacy shape {code, message, more_info, status}
                  or RFC-9457 {type, title, status, code, detail}

No Twilio SDK — a single form POST does not justify a dependency.

The gateway path exists because A2P 10DLC registration can sit in review for
weeks, and a cash-flow alert that arrives in three weeks is not an alert.
Flip "sms_provider" in config.json to switch; both paths are exercised by the
same send() call.
"""

from __future__ import annotations

import os
import time
import re
import smtplib
from email.message import EmailMessage

import requests

TWILIO_BASE = "https://api.twilio.com"
TWILIO_VERSION = "2010-04-01"
TIMEOUT = 30

SMTP_HOST = os.environ.get("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))


class MessagingError(RuntimeError):
    """A message could not be delivered."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise MessagingError(f"missing secret: {name}")
    return value


def _recipients(name: str) -> list[str]:
    raw = _require(name)
    out = [part.strip() for part in raw.split(",") if part.strip()]
    if not out:
        raise MessagingError(f"{name} is set but contains no addresses")
    return out


# E.164: a literal '+', a non-zero country code digit, then 7-14 more digits.
E164 = re.compile(r"^\+[1-9]\d{7,14}$")


def _check_e164(number: str, secret: str) -> str:
    """Reject a phone number Twilio would reject, with a usable explanation.

    This exists because a spreadsheet will happily turn
        +19085551234,+19085555678
    into
        19,085,551,234,190,855,556,780
    by reading it as one enormous number and adding thousands separators.
    Split on commas, that yields eight meaningless fragments, and the only
    symptom without this check is a wall of Twilio 21211 errors. Catching it
    here names the offending value and says what it should look like.
    """
    number = number.strip()
    if E164.match(number):
        return number

    if number.isdigit():
        hint = f"missing the leading '+' — try '+{number}'"
    elif " " in number or "-" in number or "(" in number:
        stripped = "".join(c for c in number if c.isdigit())
        hint = f"remove spaces, dashes and brackets — try '+{stripped}'"
    else:
        hint = (
            "expected E.164: a '+' then digits only. If this looks like part of a "
            "comma-grouped number, a spreadsheet reformatted it — retype the value "
            "by hand instead of pasting from a cell"
        )

    raise MessagingError(f"{secret}: {number!r} is not a valid phone number — {hint}")


# --------------------------------------------------------------------------
# Twilio
# --------------------------------------------------------------------------

def _twilio_error(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"

    if not isinstance(payload, dict):
        return f"HTTP {resp.status_code}: {resp.text[:200]}"

    # RFC-9457 problem details: type/title/status/code/detail
    if {"type", "title", "status", "code"} <= set(payload):
        detail = payload.get("detail") or payload.get("title")
        return f"HTTP {resp.status_code} [{payload.get('code')}]: {detail}"

    # Legacy Twilio error object
    code = payload.get("code")
    message = payload.get("message") or resp.text[:200]
    more_info = payload.get("more_info")
    suffix = f" ({more_info})" if more_info else ""
    return f"HTTP {resp.status_code} [{code}]: {message}{suffix}"


def send_twilio(body: str) -> list[str]:
    sid = _require("TWILIO_ACCOUNT_SID")
    token = _require("TWILIO_AUTH_TOKEN")

    # Validate before spending a request. Every number is checked, so one bad
    # entry reports itself rather than surfacing as a Twilio error later.
    from_number = _check_e164(_require("TWILIO_FROM_NUMBER"), "TWILIO_FROM_NUMBER")
    to_numbers = [
        _check_e164(n, "ALERT_TO_NUMBERS") for n in _recipients("ALERT_TO_NUMBERS")
    ]

    url = f"{TWILIO_BASE}/{TWILIO_VERSION}/Accounts/{sid}/Messages.json"
    accepted: list[tuple[str, str]] = []      # (to, message_sid)
    failures: list[str] = []

    for to in to_numbers:
        try:
            resp = requests.post(
                url,
                data={"To": to, "From": from_number, "Body": body},
                auth=(sid, token),
                timeout=TIMEOUT,
            )
        except requests.RequestException as exc:
            failures.append(f"{to}: {exc}")
            continue

        if not resp.ok:
            failures.append(f"{to}: {_twilio_error(resp)}")
            continue

        try:
            payload = resp.json()
        except ValueError:
            payload = {}
        message_sid = payload.get("sid", "")
        print(f"  accepted by Twilio: {to} sid={message_sid or '?'} "
              f"status={payload.get('status', '?')}")
        accepted.append((to, message_sid))

    if failures and not accepted:
        raise MessagingError("Twilio rejected every recipient — " + "; ".join(failures))
    if failures:
        print(f"WARNING: Twilio failed for some recipients — {'; '.join(failures)}")

    # Acceptance is not delivery. Twilio answers 201 the moment it queues a
    # message; whether a carrier actually took it is decided seconds later and
    # reported only on the message resource. Without this check an
    # unregistered A2P number looks like a clean success in the log while no
    # phone ever buzzes — which is exactly what happened.
    delivered, undelivered = _confirm_delivery(sid, token, accepted)

    if undelivered and not delivered:
        raise MessagingError(
            "Twilio accepted every message but the carrier delivered none — "
            + "; ".join(undelivered)
        )
    if undelivered:
        print(f"WARNING: not delivered to some recipients — {'; '.join(undelivered)}")

    return [to for to, _ in accepted]


# Terminal delivery states, and what they mean.
_DELIVERED = {"delivered", "sent", "received"}
_UNDELIVERED = {"undelivered", "failed"}

# The Twilio error codes worth explaining rather than just quoting.
_ERROR_HINTS = {
    "30034": ("the sending number is not registered for A2P 10DLC. US carriers "
              "drop unregistered traffic. Register the brand and campaign in "
              "Twilio (Messaging -> Regulatory Compliance -> A2P 10DLC), or "
              "switch sms_provider to email_gateway until it clears."),
    "21608": ("trial account: the destination is not a Verified Caller ID. Add "
              "it under Phone Numbers -> Verified Caller IDs, or upgrade the "
              "account."),
    "30007": ("the carrier filtered the message as spam. Usually A2P "
              "registration, or wording that trips a filter."),
    "30003": "the handset is unreachable — off, out of coverage, or blocked.",
    "30005": "unknown destination — the number does not exist on that carrier.",
    "30006": "landline or unreachable carrier.",
    "21610": "that number replied STOP. It must reply START before it can be texted again.",
}


def _confirm_delivery(
    account_sid: str,
    token: str,
    accepted: list[tuple[str, str]],
    *,
    attempts: int = 6,
    interval: float = 3.0,
) -> tuple[list[str], list[str]]:
    """Poll each accepted message until its delivery state settles.

    Returns (delivered, undelivered-with-reason). A message still queued when
    the polling budget runs out is reported as pending rather than failed —
    slow is not the same as broken, and this must not cry wolf.
    """
    pending = {sid_: to for to, sid_ in accepted if sid_}
    delivered: list[str] = []
    undelivered: list[str] = []

    if not pending:
        return delivered, undelivered

    for attempt in range(attempts):
        time.sleep(interval)
        for message_sid in list(pending):
            to = pending[message_sid]
            url = (f"{TWILIO_BASE}/{TWILIO_VERSION}/Accounts/{account_sid}"
                   f"/Messages/{message_sid}.json")
            try:
                resp = requests.get(url, auth=(account_sid, token), timeout=TIMEOUT)
            except requests.RequestException:
                continue
            if not resp.ok:
                continue

            try:
                data = resp.json()
            except ValueError:
                continue

            status = str(data.get("status", "")).lower()
            if status in _DELIVERED:
                print(f"  delivered: {to} ({status})")
                delivered.append(to)
                del pending[message_sid]
            elif status in _UNDELIVERED:
                code = str(data.get("error_code") or "")
                message = data.get("error_message") or "no reason given"
                hint = _ERROR_HINTS.get(code)
                detail = f"{to}: {status} [{code or 'no code'}] {message}"
                if hint:
                    detail += f" -- {hint}"
                print(f"  NOT DELIVERED: {detail}")
                undelivered.append(detail)
                del pending[message_sid]

        if not pending:
            break

    for message_sid, to in pending.items():
        print(f"  still pending after {attempts * interval:.0f}s: {to} "
              f"(sid={message_sid}). Check Twilio -> Monitor -> Logs -> Messaging.")

    return delivered, undelivered


# --------------------------------------------------------------------------
# carrier email-to-SMS gateway
# --------------------------------------------------------------------------

def send_email_gateway(body: str) -> list[str]:
    user = _require("SMTP_USER")
    password = _require("SMTP_APP_PASSWORD")
    to_addresses = _recipients("SMS_GATEWAY_ADDRESSES")

    # Carriers prepend the subject to the message body, so leave it empty.
    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = ", ".join(to_addresses)
    msg["Subject"] = ""
    msg.set_content(body)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MessagingError(f"SMTP delivery failed: {exc}") from exc

    return to_addresses


# --------------------------------------------------------------------------
# real email — the primary channel
# --------------------------------------------------------------------------

def send_mail(
    subject: str,
    text_body: str,
    html_body: str | None = None,
    *,
    dry_run: bool = False,
) -> list[str]:
    """Send a proper email with a subject line to EMAIL_RECIPIENTS.

    This is the primary user-facing channel. `send_email_gateway` below is a
    different thing that happens to share a transport: it pushes a body through
    a carrier's email-to-SMS bridge and must keep its subject empty. Do not
    merge the two.

    A multipart/alternative message is built when `html_body` is supplied, so a
    plain-text reader still gets a usable message.
    """
    if dry_run:
        print("=" * 60)
        print(f"DRY RUN — would email: {subject}")
        print("-" * 60)
        print(text_body)
        print("=" * 60)
        return []

    user = _require("SMTP_USER")
    password = _require("SMTP_APP_PASSWORD")
    to_addresses = _recipients("EMAIL_RECIPIENTS")

    msg = EmailMessage()
    msg["From"] = os.environ.get("EMAIL_FROM", user)
    msg["To"] = ", ".join(to_addresses)
    msg["Subject"] = subject
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=TIMEOUT) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MessagingError(f"SMTP delivery failed: {exc}") from exc

    return to_addresses


# --------------------------------------------------------------------------
# dispatch
# --------------------------------------------------------------------------

def send(body: str, provider: str, *, dry_run: bool = False) -> list[str]:
    """Deliver `body`. In dry-run mode, print it and send nothing."""
    if dry_run:
        print("=" * 60)
        print(f"DRY RUN — would send via {provider} ({len(body)} chars):")
        print("-" * 60)
        print(body)
        print("=" * 60)
        return []

    if provider == "twilio":
        return send_twilio(body)
    if provider == "email_gateway":
        return send_email_gateway(body)
    raise MessagingError(
        f"unknown sms_provider {provider!r} — expected 'twilio' or 'email_gateway'"
    )
