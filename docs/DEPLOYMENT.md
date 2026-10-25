# Install & run

<p class="lead">Everything you need to run your own Weirling: install it, set
up AI summaries and email, keep it healthy, and back it up.</p>

Running the managed, multi-user service instead? See [Hosted service](HOSTING.md).
Upgrading an existing install? See [Upgrading](UPGRADING.md).

## Quickstart

You need Docker with Compose. Pick a setup:

| Setup | Containers | RAM | Choose it when |
|---|---|---|---|
| **Standard** | 8 | ~2.5 GB | You want the full app with good topic matching. |
| **Lite** | 8 | ~1 GB | You have a small VPS. Topic matching is weaker. |
| **All-in-one** | 3 | ~0.5 GB idle (lite) | You use a homelab app store, or want the fewest moving parts. |
| **Full** | 10 | ~3.5 GB | You also want JavaScript-heavy pages rendered. |

=== "Standard"

    ```bash
    ./scripts/init_env.sh      # writes .env with generated secrets
    docker compose up -d       # migrations run on start
    ```

=== "Lite"

    ```bash
    ./scripts/init_env.sh
    make up-lite               # torch-free image, lexical embeddings
    ```

=== "All-in-one"

    ```bash
    ./scripts/init_env.sh
    docker compose -f compose.aio.yml up -d
    ```

=== "Full"

    ```bash
    ./scripts/init_env.sh
    docker compose --profile full up -d   # adds the page renderer and egress proxy
    ```

