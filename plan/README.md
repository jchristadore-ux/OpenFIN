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

> **No terminal required.** Everything in the setup below happens in a web
> browser — the Firebase console and GitHub's own web editor. Nothing needs Node,
> git on the command line, or a local checkout.

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

## Setup, entirely in a browser

### 1. Check the page is live

Go to:

```
https://jchristadore-ux.github.io/TogetherLedger/plan/
```

You should see a lock screen and nothing else. Enter the passphrase and the plan
appears.

*Getting a 404?* On GitHub go to **Settings → Pages** and confirm the site is
building from the `main` branch, folder `/ (root)`. Give it a minute after any
merge.

### 2. Create the Firebase project

All browser, no install.

1. Go to [console.firebase.google.com](https://console.firebase.google.com) and
   click **Add project**. Any name will do. Turn Google Analytics **off** — it is
   not needed and only adds screens.
2. In the left sidebar choose **Build → Realtime Database**, then **Create Database**.
3. Pick a location near New Jersey — `us-central1` is fine.
4. When it asks about security rules, choose **Start in locked mode**. The next
   step replaces them.

### 3. Publish the security rules

Open the **Rules** tab, select everything in the box, replace it with this, and
click **Publish**:

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

### 4. Copy the database URL

At the top of the Realtime Database page there is a URL like:

```
https://your-project-name-default-rtdb.firebaseio.com
```

Copy the whole thing, and drop any trailing slash.

### 5. Paste it in, using GitHub's web editor

1. Open **`plan/index.html`** on GitHub.
2. Click the **pencil icon** (Edit this file) near the top right.
3. Press <kbd>Ctrl</kbd>+<kbd>F</kbd> (<kbd>Cmd</kbd>+<kbd>F</kbd> on a Mac) and
   search for `databaseURL`.
4. You are looking for this:

   ```js
     var FIREBASE = {
       databaseURL: ""
     };
   ```

5. Put your URL between the two quote marks, so it reads:

   ```js
     var FIREBASE = {
       databaseURL: "https://your-project-name-default-rtdb.firebaseio.com"
     };
   ```

6. Scroll to the bottom, choose **Commit directly to the `main` branch**, and
   click **Commit changes**.

The file is large and mostly base64 — that is expected. Change only what sits
between those two quotes and leave the rest alone. GitHub Pages rebuilds in a
minute or two.

> The database URL being public is fine and by design — Firebase URLs are not
> secrets. The rules above plus an unguessable room address are what protect the
> data, and what is stored there is ciphertext regardless.

### 6. Install it on both phones

**iPhone (Safari):** open the URL → Share → **Add to Home Screen**.
**Android (Chrome):** open the URL → menu → **Add to Home screen** / **Install app**.

It launches without browser chrome and behaves like an app. Each device asks for
the passphrase once per session.

### 7. Confirm sync actually works

With the page open on both phones, tick something on one. It should appear on the
other within about five seconds, and the badge should read **Updated just now**.

If it still says *This device only*, the URL did not save — go back to step 5 and
check for a stray space or a missing `https://`.

---

## Day to day

- **Tick a payment** when it clears.
- **Edit an amount** if it came out different from the plan.
- **Defer** pushes an unsecured bill to next month and re-runs every balance.
- **Update "Balance today"** whenever the account drifts from the projection.

Watch the **Lowest point** tile. It is the early warning — it turns red and names
the day you would go negative, before it happens.

---

## Changing the passphrase, or the plan's contents

Both re-encrypt the whole page, which needs Node and a checkout. **If you don't
work at a command line, ask for these as a pull request instead** — describe what
you want changed and review the PR in the browser like any other.

Two things worth knowing before asking for a passphrase change:

- **It resets the sync room.** Progress does not carry across. Finish the week, or
  note where you are, before asking.
- **There is no reset.** If you both forget the passphrase the page cannot be
  recovered — the plaintext exists nowhere else. Write it down somewhere real.

<details>
<summary>Command-line details, if you ever do want them</summary>

`tools/plan-tool.mjs` edits the page without putting plaintext in the repository.
Needs Node 18+.

```bash
node tools/plan-tool.mjs rekey  "current passphrase" "new passphrase"
node tools/plan-tool.mjs export "passphrase"            # → tools/payload.json
node tools/plan-tool.mjs import "passphrase" tools/payload.json
```

`payload.json` is plaintext and gitignored. Delete it when you're done. Inside
it, `plan` is the timeline, one row per entry:

```
[ "MM-DD", "Name", "Sub-label", amount, kind, tier, deferrable ]
```

`amount` is negative for money out, positive for money in. `kind` is `bill`,
`living`, or `income`. `deferrable` is `true` if the page should offer a **Defer**
button — reserve that for unsecured debts, never for housing, utilities,
insurance, or the car.

</details>

---

## What this is not

Progress lives in the browser and in your Firebase project. There is no account
system and no password reset. The passphrase gate protects against someone
finding the URL; it is not a defence against someone using a phone that is
already unlocked and signed in.
