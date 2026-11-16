"""Build the feed: fetch every source in the catalog and write the files the app reads.

    python -m pipeline.build --catalog catalog --out out

Writes into --out (the checked-out `feed` branch):
    catalog.json           the stacks, for search and browsing
    stacks/<id>.json       the last 30 days for one stack
    health.json            each source's last success or error
    state/sources.json     ETags and failure counts
    state/cache/<h>.json   each feed's last parsed entries (used on 304 or failure)
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import re
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import feedparser

from . import classify as C
from .catalog import load_catalog
from .fetch import FetchResult, fetch_advisories, fetch_feed, make_session

WINDOW_DAYS = 30
MAX_ITEMS = 150
GROUP_ORDER = {"act": 0, "notable": 1, "read": 2, "routine": 3}
DEFAULT_SOURCE = {"blog": "Official blog", "release": "Release", "changelog": "Changelog",
                  "security": "Security advisory"}
BARE_TAG_RE = re.compile(r"(?i)(v|go|r|version-|release-|rel-|mysql-|jdk-|rel/)?\d[\w.+-]*")


class CatalogError(Exception):
    pass


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def short_hash(text: str, n: int = 16) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:n]


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n",
                    encoding="utf-8")


# --- feeds -------------------------------------------------------------------

def parse_feed(body: bytes, kind: str) -> list[dict] | None:
    """Normalized entries, or None when the body isn't a feed."""
    fp = feedparser.parse(body)
    if not fp.entries and (fp.bozo or not fp.feed):
        return None
    entries = []
    for e in fp.entries[:50]:
        link = e.get("link") or ""
        title = " ".join(C.html_lines(e.get("title") or "")) or link
        content = ""
        if e.get("content"):
            content = e["content"][0].get("value") or ""
        raw = content or e.get("summary") or ""
        when = e.get("published_parsed") or e.get("updated_parsed")
        published = (iso(datetime.fromtimestamp(calendar.timegm(when), tz=timezone.utc))
                     if when else None)
        if kind == "release":
            summary = C.release_summary(raw, title)
        else:
            summary = C.sentence_summary(e.get("summary") or raw, title)
        entries.append({
            "key": e.get("id") or link or title,
            "title": title,
            "link": link,
            "published": published,
            "summary": summary,
            "text": C.plain_text(raw, 1500),
            "tags": [t.get("term", "") for t in e.get("tags", []) if t.get("term")],
        })
    return entries


def display_title(stack: dict, src: dict, title: str, link: str) -> str:
    """Give bare tags ("v1.0.0", "go1.25.4") the project's name."""
    if not BARE_TAG_RE.fullmatch(title.strip()):
        return title
    m = C.VERSION_RE.search(title)
    version = title[m.start():] if m else title
    name = stack["name"]
    label = src.get("label")
    if label and label.lower() not in name.lower():
        name = f"{name} {label}"
    return f"{name} {version}"


