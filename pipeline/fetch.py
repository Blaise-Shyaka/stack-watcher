"""HTTP fetching: feeds with conditional requests, and GitHub security advisories."""

from __future__ import annotations

import os
from dataclasses import dataclass

import requests

USER_AGENT = "stack-watcher-feed/1.0 (+https://github.com/Blaise-Shyaka/stack-watcher)"
TIMEOUT = (10, 30)
MAX_BYTES = 8 * 1024 * 1024
ADVISORIES_URL = "https://api.github.com/advisories"


@dataclass
class FetchResult:
    url: str
    status: int | None          # 200, 304, an HTTP error code, or None on a network error
    body: bytes | None = None
    etag: str | None = None
    modified: str | None = None
    error: str | None = None


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/atom+xml, application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
    })
    adapter = requests.adapters.HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=1)
    session.mount("https://", adapter)
    return session


def fetch_feed(session: requests.Session, url: str, etag: str | None = None,
               modified: str | None = None) -> FetchResult:
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if modified:
        headers["If-Modified-Since"] = modified
    try:
        with session.get(url, headers=headers, timeout=TIMEOUT, stream=True, allow_redirects=True) as resp:
            if resp.status_code == 304:
                return FetchResult(url, 304, etag=etag, modified=modified)
            if resp.status_code != 200:
                return FetchResult(url, resp.status_code, error=f"HTTP {resp.status_code}")
            chunks, size = [], 0
            for chunk in resp.iter_content(64 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    return FetchResult(url, 200, error="feed is larger than 8 MB")
                chunks.append(chunk)
            return FetchResult(
                url, 200, b"".join(chunks),
                etag=resp.headers.get("ETag"), modified=resp.headers.get("Last-Modified"),
            )
    except requests.RequestException as exc:
        return FetchResult(url, None, error=f"{type(exc).__name__}: {exc}"[:300])


def _patched(value) -> str | None:
    if isinstance(value, dict):
        return value.get("identifier")
    return value or None


def fetch_advisories(session: requests.Session, ecosystem: str, packages: list[str],
                     token: str | None = None) -> tuple[list[dict], str | None]:
    """Reviewed advisories affecting any of the packages, newest first.

    Returns (entries, error). Each entry names the packages it affects."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = token if token is not None else os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params = {
        "ecosystem": ecosystem, "affects": ",".join(packages), "type": "reviewed",
        "sort": "published", "direction": "desc", "per_page": 100,
    }
    try:
        resp = session.get(ADVISORIES_URL, headers=headers, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        return [], f"{type(exc).__name__}: {exc}"[:300]
    if resp.status_code != 200:
        return [], f"HTTP {resp.status_code}: {resp.text[:200]}"
    wanted = set(packages)
    entries = []
    for adv in resp.json():
        hits = []
        for vuln in adv.get("vulnerabilities") or []:
            pkg = (vuln.get("package") or {})
            if pkg.get("ecosystem") == ecosystem and pkg.get("name") in wanted:
                hits.append((pkg["name"], vuln.get("vulnerable_version_range"), _patched(vuln.get("first_patched_version"))))
        if not hits:
            continue
        name, rng, fixed = hits[0]
        summary = f"Affects {name} {rng}." if rng else f"Affects {name}."
        summary += f" Fixed in {fixed}." if fixed else " No fixed version yet."
        entries.append({
            "key": adv.get("ghsa_id"),
            "title": (adv.get("summary") or adv.get("ghsa_id") or "Security advisory").strip(),
            "link": adv.get("html_url"),
            "published": adv.get("published_at"),
            "severity": adv.get("severity"),
            "cve": adv.get("cve_id"),
            "packages": sorted({h[0] for h in hits}),
            "summary": summary,
        })
    return entries, None
