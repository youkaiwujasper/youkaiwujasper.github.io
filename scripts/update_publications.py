#!/usr/bin/env python3
"""Check publication metadata and prepare reviewable website changes.

Only known DOI records are automatically updated in HTML. Unrecognized records
are listed for human review; an author-name match is not proof of authorship.
"""
from __future__ import annotations

import html
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import json

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "recent_research.html"
CANDIDATES = ROOT / "publication_candidates.md"
API = "https://api.crossref.org/works"
USER_AGENT = "YoukaiWu-Publications-Checker/1.0 (https://github.com/youkaiwujasper/youkaiwujasper.github.io)"

# These DOIs already appear on the personal website.
# Add a verified DOI here after manually approving a new publication.
KNOWN_YEARS = {
    "10.1016/j.tranpol.2026.104416": 2026,
    "10.1016/j.asoc.2024.112582": 2025,
    "10.3390/su11154252": 2019,
}
DOI_RE = re.compile(r"(?:https?://(?:dx\.)?doi\.org/)(10\.\d{4,9}/[^\"'<>\s]+)", re.I)
LI_RE = re.compile(r"(?P<indent>^[ \t]*)<li>\s*(?P<body>.*?)\s*</li>", re.M | re.S | re.I)
BR_RE = re.compile(r"<br\s*/?>", re.I)
TAG_RE = re.compile(r"<[^>]+>")


def request_json(url: str) -> dict:
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(req, timeout=30) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError) as exc:
            if attempt == 2:
                raise RuntimeError(f"Crossref request failed: {url}: {exc}") from exc
            time.sleep(2 ** attempt)
    raise AssertionError("unreachable")


def plain(value: object) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    value = html.unescape(str(value or ""))
    value = TAG_RE.sub("", value)
    return " ".join(value.split())


def year_of(record: dict) -> int | None:
    for key in ("published-print", "published", "issued", "published-online"):
        dates = record.get(key, {}).get("date-parts", [])
        if dates and dates[0] and isinstance(dates[0][0], int):
            return dates[0][0]
    return None


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", plain(title).casefold())


def doi_in(text: str) -> str | None:
    m = DOI_RE.search(text)
    return m.group(1).rstrip(".,;)").lower() if m else None


def metadata(doi: str) -> dict:
    record = request_json(f"{API}/{quote(doi, safe='/')}")["message"]
    if str(record.get("DOI", "")).lower() != doi:
        raise RuntimeError(f"DOI mismatch: {doi}")
    return record


def make_entry(existing: str, record: dict, doi: str, indent: str) -> str:
    """Preserve existing author emphasis while refreshing verified journal data."""
    parts = [part.strip() for part in BR_RE.split(existing)]
    if len(parts) < 4 or record.get("type") != "journal-article":
        return f"{indent}<li>\n{existing.strip()}\n{indent}</li>"
    authors = parts[0]
    title = plain(record.get("title")).rstrip(" .,;")
    journal = plain(record.get("container-title")).rstrip(" .,;")
    volume = plain(record.get("volume"))
    issue = plain(record.get("issue"))
    locator = plain(record.get("article-number")) or plain(record.get("page"))
    year = KNOWN_YEARS.get(doi) or year_of(record)
    if not all((authors, title, journal, volume, locator, year)):
        # Do not remove manually curated details when Crossref is incomplete.
        return f"{indent}<li>\n{existing.strip()}\n{indent}</li>"
    venue = f"<i>{html.escape(journal)}</i>, {html.escape(volume)}"
    if issue:
        venue += f" ({html.escape(issue)})"
    venue += f", {html.escape(locator)}, {year}."
    pad = indent + "    "
    return (
        f"{indent}<li>\n"
        f"{pad}{authors}<br>\n"
        f"{pad}{html.escape(title)},<br>\n"
        f"{pad}{venue}<br>\n"
        f'{pad}DOI: <a href="https://doi.org/{doi}" target="_blank" '
        f'rel="noopener noreferrer">{doi}</a>\n'
        f"{indent}</li>"
    )


