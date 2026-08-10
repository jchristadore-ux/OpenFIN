# Setup

Everything here happens in a web browser. No terminal, no installs.

## 1. Secrets to create

**Settings → Secrets and variables → Actions → New repository secret.** Names must match exactly.

| Secret | Format | Where it comes from |
|---|---|---|
| `SIMPLEFIN_ACCESS_URL` | `https://user:pass@beta-bridge.simplefin.org/simplefin` | Step 2 below prints it |
| `ANTHROPIC_API_KEY` | `sk-ant-api03-...` | Step 5 below |
| `TWILIO_ACCOUNT_SID` | `AC` + 32 hex characters | Twilio console home |
| `TWILIO_AUTH_TOKEN` | 32 hex characters | Twilio console home, behind **Show** |
| `TWILIO_FROM_NUMBER` | `+15551234567` (E.164, leading `+`) | The Twilio number you buy |
| `ALERT_TO_NUMBERS` | `+15551234567,+15559876543` (comma separated, no spaces) | Your phone, and your wife's |
| `SMTP_USER` | `you@gmail.com` | Your Gmail address |
| `SMTP_APP_PASSWORD` | 16 characters, e.g. `abcd efgh ijkl mnop` | Step 4 below |
| `SMS_GATEWAY_ADDRESSES` | `5551234567@vtext.com,5559876543@tmomail.net` | Step 4 below |

You only need the secret set matching `sms_provider` in `config.json`; add the other later if you switch.

## 2. SimpleFIN

1. Go to **https://beta-bridge.simplefin.org**, create an account, click **Connect an account**, and log in to your bank through their screen.
2. Once it shows as connected: **My Account → Setup Token → Generate**. Copy the whole token.
3. In this repo: **Actions → "Setup — claim SimpleFIN access URL" → Run workflow**. Paste the token, click the green **Run workflow**.
4. Open the finished run and expand the step. It prints the URL, and also its three parts separately:

   ```
     username : abc123
     password : xyz789
     host     : beta-bridge.simplefin.org/simplefin

     https://abc123:xyz789@beta-bridge.simplefin.org/simplefin
   ```

   Save that last line as `SIMPLEFIN_ACCESS_URL`. **If it shows `***` instead**, the secret already exists and GitHub is redacting it — assemble the URL yourself from the three parts above, which are printed for exactly that reason.
5. **Delete the run** — `...` menu, top right → **Delete run**. The log holds a live credential until you do.

**A setup token works exactly once.** Running the workflow a second time with the same token fails with 403. If you need to redo it, generate a fresh token in the SimpleFIN Bridge first.

## 3. Twilio

1. Sign up at **https://www.twilio.com/try-twilio** and verify your own mobile number.
2. On the console home page, copy **Account SID** and **Auth Token** into the secrets from step 1.
3. **Phone Numbers → Buy a number.** Tick **SMS** under capabilities. Pick any US local number, around $1.15/month.
4. Save it as `TWILIO_FROM_NUMBER` in E.164 form — `+1` then ten digits, no spaces or dashes: `+19085551234`.
5. Save your phone numbers as `ALERT_TO_NUMBERS`, comma separated: `+19085551234,+19085555678`.

> ⚠️ **Type these straight into the GitHub secret box. Do not paste them out of a spreadsheet.**
> Excel reads `+19085551234,+19085555678` as one enormous number and reformats it to
> `19,085,551,234,190,855,556,780`. The phone numbers are then unrecoverable — the commas
> have eaten the boundary between them. The code rejects mangled numbers with a clear
> message rather than sending them, but it cannot repair them.

**About A2P 10DLC.** US carriers require every business-owned number sending to US phones to be registered. Twilio will prompt you; it is a form about who you are and what you send, then a wait. Trial messages to your own verified number work immediately.

**If registration stalls or gets rejected:** don't fight it. Open `config.json`, change `"sms_provider": "twilio"` to `"sms_provider": "email_gateway"`, commit, and do step 4. Everything else keeps working.

## 4. Email gateway (the fallback)

1. Turn on 2-Step Verification: **https://myaccount.google.com/security**.
2. Go to **https://myaccount.google.com/apppasswords**, type any name, click **Create**. Save the 16-character password as `SMTP_APP_PASSWORD` and your Gmail address as `SMTP_USER`.
3. Build `SMS_GATEWAY_ADDRESSES` from your 10-digit numbers and this table:

