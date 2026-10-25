# Privacy policy

*Last updated: 2026-07-21.*

!!! info "A template, not legal advice"
    This is a baseline policy for the Weirling software. Operators of a
    hosted instance should have it reviewed by counsel and adapt it to their
    jurisdiction before collecting user data.

Weirling is a personalized reading tool. To rank content for you, it processes
data about what you read and how you read it. This document explains what is
collected, why, how long it is kept, and the controls you have.

## Who is the data controller

For a **self-hosted** instance, the operator who runs it is the data controller.
For any **hosted** Weirling service, the entity offering that service is the
controller. This template refers to whoever runs the instance as "we".

## What we collect

- **Account data:** email address, a bcrypt password hash, display name, and
  your preferences (digest frequency, digest time, timezone, serendipity level,
  tier).
- **Content you add:** the sources, creators, and OPML feeds you subscribe to,
  and newsletters you forward to your Weirling inbox address.
- **Reading telemetry:** for items you open in the in-app reader, we record
  scroll depth, active reading time, whether you reached the end, re-reads,
  completion percentage, saves, shares, skips, and explicit ratings/feedback.
  This is the behavioural signal that powers ranking.
- **Derived data:** an interest graph (topics + weights), per-source and
  per-creator trust weights, learned ranking meta-weights, and content
  embeddings.
- **Product events (hosted service only):** to know whether the product
  works, we record when you sign up, finish onboarding, add sources, are
  sent or open a digest, open or finish an article, rate or save one, answer
  "how many of today's top 5 did you want?", subscribe, or cancel. Each event
  is linked to your account and carries at most a small detail (a plan name,
  a count, your top-5 answer), never article titles or text. They are used
  only for aggregate measures (weekly satisfied readers, retention, time to a
  first good digest), stay on our server, and use no third-party analytics.
  Self-hosted instances record none unless the operator turns
  `PRODUCT_METRICS` on.

We do **not** sell personal data, and we do not use it for advertising.

- **Collaborative warmup (#210):** to give new readers a better first
  digest, Weirling may suggest public articles that at least
  `COLLABORATIVE_K_ANONYMITY` readers (default 20) recently finished or
  upvoted, matched to the new reader's own topics. Only per-article counts
  are used, never one person's history, and private items never qualify.
  You can leave the counts in Preferences → Your Data.

## Cookies and browser storage

Weirling sets only essential cookies, so it shows no consent banner:

| Name | Purpose | Lifetime |
|---|---|---|
| `rp_access` | Session (access token), httpOnly | `ACCESS_TOKEN_EXPIRE_MINUTES` |
| `rp_refresh` | Session renewal, httpOnly, path `/api/v1/auth` | `REFRESH_TOKEN_EXPIRE_DAYS` |
| `rp_csrf` | Double-submit CSRF protection, httpOnly | `REFRESH_TOKEN_EXPIRE_DAYS` |

The web app also keeps a few settings in local storage (reader text size and
theme, a dismissed install hint, a "signed in on this device" flag for offline
use) and an offline cache of pages you opened. No analytics, advertising or
third-party cookies are used. The full list is on the in-app `/privacy` page
and is checked against the code in CI.

## Why we process it (legal bases)

- To provide the service you asked for (ranking, digests): performance of a
  contract.
- To improve ranking quality for you: legitimate interest, balanced against
  your rights; you can turn telemetry-driven features off by not using the
  in-app reader.

## Retention

- **Raw reading telemetry** is kept for `TELEMETRY_RAW_RETENTION_DAYS`
  (default 90 days). After that, each record of an item being shown to you is
  folded into daily totals that are not linked to any account, and the
  finest-grained reading measurements (scroll depth, active reading time) are
  cleared.
- **Derived data and coarse reading history** (your interest graph, what you
  finished, rated or saved) persist while your account exists so the model
  can keep learning. What you read can reveal sensitive things about you
  (health, politics, religion), so this data is never shared, sold, or used
  in logs or support tools, and operator metrics endpoints only report
  aggregates.
- **Full article text** we fetch is pruned to a short excerpt after
  `CONTENT_FULL_TEXT_RETENTION_DAYS` (default 90 days); we keep the summary,
  excerpt, and link, not an indefinite full copy.
- **Product events** are kept while your account exists and deleted with
  it; daily totals with no account attached are kept for trends.
- When you delete your account, all your personal data is erased (see below).

## Shared content model

Public web/RSS content is deduplicated across users and stored once, keyed by
URL, with no owner; this keeps costs and summarization efficient. Content that
is personal to you (**forwarded newsletters**) is tagged with your user id, is
never shown to other users, and is only readable by you.

## Third-party processors

Weirling can send data to the third-party services below when configured. See
[THIRD_PARTY_SERVICES.md](THIRD_PARTY_SERVICES.md) for details.

- **Groq** (and optionally OpenAI): article text/titles are sent to generate
  summaries. Summaries are cached.
- **Email provider (Resend, on the hosted service):** your email address and
  the content of the emails we send you (digests, sign-in and account
  messages). Replies you send us go to our own mailboxes. Self-hosters use
  their own provider.
- **Postgres, Redis, Browserless:** run within the operator's own
  infrastructure.

Self-hosters may supply their own API keys, in which case those providers'
terms apply to the operator.

## Your rights and controls

- **Access / export:** `GET /api/v1/account/export` returns a portable JSON copy
  of your data (GDPR Art. 20 / CCPA).
- **Erasure:** `DELETE /api/v1/account` permanently deletes your account and all
  associated personal data (GDPR Art. 17).
- **Unsubscribe:** every digest email has a one-click unsubscribe link; you can
  also set digest frequency to in-app-only in preferences.
- **Topic controls:** you can suppress topics and adjust interests in the app.

## Digest emails

Digest emails contain **no tracking pixel** and no per-recipient beacons.
Links in the email (titles, rate/save) are signed with an expiring HMAC and
route through your own Weirling instance, which records that you opened or
rated an item so the ranking can learn; this is disclosed in the email
footer. Signed links expire after 30 days.

EU/California residents additionally have the rights to rectification,
restriction, objection, and to lodge a complaint with a supervisory authority.

## Security

Passwords are hashed with bcrypt. The API authenticates the newsletter webhook,
guards server-side fetches against SSRF, sanitizes ingested HTML, and rate-limits
authentication. See [SECURITY.md](https://github.com/mohitmishra786/weirling/blob/main/SECURITY.md) to report a vulnerability.

## Changes

Material changes to this policy will be reflected here with an updated date.
