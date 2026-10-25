# Development

How to run Weirling from source, where the code lives, and what runs on a
schedule. Test commands and the pull-request checklist are in
[CONTRIBUTING.md](https://github.com/mohitmishra786/weirling/blob/main/CONTRIBUTING.md).

## Run it from source

You need Docker, Python 3.11 or newer (3.12 in the images) and Node.js
22.22.2 or newer (CI uses 26).

```bash
./scripts/init_env.sh   # .env with fresh secrets
make up-dev             # the stack with --reload and source mounts
```

`make up-dev` sets `APP_ENV=development`, which relaxes webhook checks and
opens operator metrics. Never expose it.

To work on one part outside Docker:

```bash
# API
cd backend && pip install -r requirements.txt
uvicorn app.main:app --reload

# Worker and scheduler, in two terminals
celery -A app.workers.celery_app worker --loglevel=info --pool=solo
celery -A app.workers.celery_app beat --loglevel=info

# Web app (set NEXT_PUBLIC_API_URL=http://localhost:8000 in frontend/.env.local)
cd frontend && npm install && npm run dev
```

The API's interactive docs are at `http://localhost:8000/docs`.

## Migrations

```bash
cd backend
alembic upgrade head                               # apply
alembic revision --autogenerate -m "what changed"  # new migration
alembic downgrade -1                               # undo the last one
```

Migrations also run on start (`AUTO_MIGRATE=true`). A release's notes list
the ones it adds.

## Where the code is

```text
backend/
  app/
    api/          HTTP routes, all under /api/v1
    models/       SQLAlchemy tables
    services/     ingestion, ranking, interest graph, digest, summaries,
                  billing, email, embeddings, search
    workers/      Celery app, schedules and tasks
    utils/        email sending, SSRF-safe fetching, cache, logging
  migrations/     Alembic versions
  tests/          pytest
  weirling_rank/  the ranking library published to PyPI as weirling-rank
frontend/
  src/app/        Next.js pages (App Router)
  src/components/ UI, including the topic graph
  src/lib/        API client, types, the graph simulation
  tests/          Vitest
extension/        browser extension
docs/             this site (MkDocs)
```

## Scheduled tasks

Celery beat runs these (`backend/app/workers/schedules.py`). Times are UTC.

| Task | When |
|---|---|
| Fetch feeds | every 30 minutes |
| Fetch tracked creators | hourly |
| Queue digests that are due (per reader's time zone) | every 30 minutes |
| Embed items that missed it; re-embed after a model change | every 30 minutes |
| Decay interest weights | daily, 02:00 |
| Learn each reader's send time | weekly, Sunday 03:15 |
| Cut stored article text to an excerpt after the retention period | daily, 03:30 |
| Aggregate old reading telemetry | daily, 03:45 |
| Refresh discovery pools | daily, 04:30 |
| Roll up product metrics | daily, 00:40 |
| Trial emails (hosted, with billing on) | hourly |
| Re-engagement emails | daily, 16:00 |
| Weekly "what Weirling learned" email | Sunday 09:00 |
| Paused-account reminders and deletion (hosted, with billing on) | daily, 05:30 |
| Worker and scheduler heartbeats, operator alerts | every 1 to 10 minutes |

## Stack

| Part | Technology |
|---|---|
| API | Python 3.12, FastAPI, Pydantic 2 |
| Database | PostgreSQL 16 with pgvector, SQLAlchemy 2 (async), Alembic |
| Jobs | Celery with Redis 7 |
| Web app | Next.js 16, React 19, TypeScript, Tailwind 4 |
| Embeddings | sentence-transformers `all-MiniLM-L6-v2`, run locally; a hash embedder in the lite image |
| Summaries | any OpenAI-compatible API (Groq by default); extracts without a key |
| Extraction | trafilatura, readability; Playwright or Browserless for JavaScript pages |
| Email | SMTP or Resend |