| Carrier | Address form | Example |
|---|---|---|
| Verizon | `number@vtext.com` | `5551234567@vtext.com` |
| AT&T | `number@txt.att.net` | `5551234567@txt.att.net` |
| T-Mobile | `number@tmomail.net` | `5551234567@tmomail.net` |

Comma separated, no spaces, digits only — no `+1`, no dashes.

4. Set `"sms_provider": "email_gateway"` in `config.json`.

## 5. Anthropic API key

Go to **https://console.anthropic.com**, add a little credit under **Billing**, then **API Keys → Create Key**. Save it as `ANTHROPIC_API_KEY`. Used only when you upload a bill screenshot — under a cent a time.

## 6. Run a dry run

**Actions → "Daily cash brief" → Run workflow ▾.** A small panel drops down with a branch selector and a **What to do** dropdown:

| Mode | What happens |
|---|---|
| `preview` (default) | Prints the text to the log. Sends nothing, saves nothing. |
| `send-now` | Really sends, ignoring the "is it 7am?" check. |

Leave it on `preview`, click the green **Run workflow**. Open the run → the `brief` job → expand **Send the daily brief**. You will see the exact text:

```
DRY RUN — would send via twilio (166 chars):
------------------------------------------------------------
CASH 08/09
Now: $3,512.20 (incl pending)
Bills today: $750.00
 - Hanover Auto $750.00 (PAID)
Disc spent: $140.36 of $100.00
End of day: $3,512.20

Clear through 09/23
============================================================
```

Nothing was sent and nothing was saved. To send it for real right now, run it again with the dropdown on **`send-now`**.

If the log says `Local time is 20:31 EDT; send_hour_local is 07. Nothing to do.` you left it on `preview` — that message means the run stopped at the send-hour gate.

If it says `CASH DATA STALE`, the bank feed failed. The reason is on the line below it in the message and in red in the log.

**If the log says the message was sent but no text arrives**, look for the delivery lines:

```
  accepted by Twilio: +19085551234 sid=SM… status=queued
  NOT DELIVERED: +19085551234: undelivered [30034] … -- the sending number is
  not registered for A2P 10DLC. US carriers drop unregistered traffic.
```

Twilio answers `201 Created` the moment it queues a message; whether a carrier
took it is decided seconds later. The run polls for that outcome and fails red
if nothing was delivered. The common codes:

| Code | Meaning | Fix |
|---|---|---|
| `30034` | Number not registered for A2P 10DLC | Register in Twilio, or switch to `email_gateway` meanwhile |
| `21608` | Trial account, destination unverified | Add it under **Phone Numbers → Verified Caller IDs** |
| `30007` | Carrier filtered it as spam | Usually A2P registration |
| `21610` | That number replied STOP | It must reply START |

## 7. Upload a bill screenshot

Click **inbox** → **Add file → Upload files**. Drag in a PNG, JPG, `.xlsx`, or `.csv`, scroll down, **Commit changes**. "Ingest uploaded bills" starts on its own and takes about a minute. You get a text like:

```
BILLS UPDATED: 2 changed, 1 added, 3 deactivated.
Mortgage 1801->1865. JCP&L 236->251. Orthodontist $189.00.
Deactivated: Kia, Sewer, Trash pickup.
```

If an amount moved more than 40%, or reads above $10,000, the change is applied and the text adds:

```
NEEDS REVIEW: Small $100.00->$180.00 (>40%)
```

A bill missing from the upload is **never deleted** — it is switched off with a note, so an OCR miss can't wipe your mortgage. Turn it back on by editing `bills.json` and setting `"active": true`.

If nothing could be read you get `BILL UPLOAD FAILED` and nothing changes; the file stays in `inbox/` so you can try a clearer photo.

## 8. Check the schedule

The brief runs at **7:00am Eastern**, year round. Two cron entries fire (11:00 and 12:00 UTC) and the script ignores the one that isn't 7am locally, so the daylight saving switch needs nothing from you.

To change the hour, edit `send_hour_local` in `config.json` and adjust both cron lines in `.github/workflows/daily-brief.yml`.
