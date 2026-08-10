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
    sent: list[str] = []
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

        if resp.ok:
            sent.append(to)
        else:
            failures.append(f"{to}: {_twilio_error(resp)}")

    if failures and not sent:
        raise MessagingError("Twilio rejected every recipient — " + "; ".join(failures))
    if failures:
        # Partial delivery still counts as delivered, but say so loudly in the log.
        print(f"WARNING: Twilio failed for some recipients — {'; '.join(failures)}")
    return sent


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