def keyword_hit(entry: dict, words: list[str]) -> bool:
    hay = " ".join([entry.get("title", "")] + entry.get("tags", []))
    return any(re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", hay, re.I) for w in words)


def stack_items(stack: dict, feed_entries: dict, adv_entries: dict, first_seen: dict,
                now: datetime) -> list[dict]:
    cutoff = now - timedelta(days=WINDOW_DAYS)
    items: dict[str, dict] = {}

    def add(item: dict, when: datetime | None):
        seen = first_seen.get(item["id"]) or iso(now)
        item["firstSeenAt"] = seen
        ref = when or parse_iso(seen)
        if ref and ref < cutoff:
            return
        old = items.get(item["id"])
        if not old or item["score"] > old["score"]:
            items[item["id"]] = item

    for src in stack["sources"]:
        source_name = src.get("label") or DEFAULT_SOURCE[src["kind"]]
        official = src.get("official", True)
        if "advisories" in src:
            eco = src["advisories"]["ecosystem"]
            for pkg in src["advisories"]["packages"]:
                for e in adv_entries.get((eco, pkg), []):
                    v = C.classify_advisory(e.get("severity"))
                    add({
                        "id": short_hash(e["link"] or e["key"]), "kind": "security",
                        "group": v.group, "score": v.score, "title": e["title"],
                        "summary": e["summary"], "url": e["link"],
                        "source": "GitHub Advisory Database", "official": True,
                        "badges": v.badges, "severity": (e.get("severity") or None),
                        "cve": e.get("cve"), "publishedAt": e.get("published"),
                    }, parse_iso(e.get("published")))
            continue

        entries, seen_count = feed_entries.get(src["url"], ([], 0))
        if src.get("match"):
            entries = [e for e in entries if keyword_hit(e, src["match"])]
        if src.get("title_match"):
            rx = re.compile(src["title_match"])
            entries = [e for e in entries
                       if rx.search(e["title"]) or rx.search(C.tag_from_url(e["link"]) or "")]
        scheme = src.get("scheme", "semver")
        first_release = src["kind"] == "release" and len(entries) == 1 and seen_count <= 1
        for e in entries:
            if src["kind"] == "release":
                v = C.classify_release(e["title"], e["text"], e["link"], scheme, first_release)
            else:
                v = C.classify_post(e["title"], e["text"], src["kind"], scheme,
                                    [stack["name"]] + stack.get("aliases", []))
            if v.group == "hidden":
                continue
            if src.get("notable_only") and v.group in ("routine", "read"):
                continue
            add({
                "id": short_hash(e["link"] or e["key"]), "kind": src["kind"],
                "group": v.group, "score": v.score,
                "title": display_title(stack, src, e["title"], e["link"]),
                "summary": e["summary"], "url": e["link"] or None,
                "source": source_name, "official": official,
                "badges": v.badges + (["official"] if official else ["community"]),
                "severity": None, "cve": v.cve, "publishedAt": e["published"],
            }, parse_iso(e["published"]))

    ranked = sorted(items.values(),
                    key=lambda i: (GROUP_ORDER[i["group"]], -(parse_iso(i["publishedAt"] or i["firstSeenAt"]) or now).timestamp()))
    kept = ranked[:MAX_ITEMS]
    kept.sort(key=lambda i: i["publishedAt"] or i["firstSeenAt"], reverse=True)
    return kept


# --- the build -----------------------------------------------------------------

def build(catalog_dir: Path, out_dir: Path, now: datetime | None = None,
          fetch_fn: Callable[[str, str | None, str | None], FetchResult] | None = None,
          advisories_fn: Callable[[str, list[str]], tuple[list[dict], str | None]] | None = None,
          log: Callable[[str], None] = print) -> dict:
    now = now or datetime.now(timezone.utc)
    now_iso = iso(now)
    categories, stacks, errors = load_catalog(catalog_dir)
    if errors:
        raise CatalogError(errors)

    if fetch_fn is None or advisories_fn is None:
        session = make_session()
        fetch_fn = fetch_fn or (lambda u, et, mod: fetch_feed(session, u, et, mod))
        advisories_fn = advisories_fn or (lambda eco, pkgs: fetch_advisories(session, eco, pkgs))

    out = Path(out_dir)
    state_path = out / "state" / "sources.json"
    state: dict = read_json(state_path, {})

    feeds: dict[str, dict] = {}
    adv_wanted: dict[str, set] = defaultdict(set)
    for s in stacks:
        for src in s["sources"]:
            if "advisories" in src:
                adv_wanted[src["advisories"]["ecosystem"]].update(src["advisories"]["packages"])
            else:
                f = feeds.setdefault(src["url"], {"kind": src["kind"], "stacks": set()})
                f["stacks"].add(s["id"])

    urls = sorted(feeds)
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(
            lambda u: fetch_fn(u, state.get(u, {}).get("etag"), state.get(u, {}).get("modified")), urls))

    feed_entries: dict[str, tuple[list[dict], int]] = {}
    health_sources = []
    for url, res in zip(urls, results):
        st = state.setdefault(url, {})
        cache_path = out / "state" / "cache" / f"{short_hash(url)}.json"
        cache = read_json(cache_path, {"entries": [], "seen": []})
        error = None
        if res.status == 200 and res.body is not None and not res.error:
            parsed = parse_feed(res.body, feeds[url]["kind"])
            if parsed is None:
                error = "the address did not return an RSS or Atom feed"
            else:
                seen = list(dict.fromkeys(cache.get("seen", []) + [e["key"] for e in parsed]))[-500:]
                cache = {"entries": parsed, "seen": seen}
                write_json(cache_path, cache)
                st.update(etag=res.etag, modified=res.modified)
        elif res.status != 304:
            error = res.error or f"HTTP {res.status}"
        if error:
            st["failures"] = st.get("failures", 0) + 1
            st["error"], st["lastError"] = error, now_iso
            st.pop("etag", None)
            st.pop("modified", None)
        else:
            st.update(failures=0, error=None, lastOk=now_iso)
        feed_entries[url] = (cache.get("entries", []), len(cache.get("seen", [])))
        health_sources.append({
            "url": url, "kind": feeds[url]["kind"], "stacks": sorted(feeds[url]["stacks"]),
            "ok": error is None, "error": error, "failures": st.get("failures", 0),
            "lastOk": st.get("lastOk"), "entries": len(cache.get("entries", [])),
        })

    adv_entries: dict[tuple[str, str], list[dict]] = defaultdict(list)
    adv_health = {}
    for eco in sorted(adv_wanted):
        entries, err = advisories_fn(eco, sorted(adv_wanted[eco]))
        adv_health[eco] = {"ok": err is None, "error": err, "count": len(entries)}
        for e in entries:
            for pkg in e["packages"]:
                adv_entries[(eco, pkg)].append(e)

    (out / "stacks").mkdir(parents=True, exist_ok=True)
    total_items = 0
    for s in stacks:
        path = out / "stacks" / f"{s['id']}.json"
        previous = read_json(path, {}).get("items", [])
        first_seen = {i["id"]: i.get("firstSeenAt") for i in previous}
        items = stack_items(s, feed_entries, adv_entries, first_seen, now)
        total_items += len(items)
        write_json(path, {"version": 1, "id": s["id"], "name": s["name"],
                          "generatedAt": now_iso, "items": items})
    keep = {f"{s['id']}.json" for s in stacks}
    for path in (out / "stacks").glob("*.json"):
        if path.name not in keep:
            path.unlink()

    write_json(out / "catalog.json", {
        "version": 1, "generatedAt": now_iso,
        "categories": [{"id": c["id"], "name": c["name"]} for c in categories],
        "stacks": [{
            "id": s["id"], "name": s["name"], "category": s["category"],
            "aliases": s.get("aliases", []), "mono": s["mono"], "hue": s["hue"],
            "homepage": s.get("homepage"),
            "kinds": sorted({src["kind"] for src in s["sources"]}),
        } for s in stacks],
    })
    ok = sum(1 for h in health_sources if h["ok"])
    write_json(out / "health.json", {
        "generatedAt": now_iso, "ok": ok, "failed": len(health_sources) - ok,
        "sources": health_sources, "advisories": adv_health,
    })
    write_json(state_path, state)

    failed = [h for h in health_sources if not h["ok"]]
    log(f"{len(stacks)} stacks, {len(urls)} feeds ({ok} ok, {len(failed)} failed), "
        f"{len(adv_wanted)} advisory ecosystems, {total_items} items")
    for h in failed:
        log(f"  FAILED {h['url']} ({', '.join(h['stacks'])}): {h['error']}")
    for eco, h in adv_health.items():
        if not h["ok"]:
            log(f"  FAILED advisories {eco}: {h['error']}")
    return {"stacks": len(stacks), "feeds": len(urls), "ok": ok, "failed": len(failed),
            "items": total_items}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--catalog", default="catalog", type=Path)
    parser.add_argument("--out", default="out", type=Path)
    args = parser.parse_args(argv)
    try:
        build(args.catalog, args.out)
    except CatalogError as exc:
        for err in exc.args[0]:
            print(f"catalog error: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
