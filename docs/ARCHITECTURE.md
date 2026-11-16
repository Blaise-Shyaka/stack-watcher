# Stack Watch: architecture

A daily briefing of what changed in the tech you work with: releases, security
advisories and posts from each project's own team. You pick your stacks from a
curated list; each morning you see only what changed in them, most important
first. Think TLDR, built around your own stack.

Built as a CloudCrane phone app, fed by a small pipeline in this repository.

## Decisions

| Decision | Choice | Why |
| --- | --- | --- |
| Sources | A curated catalog of official blogs, release feeds and changelogs (RSS/Atom), plus security advisories | Trustworthy, and in line with what developers act on |
| Catalog growth | Users request a missing stack through a GitHub issue form; a curator adds it by PR | Quality stays curated; anyone can ask |
| Scale | Launch with about 65 stacks, grow towards 100+ | Built for other people, not one person's stack |
| Collection | A scheduled GitHub Actions workflow reads every feed | Free for public repos, no server, no database |
| Storage | Generated JSON on a `feed` branch, served by GitHub Pages | A git branch is the database and its history; Pages is the CDN |
| CVEs | GitHub Advisory Database, queried by the workflow (never by the app), plus projects' own security feeds | RSS has no reliable per-package CVE feed |
| Pipeline language | Python with `feedparser` | The most forgiving parser for real-world feeds |
| App | One HTML file on CloudCrane, talking to one site (the Pages feed) | Fits CloudCrane's limits: 1 MB, 5 sites, data on the phone |
| Personalization | Done on the phone | What you follow never leaves your phone except as file names requested from GitHub Pages |
| Design | CloudCrane UI kit, plus a small feed layer of our own | Native on iPhone and Android, passes CloudCrane's checks; see [DESIGN.md](DESIGN.md) |

## How it fits together

```
This repo (public)                              Phone: CloudCrane app
catalog/<stack>.yml  (curated, via PRs)         Search the list, follow stacks
        |                                       Build today's briefing on the phone
        v                                       Works offline
Workflow, every 6 hours (GitHub Actions)               ^
  fetch RSS/Atom feeds + security advisories           |  one site: blaise-shyaka.github.io
  clean, dedupe, classify, score, keep 30 days         |  sends: which stacks you follow
        |                                              |
        v                                              |
feed branch -> GitHub Pages: catalog.json, stacks/<id>.json

"Request a stack" issue form -> PR adding catalog/<id>.yml -> CI checks every feed -> merge
```

Why not the alternatives:

