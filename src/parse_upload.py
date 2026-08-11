"""Read a bill screenshot or spreadsheet, diff it against bills.json, commit.

Anthropic Messages API, from the current reference:

    POST https://api.anthropic.com/v1/messages
    Headers: x-api-key, anthropic-version: 2023-06-01, content-type: application/json
    Image block:
        {"type": "image",
         "source": {"type": "base64", "media_type": "image/png", "data": "<b64>"}}
    Response: {"content": [{"type": "text", "text": "..."}],
               "stop_reason": "...", "usage": {...}}

Two safety properties matter more than convenience here:

  * A bill the model failed to read is NEVER deleted. It is deactivated with a
    note. OCR misses a line far more often than a household actually cancels a
    bill, and a silently vanished mortgage is the worst possible failure.
  * Zero parsed rows changes nothing at all and texts a failure notice.

Structured outputs (`output_config.format`) are requested so the model returns
schema-valid JSON rather than prose. If the configured model does not support
them the request is retried without, and the defensive parser handles whatever
comes back — code fences, a prose preamble, or a bare array.
"""

from __future__ import annotations

import base64
import csv
import io
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests

import messaging
from bills import (
    VALID_FREQUENCIES,
    BillError,
    fmt_money,
    load_items,
    money,
    save_items,
    validate,
)

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config.json"
BILLS = ROOT / "bills.json"
INBOX = ROOT / "inbox"
PROCESSED = INBOX / "processed"

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
TIMEOUT = 180
MAX_TOKENS = 16000

REVIEW_PCT = Decimal("0.40")        # amount moved by more than 40%
REVIEW_ABS = Decimal("10000.00")    # ...or is implausibly large

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
SHEET_TYPES = {".xlsx", ".xlsm", ".csv"}

EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "bills": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "amount": {"type": ["number", "null"]},
                    "due_day": {"type": ["integer", "null"]},
                    "frequency": {
                        "type": ["string", "null"],
                        "enum": [*sorted(VALID_FREQUENCIES), None],
                    },
                    "due_date": {"type": ["string", "null"]},
                },
                "required": ["name", "amount", "due_day", "frequency", "due_date"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["bills"],
    "additionalProperties": False,
}

PROMPT = """\
You are reading a household bill statement, bill list, or budget spreadsheet.

Extract every recurring bill you can see. Return ONLY a JSON array — no prose,
no explanation, no markdown code fences.

Each element must have exactly these keys:

  "name"      string  — the biller's name as shown
  "amount"    number  — the recurring amount in dollars, or null if unreadable
  "due_day"   integer — day of the month, 1-31, or null
  "frequency" string  — one of: monthly, weekly, biweekly, semimonthly, annual, once
  "due_date"  string  — ISO YYYY-MM-DD, only for "once" or "annual"; otherwise null

Rules:

  * Use null for anything you cannot read with confidence. NEVER guess a number.
    A null is useful; an invented amount is harmful.
  * Where the source shows a recurring monthly bill, normalise the date to a
    day-of-month integer in "due_day" and leave "due_date" null.
  * Do not include one-off purchases, account balances, totals, subtotals, or
    payments already made. Only recurring obligations.
  * If you can see no bills at all, return an empty array: []
"""


class IngestError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# reading the upload
# --------------------------------------------------------------------------

def sheet_to_text(path: Path) -> str:
    """Flatten a spreadsheet or CSV into a plain text grid for the model."""
    rows: list[list[str]] = []

    if path.suffix.lower() == ".csv":
        with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
            rows = [[(c or "").strip() for c in row] for row in csv.reader(fh)]
    else:
        try:
            import openpyxl
        except ImportError as exc:                              # pragma: no cover
            raise IngestError("openpyxl is required to read .xlsx files") from exc

        book = openpyxl.load_workbook(path, data_only=True, read_only=True)
        for sheet in book.worksheets:
            rows.append([f"# sheet: {sheet.title}"])
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if c is None else str(c).strip() for c in row]
                if any(cells):
                    rows.append(cells)
        book.close()

    lines = [" | ".join(r) for r in rows if any(c for c in r)]
    if not lines:
        raise IngestError(f"{path.name} has no readable rows")
    return "\n".join(lines[:2000])


def build_content(path: Path) -> list[dict]:
    suffix = path.suffix.lower()

    if suffix in IMAGE_TYPES:
        data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
        return [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": IMAGE_TYPES[suffix],
                    "data": data,
                },
            },
            {"type": "text", "text": PROMPT},
        ]

    if suffix in SHEET_TYPES:
        return [
            {"type": "text", "text": f"{PROMPT}\n\nSpreadsheet contents:\n\n{sheet_to_text(path)}"}
        ]

    raise IngestError(
        f"{path.name}: unsupported file type {suffix!r}. "
        f"Upload one of: {', '.join(sorted(IMAGE_TYPES) + sorted(SHEET_TYPES))}"
    )


# --------------------------------------------------------------------------
# the model call
# --------------------------------------------------------------------------

