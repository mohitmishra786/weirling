<div align="center">

<img src="docs/assets/logo.svg" alt="Weirling logo: a white diamond on black" width="72" height="72">

# Weirling

*Stop skimming 50 feeds: one daily digest, ranked by what you actually finish reading.*

Self-hostable reader for RSS, newsletters and creators. Learns from scroll depth and reading time, and says why each item ranked.

[![release](https://img.shields.io/github/v/release/mohitmishra786/weirling?label=release&color=b45309)](https://github.com/mohitmishra786/weirling/releases)
[![tests](https://img.shields.io/github/actions/workflow/status/mohitmishra786/weirling/backend.yml?branch=main&label=tests)](https://github.com/mohitmishra786/weirling/actions/workflows/backend.yml)
[![weirling-rank on PyPI](https://img.shields.io/pypi/v/weirling-rank?label=weirling-rank&color=b45309)](https://pypi.org/project/weirling-rank/)
[![license MIT](https://img.shields.io/github/license/mohitmishra786/weirling?color=555)](LICENSE)

[Hosted beta](https://weirling.com) · [Documentation](https://mohitmishra786.github.io/weirling/) · [Quickstart](#quickstart) · [Releases](https://github.com/mohitmishra786/weirling/releases)

</div>

![Weirling's daily digest: ranked articles, each with its score and reason](docs/media/digest.png)

[Watch a 40-second narrated tour](frontend/public/landing/weirling-demo.mp4) of the digest, why each item ranks and what it learns.

## What it does

- **Brings your reading into one place:** RSS and Atom feeds, newsletters
  forwarded to a private address, and creators on Substack, YouTube, Medium,
  Bluesky, Mastodon, GitHub and podcasts.
- **Ranks by behaviour, not keywords.** The in-app reader records how far
  you scroll and how long you actually read. Eight signals, weighted for you,
  give each item a score; the biggest contributions become the reason shown
  next to it ([how ranking works](docs/signals.md)).
- **Sends one digest** by email or in the app, at a time it learns from when
  you open it.
- **Shows what it learned:** your interests are a topic graph you can browse,
  rename, merge, boost or mute.
- **Runs on your machine.** Embeddings are computed locally. An LLM key is
  optional; without one, summaries are extracts and nothing leaves your
  server.

## Compared with other readers

| | What decides importance | Self-host |
|---|---|---|
| **Weirling** | Ranking learned from how you read (scroll depth, time, ratings), explained per item | yes (MIT) |
| Feedly | AI priorities you train with topics, keywords and examples (Pro+) | no |
| Inoreader | Rules and filters; AI summaries and tags, no ranking by reading | no |
| NewsBlur | A classifier you train on keywords, authors and tags | yes |
| Readwise Reader | A read-later queue; not ranked | no |

Prices, AI features and sources: [docs/COMPETITORS.md](docs/COMPETITORS.md).

## When to use something else

- You follow a handful of feeds and read them all: a plain RSS reader is simpler.
- You want X, LinkedIn or Reddit timelines: they have no usable public feeds,
  and Weirling marks them unsupported rather than scraping them.
- You need a team or enterprise product with SSO and an SLA: Weirling is an
  early beta run by one maintainer.

## Quickstart

Needs Docker with Compose v2 and about 2.5 GB of RAM (1 GB for the lite image).

```bash
git clone https://github.com/mohitmishra786/weirling.git && cd weirling
./scripts/init_env.sh    # writes .env with fresh secrets
docker compose up -d     # 8 services; migrations run on start
```

Open <http://localhost:3001> and create your account: the first one owns the
instance. Ports 8000 and 3001 must be free; [Install & run](docs/DEPLOYMENT.md)
covers changing them, the lite and full profiles, AI summaries, email,
backups and upgrades.

## Status

Early beta, v0.3.x. Self-hosting works today; the hosted service at
[weirling.com](https://weirling.com) is invite-only. Expect breaking changes
between 0.x releases: every release's notes say what to do, and migrations
run automatically ([upgrading](docs/UPGRADING.md)).

## Documentation

| | |
|---|---|
| [Install & run](docs/DEPLOYMENT.md) | Profiles, first-boot setup, AI summaries, email, monitoring, backups |
| [Configuration](docs/CONFIGURATION.md) | Every setting you are likely to change |
| [Deploy to Vercel + Railway](docs/deploy/railway-vercel.md) | The setup behind weirling.com |
| [Architecture](docs/ARCHITECTURE.md) | Components, ingestion, how a digest is built |
| [How ranking works](docs/signals.md) | The eight signals and how your weights change |
| [Development](docs/DEVELOPMENT.md) | Running from source, code layout, scheduled tasks |
| [Privacy](docs/PRIVACY.md) · [Terms](docs/TERMS.md) · [Security](SECURITY.md) | What is collected, the rules, reporting a vulnerability |

## Contributing

Issues and pull requests are welcome; [CONTRIBUTING.md](CONTRIBUTING.md) has
the setup, the checks to run and how to add starter packs. Security reports go
to [SECURITY.md](SECURITY.md), not public issues.

## License

[MIT](LICENSE). The ranking engine is also published on its own as
[`weirling-rank`](https://pypi.org/project/weirling-rank/).