def update_existing(source: str, records: dict[str, dict]) -> str:
    def replace(m: re.Match) -> str:
        doi = doi_in(m.group("body"))
        if not doi or doi not in records:
            return m.group(0)
        return make_entry(m.group("body"), records[doi], doi, m.group("indent"))

    updated = LI_RE.sub(replace, source)

    # Repair the existing Transport Policy entry if it is still filed under
    # "Publishing Works" instead of the peer-reviewed journal section.
    publishing = re.search(
        r'(?P<all>^[ \t]*<h2[^>]*>\s*Publishing Works\s*</h2>\s*'
        r'<ul\s+class="pubs">(?P<body>.*?)</ul>\s*)',
        updated, re.M | re.S | re.I,
    )
    if publishing:
        moving = []
        kept = publishing.group("body")
        for li in reversed(list(LI_RE.finditer(kept))):
            doi = doi_in(li.group("body"))
            if doi in records and records[doi].get("type") == "journal-article":
                moving.insert(0, li.group(0).strip())
                kept = kept[:li.start()] + kept[li.end():]
        if moving:
            if "<li" not in kept.lower():
                heading_indent = re.match(r"^[ \\t]*", publishing.group("all")).group(0)
                updated = updated[:publishing.start()] + heading_indent + updated[publishing.end():]
            else:
                replacement = publishing.group("all").replace(publishing.group("body"), kept, 1)
                updated = updated[:publishing.start()] + replacement + updated[publishing.end():]
            journal = re.search(
                r'(<h2\s+id="journal"[^>]*>.*?</h2>\s*<ul\s+class="pubs">)',
                updated, re.I | re.S,
            )
            if not journal:
                raise RuntimeError("Cannot locate Journal Papers section")
            new_items = "\\n" + "\\n".join(
                "\\n".join(
                    ("            " if i in (0, len(lines) - 1) else "                ") + line.strip()
                    for i, line in enumerate(lines)
                )
                for lines in (block.splitlines() for block in moving)
            )
            updated = updated[:journal.end()] + new_items + updated[journal.end():]

    return updated


def exact_author(record: dict) -> bool:
    for author in record.get("author", []):
        given = plain(author.get("given")).casefold()
        family = plain(author.get("family")).casefold()
        if (given == "youkai" and family == "wu") or (given == "wu" and family == "youkai"):
            return True
    return False


def get_candidates(page: str, records: dict[str, dict]) -> list[dict]:
    params = urlencode({
        "query.author": "Youkai Wu",
        "filter": "from-pub-date:2024-01-01",
        "rows": "100",
        "select": "DOI,title,author,container-title,published,issued,type",
    })
    items = request_json(f"{API}?{params}")["message"]["items"]
    existing_dois = set(KNOWN_YEARS)
    existing_dois.update(d.lower() for d in DOI_RE.findall(page))
    existing_titles = {norm_title(TAG_RE.sub("", li.group("body").split("<br>")[1]))
                       for li in LI_RE.finditer(page) if "<br>" in li.group("body")}
    found = {}
    for record in items:
        doi = plain(record.get("DOI")).lower()
        title = plain(record.get("title"))
        if (not doi or not title or not exact_author(record)
                or doi in existing_dois or norm_title(title) in existing_titles):
            continue
        if record.get("type") not in ("journal-article", "proceedings-article", "posted-content"):
            continue
        found[doi] = record
    return [found[key] for key in sorted(found)]


def render_candidates(items: list[dict]) -> str:
    lines = [
        "# Possible publications to review",
        "",
        "Crossref records with an exact **Youkai Wu** author-name match that are",
        "not yet listed on the website. **These are NOT automatically verified**;",
        "check title, coauthors and DOI before adding anything to the website.",
        "Do not merge an entry into Journal Papers solely based on this report.",
        "",
    ]
    if not items:
        lines.append("No new candidates found in the Crossref search window.")
    for record in items:
        doi = plain(record.get("DOI")).lower()
        authors = ", ".join(
            (plain(a.get("given")) + " " + plain(a.get("family"))).strip()
            for a in record.get("author", [])
        )
        lines.extend([
            f"## {plain(record.get('title'))}",
            "",
            f"- Authors: {authors}",
            f"- Venue: {plain(record.get('container-title')) or 'Not available'}",
            f"- Year: {year_of(record) or 'Not available'}",
            f"- Type: {plain(record.get('type'))}",
            f"- DOI: https://doi.org/{doi}",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    original = PAGE.read_text(encoding="utf-8")
    seen = {d.lower() for d in DOI_RE.findall(original)}
    targets = seen.intersection(KNOWN_YEARS)
    if not targets:
        raise RuntimeError("No known publication DOI found; check the website path/content.")
    records = {doi: metadata(doi) for doi in sorted(targets)}
    updated = update_existing(original, records)
    candidates = get_candidates(updated, records)
    if updated != original:
        PAGE.write_text(updated, encoding="utf-8")
        print("Updated known publication metadata and/or journal placement.")
    report = render_candidates(candidates)
    if not CANDIDATES.exists() or CANDIDATES.read_text(encoding="utf-8") != report:
        CANDIDATES.write_text(report, encoding="utf-8")
        print(f"Saved {len(candidates)} publication candidates for review.")
    else:
        print("No change in publication candidates.")


if __name__ == "__main__":
    main()