def call_model(content: list[dict], model: str) -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        raise IngestError("missing secret: ANTHROPIC_API_KEY")

    headers = {
        "x-api-key": api_key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }
    base = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": content}],
    }

    # Ask for schema-constrained JSON first. Older models reject the field, so
    # fall back to plain prompting and let the defensive parser earn its keep.
    attempts = [
        {**base, "output_config": {"format": {"type": "json_schema", "schema": EXTRACTION_SCHEMA}}},
        base,
    ]

    last_error = ""
    for index, payload in enumerate(attempts):
        try:
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=TIMEOUT)
        except requests.RequestException as exc:
            raise IngestError(f"could not reach the Anthropic API: {exc}") from exc

        if resp.ok:
            data = resp.json()
            if data.get("stop_reason") == "refusal":
                raise IngestError("the model declined to read this file")
            if data.get("stop_reason") == "max_tokens":
                print("WARNING: response hit max_tokens; the extraction may be truncated.")
            texts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
            if not texts:
                raise IngestError(f"model returned no text. stop_reason={data.get('stop_reason')}")
            usage = data.get("usage", {})
            print(f"Model: {data.get('model')} | in={usage.get('input_tokens')} "
                  f"out={usage.get('output_tokens')}")
            return "\n".join(texts)

        last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
        if index == 0 and resp.status_code == 400:
            print("Structured outputs rejected by this model; retrying without them.")
            continue
        break

    raise IngestError(f"Anthropic API call failed — {last_error}")


def parse_rows(raw: str) -> list[dict]:
    """Pull a JSON array out of whatever the model returned.

    Handles: a bare array, an object with a "bills" key (what the schema asks
    for), markdown code fences, and a prose preamble before the JSON.
    """
    text = (raw or "").strip()

    if "```" in text:
        chunks = text.split("```")
        for chunk in chunks[1:]:
            candidate = chunk.split("\n", 1)[-1] if chunk[:20].lower().startswith("json") else chunk
            candidate = candidate.strip()
            if candidate.startswith(("[", "{")):
                text = candidate
                break

    parsed = None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        for opener, closer in (("[", "]"), ("{", "}")):
            start, end = text.find(opener), text.rfind(closer)
            if start != -1 and end > start:
                try:
                    parsed = json.loads(text[start:end + 1])
                    break
                except json.JSONDecodeError:
                    continue

    if parsed is None:
        raise IngestError(f"could not find JSON in the model's reply: {text[:200]!r}")

    if isinstance(parsed, dict):
        parsed = parsed.get("bills", [])
    if not isinstance(parsed, list):
        raise IngestError(f"expected a JSON array, got {type(parsed).__name__}")

    rows: list[dict] = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        if entry.get("amount") is None:
            print(f"  skipping {name!r}: no amount could be read")
            continue
        try:
            amount = money(entry["amount"])
        except Exception:                                       # noqa: BLE001
            print(f"  skipping {name!r}: amount {entry.get('amount')!r} is not a number")
            continue
        if amount <= 0:
            print(f"  skipping {name!r}: amount is not positive")
            continue

        frequency = (entry.get("frequency") or "monthly").strip().lower()
        if frequency not in VALID_FREQUENCIES:
            print(f"  {name!r}: unknown frequency {frequency!r}; treating as monthly")
            frequency = "monthly"

        due_day = entry.get("due_day")
        if isinstance(due_day, str) and due_day.strip().isdigit():
            due_day = int(due_day.strip())
        if not isinstance(due_day, int) or not 1 <= due_day <= 31:
            due_day = None

        rows.append({
            "name": name,
            "amount": amount,
            "due_day": due_day,
            "frequency": frequency,
            "due_date": entry.get("due_date") or None,
        })

    return rows


# --------------------------------------------------------------------------
# the diff
# --------------------------------------------------------------------------

def slug(name: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in name)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "bill"


