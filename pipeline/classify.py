"""Turn feed entries into briefing items: summaries, versions, groups and scores.

Groups, in briefing order:
  act      security, breaking changes, removals, deprecations   (75-100)
  notable  major, first stable, new project, release news        (60-74)
  read     official posts                                       (40-59)
  routine  minor and patch releases                             (10-39)
  hidden   nightlies, canaries, ordinary pre-releases (not published)
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

SUMMARY_MAX = 220

VERSION_RE = re.compile(r"(?<![\d.])(\d+)\.(\d+)(?:\.(\d+))?")
PRE_AFTER_RE = re.compile(
    r"^[-_.+~ ]?(?:alpha|beta|rc|pre|preview|canary|nightly|snapshot|dev|next|insiders|"
    r"experimental|exp|ea|a|b|m)(?![a-z])",
    re.I,
)
PRE_ANYWHERE_RE = re.compile(r"\b(nightly|canary|snapshot|insiders)\b|[.-]pre\b", re.I)
CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b")
SECURITY_TITLE_RE = re.compile(
    r"security[- ](releases?|updates?|fix(es)?|patch(es)?|advisor(y|ies)|bulletins?|announcements?)"
    r"|vulnerabilit(y|ies)|\bCVE-\d{4}-\d{4,}\b",
    re.I,
)
DEPRECATION_RE = re.compile(
    r"\b(deprecat\w*|end[- ]of[- ](life|support)|EOL|sunset(ting)?|removals?|"
    r"breaking changes?|retir(e|es|ing|ement))\b",
    re.I,
)
GA_RE = re.compile(r"general(ly)? availab(le|ility)|\bGA\b", re.I)
BREAKING_BODY_RE = re.compile(r"(?i:\bbreaking changes?\b)|\bBREAKING\b")
NO_BREAKING_RE = re.compile(r"\bno breaking changes?\b", re.I)
ANNOUNCE_RE = re.compile(
    r"\b(released?|announcing|now available|is out|is here|general(ly)? availab(le|ility)|GA|introducing)\b",
    re.I,
)
SKIP_LINE_RE = re.compile(
    r"^(what'?s changed|new contributors|full changelog|contributors|changelog|"
    r"release notes|assets|commits|sha256|checksums?)\b",
    re.I,
)
# Changelog section headings carry no information on their own.
HEADING_RE = re.compile(
    r"^(highlights?|added|changed|changes|fixed|fixes|removed|deprecated|deprecations|security|"
    r"features?|new features|bug ?fixes|notable changes|improvements|enhancements|breaking changes?|"
    r"other changes|dependencies|dependency updates|maintenance|documentation|docs|internal|"
    r"miscellaneous|misc|summary|overview|performance|refactor(ing)?|chores?|tests?)[:.]?$",
    re.I,
)
PR_TAIL_RE = re.compile(r"\s+by\s+@[\w-]+(\s+in\s+(https?://\S+|#\d+))?", re.I)
URL_RE = re.compile(r"https?://\S+")

SEVERITY_SCORE = {"critical": 100, "high": 92, "medium": 84, "moderate": 84, "low": 76}


@dataclass
class Version:
    major: int
    minor: int
    patch: int | None
    pre: bool

    @property
    def is_x0(self) -> bool:
        """x.0 or x.0.0"""
        return self.minor == 0 and self.patch in (0, None)

    @property
    def is_feature(self) -> bool:
        """x.y or x.y.0"""
        return self.patch in (0, None)


def parse_version(text: str | None) -> Version | None:
    if not text:
        return None
    m = VERSION_RE.search(text)
    if not m:
        return None
    rest = text[m.end():]
    pre = bool(PRE_AFTER_RE.match(rest)) or bool(PRE_ANYWHERE_RE.search(text))
    patch = int(m.group(3)) if m.group(3) is not None else None
    return Version(int(m.group(1)), int(m.group(2)), patch, pre)


def tag_from_url(url: str | None) -> str | None:
    if url and "/releases/tag/" in url:
        return url.rsplit("/releases/tag/", 1)[1].split("?")[0]
    if url and "/tag/" in url:
        return url.rsplit("/tag/", 1)[1].split("?")[0]
    return None


# --- text -----------------------------------------------------------------

_BLOCK_RE = re.compile(r"</?(p|li|br|h[1-6]|div|tr|ul|ol|pre|blockquote)[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v ]+")


def html_lines(raw: str | None) -> list[str]:
    """Split HTML into plain-text lines, one per block element."""
    if not raw:
        return []
    text = _BLOCK_RE.sub("\n", raw)
    text = _TAG_RE.sub("", text)
    text = html.unescape(text)
    lines = []
    for line in text.split("\n"):
        line = _WS_RE.sub(" ", line).strip(" -*••")
        if line:
            lines.append(line.strip())
    return lines


def plain_text(raw: str | None, limit: int = 4000) -> str:
    return " ".join(html_lines(raw))[:limit]


def truncate(text: str, limit: int = SUMMARY_MAX) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:.-") + "…"


def sentence_summary(raw: str | None, title: str = "") -> str:
    """First one or two sentences of a post."""
    text = plain_text(raw, 2000)
    if not text:
        return ""
    if title and text.lower().startswith(title.lower()):
        text = text[len(title):].lstrip(" :.-")
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sentences:
        if len(out) + len(s) + 1 > SUMMARY_MAX and out:
            break
        out = f"{out} {s}".strip()
        if len(out) >= 120:
            break
    return truncate(out)


def release_summary(raw: str | None, title: str = "") -> str:
    """The first meaningful lines of release notes, without PR noise."""
    parts: list[str] = []
    for line in html_lines(raw):
        core = re.sub(r"^[^\w(]+", "", line)
        if SKIP_LINE_RE.match(core) or HEADING_RE.match(core) or line.startswith("@"):
            continue
        line = PR_TAIL_RE.sub("", line)
        line = URL_RE.sub("", line).strip(" ()")
        if len(line) < 4 or line.lower() == title.lower():
            continue
        parts.append(line.rstrip("."))
        if sum(len(p) + 2 for p in parts) >= SUMMARY_MAX:
            break
    return truncate("; ".join(parts) + ("." if parts else ""))


# --- classification ---------------------------------------------------------

@dataclass
class Verdict:
    group: str
    score: int
    badges: list[str]
    cve: str | None = None


def _first_cve(*texts: str | None) -> str | None:
    for t in texts:
        if t:
            m = CVE_RE.search(t)
            if m:
                return m.group(0)
    return None


def classify_release(title: str, text: str, url: str | None, scheme: str = "semver",
                     first_release: bool = False) -> Verdict:
    v = parse_version(tag_from_url(url)) or parse_version(title)
    cve = _first_cve(title, text)
    breaking = bool(BREAKING_BODY_RE.search(text or "")) and not NO_BREAKING_RE.search(text or "")

    if v and v.pre:
        if first_release:
            return Verdict("notable", 66, ["new-project", "preview"], cve)
        return Verdict("hidden", 5, ["preview"], cve)
    if SECURITY_TITLE_RE.search(title) or cve:
        return Verdict("act", 86, ["security"], cve)
    if first_release:
        return Verdict("notable", 68, ["new-project"], cve)
    if v is None:
        return Verdict("routine", 25, [], cve)

    if v.major == 1 and v.is_x0:
        verdict = Verdict("notable", 74, ["first-stable"], cve)
    elif v.major >= 1 and v.is_x0:
        verdict = Verdict("notable", 70, ["major"], cve)
    elif scheme == "minor" and v.is_feature:
        verdict = Verdict("notable", 64, ["major"], cve)
    elif v.is_feature:
        verdict = Verdict("routine", 35, ["minor"], cve)
    else:
        verdict = Verdict("routine", 20, ["patch"], cve)

    if breaking:
        if verdict.group == "notable":
            verdict.badges.append("breaking")
        elif "minor" in verdict.badges and v.major >= 1:
            verdict = Verdict("act", 76, ["breaking"], cve)
    return verdict


def classify_post(title: str, text: str, kind: str, scheme: str = "semver",
                  names: list[str] | None = None) -> Verdict:
    cve = _first_cve(title, text)
    if kind == "security":
        return Verdict("act", 85, ["security"], cve)
    if SECURITY_TITLE_RE.search(title) or (cve and kind != "changelog"):
        return Verdict("act", 82, ["security"], cve)
    if DEPRECATION_RE.search(title) and not NO_BREAKING_RE.search(title):
        badge = "deprecation" if re.search(r"deprecat|end[- ]of|EOL|sunset|retir", title, re.I) else "breaking"
        return Verdict("act", 76, [badge], cve)
    if ANNOUNCE_RE.search(title):
        v = parse_version(title)
        if v and not v.pre and (v.is_x0 or (scheme == "minor" and v.is_feature)):
            return Verdict("notable", 62, ["release"], cve)
        if v:
            return Verdict("routine", 28, ["release"], cve)
        # "PostgreSQL 18 Released!": a bare major number right after the project's name.
        for name in names or []:
            if re.search(rf"\b{re.escape(name)}\s+v?\d+(?![.\d])", title, re.I):
                return Verdict("notable", 62, ["release"], cve)
        if kind == "changelog" and GA_RE.search(title):
            return Verdict("notable", 60, ["release"], cve)
    if kind == "changelog":
        return Verdict("read", 42, [], cve)
    return Verdict("read", 50, [], cve)


def classify_advisory(severity: str | None) -> Verdict:
    sev = (severity or "").lower()
    return Verdict("act", SEVERITY_SCORE.get(sev, 80), ["security"])
