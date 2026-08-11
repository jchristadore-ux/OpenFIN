# The plan page has been retired

This directory used to serve an encrypted August–September operating plan,
unlocked with a shared passphrase typed into the browser.

Both halves of that are gone:

* **The passphrase is removed.** It was the only authentication anywhere in the
  project and the rebuild removes it entirely — from the page, the tooling, and
  the build step that produced the encrypted payload.
* **The page is retired rather than decrypted.** The encryption existed because
  this repository is public. Publishing the plaintext to satisfy "remove the
  passphrase" would have leaked exactly what the passphrase was protecting, so
  the page was withdrawn instead. The historical encrypted payload remains in
  git history; nothing was rewritten.

Everything the plan page did — the payment tracker, the forward view, the
"what is coming" list — is now live and automatic in the dashboard at the
repository root, driven by `snapshot.json` from the forecasting engine.

**Go to [the dashboard](../).**
