import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import classify as C
from pipeline.build import CatalogError, build, display_title
from pipeline.catalog import load_catalog, validate_stack
from pipeline.fetch import FetchResult, fetch_advisories

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent
NOW = datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc)


# --- versions and classification ----------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("v1.0.0", (1, 0, 0, False)),
    ("v0.1.0-alpha.1", (0, 1, 0, True)),
    ("go1.26rc1", (1, 26, None, True)),
    ("go1.25.4", (1, 25, 4, False)),
    ("v3.15.0a1", (3, 15, 0, True)),
    ("next@15.0.0-canary.3", (15, 0, 0, True)),
    ("Next.js 15.2.4", (15, 2, 4, False)),
    ("v4.0.0-M3", (4, 0, 0, True)),
    ("v25.10.2.65-stable", (25, 10, 2, False)),
    ("r8.2.1-rc0", (8, 2, 1, True)),
])
def test_parse_version(text, expected):
    v = C.parse_version(text)
    assert (v.major, v.minor, v.patch, v.pre) == expected


def test_parse_version_none():
    assert C.parse_version("Weekly update") is None


@pytest.mark.parametrize("tag, scheme, group, badge", [
    ("v1.0.0", "semver", "notable", "first-stable"),
    ("v2.0.0", "semver", "notable", "major"),
    ("v1.27.0", "minor", "notable", "major"),
    ("v1.2.0", "semver", "routine", "minor"),
    ("v1.2.3", "semver", "routine", "patch"),
])
def test_classify_release_versions(tag, scheme, group, badge):
    v = C.classify_release(tag, "", f"https://github.com/o/r/releases/tag/{tag}", scheme)
    assert v.group == group and badge in v.badges


def test_prerelease_hidden_unless_first():
    url = "https://github.com/o/r/releases/tag/v0.1.0-alpha.1"
    assert C.classify_release("v0.1.0-alpha.1", "", url).group == "hidden"
    first = C.classify_release("v0.1.0-alpha.1", "", url, first_release=True)
    assert first.group == "notable" and "new-project" in first.badges


def test_cve_in_release_notes_is_act():
    v = C.classify_release("v1.2.3", "Fixes CVE-2026-0001 in the parser", None)
    assert v.group == "act" and v.cve == "CVE-2026-0001"


def test_breaking_minor_is_act_but_not_when_denied():
    assert C.classify_release("v1.3.0", "## Breaking changes\n- removed x", None).group == "act"
    assert C.classify_release("v1.3.0", "No breaking changes.", None).group == "routine"


@pytest.mark.parametrize("title, kind, group, badge", [
    ("Django security releases issued: 5.2.8 and 5.1.14", "blog", "act", "security"),
    ("Deprecating the legacy visibility API", "blog", "act", "deprecation"),
    ("Kubernetes v1.35: API removals ahead", "blog", "act", "breaking"),
    ("Announcing Rust 1.91.0", "blog", "notable", "release"),
    ("Writing your first Workflow in Rust", "blog", "read", None),
    ("New dashboard filters", "changelog", "read", None),
])
def test_classify_post(title, kind, group, badge):
    scheme = "minor" if "Rust" in title else "semver"
    v = C.classify_post(title, "", kind, scheme)
    assert v.group == group
    if badge:
        assert badge in v.badges


def test_bare_major_announcement_and_changelog_launch():
    assert C.classify_post("PostgreSQL 18 Released!", "", "blog", names=["PostgreSQL"]).group == "notable"
    assert C.classify_post("Generally available: Flex consumption plan", "", "changelog").group == "notable"
    assert C.classify_post("Introducing our new office", "", "blog").group == "read"


def test_release_summary_drops_pr_noise():
    html = ("<h2>What's Changed</h2><ul><li>Remove public preview warnings by @a in "
            "https://github.com/o/r/pull/1</li><li>Stabilize the API by @b in https://github.com/o/r/pull/2</li></ul>"
            "<p>Full Changelog: v0.9.0...v1.0.0</p>")
    assert C.release_summary(html, "v1.0.0") == "Remove public preview warnings; Stabilize the API."


def test_sentence_summary_is_short():
    s = C.sentence_summary("<p>" + "One sentence here. " * 40 + "</p>")
    assert len(s) <= C.SUMMARY_MAX


def test_display_title():
    stack = {"name": "Temporal"}
    assert display_title(stack, {"label": "Rust SDK"}, "v1.0.0", "") == "Temporal Rust SDK 1.0.0"
    assert display_title({"name": "Go"}, {}, "go1.25.4", "") == "Go 1.25.4"
    assert display_title(stack, {}, "Temporal Server 1.29", "") == "Temporal Server 1.29"


# --- catalog -------------------------------------------------------------------

