# Contributing to Weirling

Thanks for your interest in contributing! A few things to know before you open
a pull request.

## Developer environment

See [Development](docs/DEVELOPMENT.md) for running from source and the code layout, and [Architecture](docs/ARCHITECTURE.md) for how the pieces fit.

### Running Backend Tests

The backend test suite requires **PostgreSQL 16 with pgvector** (database `readprism_test`) and **Redis 7** (for auth sessions, magic links, rate limits, and Celery broker). Host-side tests require Python ≥ 3.11.

You can run the tests in two ways:

1. **Inside Docker (Production-equivalent):**
   ```bash
   docker compose exec backend pytest tests/ -v
   ```

2. **On Host with Test Containers (`docker-compose.test.yml`):**
   ```bash
   # Start test Postgres + Redis containers:
   make test-services
   # or: docker compose -f docker-compose.test.yml up -d

   # Run tests from the backend directory:
   cd backend
   pytest -q

   # Tear down test services when done:
   make test-services-down
   # or: docker compose -f docker-compose.test.yml down -v
   ```

The test runner in `backend/tests/conftest.py` automatically defaults `TEST_DATABASE_URL` to `localhost:5432/readprism_test` and `REDIS_URL` / `CELERY_BROKER_URL` to `redis://127.0.0.1:6379/0` when running outside Docker.

Before submitting a pull request:
- **Backend checks:**
  ```bash
  cd backend
  ruff check . && ruff format --check .
  mypy app --python-version 3.11 --ignore-missing-imports
  pytest -q
  ```
- **Frontend checks:**
  ```bash
  cd frontend
  npm run lint
  npx tsc --noEmit -p .
  npm test
  ```
- Add or update tests for any code change: an item isn't done until it's covered.
- Keep commits focused and reference the relevant area in the message.

## Contributing starter packs and platform recipes

Starter packs and platform recipes are high-impact, low-risk ways to improve
Weirling's discovery and onboarding without touching database schemas or ranking
algorithms. If you have ideas for new topics or platforms, feel free to discuss them
first in [GitHub Discussions](https://github.com/mohitmishra786/weirling/discussions).

### 1. Starter packs

Starter packs are curated sets of 10 to 25 high-quality RSS or Atom feeds that help
new users bootstrap their reading digest during cold-start onboarding.

- **Location:** `backend/app/data/starter_packs/<slug>.opml`
- **Format:** Standard OPML 2.0 with feed titles, site URLs, and XML feed endpoints.
- **Liveness requirement:** At least 90% of feeds in every pack must be live and
  reachable (`HTTP < 400`).
- **Validation command:** Run the liveness check before opening a PR:
  ```bash
  make starter-pack-check
  # or: cd backend && PYTHONPATH=. python3 scripts/check_starter_packs.py
  ```

#### Worked example: Starter pack OPML

Create `backend/app/data/starter_packs/systems-programming.opml`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head>
    <title>Systems Programming: Weirling starter pack</title>
  </head>
  <body>
    <outline text="Systems Programming" title="Systems Programming">
      <outline text="Rust Blog" title="Rust Blog" type="rss" xmlUrl="https://blog.rust-lang.org/feed.xml" htmlUrl="https://blog.rust-lang.org/"/>
      <outline text="LWN.net" title="LWN.net" type="rss" xmlUrl="https://lwn.net/headlines/rss" htmlUrl="https://lwn.net/"/>
      <outline text="Faster Than Lime" title="Faster Than Lime" type="rss" xmlUrl="https://fasterthanli.me/index.xml" htmlUrl="https://fasterthanli.me/"/>
    </outline>
  </body>
</opml>
```

### 2. Platform recipes

Platform recipes recognize well-known publication platforms, blogs, and creator URLs,
resolving them directly to their native RSS or Atom feeds without relying on scraping.

- **Location:** `backend/app/services/ingestion/discover.py` in the `platform_candidates` function.
- **Tests:** `backend/tests/test_ingestion/test_discover.py` in the `CASES` list.

#### Worked example: Platform recipe

Adding support for a platform (e.g. Bluesky profiles at `https://bsky.app/profile/<handle>`):

1. **In `backend/app/services/ingestion/discover.py`:**
   ```python
   # Inside platform_candidates(page_url: str, html: str):
   if _is_host(page_url, "bsky.app") and len(parts) >= 2 and parts[0] == "profile":
       add(f"https://bsky.app/profile/{parts[1]}/rss")
   ```

2. **In `backend/tests/test_ingestion/test_discover.py`:**
   Add a test case to `CASES`:
   ```python
   (
       "bluesky-profile",
       "https://bsky.app/profile/alice.bsky.social",
       "",
       "https://bsky.app/profile/alice.bsky.social/rss",
       "platform",
   ),
   ```

3. **Verify:**
   ```bash
   cd backend && pytest tests/test_ingestion/test_discover.py -k bluesky
   ```

## Contributor License / sign-off

> **Note:** The project is licensed under [MIT](LICENSE). Have counsel
> review this section before accepting the first external contribution. It is
> not legal advice.

By submitting a contribution you agree to the **Developer Certificate of Origin**
([DCO 1.1](https://developercertificate.org/)): you certify that you wrote the
contribution (or have the right to submit it) and that it may be distributed
under the project's MIT license. Sign your commits off:

```bash
git commit -s -m "your message"
```

which appends a `Signed-off-by: Your Name <you@example.com>` line.

**Relicensing grant.** So the project can adopt a different OSI-approved license
in the future (e.g. MIT) without chasing down every past contributor, you
also grant the maintainer a perpetual, irrevocable right to relicense your
contribution under any [OSI-approved license](https://opensource.org/licenses).
This is the cheapest to establish now, while the contributor set is small.

## Reporting issues

Use GitHub Issues for bugs and GitHub Discussions for questions and ideas.
For anything security-sensitive, please see [SECURITY.md](SECURITY.md) instead of
opening a public issue.