- A Cloudflare Worker on a timer: fresher, but another account and another deploy.
- Reading feeds on the phone: most official feeds can't be read by a browser app
  (no CORS header; checked on nodejs.org's blog feed), and CloudCrane allows 5 sites per app.

## 1. Catalog

One YAML file per stack in `catalog/`. Example:

```yaml
id: temporal
name: Temporal
category: workflows
aliases: [temporal.io, temporalio, durable execution]   # search
mono: T                                                  # monogram, 1-2 letters
hue: 265                                                 # monogram colour
homepage: https://temporal.io
sources:
  - kind: blog            # blog | release | changelog | security
    url: <Temporal blog feed>
    official: true
  - kind: release
    url: https://github.com/temporalio/temporal/releases.atom
    label: Server
  - kind: release
    url: https://github.com/temporalio/sdk-rust/releases.atom
    label: Rust SDK
    notable_only: true    # only major, first and first-stable releases surface
  - kind: security
    advisories: { ecosystem: go, package: go.temporal.io/server }
```

- `official: false` marks a reputable community source; it shows as "Community source".
- A shared feed (AWS, Google Cloud, Cloudflare) takes `match: [keywords]` so one
  feed can serve several stacks.
- A JSON Schema in `catalog/schema.json` validates every file.

## 2. Feed workflow

`.github/workflows/feed.yml`, on a schedule (every 6 hours, at an odd minute) and by hand.

1. Load the catalog and the previous state from the `feed` branch.
2. Fetch every source with conditional requests (ETag / Last-Modified), 10 at a
   time, with timeouts. Shared feeds are fetched once.
3. Parse with `feedparser`; query the GitHub Advisory Database for security sources.
4. Normalize each entry into an item: stable id (hash of guid or URL), title,
   cleaned summary (HTML stripped, first one or two sentences, at most about 220
   characters), URL, published date, first-seen date.
5. Classify and score:

   | Group | Rule | Score |
   | --- | --- | --- |
   | Act on it | security advisory or security release; release notes or posts mentioning breaking changes, removals, deprecations, end of life | 75-100 (by severity) |
   | Notable | major version, x.0, GA or 1.0, a source's first-ever release, a new project | 60-74 |
   | Worth reading | official blog posts | 40-59 |
   | Routine | minor and patch releases | 10-39 |
   | Hidden | nightlies, canaries, ordinary pre-releases | 0-9 |

   A source's first-ever release counts as Notable even when it is a preview.
6. Drop duplicates (same URL from two feeds) and items older than 30 days.
7. Write the files below, commit them to the `feed` branch, and deploy to Pages.
   Committing also keeps the schedule alive: GitHub pauses scheduled workflows
   in public repos after 60 days without activity.
8. One broken feed never fails the run. `health.json` records each source's last
   success and error.

`.github/workflows/validate.yml` runs on every PR that touches `catalog/`:
schema check, fetch each new or changed source, and a job summary with its
latest three items so the curator can judge relevance.

## 3. Feed files (the contract between the workflow and the app)

`catalog.json`, for search and browsing (about 20 KB):

```json
{
  "version": 1,
  "generatedAt": "2026-11-17T01:17:00Z",
  "categories": [{ "id": "workflows", "name": "Workflows and messaging" }],
  "stacks": [
    { "id": "temporal", "name": "Temporal", "category": "workflows",
      "aliases": ["temporal.io", "temporalio"], "mono": "T", "hue": 265,
      "kinds": ["blog", "release", "security"] }
  ]
}
```

`stacks/<id>.json`, the last 30 days for one stack:

```json
{
  "version": 1,
  "id": "temporal",
  "generatedAt": "2026-11-17T01:17:00Z",
  "items": [
    { "id": "3f9c...", "kind": "release", "group": "notable", "score": 72,
      "title": "Temporal Rust SDK 1.0.0",
      "summary": "The Rust SDK leaves public preview.",
      "url": "https://github.com/temporalio/sdk-rust/releases/tag/v1.0.0",
      "source": "Rust SDK", "official": true,
      "badges": ["first-stable", "official"],
      "severity": null, "cve": null,
      "publishedAt": "2026-09-04T17:02:00Z", "firstSeenAt": "2026-09-04T19:17:00Z" }
  ]
}
```

`health.json`: per source, last success, last error, item count.

## 4. The app (CloudCrane)

- One self-contained HTML file using the CloudCrane UI kit (`/_cc/lib/ccui-1.0.0.*`)
  and its icons.
- Network: one site, `blaise-shyaka.github.io`. Sends: which stacks you follow
  (as file names). Nothing else of yours leaves the phone.
- Once a day (first open), it fetches `catalog.json` and the file for each stack
  you follow, then builds Today on the phone from what is new since your last visit.
  The first briefing covers the last 14 days.
- Offline: shows the last briefing with its time.
- "Request it" opens the GitHub issue form; it sends nothing itself.
- New stacks reach the app with the catalog; no app update needed.

Saved on the phone, data version 1:

| Key | What | Shape |
| --- | --- | --- |
| `sw.follows` | stacks you follow | list of stack ids |
| `sw.seen` | when you last opened a briefing, and items you opened | object |
| `sw.cache` | last catalog and stack files, with when they were fetched | object |

Screens: Today, item detail, Stacks (search, following, browse by category,
request), first-open state, offline state. Mockups: the "Stack Watch screens"
canvas (linked in [DESIGN.md](DESIGN.md)).

Tests (CloudCrane runs them offline, with faked answers from the feed site and a
fixed clock):

- Follow Temporal; a faked `stacks/temporal.json` with the Rust SDK 1.0.0
  release shows under Notable releases with "First stable release".
- A faked advisory shows under Act on it with its severity.
- With no network, the last briefing shows with the offline notice.
- What you follow survives a reload.
- From v2 on, a data test proves what v1 saved carries over.

## 5. Curation loop

1. In the app, a search with no match offers "Request it on GitHub".
2. The issue form asks for: stack name, homepage, blog or RSS feed, release notes
   or changelog, GitHub repo, package names (npm, PyPI, Go, crates...).
3. A curator (or Claude, in a session) adds `catalog/<id>.yml` in a PR; CI shows
   what the feeds return.
4. Merge. The next workflow run publishes it, and the app picks it up with the catalog.

## 6. Plan

Phase A, the feed (this repo):

1. Catalog schema, validator, issue form.
2. Draft stack list (below), reviewed by you.
3. Sources for each stack, checked by the validate workflow (this sandbox can't
   reach most feed hosts, so CI is the checker).
4. Feed workflow and Pages.
5. You: merge to `main` (scheduled workflows only run on the default branch) and
   turn on Pages (Settings, Pages, Source: GitHub Actions).

Phase B, the app (CloudCrane):

- v1: follow stacks, the briefing, item detail, offline, request link, tests.
- Live with it.
- v2, the data change: a weight per stack (Core / Use / Watching) and muting
  kinds of items. `sw.follows` changes from `["temporal"]` to
  `[{ "id": "temporal", "level": "core", "mute": [] }]`, with a migration and a
  data test.
- v3, from real use. Candidates: AI summaries with your own Anthropic, OpenRouter
  or Gemini account (CloudCrane `connects`), a daily reminder, topic filters.

## 7. Known limits

- Freshness is up to 6 hours behind the source.
- A stack is only as good as its feeds; some projects publish no feed (fallbacks:
  GitHub release feeds, a changelog page feed, or leave it out).
- GitHub Pages caches files for about 10 minutes.
- What you follow is visible to GitHub as the file names your phone requests.

## Appendix: draft stack list (for review)

About 67 stacks. Counts in the mockups are illustrative.

- **Languages and runtimes (12):** Python, TypeScript, Node.js, Bun, Go, Rust,
  Java, Kotlin, Swift, .NET, Ruby, PHP
- **Frontend (9):** React, Next.js, Vue, Nuxt, Svelte, Angular, Astro,
  Tailwind CSS, Vite
- **Mobile (3):** React Native, Expo, Flutter
- **Backend frameworks (10):** Django, FastAPI, Flask, Ruby on Rails, Laravel,
  Spring Boot, Express, NestJS, ASP.NET Core, Phoenix
- **Databases and data (10):** PostgreSQL, MySQL, SQLite, MongoDB, Redis,
  Elasticsearch, ClickHouse, DuckDB, Supabase, Prisma
- **Cloud and infrastructure (11):** AWS, Google Cloud, Azure, Cloudflare,
  Vercel, Kubernetes, Docker, Terraform, GitHub, Prometheus, Grafana
- **Workflows and messaging (6):** Temporal, Apache Kafka, RabbitMQ, NATS,
  Apache Airflow, Celery
- **AI and machine learning (6):** PyTorch, Hugging Face, LangChain, OpenAI,
  Anthropic, Ollama