def diff_and_apply(existing: list[dict], rows: list[dict]) -> tuple[list[dict], dict]:
    """Merge extracted rows into the current bill list.

    Classifications: ADDED, AMOUNT CHANGED, DATE CHANGED, UNCHANGED, MISSING.
    MISSING is never a delete — the bill is deactivated with a note.
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    by_name = {b["name"].strip().lower(): b for b in existing}
    seen: set[str] = set()
    report = {"added": [], "amount_changed": [], "date_changed": [],
              "unchanged": [], "deactivated": [], "needs_review": []}

    for row in rows:
        key = row["name"].strip().lower()
        seen.add(key)
        current = by_name.get(key)

        if current is None:
            new_id = slug(row["name"])
            taken = {b["id"] for b in existing}
            n = 2
            while new_id in taken:
                new_id, n = f"{slug(row['name'])}-{n}", n + 1

            bill = {
                "id": new_id,
                "name": row["name"],
                "amount": float(row["amount"]),
                "due_day": row["due_day"] or 1,
                "frequency": row["frequency"],
                "due_date": row["due_date"],
                "anchor_date": None,
                "autopay": False,
                "match_keywords": [row["name"].split()[0].lower()],
                "active": True,
                "last_updated": now,
                "source": "screenshot",
            }
            if row["amount"] > REVIEW_ABS:
                bill["needs_review"] = True
                report["needs_review"].append(f"{row['name']} {fmt_money(row['amount'])} (over $10k)")
            existing.append(bill)
            by_name[key] = bill
            report["added"].append(f"{row['name']} {fmt_money(row['amount'])}")
            continue

        changed = False
        old_amount = money(current.get("amount", 0))
        new_amount = row["amount"]

        if old_amount != new_amount:
            if old_amount > 0 and abs(new_amount - old_amount) / old_amount > REVIEW_PCT:
                current["needs_review"] = True
                report["needs_review"].append(
                    f"{current['name']} {fmt_money(old_amount)}->{fmt_money(new_amount)} (>40%)"
                )
            if new_amount > REVIEW_ABS:
                current["needs_review"] = True
                report["needs_review"].append(
                    f"{current['name']} {fmt_money(new_amount)} (over $10k)"
                )
            current["amount"] = float(new_amount)
            report["amount_changed"].append(
                f"{current['name']} {int(old_amount)}->{int(new_amount)}"
            )
            changed = True

        if row["due_day"] and row["due_day"] != current.get("due_day"):
            report["date_changed"].append(
                f"{current['name']} due {current.get('due_day')}->{row['due_day']}"
            )
            current["due_day"] = row["due_day"]
            changed = True

        if not current.get("active", True):
            current["active"] = True
            current.pop("note", None)
            changed = True

        if changed:
            current["last_updated"] = now
            current["source"] = "screenshot"
        else:
            report["unchanged"].append(current["name"])

    # Anything the upload did not mention is deactivated, never deleted.
    for bill in existing:
        if bill["name"].strip().lower() in seen:
            continue
        if not bill.get("active", True):
            continue
        bill["active"] = False
        bill["note"] = f"Not present in upload of {now[:10]}; deactivated, not deleted."
        bill["last_updated"] = now
        report["deactivated"].append(bill["name"])

    return existing, report


def summarise(report: dict) -> str:
    counts = (
        f"BILLS UPDATED: {len(report['amount_changed']) + len(report['date_changed'])} changed, "
        f"{len(report['added'])} added, {len(report['deactivated'])} deactivated."
    )
    details = report["amount_changed"] + report["date_changed"] + report["added"]
    lines = [counts]
    if details:
        lines.append(" ".join(f"{d}." for d in details[:6]))
    if report["deactivated"]:
        lines.append("Deactivated: " + ", ".join(report["deactivated"][:4]) + ".")
    if report["needs_review"]:
        lines.append("NEEDS REVIEW: " + "; ".join(report["needs_review"][:3]))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def pending_uploads() -> list[Path]:
    if not INBOX.exists():
        return []
    return sorted(
        p for p in INBOX.iterdir()
        if p.is_file() and p.name != ".gitkeep" and not p.name.startswith(".")
    )


def main() -> int:
    dry_run = os.environ.get("DRY_RUN", "").strip().lower() in {"1", "true", "yes", "on"}
    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    model = config["parser_model"]

    uploads = pending_uploads()
    if not uploads:
        print("Nothing in inbox/. Nothing to do.")
        return 0

    existing = load_items(BILLS, "bills")
    total = {"added": [], "amount_changed": [], "date_changed": [],
             "unchanged": [], "deactivated": [], "needs_review": []}
    processed_any = False

    for upload in uploads:
        print(f"\n--- {upload.name} ---")
        try:
            rows = parse_rows(call_model(build_content(upload), model))
        except IngestError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            messaging.send_mail(
                "OpenFIN: bill upload",
                f"BILL UPLOAD FAILED: {upload.name}\n{exc}\nNothing was changed.",
                dry_run=dry_run,
            )
            return 1

        print(f"Parsed {len(rows)} row(s).")
        if not rows:
            messaging.send_mail(
                "OpenFIN: bill upload",
                f"BILL UPLOAD FAILED: no bills could be read from {upload.name}. "
                f"Nothing was changed.",
                dry_run=dry_run,
            )
            return 1

        existing, report = diff_and_apply(existing, rows)
        for key in total:
            total[key].extend(report[key])
        processed_any = True

        if not dry_run:
            PROCESSED.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            shutil.move(str(upload), str(PROCESSED / f"{stamp}-{upload.name}"))

    if not processed_any:
        return 0

    for bill in existing:
        try:
            validate(bill)
        except BillError as exc:
            print(f"ERROR: the merged bill list is invalid — {exc}", file=sys.stderr)
            messaging.send_mail(
                "OpenFIN: bill upload",
                f"BILL UPLOAD REJECTED: merged list failed validation.\n{exc}\n"
                f"bills.json was NOT changed.",
                dry_run=dry_run,
            )
            return 1

    summary = summarise(total)
    print("\n" + summary)

    if dry_run:
        print("\nDry run: bills.json was not written and no file was moved.")
        messaging.send_mail("OpenFIN: bill upload", summary, dry_run=True)
        return 0

    save_items(BILLS, "bills", existing)
    messaging.send_mail("OpenFIN: bill upload", summary, dry_run=False)
    print("Wrote bills.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
