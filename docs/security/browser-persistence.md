# Browser persistence inventory

Last verified: 2026-09-03

Edge Tracker keeps authentication server-signed and stores only disposable bet
composition in browser-managed storage. Passwords, email addresses, API keys,
provider payloads, bankroll notes, and authentication tokens must never be
written to `localStorage` or `sessionStorage`.

| Name | Mechanism | Contents | Lifetime | Deletion |
|---|---|---|---|---|
| `session` | Secure, HttpOnly, SameSite=Lax cookie in production | Signed Flask session identifier/state; never credentials | 30-minute sliding idle window, bounded by a 12-hour absolute application lifetime | Logout, expiry, invalid signature, or browser cookie deletion |
| `remember_token` | Secure, HttpOnly, SameSite=Lax cookie in production | Flask-Login signed persistent-login token | 14 days | Logout, expiry, or browser cookie deletion |
| `sbt_parlay_queue_v1` | `sessionStorage` | Non-sensitive `BetLegV1` composition for the current tab | Browser-tab session | Successful placement, explicit queue clear, logout, or tab close |
| `parlayQueue` | Legacy `localStorage` and `sessionStorage` key | Retired pre-v1 queue | Compatibility cleanup only | Every logout |

Changing the v1 queue schema requires a new versioned storage key and either a
safe migration or explicit reset. The server treats all browser values as
untrusted and derives user ownership from the authenticated session.

Logout invalidates the current browser's session and remember cookies. Sessions
are client-side signed cookies rather than centrally stored records, so a logout
on one device does not revoke another device immediately; the other device
remains bounded by its idle and absolute lifetimes. Global multi-device session
revocation requires a durable server-side session/version registry and belongs
with future password-reset/account-lifecycle work.