def test_validate_stack_errors():
    bad = {"id": "Bad Id", "name": "", "category": "nope", "mono": "TOOLONG", "hue": 400,
           "sources": [{"kind": "blog", "url": "http://insecure"}], "extra": 1}
    errors = validate_stack(bad, "stacks/bad.yml", {"workflows"})
    text = " ".join(errors)
    for word in ("unknown keys", "id must", "name is required", "category", "mono", "hue", "https://"):
        assert word in text


def test_fixture_catalog_loads():
    cats, stacks, errors = load_catalog(FIX / "catalog")
    assert errors == [] and stacks[0]["id"] == "temporal"


def test_real_catalog_is_valid():
    if not (ROOT / "catalog" / "categories.yml").exists():
        pytest.skip("no catalog yet")
    _, stacks, errors = load_catalog(ROOT / "catalog")
    assert errors == []
    assert len(stacks) >= 50


# --- advisories ------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status, data):
        self.status_code, self._data, self.text = status, data, json.dumps(data)

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, data):
        self.data, self.calls = data, []

    def get(self, url, headers=None, params=None, timeout=None):
        self.calls.append(params)
        return FakeResponse(200, self.data)


def test_fetch_advisories_keeps_only_wanted_packages():
    session = FakeSession(json.loads((FIX / "advisories.json").read_text()))
    entries, err = fetch_advisories(session, "npm", ["@temporalio/worker"], token="")
    assert err is None and len(entries) == 1
    e = entries[0]
    assert e["cve"] == "CVE-2026-12345" and e["severity"] == "high"
    assert e["summary"] == "Affects @temporalio/worker < 1.13.2. Fixed in 1.13.2."
    assert session.calls[0]["affects"] == "@temporalio/worker"


# --- end to end ----------------------------------------------------------------------

def fake_fetch(mapping):
    def fetch(url, etag, modified):
        if url not in mapping:
            return FetchResult(url, None, error="ConnectionError: unreachable")
        value = mapping[url]
        if value == 304:
            return FetchResult(url, 304)
        return FetchResult(url, 200, (FIX / value).read_bytes(), etag='"abc"')
    return fetch


def fake_advisories(eco, pkgs):
    session = FakeSession(json.loads((FIX / "advisories.json").read_text()))
    return fetch_advisories(session, eco, pkgs, token="")


def test_build_end_to_end(tmp_path):
    feeds = {"https://example.test/blog.rss": "blog.rss",
             "https://example.test/sdk-rust.atom": "sdk-rust.atom"}
    summary = build(FIX / "catalog", tmp_path, NOW, fake_fetch(feeds), fake_advisories, log=lambda _: None)
    assert summary["failed"] == 0

    catalog = json.loads((tmp_path / "catalog.json").read_text())
    assert catalog["stacks"][0]["id"] == "temporal"
    assert catalog["stacks"][0]["kinds"] == ["blog", "release", "security"]

    stack = json.loads((tmp_path / "stacks" / "temporal.json").read_text())
    by_title = {i["title"]: i for i in stack["items"]}
    rust = by_title["Temporal Rust SDK 1.0.0"]
    assert rust["group"] == "notable" and "first-stable" in rust["badges"]
    assert rust["summary"].startswith("Remove public preview warnings")
    assert by_title["Worker crashes on crafted payloads"]["group"] == "act"
    assert by_title["Deprecating the legacy visibility API"]["group"] == "act"
    assert by_title["Writing your first Workflow in Rust"]["group"] == "read"
    assert "An old post" not in by_title                 # outside 30 days
    assert "Temporal Rust SDK 1.0.1-rc.1" not in by_title  # pre-release hidden
    assert "Temporal Rust SDK 0.1.0-alpha.1" not in by_title  # outside 30 days

    # Second run: the release feed answers 304 and the blog is down. Cached entries
    # are reused, firstSeenAt is kept, and health records the failure.
    first_seen = rust["firstSeenAt"]
    later = datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc)
    build(FIX / "catalog", tmp_path, later,
          fake_fetch({"https://example.test/sdk-rust.atom": 304}), fake_advisories, log=lambda _: None)
    stack2 = json.loads((tmp_path / "stacks" / "temporal.json").read_text())
    titles2 = {i["title"]: i for i in stack2["items"]}
    assert titles2["Temporal Rust SDK 1.0.0"]["firstSeenAt"] == first_seen
    assert "Writing your first Workflow in Rust" in titles2
    health = json.loads((tmp_path / "health.json").read_text())
    blog = next(s for s in health["sources"] if s["url"].endswith("blog.rss"))
    assert blog["ok"] is False and blog["failures"] == 1


def test_build_refuses_invalid_catalog(tmp_path):
    (tmp_path / "cat" / "stacks").mkdir(parents=True)
    (tmp_path / "cat" / "categories.yml").write_text("- id: x\n  name: X\n")
    (tmp_path / "cat" / "stacks" / "bad.yml").write_text("id: bad\nname: Bad\n")
    with pytest.raises(CatalogError):
        build(tmp_path / "cat", tmp_path / "out", NOW, fake_fetch({}), fake_advisories)