Then open <http://localhost:3001>. The first account you create is the
**owner**, and the setup screen opens next (see [First-boot setup](#first-boot-setup)).

!!! tip "Build from your checkout"
    `make up-build` builds the images locally instead of pulling them from
    GHCR. For the all-in-one image, add `--build` to the compose command.

## Choosing a host

The embedding model loads into memory in the API and in each worker.

| Scale | RAM | Notes |
|---|---|---|
| Local or demo | 4 GB | The whole stack. First boot loads the model in 60 to 90 seconds per container. |
| Small (up to 50 users, 100 sources) | 8 GB | Database, Redis, renderer and the model in the API and 3 workers. |
| Growing | 16 GB or more | Consider managed Postgres and one shared embedding service. |

CPU is the bottleneck for embedding and ingestion. With a GPU, set
`EMBEDDING_DEVICE=cuda`. Before you rely on it, check the 30-minute ingestion
target under load with `scripts/loadtest_ingestion.py`.

### The all-in-one image

`compose.aio.yml` runs three containers: `readprism`, Postgres and Redis. The
`readprism` container supervises (with s6-overlay) a one-shot migration, the
API, one worker per queue, a single scheduler and the web app. Its health
check needs every process up, the API and web app answering, and a fresh
scheduler heartbeat. A failed migration stops the container.

- Run **one** copy per database. A second copy would start a second scheduler.
  To scale workers, use the standard `docker-compose.yml`.
- The page renderer isn't included: JavaScript-only pages keep their feed
  summary.
- `--build-arg LITE=1` builds it without torch. Measured idle memory (lite,
  arm64, no sources yet): `readprism` 419 MiB, Postgres 55 MiB, Redis 11 MiB.
  The standard image also loads the embedding model, so it needs more.

### Production and development modes

`docker-compose.yml` is production-shaped and is what `docker compose up`
runs. Outside development the app refuses to start when `SECRET_KEY` (or
`REFRESH_SECRET_KEY`, if set) is empty, shorter than 32 characters, or a
known placeholder. All settings: [`.env.example`](https://github.com/mohitmishra786/weirling/blob/main/.env.example).

!!! warning "Never expose a development stack"
    `make up-dev` adds `docker-compose.dev.yml`: `APP_ENV=development`, hot
    reload and source mounts. In development, webhooks without a secret are
    accepted, metrics need no token and an empty `SECRET_KEY` falls back to a
    public key.

## First-boot setup

On a fresh instance, sign-up creates the **owner account** and opens
**Instance setup** (`/admin/setup`, also linked from Preferences). There the
owner can enter, without editing files:

- the LLM key, base URL and models, with a **Test** button;
- SMTP details, with a test email to the owner's address;
- who may sign up: `closed`, `invite` (with codes) or `open`.

How the values are kept:

- They're stored encrypted in the `instance_settings` table, with a key derived
  from `SECRETS_ENCRYPTION_KEY` (`init_env.sh` generates it). Without that
  key the screen is read-only. If you change it, re-enter the values.
- Anything set in `.env` wins and shows as locked.
- Secrets are write-only: the API only says whether one is saved.
- The API applies changes at once; workers pick them up within a minute.

## AI summaries

Bring a key for any OpenAI-compatible endpoint (`LLM_BASE_URL` and
`LLM_API_KEY`). Nothing goes through a Weirling service. Without a key,
summaries are extracts from the article, and everything else works.

### Which model does what (#225)

| Task | Setting | Default |
|---|---|---|
| Item summaries | `LLM_MODEL_SUMMARY` | `LLM_MODEL_FAST` (`openai/gpt-oss-20b`) |
| Topic extraction | `LLM_MODEL_TOPICS` | `LLM_MODEL_FAST` |
| Story synthesis across sources | `LLM_MODEL_SYNTHESIS` | `LLM_MODEL_PRIMARY` (`openai/gpt-oss-120b`) |
| Interest expansion at onboarding | `LLM_MODEL_INTERESTS` | `LLM_MODEL_PRIMARY` |

Summaries are most of the calls, so they use the cheaper model, and only
for items shown in a digest or opened in the reader. To compare the two
models on your key:

```bash
cd backend && python scripts/summary_spot_check.py --n 50 --out ../docs/eval/summary-models.md
```

!!! note "Free Groq keys run out fast"
    Groq's free tier has a daily token cap; in testing it stopped after about
    40 summaries in a day, and then asks for 15-minute waits. Weirling
    doesn't wait longer than `LLM_MAX_RETRY_WAIT_SECONDS` (30): it keeps the
    extract and moves on. For a hosted service, use a paid tier.

The gpt-oss models reason before answering. `LLM_REASONING_EFFORT=auto` (the
default) asks them for low effort, which keeps summaries inside their token
budget; set it to `""` if your provider rejects the field. Results:
[summary model check](eval/summary-models.md).

### Local models with Ollama

1. Install [Ollama](https://ollama.com) and pull a model:
   `ollama pull llama3.1:8b`. Add a larger one for synthesis if you have the
   memory, such as `qwen2.5:32b`.
2. Point Weirling at it in `.env`:

    ```bash
    LLM_BASE_URL=http://host.docker.internal:11434/v1
    LLM_API_KEY=ollama          # any non-empty value; Ollama ignores it
    LLM_MODEL_FAST=llama3.1:8b
    LLM_MODEL_PRIMARY=llama3.1:8b
    LLM_TPM_LIMIT=1000000       # no provider rate limit to respect
    LLM_TIMEOUT_SECONDS=120     # local models are slower
    ```

3. On Linux, add `extra_hosts: ["host.docker.internal:host-gateway"]` to the
   `backend` and `worker-*` services, or use the host's LAN address.
4. Restart, then open an item in the reader. Its summary should say it came
   from the model.

## Email

Set the SMTP settings (in `.env` or on the setup screen) to send digests.
Digest emails carry one-click feedback links and a one-click unsubscribe
header, which only help if the mail arrives. For the domain in the `from`
address:

1. **SPF:** a TXT record allowing your provider's servers, for example
   `v=spf1 include:spf.spacemail.com ~all` for Spacemail.
2. **DKIM:** turn it on in the provider's console and publish its keys.
3. **DMARC** (recommended): start with
   `v=DMARC1; p=none; rua=mailto:postmaster@yourdomain` and tighten it once
   the reports look clean.
4. Keep `EMAIL_PHYSICAL_ADDRESS` set; it goes in the footer. There is no
   tracking pixel and no setting to add one.

## Workers & scaling

Ingestion, embedding and digests run on separate queues, each with its own
single-process worker (`worker-scrape`, `worker-embed`, `worker-digest`), so
a slow scrape can't delay digests. Why single-process:
[ADR 0001](adr/0001-celery-solo-pool.md).

To add capacity, run more workers for the queue that is behind, and keep
exactly one `beat`:

```bash
docker compose up -d --scale worker-digest=4 --scale worker-embed=2
```

`GET /api/v1/metrics/queues` (with the metrics token) shows the wait per
queue, the runtime per task and the share of digests delivered on time.
Targets and the load test (`scripts/load_test_digests.py`) are in ADR 0001.

## Switching the embedding model

Weirling defaults to `all-MiniLM-L6-v2` (384 dimensions). `BAAI/bge-small-en-v1.5`
has the same size and finds the right article more often
([embedding models on real articles](eval/embedding-models.md)). The standard
image only; the lite image uses hash embeddings.

1. Set both, then restart:
   ```bash
   EMBEDDING_MODEL=bge-small-en-v1.5
   EMBEDDING_CUTOVER_ENABLED=true
   ```
   New articles and interest topics are embedded with bge-small from then on.
2. Older vectors are re-encoded in the background by a scheduled task (every
   30 minutes, 50 batches of `EMBEDDING_REEMBED_BATCH` each; set
   `EMBEDDING_REEMBED_PAUSE_SECONDS` to go easier on a small box). To start it
   now instead of waiting:
   ```bash
   docker compose exec worker-embed celery -A app.workers.celery_app call app.workers.tasks.compute_embeddings.reembed_outdated_items
   ```
3. Watch progress (metrics token or the owner's sign-in):
   ```bash
   curl -H "X-Metrics-Token: $METRICS_TOKEN" http://localhost:8000/api/v1/metrics/embeddings
   ```
   Until an item is re-encoded, ranking and search simply skip its vector
   (vectors from two models are never compared), so it ranks on the other
   signals for a while.
4. To go back, set `EMBEDDING_CUTOVER_ENABLED=false` and restart; the same
   task re-encodes with MiniLM.

`scripts/reembed_benchmark.py` measures re-embed speed on a **scratch**
database it seeds itself; never point it at a live one.

## Monitoring

| What | How | Default |
|---|---|---|
| Health banner | The owner sees a banner when a worker or the scheduler stops, a queue backs up, feeds stall or the LLM fails | On |
| Scheduler health | The `beat` container turns unhealthy if its heartbeat stops | On |
| Alerts | ntfy and/or email when something breaks | Off until you set a destination |
| Dashboards | Prometheus and Grafana | Off (`--profile monitoring`) |
| Error tracking | Sentry | Off until you set a DSN |

### Health banner

The owner (the first account) sees a red banner when:

- the scheduler or a worker queue stopped (each queue checks in every 5
  minutes; reported after `HEALTH_WORKER_STALE_MINUTES`, default 30);
- a queue has more than `HEALTH_QUEUE_BACKLOG` jobs waiting;
- feeds produced nothing for `HEALTH_INGEST_STALE_HOURS` (default 6);
- the last LLM call failed.

The same data is at `GET /api/v1/metrics/instance-health`. Other accounts
get an empty list.

### Alerts

Every 10 minutes the workers check for problems and notify you. Set
`ALERT_NTFY_URL` (an [ntfy](https://ntfy.sh) topic such as
`https://ntfy.sh/<random-topic>`), `ALERT_EMAIL`, or both. Each alert is sent
when it starts, every `ALERT_REPEAT_HOURS` while it lasts, and once when it
clears.

| Alert | Fires when | Setting |
|---|---|---|
| Ingest stalled | No new items | `ALERT_INGEST_STALL_HOURS` (2) |
| Queue lag | Digest wait p95 over 60 s, embed over 10 min | ADR 0001 targets |
| Late digests | Fewer than 99% on time | ADR 0001 target |
| API errors | Share of 5xx responses | `ALERT_5XX_RATE` (0.05) |
| LLM errors | Share of failed model calls | `ALERT_LLM_ERROR_RATE` (0.5) |
| Disk | Usage on the listed paths | `ALERT_DISK_PERCENT` (80), `ALERT_DISK_PATHS` |
| Backups | Last successful backup too old | `ALERT_BACKUP_MAX_AGE_HOURS` (36) |
| Workers | A worker or the scheduler stopped | `HEALTH_WORKER_STALE_MINUTES` |

Disk is measured where the worker runs: mount the volume you care about into
a worker and list its path.

### Prometheus and Grafana

```bash
docker compose --profile monitoring up -d
```

This adds Prometheus (30-day retention, not published) and Grafana on
<http://localhost:3002> with a ready-made "Weirling overview" dashboard:
requests and latency by route, errors, queues, items ingested, feed health,
digest timing and LLM calls, tokens and cost. The same alert conditions
are loaded as Prometheus rules (`deploy/monitoring/alerts.yml`).

- The API serves `GET /metrics` with `METRICS_TOKEN`, as
  `Authorization: Bearer …` or `X-Metrics-Token`.
- Set `LLM_COST_PER_MILLION_TOKENS` to see cost.
- Set `GRAFANA_ADMIN_PASSWORD` before exposing Grafana.

### Errors and ranking quality

- **Sentry:** set `SENTRY_DSN` for the backend, and `NEXT_PUBLIC_SENTRY_DSN`
  or `SENTRY_DSN` for the web app.
- **Ranking quality:** `python scripts/ranking_eval.py --days 30` prints how
  well the ranker predicted what each group of readers opened.

## Backups & restore

The interest graph and your reading history are what make the digest yours.
Back them up, keep a copy off the machine, and rehearse a restore.

`scripts/backup.sh` (or `make backup`) writes one
`readprism-<UTC time>.tar.gz` holding:

- the Postgres dump (`pg_dump -Fc`);
- a Redis snapshot (sessions and sign-in links);
- your `.env`;
- a manifest with the schema version and row counts.

!!! danger "The archive contains your secrets"
    It includes `.env`. Store it like a password, and use encryption for
    off-box copies.

After each run, archives older than `BACKUP_RETENTION_DAYS` (default 14) in
the same folder are deleted.

### Schedule it

Both examples run nightly at 03:15 from the folder with your
`docker-compose.yml`.

=== "cron"

    ```cron
    15 3 * * * cd /opt/readprism && ./scripts/backup.sh /var/backups/readprism >> /var/log/readprism-backup.log 2>&1
    ```

=== "systemd"

    `/etc/systemd/system/readprism-backup.service`:

    ```ini
    [Unit]
    Description=Weirling backup
    After=docker.service

    [Service]
    Type=oneshot
    WorkingDirectory=/opt/readprism
    Environment=BACKUP_RETENTION_DAYS=14
    ExecStart=/opt/readprism/scripts/backup.sh /var/backups/readprism
    ```

    `/etc/systemd/system/readprism-backup.timer`:

    ```ini
    [Unit]
    Description=Nightly Weirling backup

    [Timer]
    OnCalendar=*-*-* 03:15:00
    Persistent=true

    [Install]
    WantedBy=timers.target
    ```

    ```bash
    sudo systemctl daemon-reload && sudo systemctl enable --now readprism-backup.timer
    systemctl list-timers readprism-backup.timer   # next run
    ```

### Copy it off the box

A backup on the same disk doesn't survive that disk. Run one of these after
the backup (with `&&` in the cron line, or as `ExecStartPost=` in the unit).

=== "restic"

    Encrypted and deduplicated, to any S3, B2 or SFTP repository:

    ```bash
    export RESTIC_REPOSITORY=s3:s3.amazonaws.com/my-bucket/readprism RESTIC_PASSWORD_FILE=/root/.restic-pass
    restic backup /var/backups/readprism && restic forget --keep-daily 14 --keep-weekly 8 --prune
    ```

=== "rclone"

    A plain copy to any remote. Use an encrypted `crypt` remote:

    ```bash
    rclone copy /var/backups/readprism remote-crypt:readprism-backups --max-age 48h
    ```

### Rehearse a restore (monthly)

```bash
make restore-drill                                       # newest backup in ./backups
./scripts/restore_drill.sh /var/backups/readprism/readprism-<ts>.tar.gz
```

The drill restores the dump into a throwaway database, checks every table's
row count against the manifest, and drops it. Your live data isn't touched;
exit status 0 means the backup restores. CI runs the same drill on every
change to the backup scripts. To rehearse an off-box copy, download it first
(`restic restore latest --target /tmp/rp`) and point the drill at it.

### Restore for real

!!! danger "This replaces your live data"
    The restore overwrites the database and Redis.

```bash
./scripts/restore.sh /var/backups/readprism/readprism-<ts>.tar.gz   # or: make restore FILE=...
```

It stops the API and workers, recreates the database from the dump, loads
the Redis snapshot and starts everything again; migrations run on boot. The
archive's `env` file is the `.env` from backup time: compare it with yours
when restoring onto a new machine.

## Rolling back a bad release

CI tags an image for every commit. Keep the last good tag written down so a
bad release is a five-minute undo:

1. **Before deploying**, note the running image tag as `LAST_GOOD`.
2. **To revert**, deploy `LAST_GOOD`: `docker compose pull` that tag (or
   `git checkout LAST_GOOD` if you build on the host), then
   `docker compose up -d`.
3. **Migrations only go forward.** The older image runs on the newer schema
   within a release. If a migration must be undone, run
   `alembic downgrade -1` before deploying the older image, or restore the
   backup (see [Upgrading](UPGRADING.md#rolling-back)).

Rehearse the revert once: a rollback plan you haven't run isn't a plan.
