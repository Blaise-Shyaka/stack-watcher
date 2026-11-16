"""Check the catalog, and optionally fetch stacks' sources to show what they return.

    python -m pipeline.validate catalog                    # schema only
    python -m pipeline.validate catalog --fetch temporal   # plus a live check of these stacks
    python -m pipeline.validate catalog --fetch all --summary "$GITHUB_STEP_SUMMARY"

Exits 1 when a file is invalid, or when a fetched source fails.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from .build import parse_feed, stack_items
from .catalog import load_catalog
from .fetch import fetch_advisories, fetch_feed, make_session


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("catalog", type=Path)
    parser.add_argument("--fetch", nargs="*", default=None,
                        help="stack ids to fetch live, or 'all'")
    parser.add_argument("--summary", type=Path, help="append a Markdown report here")
    args = parser.parse_args(argv)

    categories, stacks, errors = load_catalog(args.catalog)
    if errors:
        for err in errors:
            print(f"error: {err}")
        return 1
    print(f"{len(stacks)} stacks in {len(categories)} categories: all valid")
    if args.fetch is None:
        return 0

    wanted = set(args.fetch)
    chosen = stacks if "all" in wanted else [s for s in stacks if s["id"] in wanted]
    unknown = wanted - {"all"} - {s["id"] for s in stacks}
    if unknown:
        print(f"note: no such stack ids: {sorted(unknown)}")
    if not chosen:
        return 0

    session = make_session()
    urls = sorted({src["url"] for s in chosen for src in s["sources"] if "url" in src})
    kinds = {src["url"]: src["kind"] for s in chosen for src in s["sources"] if "url" in src}
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = dict(zip(urls, pool.map(lambda u: fetch_feed(session, u), urls)))

    feed_entries, problems = {}, []
    for url, res in results.items():
        entries = parse_feed(res.body, kinds[url], url) if res.status == 200 and res.body else None
        if entries is None:
            problems.append((url, res.error or "not an RSS or Atom feed"))
            entries = []
        elif not entries:
            problems.append((url, "the feed has no entries"))
        feed_entries[url] = (entries, len(entries))

    adv_entries = {}
    adv_wanted: dict[str, set] = {}
    for s in chosen:
        for src in s["sources"]:
            if "advisories" in src:
                adv_wanted.setdefault(src["advisories"]["ecosystem"], set()).update(src["advisories"]["packages"])
    for eco, pkgs in sorted(adv_wanted.items()):
        entries, err = fetch_advisories(session, eco, sorted(pkgs))
        if err:
            problems.append((f"advisories {eco}", err))
        for e in entries:
            for p in e["packages"]:
                adv_entries.setdefault((eco, p), []).append(e)

    now = datetime.now(timezone.utc)
    lines = [f"## Catalog check: {len(chosen)} stacks, {len(urls)} feeds, {len(problems)} problems", ""]
    if problems:
        lines += ["### Problems", "", "| Source | Error |", "| --- | --- |"]
        lines += [f"| {u} | {e.replace('|', '/')} |" for u, e in problems]
        lines.append("")
    lines += ["### Latest items per stack", ""]
    for s in chosen:
        items = stack_items(s, feed_entries, adv_entries, {}, now)
        lines.append(f"**{s['name']}** ({len(items)} items in 30 days)")
        for i in items[:3]:
            lines.append(f"- `{i['group']}` {i['title'][:90]} ({(i['publishedAt'] or '')[:10]})")
        lines.append("")
    report = "\n".join(lines)
    print(report)
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
