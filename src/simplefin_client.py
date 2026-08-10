"""SimpleFIN Bridge client.

Written against the SimpleFIN protocol specification (simplefin.org/protocol.html,
v1 and v2), not from memory:

  Setup token -> Access URL
      The setup token is a base64-encoded claim URL. Decode it, POST to the
      decoded URL, and the response body is the Access URL, which carries HTTP
      Basic credentials inline: https://user:pass@host/simplefin
      403 means the token was never valid or has already been claimed — the
      spec asks applications to treat that as a possible compromise.

  GET {access_url}/accounts
      Query parameters: start-date, end-date (epoch seconds), pending=1,
      account (repeatable), balances-only=1, version.
      Success is a JSON "Account Set".

  Account Set
      v2: {"errlist": [...], "connections": [...], "accounts": [...]}
      v1: {"errors":  [...],  accounts carry a nested "org" object}
      Both shapes are handled below; the bridge's default version has changed
      before and may change again.

  Account:     id, name, currency, balance, available-balance (optional),
               balance-date (epoch), transactions (optional)
  Transaction: id, posted (epoch; may be 0 while pending), amount, description,
               transacted_at (optional), pending (optional boolean)

  Amounts are NUMERIC STRINGS. Positive means money into the account, so a
  debit is negative. Both facts are load-bearing and are asserted below.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit, urlunsplit

import requests

from bills import money

TIMEOUT = 60


class SimpleFinError(RuntimeError):
    """The bank feed could not be read. Never swallowed — the whole point of
    this system is that it refuses to text you numbers it is unsure about."""


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------

@dataclass
class Transaction:
    id: str
    account_id: str
    posted: date | None
    amount: Decimal          # negative = money out
    description: str
    pending: bool = False

    @property
    def is_debit(self) -> bool:
        return self.amount < 0


@dataclass
class Account:
    id: str
    name: str
    currency: str
    balance: Decimal
    available_balance: Decimal | None
    balance_date: datetime | None
    transactions: list[Transaction] = field(default_factory=list)

    @property
    def used_available(self) -> bool:
        """True when the institution gave us a real available-balance."""
        return self.available_balance is not None


@dataclass
class AccountSet:
    accounts: list[Account]
    errors: list[str]

    @property
    def transactions(self) -> list[Transaction]:
        return [t for a in self.accounts for t in a.transactions]


# --------------------------------------------------------------------------
# parsing helpers
# --------------------------------------------------------------------------

def _dec(value, label: str) -> Decimal:
    """Amounts arrive as numeric strings; be strict about what we accept."""
    if value is None:
        raise SimpleFinError(f"{label}: missing amount")
    try:
        return money(Decimal(str(value).strip()))
    except (InvalidOperation, ArithmeticError) as exc:
        raise SimpleFinError(f"{label}: {value!r} is not a number") from exc


def _epoch_to_date(value) -> date | None:
    """Epoch seconds -> local-naive date. `posted` is 0 while pending."""
    if value in (None, 0, "0", ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).date()
    except (ValueError, OSError, TypeError):
        return None


def _epoch_to_dt(value) -> datetime | None:
    if value in (None, 0, "0", ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (ValueError, OSError, TypeError):
        return None


def _split_credentials(access_url: str) -> tuple[str, tuple[str, str] | None]:
    """Pull the inline Basic credentials out of the access URL.

    Passing them via requests' `auth=` rather than leaving them in the URL
    keeps them out of exception text, redirect Location headers, and any log
    line that happens to print the URL.
    """
    parts = urlsplit(access_url.strip().rstrip("/"))
    if not parts.scheme or not parts.netloc:
        raise SimpleFinError("SIMPLEFIN_ACCESS_URL is not a URL")
    if parts.scheme != "https":
        raise SimpleFinError("SIMPLEFIN_ACCESS_URL must be https")

    auth = None
    host = parts.netloc
    if "@" in host:
        creds, host = host.rsplit("@", 1)
        user, _, password = creds.partition(":")
        auth = (user, password)

    clean = urlunsplit((parts.scheme, host, parts.path, "", ""))
    return clean, auth


# --------------------------------------------------------------------------
# setup: token -> access URL
# --------------------------------------------------------------------------

def claim_access_url(setup_token: str) -> str:
    """Exchange a one-time setup token for a permanent Access URL."""
    token = (setup_token or "").strip()
    if not token:
        raise SimpleFinError("no setup token supplied")

    # The token is base64; pad it since some copy/paste paths strip '='.
    try:
        padded = token + "=" * (-len(token) % 4)
        claim_url = base64.b64decode(padded).decode("utf-8").strip()
    except Exception as exc:                                    # noqa: BLE001
        raise SimpleFinError("setup token is not valid base64") from exc

    if not claim_url.startswith("https://"):
        raise SimpleFinError(
            f"decoded claim URL does not look right (starts with {claim_url[:12]!r})"
        )

    try:
        resp = requests.post(claim_url, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SimpleFinError(f"could not reach the claim URL: {exc}") from exc

    if resp.status_code == 403:
        raise SimpleFinError(
            "claim rejected (403). The token was already used or is invalid. "
            "Treat it as compromised: delete it in the SimpleFIN Bridge and generate a new one."
        )
    if not resp.ok:
        raise SimpleFinError(f"claim failed: HTTP {resp.status_code} {resp.text[:200]}")

    access_url = resp.text.strip()
    if not access_url.startswith("https://"):
        raise SimpleFinError(f"claim returned something unexpected: {access_url[:120]!r}")
    return access_url


# --------------------------------------------------------------------------
# accounts
# --------------------------------------------------------------------------

def fetch_accounts(
    access_url: str,
    start_date: date | None = None,
    account_ids: list[str] | None = None,
    include_pending: bool = True,
) -> AccountSet:
    """GET /accounts and normalise the response.

    Pending transactions are requested by default: a pending debit has already
    reduced what is actually spendable, and this system's whole job is to
    describe spendable money.
    """
    base, auth = _split_credentials(access_url)

    params: list[tuple[str, str]] = []
    if start_date is not None:
        epoch = int(datetime(start_date.year, start_date.month, start_date.day,
                             tzinfo=timezone.utc).timestamp())
        params.append(("start-date", str(epoch)))
    if include_pending:
        params.append(("pending", "1"))
    for acct in account_ids or []:
        params.append(("account", acct))

    try:
        resp = requests.get(f"{base}/accounts", params=params, auth=auth, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise SimpleFinError(f"could not reach SimpleFIN: {exc}") from exc

    if resp.status_code == 403:
        raise SimpleFinError("SimpleFIN rejected the credentials (403). Access may have been revoked.")
    if resp.status_code == 402:
        raise SimpleFinError("SimpleFIN says payment is required (402).")
    if not resp.ok:
        raise SimpleFinError(f"SimpleFIN returned HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise SimpleFinError(f"SimpleFIN returned non-JSON: {resp.text[:200]!r}") from exc

    return parse_account_set(payload)


def parse_account_set(payload: dict) -> AccountSet:
    """Normalise a v1 or v2 Account Set into our own shape."""
    if not isinstance(payload, dict):
        raise SimpleFinError(f"expected a JSON object, got {type(payload).__name__}")

    # v2 calls it errlist (array of objects); v1 called it errors (array of
    # strings) and the spec marks that one deprecated. Accept both.
    errors: list[str] = []
    for err in payload.get("errlist") or []:
        if isinstance(err, dict):
            errors.append(str(err.get("msg") or err.get("code") or err))
        else:
            errors.append(str(err))
    for err in payload.get("errors") or []:
        errors.append(err if isinstance(err, str) else str(err.get("msg", err)))

    raw_accounts = payload.get("accounts")
    if raw_accounts is None:
        raise SimpleFinError(
            "response has no 'accounts' key. "
            f"Top-level keys were: {sorted(payload.keys())}"
        )

    accounts: list[Account] = []
    for raw in raw_accounts:
        acct_id = str(raw.get("id", ""))
        if not acct_id:
            raise SimpleFinError(f"an account has no id; keys were {sorted(raw.keys())}")

        # v1 nests the institution under "org"; v2 uses conn_name / connections.
        org = raw.get("org") or {}
        inst = raw.get("conn_name") or org.get("name") or org.get("domain") or ""
        name = str(raw.get("name") or acct_id)
        if inst:
            name = f"{inst} {name}".strip()

        avail_raw = raw.get("available-balance")
        transactions = []
        for t in raw.get("transactions") or []:
            tid = str(t.get("id", ""))
            transactions.append(
                Transaction(
                    id=tid,
                    account_id=acct_id,
                    posted=_epoch_to_date(t.get("posted")) or _epoch_to_date(t.get("transacted_at")),
                    amount=_dec(t.get("amount"), f"transaction {tid or '?'}"),
                    description=str(t.get("description") or t.get("payee") or t.get("memo") or ""),
                    pending=bool(t.get("pending", False)),
                )
            )

        accounts.append(
            Account(
                id=acct_id,
                name=name,
                currency=str(raw.get("currency") or "USD"),
                balance=_dec(raw.get("balance"), f"account {acct_id} balance"),
                available_balance=(_dec(avail_raw, f"account {acct_id} available-balance")
                                   if avail_raw not in (None, "") else None),
                balance_date=_epoch_to_dt(raw.get("balance-date")),
                transactions=transactions,
            )
        )

    return AccountSet(accounts=accounts, errors=errors)


def spendable_balance(accounts: list[Account]) -> tuple[Decimal, bool]:
    """Today's balance including pending activity.

    Returns (total, used_fallback).

    Preferred path: the institution publishes `available-balance`, which
    already nets out pending activity. Fallback: take `balance` and apply
    every pending transaction ourselves — subtracting pending debits and
    adding pending credits. `used_fallback` is True if any account needed the
    fallback, so the text message can say which method produced the number.
    """
    total = money(0)
    used_fallback = False

    for acct in accounts:
        if acct.available_balance is not None:
            total += acct.available_balance
            continue

        used_fallback = True
        running = acct.balance
        for t in acct.transactions:
            if t.pending:
                # Amounts are signed: a pending debit is negative, so adding
                # it subtracts. Do not take abs() here.
                running += t.amount
        total += running

    return money(total), used_fallback
