# Deploy the hosted service: Vercel + Railway

The setup behind weirling.com: the web app on **Vercel**, the API, workers,
Postgres and Redis on **Railway**, the domain and mailbox at **Spaceship**
(Spacemail). Every step is something you click or paste; nothing is built on
your machine.

```text
weirling.com, www   ->  Vercel (Next.js, built from frontend/)
api.weirling.com    ->  Railway: api (FastAPI)
                        worker-ingest, worker-digest, beat (Celery)
                        Postgres + pgvector, Redis   (private network only)
mail                ->  Spacemail (hello@weirling.com sends the digests)
```

Expected cost: Vercel Pro as you have it; Railway Hobby about $20 to $30 a
month (about 2 GB of memory in use), including the $5 plan fee.

## 0. Before you start

- Generate four secrets on your machine and keep them in a password manager:

    ```bash
    for k in SECRET_KEY REFRESH_SECRET_KEY SECRETS_ENCRYPTION_KEY METRICS_TOKEN; do
      echo "$k=$(openssl rand -hex 32)"
    done
    ```

- A **paid** LLM key (Groq: the free tier runs out after about 40 summaries
  a day). Without one the app still works, with extract summaries.
- Recommended: cut the first release so you can pin a version. Edit the first
  line of `docs/releases/v0.3.0.md` from `(unreleased)` to today's date and
  merge it; the Release workflow tags `v0.3.0` and publishes the `0.3.0`
  images (see [Upgrading](../UPGRADING.md#how-a-release-happens)). Until then
  you can use `:latest`.

## 1. Email: Spacemail on weirling.com

1. In Spaceship, Spacemail, **Connect domain**: pick `weirling.com`. Because
   the domain's DNS is at Spaceship, Spacemail adds its MX, SPF and DKIM
   records for you.
2. Create the mailbox **hello@weirling.com** (digests are sent from it, and
   replies come to it). Note its password. Optionally add `privacy@` (the
   grievance contact the privacy page shows) and `support@`, and forward
   them to the inbox you read.
3. Add a DMARC record (Spaceship, Domains, weirling.com, DNS):

    | Type | Host | Value |
    |---|---|---|
    | TXT | `_dmarc` | `v=DMARC1; p=none; rua=mailto:hello@weirling.com` |

    Start with `p=none`; after two clean weeks, change it to `p=quarantine`.
4. Send a test email from webmail to a Gmail address; in Gmail, "Show
   original" should say SPF, DKIM and DMARC: PASS.

Limits: Spacemail paid plans send up to 500 emails an hour per mailbox,
plenty for the beta. Move digests to a transactional sender (Resend,
Postmark) before that becomes tight.

## 2. Railway: the backend

Create a project named **weirling**. Add services in this order.

### 2.1 Postgres with pgvector

**New, Template**: search "pgvector" and pick one based on the official
`pgvector/pgvector` image (for example "Postgres with pgVector Engine").
Name the service **Postgres**. It gets a volume (Hobby: up to 5 GB; the
database is about 50 MB per 5,000 articles). The app creates the `vector`
extension itself on first start.

Turn on **Backups** in the Postgres service (Backups tab): daily. These live
with the volume; off-site backups come in step 6.

### 2.2 Redis

**New, Database, Redis**. Name it **Redis**.

### 2.3 Shared variables

Project **Settings, Shared Variables**: add these once, then share them with
the four app services below.

```bash
DEPLOYMENT_MODE=hosted
APP_ENV=production
REGISTRATION_MODE=invite          # you sign up first (owner), then invite people
REGISTRATION_INVITE_CODES=pick-a-code,another-code

SECRET_KEY=...                    # from step 0
REFRESH_SECRET_KEY=...
SECRETS_ENCRYPTION_KEY=...
METRICS_TOKEN=...

DATABASE_URL=${{Postgres.DATABASE_URL}}    # plain postgres:// is fine
REDIS_URL=${{Redis.REDIS_URL}}/0
CELERY_BROKER_URL=${{Redis.REDIS_URL}}/1
CELERY_RESULT_BACKEND=${{Redis.REDIS_URL}}/2

FRONTEND_URL=https://weirling.com
CORS_EXTRA_ORIGINS=https://www.weirling.com
PUBLIC_API_URL=https://api.weirling.com
COOKIE_DOMAIN=.weirling.com

LLM_API_KEY=...                   # paid Groq key
SMTP_HOST=mail.spacemail.com
SMTP_PORT=587
SMTP_USER=hello@weirling.com
SMTP_PASSWORD=...                 # the mailbox password
FROM_EMAIL=hello@weirling.com
GRIEVANCE_CONTACT_EMAIL=privacy@weirling.com
NEWSLETTER_INBOX_DOMAIN=inbox.weirling.com

DB_POOL_SIZE=8
DB_MAX_OVERFLOW=4
```

Billing (`BILLING_*`) comes later with Lemon Squeezy; leave it out for the
invite-only beta.

### 2.4 The four app services

Each one: **New, Docker Image**,
`ghcr.io/mohitmishra786/weirling/weirling-backend:0.3.0` (or `:latest`),
attach the shared variables, and set the start command (Settings, Deploy,
Custom Start Command):

| Service | Start command | Notes |
|---|---|---|
| **api** | `sh -c "python -m app.migrate && exec uvicorn app.main:app --host :: --port ${PORT:-8000}"` | Settings, Networking: **Generate domain** first to test, then **Custom domain** `api.weirling.com`. Healthcheck path `/health`. |
| **worker-ingest** | `celery -A app.workers.celery_app worker --loglevel=info --pool=solo -Q scrape,embed -n ingest@%h` | Fetching and embeddings. |
| **worker-digest** | `celery -A app.workers.celery_app worker --loglevel=info --pool=solo -Q digest,celery -n digest@%h` | Kept separate so a slow feed never delays digests. |
| **beat** | `celery -A app.workers.celery_app beat --loglevel=info` | The scheduler. Exactly one replica. |

Then for each of the four: Settings, Source, **Auto Updates** on (minor and
patch updates if you pinned `0.3.0`; new pushes if `:latest`), with a
maintenance window at night.

To cap cost, set each service's resource limit (Settings, Deploy): api 1 GB,
worker-ingest 1.5 GB, worker-digest 1 GB, beat 512 MB.

Check: the api's generated `*.up.railway.app` URL answers `/health` with
`{"status": "ok", ...}`, and the worker logs show `ready`.

## 3. Vercel: the web app

1. **Add New, Project**: import `mohitmishra786/weirling`.
2. **Root Directory**: `frontend`. Framework: Next.js (detected). Node.js:
   24.x (the default).
3. **Environment Variables** (Production and Preview):
   `NEXT_PUBLIC_API_URL=https://api.weirling.com`
4. Deploy. Leave Vercel Analytics and Speed Insights **off**: the privacy page
   promises no third-party analytics.
5. **Settings, Domains**: add `weirling.com` and `www.weirling.com` (redirect
   www to the apex). Vercel shows the exact DNS records to add.

## 4. DNS at Spaceship

Spaceship, Domains, weirling.com, **DNS records**. Keep the Spacemail records
from step 1; add:

| Type | Host | Value | From |
|---|---|---|---|
| A | `@` | the IP Vercel shows (often `76.76.21.21`) | Vercel, Domains |
| CNAME | `www` | the target Vercel shows (e.g. `cname.vercel-dns.com`) | Vercel, Domains |
| CNAME | `api` | the target Railway shows for the custom domain | Railway, api, Networking |
| TXT | as Railway shows | the verification value | Railway, if it asks |

Changes usually apply within minutes. TLS certificates are issued
automatically by Vercel and Railway once the records resolve.

## 5. First sign-in (do this right away)

The **first account becomes the instance owner**, even with invite-only
sign-up. So, as soon as `https://weirling.com` loads:

1. Register with your own email. You are now the owner.
2. Open **/admin/setup**: use **Test** for the LLM and for email (it sends you
   a message through Spacemail).
3. Follow a few feeds (or a starter pack), then on **Digest** press
   **Generate digest**. To see the digest *email*, open a shell on the api
   service (`railway ssh --service api` with the Railway CLI) and run
   `python scripts/send_test_digest_email.py you@gmail.com`; check it in Gmail
   and on your phone.
4. Give testers an invite code from `REGISTRATION_INVITE_CODES`.

## 6. After launch day

- **Off-site backups** (#180): a nightly `pg_dump` to an S3-compatible bucket
  (Cloudflare R2's free tier is enough) as a Railway cron service.
- **Status page** (#243): a free monitor (Better Stack or UptimeRobot) on
  `https://api.weirling.com/health` and `https://weirling.com`, published at
  `status.weirling.com` (one more CNAME).
- **Newsletter inbox** (`you@inbox.weirling.com`): an inbound email provider
  (Resend or Postmark) with MX records on the `inbox` subdomain and a webhook
  to `https://api.weirling.com/api/v1/newsletter/inbound/resend` (or
  `/postmark`), signed with `RESEND_WEBHOOK_SECRET` / `POSTMARK_WEBHOOK_SECRET`.
  Spacemail keeps the main domain's mail; the subdomain is separate.

## When something is wrong

| Symptom | Likely cause |
|---|---|
| Signed in, but every page sends you back to sign-in | `COOKIE_DOMAIN` isn't `.weirling.com`, or `FRONTEND_URL` doesn't match the site's address |
| The browser console shows CORS errors | `FRONTEND_URL` / `CORS_EXTRA_ORIGINS` don't list the exact origin (with `https://`) |
| api restarts during deploy | the migration failed: check the api logs right after `python -m app.migrate` |
| No digests arrive | worker-digest or beat isn't running, or SMTP is wrong: test it in /admin/setup |
| "too many clients" from Postgres | lower `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` (workers don't pool) |
