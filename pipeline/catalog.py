"""Load and validate the curated catalog in catalog/."""

from __future__ import annotations

import re
from pathlib import Path

import yaml

KINDS = {"blog", "release", "changelog", "security"}
SCHEMES = {"semver", "minor"}
# Ecosystems the GitHub Advisory Database accepts.
ECOSYSTEMS = {
    "actions", "composer", "erlang", "go", "maven", "npm", "nuget",
    "other", "pip", "pub", "rubygems", "rust", "swift",
}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")

STACK_KEYS = {"id", "name", "category", "aliases", "mono", "hue", "homepage", "sources"}
SOURCE_KEYS = {
    "kind", "url", "label", "official", "match", "title_match",
    "notable_only", "scheme", "advisories",
}


def _is_str_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value)


def validate_source(src, where: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(src, dict):
        return [f"{where}: a source must be a mapping"]
    unknown = set(src) - SOURCE_KEYS
    if unknown:
        errors.append(f"{where}: unknown keys {sorted(unknown)}")
    kind = src.get("kind")
    if kind not in KINDS:
        errors.append(f"{where}: kind must be one of {sorted(KINDS)}")
    if "advisories" in src:
        adv = src["advisories"]
        if kind != "security":
            errors.append(f"{where}: advisories only go on a security source")
        if not isinstance(adv, dict):
            errors.append(f"{where}: advisories must be a mapping")
        else:
            if adv.get("ecosystem") not in ECOSYSTEMS:
                errors.append(f"{where}: advisories.ecosystem must be one of {sorted(ECOSYSTEMS)}")
            if not _is_str_list(adv.get("packages")) or not adv.get("packages"):
                errors.append(f"{where}: advisories.packages must be a non-empty list of names")
            if set(adv) - {"ecosystem", "packages"}:
                errors.append(f"{where}: advisories takes only ecosystem and packages")
        if "url" in src:
            errors.append(f"{where}: a source has either url or advisories, not both")
    else:
        url = src.get("url")
        if not isinstance(url, str) or not url.startswith("https://"):
            errors.append(f"{where}: url must be an https:// address")
    if "label" in src and not isinstance(src["label"], str):
        errors.append(f"{where}: label must be text")
    for flag in ("official", "notable_only"):
        if flag in src and not isinstance(src[flag], bool):
            errors.append(f"{where}: {flag} must be true or false")
    if "match" in src and not _is_str_list(src["match"]):
        errors.append(f"{where}: match must be a list of keywords")
    if "title_match" in src:
        try:
            re.compile(src["title_match"])
        except (re.error, TypeError) as exc:
            errors.append(f"{where}: title_match is not a valid regular expression ({exc})")
    if "scheme" in src and src["scheme"] not in SCHEMES:
        errors.append(f"{where}: scheme must be one of {sorted(SCHEMES)}")
    return errors


def validate_stack(data, filename: str, categories: set[str]) -> list[str]:
    where = filename
    if not isinstance(data, dict):
        return [f"{where}: the file must hold a mapping"]
    errors: list[str] = []
    unknown = set(data) - STACK_KEYS
    if unknown:
        errors.append(f"{where}: unknown keys {sorted(unknown)}")
    sid = data.get("id")
    if not isinstance(sid, str) or not ID_RE.match(sid):
        errors.append(f"{where}: id must be lower-case letters, digits and hyphens")
    elif Path(filename).stem != sid:
        errors.append(f"{where}: the file name must be {sid}.yml")
    if not isinstance(data.get("name"), str) or not data["name"].strip():
        errors.append(f"{where}: name is required")
    if data.get("category") not in categories:
        errors.append(f"{where}: category must be one of {sorted(categories)}")
    if "aliases" in data and not _is_str_list(data["aliases"]):
        errors.append(f"{where}: aliases must be a list of words")
    mono = data.get("mono")
    if not isinstance(mono, str) or not 1 <= len(mono) <= 3:
        errors.append(f"{where}: mono must be 1 to 3 characters")
    hue = data.get("hue")
    if not isinstance(hue, int) or isinstance(hue, bool) or not 0 <= hue <= 359:
        errors.append(f"{where}: hue must be a whole number from 0 to 359")
    if "homepage" in data and not str(data["homepage"]).startswith("https://"):
        errors.append(f"{where}: homepage must be an https:// address")
    sources = data.get("sources")
    if not isinstance(sources, list) or not sources:
        errors.append(f"{where}: sources must be a non-empty list")
    else:
        for i, src in enumerate(sources):
            errors.extend(validate_source(src, f"{where} sources[{i}]"))
    return errors


def load_catalog(catalog_dir: Path) -> tuple[list[dict], list[dict], list[str]]:
    """Return (categories, stacks, errors). Stacks are sorted by name."""
    catalog_dir = Path(catalog_dir)
    errors: list[str] = []
    cat_file = catalog_dir / "categories.yml"
    try:
        categories = yaml.safe_load(cat_file.read_text(encoding="utf-8")) or []
    except (OSError, yaml.YAMLError) as exc:
        return [], [], [f"categories.yml: {exc}"]
    if not isinstance(categories, list) or not all(
        isinstance(c, dict) and ID_RE.match(str(c.get("id", ""))) and c.get("name") for c in categories
    ):
        return [], [], ["categories.yml: must be a list of {id, name}"]
    cat_ids = {c["id"] for c in categories}

    stacks: list[dict] = []
    seen: dict[str, str] = {}
    for path in sorted((catalog_dir / "stacks").glob("*.yml")):
        rel = f"stacks/{path.name}"
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            errors.append(f"{rel}: not valid YAML ({exc})")
            continue
        problems = validate_stack(data, rel, cat_ids)
        if problems:
            errors.extend(problems)
            continue
        if data["id"] in seen:
            errors.append(f"{rel}: id {data['id']} is already used by {seen[data['id']]}")
            continue
        seen[data["id"]] = rel
        stacks.append(data)
    stacks.sort(key=lambda s: s["name"].lower())
    return categories, stacks, errors
