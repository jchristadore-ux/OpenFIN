# Household Financial Plan — shared, encrypted page

A single self-contained page at `plan/index.html`. It holds the August–September
operating plan plus a live tracker: tick a payment off, edit an amount, or defer a
bill, and every running balance recalculates.

Two properties make it safe to publish on GitHub Pages:

- **The page is encrypted.** Everything — the plan text, the bill names, the
  amounts — lives in one AES-GCM ciphertext blob. The key is derived from a shared
  passphrase with PBKDF2-SHA256 (250,000 iterations). Without the passphrase the
  URL renders a lock screen and nothing else. Viewing source shows base64.
- **Synced progress is encrypted too.** What syncs between devices is ciphertext
  under the same key, stored at a path derived from the passphrase. The sync
  service holds data it cannot read, at an address it cannot guess.

`<meta name="robots" content="noindex,nofollow">` keeps it out of search results.

---

## Using it

Open the page, enter the passphrase, and it unlocks. It stays unlocked for that
browser session — a refresh won't ask again, closing the tab will. **Lock** in the
top bar signs out immediately.

Everything you change saves automatically. The dot next to the balance box tells
you where things stand:

| Dot | Meaning |
|---|---|
| 🟢 Synced | Saved and shared with the other device |
| 🟢 Updated just now | Someone else's change just arrived |
| 🟡 This device only | Sync isn't configured — see below |
| 🔴 Offline — saved here | No connection; it'll catch up when you're back |

---

## Turning on sync between phones

Without this the page still works, but each device keeps its own progress. About
fifteen minutes, one time, free.

**1. Create a Firebase project**

Go to [console.firebase.google.com](https://console.firebase.google.com) → **Add project**.
Name it anything. Google Analytics is not needed — turn it off.

**2. Create a Realtime Database**

In the left sidebar: **Build → Realtime Database → Create Database**. Pick the
region closest to New Jersey (`us-central1` is fine). When it asks about security
rules, choose **Start in locked mode** — the next step replaces them.

**3. Set the security rules**

Open the **Rules** tab, replace everything with this, and click **Publish**:

```json
{
  "rules": {
    "plans": {
      "$room": {
        ".read":  "$room.length === 32",
        ".write": "$room.length === 32 && newData.hasChildren(['blob','at']) && newData.child('blob').isString() && newData.child('blob').val().length < 200000"
      }
    }
  }
}
```

This allows read and write only to someone who already knows a full 32-character
room address, and never allows listing what rooms exist. Your room address is
derived from the passphrase — 128 bits, not guessable.

**4. Copy the database URL**

At the top of the Realtime Database page, something like:

```
https://your-project-default-rtdb.firebaseio.com
```

**5. Paste it into the page**

In `plan/index.html`, find this near the top of the `<script>` block:

```js
  var FIREBASE = {
    databaseURL: ""
  };
```

Put the URL between the quotes, then commit and push. Within a minute or two
GitHub Pages rebuilds and both phones are in sync.

> The database URL being public is fine and by design — Firebase URLs are not
> secrets. The rules above plus the unguessable room address are what protect the
> data, and the data is ciphertext regardless.

---

## Maintenance

`tools/plan-tool.mjs` edits the page without ever putting plaintext in the repo.
Requires Node 18+.

**Change the passphrase**

```bash
node tools/plan-tool.mjs rekey "current passphrase" "new passphrase"
```

Re-encrypts in place and rotates the sync room. Commit and push, then everyone
unlocks again with the new one. Progress does not carry across a rekey — finish
the week first, or note where you are.

**Edit the plan's content or figures**

```bash
node tools/plan-tool.mjs export "passphrase"          # → tools/payload.json
# edit tools/payload.json
node tools/plan-tool.mjs import "passphrase" tools/payload.json
rm tools/payload.json
```

`payload.json` is plaintext and is gitignored. Delete it when you're done.

Inside it, `plan` is the timeline. Each row is:

```
[ "MM-DD", "Name", "Sub-label", amount, kind, tier, deferrable ]
```

`amount` is negative for money out and positive for money in. `kind` is
`bill`, `living`, or `income`. `deferrable` is `true` if the page should offer a
**Defer** button — reserve that for unsecured debts, never for housing,
utilities, insurance, or the car.

---

## What this is not

Progress lives in the browser and in your Firebase project. There's no account
system and no password reset: **if you both forget the passphrase, the page cannot
be recovered** — the plaintext exists nowhere else. Write it down somewhere real.

The passphrase gate protects against someone finding the URL. It is not a defence
against someone using a phone that's already unlocked and signed in.
