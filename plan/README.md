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

## First-time setup, step by step

Do these in order. Step 3 must come before step 4 — changing the passphrase also
changes the sync address, so re-keying after Firebase is connected wipes shared
progress.

### 1. Get the page live

Merge the open pull requests, then visit:

```
https://jchristadore-ux.github.io/TogetherLedger/plan/
```

GitHub Pages takes a minute or two after a merge. You should see a lock screen
and nothing else. If you get a 404, check **Settings → Pages** and confirm the
site is building from `main` at the repository root.

### 2. Confirm it unlocks

Enter the passphrase. You should get the full plan. Before going further, view
the page source — you should see base64 and no readable figures. That is the
whole security model working.

### 3. Choose your own passphrase

You need Node 18+ and a clone of this repository.

```bash
git clone https://github.com/jchristadore-ux/TogetherLedger.git
cd TogetherLedger/plan
node tools/plan-tool.mjs rekey "current passphrase" "the one you both agree on"
git commit -am "Change plan passphrase"
git push
```

Pick something you will both remember and neither will write in a text message.
Four unrelated words beats a short complicated string. **There is no reset** — if
you both forget it, the page is gone.

### 4. Create the Firebase project

1. Go to [console.firebase.google.com](https://console.firebase.google.com) and
   click **Add project**. Any name. Turn Google Analytics **off** — it is not needed.
2. In the left sidebar: **Build → Realtime Database → Create Database**.
3. Choose a location near New Jersey (`us-central1` is fine).
4. When asked about security rules, choose **Start in locked mode**. The next
   step replaces them.

### 5. Publish the security rules

Open the **Rules** tab, replace everything, and click **Publish**:

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

This permits read and write only to someone who already knows a full
32-character room address, and never permits listing what rooms exist. Your room
address is derived from the passphrase — 128 bits, not guessable.

### 6. Connect the page to it

Copy the database URL from the top of the Realtime Database page. It looks like:

```
https://your-project-default-rtdb.firebaseio.com
```

In `plan/index.html`, find this near the top of the `<script>` block:

```js
  var FIREBASE = {
    databaseURL: ""
  };
```

Paste the URL between the quotes, then:

```bash
git commit -am "Connect plan to Firebase"
git push
```

> The database URL being public is fine and by design — Firebase URLs are not
> secrets. The rules above plus an unguessable room address are what protect the
> data, and what is stored there is ciphertext regardless.

### 7. Install it on both phones

**iPhone (Safari):** open the URL → Share → **Add to Home Screen**.
**Android (Chrome):** open the URL → menu → **Add to Home screen** or **Install app**.

It launches without browser chrome and behaves like an app. Unlock once per
session on each device.

### 8. Test that sync actually works

With the page open on both phones, tick something on one. It should appear on
the other within about five seconds, and the badge should read **Updated just
now**. If it stays on *This device only*, the `databaseURL` did not save — check
step 6.

---

## Day to day

- **Tick a payment** when it clears.
- **Edit an amount** if it came out different from the plan.
- **Defer** pushes an unsecured bill to next month and re-runs every balance.
- **Update "Balance today"** whenever the account drifts from the projection.

Watch the **Lowest point** tile. It is the early warning — it turns red and names
the day you would go negative, before it happens.

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
