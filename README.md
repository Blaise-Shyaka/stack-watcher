# Stack Watch

A daily briefing of what changed in the tech you work with: releases, security
advisories and posts from each project's own team. Pick your stacks from a curated
list; each morning you see only what changed in them, most important first.

- **The app** is a CloudCrane phone app (`app/`). It reads the feed and builds your
  briefing on the phone; what you follow stays there.
- **The feed** is built by a GitHub Actions workflow every 6 hours from the curated
  catalog (`catalog/`) and published to the [`feed`](../../tree/feed) branch.

## Missing a stack?

[Request it](../../issues/new?template=stack-request.yml). Every stack is added by
hand from its official blog, release notes and security advisories.

## Adding a stack

1. Add `catalog/stacks/<id>.yml` (see any existing file; `pipeline/catalog.py` has
   every field).
2. Open a pull request. The **Catalog check** workflow validates it and fetches its
   sources, showing the latest items in the job summary.
3. Merge. The next feed run publishes it, and the app picks it up.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m pipeline.validate catalog
.venv/bin/python -m pipeline.build --catalog catalog --out out
```

More in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/DESIGN.md](docs/DESIGN.md).

## Testing the app locally

`app/tests.json` holds the app's CloudCrane tests (the same ones CloudCrane runs in its
browser). `tools/run-app-tests.js` replays them in Chromium against `app/index.html`, with a
stand-in for CloudCrane's UI kit and a frozen clock like CloudCrane's:

```bash
node tools/run-app-tests.js            # all tests
node tools/run-app-tests.js T-9        # one test
APP_PREV=old.html node tools/run-app-tests.js T-10   # data test: setup in old, check in new
```
