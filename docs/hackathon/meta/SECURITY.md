# Security

What protects the creator's accounts and PC, and what each piece defends against.

## Principles

- **Nothing is published without explicit approval** on the approval page:
  the clips and destinations ticked, a confirm box, and the PIN.
- **The PC stays closed.** The engine listens on `127.0.0.1` only, as
  before. The PC talks out to the relay; nothing calls in.
- **No Meta app secret or long-lived token in the desktop client.** They
  live on the creator's relay only (Wrangler secrets, and account tokens
  sealed with AES-GCM under `TOKEN_KEY` in D1). The PC holds one secret: its
  own device secret, stored with Windows DPAPI like the app's other keys.
- **A phone number alone authorises nothing.** Pairing needs a one-time code
  sent from the phone *and* confirmation on the PC; approving needs the link
  (single-purpose, expiring), the PIN, and the PC's own checks.
- **The relay has the least power that works.** It cannot read the PC's
  files or call its local API. It can only answer the PC's signed requests,
  and only run Graph steps for a clip and platform the phone approved,
  under a command the PC accepted, within 24 hours.

## Pairing a number

| Step | Protection |
|---|---|
| PC asks for a code | Signed request; 6 characters from a 31-letter alphabet without look-alikes; stored hashed; expires in 10 minutes |
| Phone sends `LINK CODE` | Webhook signature checked; number recorded as *awaiting confirmation* |
| PC confirms "ending in 4567" | Only the last four digits ever reach the PC; refusing deletes the pairing |
| `STOP` / `START` | Honoured for the number on every PC it is paired with |

## The approval page

| Threat | Protection |
|---|---|
| Someone else gets the link | Link token is 32 random bytes, stored only as its SHA-256; expires with the offer (1–72 h, 24 by default); approving also needs the PIN |
| PIN guessing | PIN is 4–8 digits, not a repeated digit or a run; stored as PBKDF2-SHA256 (100,000 rounds, salted); 5 wrong tries lock approvals for 15 minutes |
| Cross-site form posts | CSRF token bound to the approval and link (HMAC with `TOKEN_KEY`) and an `Origin` check |
| Tampered form | Clips must be in the offer, platforms must be connected; anything else is refused and nothing is approved |
| Script injection | Server-rendered HTML with everything escaped, no JavaScript, `Content-Security-Policy: default-src 'none'`, `frame-ancestors 'none'`, `Referrer-Policy: no-referrer`, `Cache-Control: no-store` |
| A generic "yes" in WhatsApp | Never approves; replies with where to approve |
| Quick-reply buttons | Can only defer or cancel, and only the offer they were sent with, from the paired number |

## PC ↔ relay

| Threat | Protection |
|---|---|
| Impersonating the PC | HMAC-SHA256 over method, path and query, timestamp, nonce, and body hash, with the device secret registered once using the relay owner's enrolment key |
| Replaying a request | Timestamp within ±5 minutes; nonce remembered 15 minutes |
| Forging a decision | Commands are signed by the relay over canonical JSON; the PC verifies before acting |
| Re-delivered command | Recorded by id on the PC; applied once |
| Old approval, new clips | The offer's fingerprint covers each file's path, size and mtime; any change refuses the approval and the upload |
| Stale approval | Decided after expiry, or more than 48 h old when the PC sees it → `EXPIRED`, ask again |
| Approval without the PC agreeing | The relay allows Graph steps only after the PC acknowledges the command as accepted, only for that command's clips and platforms, at most 3 container creations each, within 24 hours |
| A compromised relay | It can't read files or reach the PC; it only receives bytes the PC chooses to upload for an approved, accepted command |

## Data shown and logged

- WhatsApp messages contain the video title, counts and links; never a
  credential, path, or error log. The failure message is fixed text.
- Errors shown to the creator are written by Clips Kitty; Meta's raw error
  text stays on the relay. Exceptions are redacted before printing.
- The relay logs error codes only, never tokens, numbers or message bodies.
- Every state change is in an audit log on the PC (`remote_audit`) and on
  the relay (`audit`), with the actor (desktop, relay, phone, publisher).

## Settings routes on the PC

Every `/remote-approval` route that changes something requires the desktop
app's session secret (`X-Clips-Kitty-Session`), the same one the plugin and
Discord routes use, so a web page or stray script can't pair a number or
switch approvals on. Reads return no secret and no phone number. Remote
approval is off by default and can only be switched on after a number is
paired and confirmed and a PIN is set.

## Reporting a problem

See the repository's `SECURITY.md` for how to report a vulnerability.
