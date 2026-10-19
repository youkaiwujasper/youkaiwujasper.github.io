#!/usr/bin/env python3
"""Automatically update publications from Crossref with strict author verification.

A name match alone cannot verify authorship. New records need exact author name
and a recognized collaborator or explicit matching academic affiliation.
"""
from __future__ import annotations

import html
import re
import time
from pathlib import Path
from difflib import SequenceMatcher
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import json

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "recent_research.html"
API = "https://api.crossref.org/works"
USER_AGENT = "YoukaiWu-Publications-Checker/1.0 (https://github.com/youkaiwujasper/youkaiwujasper.github.io)"

# Known authoritative publication years (online publication may precede volume year).
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
        return None
    authors = parts[0]
    title = plain(record.get("title")).rstrip(" .,;")
    journal = plain(record.get("container-title")).rstrip(" .,;")
    volume = plain(record.get("volume"))
    issue = plain(record.get("issue"))
    locator = plain(record.get("article-number")) or plain(record.get("page"))
    year = KNOWN_YEARS.get(doi) or year_of(record)
    if not all((authors, title, journal, volume, locator, year)):
        # Do not remove manually curated details when Crossref is incomplete.
        return None
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
        proposed = make_entry(m.group("body"), records[doi], doi, m.group("indent"))
        return proposed if proposed is not None else m.group(0)

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
                heading_indent = re.match(r"^[ \t]*", publishing.group("all")).group(0)
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
            new_items = "\n" + "\n".join(
                "\n".join(
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



# Manual aliases for titles that were substantially revised between a preprint
# and the formally published version. These must not be inserted twice.
TITLE_ALIASES = {
    norm_title("Assessing On-Demand Mobility Services and Policy Impacts: A Case Study from Chengdu, China"),
    norm_title("Assessing Autonomous Mobility-on-Demand Services and Policy Impacts: A Case Study of Chengdu, China"),
}


# Known exact collaborators in the existing publication list. Combine with
# exact Youkai Wu author match to avoid unrelated authors with the same name.
TRUSTED_COAUTHORS = {
    ("zhaoxia", "guo"),
    ("haifan", "tang"),
    ("guangxin", "ou"),
    ("weiwei", "le"),
    ("fansheng", "zhou"),
    ("stein", "wallace"),
    ("feng", "guo"),
}
TRUSTED_AFFILIATIONS = (
    "nhh", "norwegian school of economics", "sichuan university",
)
ACCEPTED_TYPES = {"journal-article", "proceedings-article"}
NEW_PUBLICATION_LOOKBACK = "2018-01-01"


def author_pair(author: dict) -> tuple[str, str]:
    given = plain(author.get("given")).casefold()
    family = plain(author.get("family")).casefold()
    first = re.sub(r"[^a-z]", " ", given).split()
    surname = re.sub(r"[^a-z]", "", family)
    return (first[0] if first else "", surname)


def identity_verified(record: dict) -> bool:
    """Reject same-name scholars without a matching collaboration or affiliation."""
    if not exact_author(record):
        return False
    authors = record.get("author", [])
    if any(author_pair(a) in TRUSTED_COAUTHORS for a in authors):
        return True
    for author in authors:
        if author_pair(author) == ("youkai", "wu"):
            for affiliation in author.get("affiliation", []):
                if isinstance(affiliation, dict):
                    name = plain(affiliation.get("name")).casefold()
                else:
                    name = plain(affiliation).casefold()
                if any(aff in name for aff in TRUSTED_AFFILIATIONS):
                    return True
    return False


def crossref_publications() -> list[dict]:
    """Query Crossref instead of the Google Scholar page (which blocks runners)."""
    params = urlencode({
        "query.author": "Youkai Wu",
        "filter": f"from-pub-date:{NEW_PUBLICATION_LOOKBACK}",
        "rows": "250",
        "select": (
            "DOI,title,author,type,container-title,volume,issue,"
            "article-number,page,published,published-online,published-print,issued"
        ),
    })
    data = request_json(f"{API}?{params}")["message"]
    items = data.get("items", [])
    if len(items) >= 250:
        raise RuntimeError("Crossref results reached safety limit; use pagination before publishing.")
    trusted = []
    for record in items:
        doi = plain(record.get("DOI")).lower()
        if (not doi.startswith("10.") or record.get("type") not in ACCEPTED_TYPES
                or not plain(record.get("title")) or not plain(record.get("container-title"))):
            continue
        if identity_verified(record):
            trusted.append(record)
    print(
        f"Crossref returned {len(items)} author-query records; "
        f"{len(trusted)} passed the identity checks.",
        flush=True,
    )
    return trusted


def title_already_listed(title: str, known_titles: set[str]) -> bool:
    norm = norm_title(title)
    if norm in known_titles:
        return True
    # Small punctuation and deposited-title differences must not create copies.
    return any(SequenceMatcher(None, norm, old).ratio() >= 0.94
               for old in known_titles if old)


def existing_titles(source: str) -> set[str]:
    titles = set(TITLE_ALIASES)
    for match in LI_RE.finditer(source):
        chunks = BR_RE.split(match.group("body"))
        if len(chunks) > 1:
            titles.add(norm_title(TAG_RE.sub("", chunks[1])))
    return titles


def authors_html(record: dict | None, paper: dict) -> str:
    if record and record.get("author"):
        names = []
        for author in record["author"]:
            given = plain(author.get("given"))
            family = plain(author.get("family"))
            name = (given + " " + family).strip()
            if given.casefold() == "youkai" and family.casefold() == "wu":
                names.append("<b>Youkai Wu</b>")
            else:
                names.append(html.escape(name))
        return ", ".join(names)
    # Scholar sometimes abbreviates the authors in its table.
    result = html.escape(paper["authors"])
    result = re.sub(r"\bYoukai Wu\b", "<b>Youkai Wu</b>", result, flags=re.I)
    return result or "<b>Youkai Wu</b>"


def category_of(record: dict | None, venue: str) -> str:
    if record:
        return {"journal-article": "journal", "proceedings-article": "conference",
                "posted-content": "working"}.get(record.get("type"), "working")
    venue = venue.casefold()
    if any(token in venue for token in ("arxiv", "preprint", "working paper", "ssrn", "research square")):
        return "working"
    if any(token in venue for token in ("proceedings", "conference", "symposium", "congress", "workshop")):
        return "conference"
    # When Scholar does not identify a journal unambiguously, use Working
    # Papers rather than asserting that the work was peer reviewed.
    return "working"


def new_entry(paper: dict, record: dict | None) -> tuple[str, str]:
    doi = plain(record.get("DOI")).lower() if record else ""
    title = plain(record.get("title")) if record else paper["title"]
    journal = plain(record.get("container-title")) if record else paper["venue"]
    year = (KNOWN_YEARS.get(doi) or year_of(record)) if record else paper["year"]
    year_part = str(year) if year else "n.d."
    category = category_of(record, paper["venue"])
    venue = f"<i>{html.escape(journal)}</i>" if journal else "Google Scholar"
    if record and category == "journal":
        volume = plain(record.get("volume"))
        issue = plain(record.get("issue"))
        locator = plain(record.get("article-number")) or plain(record.get("page"))
        if volume:
            venue += f", {html.escape(volume)}"
        if issue:
            venue += f" ({html.escape(issue)})"
        if locator:
            venue += f", {html.escape(locator)}"
    venue += f", {year_part}."
    url = f"https://doi.org/{doi}" if doi else paper["url"]
    link = (f'DOI: <a href="{html.escape(url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">{html.escape(doi)}</a>' if doi else
            f'<a href="{html.escape(url, quote=True)}" target="_blank" '
            f'rel="noopener noreferrer">Google Scholar</a>')
    return category, (
        "            <li>\n"
        f"                {authors_html(record, paper)},<br>\n"
        f"                {html.escape(title.rstrip(' ,.;'))},<br>\n"
        f"                {venue}<br>\n"
        f"                {link}\n"
        "            </li>"
    )


def insert_publication(source: str, category: str, entry: str) -> str:
    pattern = re.compile(
        rf'(<h2\s+id="{category}"[^>]*>.*?</h2>\s*<ul\s+class="pubs">)',
        re.I | re.S,
    )
    match = pattern.search(source)
    if match is None:
        raise RuntimeError(f"Could not locate publication section: {category}")
    # Remove the empty placeholder used in Working Papers, if present.
    if category == "working":
        tail = source[match.end():]
        tail = re.sub(r"^\s*<li>\s*</li>", "", tail, count=1)
        source = source[:match.end()] + tail
        match = pattern.search(source)
    return source[:match.end()] + "\n" + entry + source[match.end():]


def main() -> None:
    source = PAGE.read_text(encoding="utf-8")

    # Failure to contact Crossref aborts before writing any output.
    crossref_records = crossref_publications()
    known = {doi.lower() for doi in DOI_RE.findall(source)}
    # Update metadata for every existing DOI even when it was not returned
    # by the publication-date or author search.
    records = {doi: metadata(doi) for doi in sorted(known)}
    updated = update_existing(source, records)
    known_titles = existing_titles(updated)

    added = []
    for record in sorted(
        crossref_records,
        key=lambda item: (year_of(item) or 0, plain(item.get("title")).casefold()),
    ):
        doi = plain(record.get("DOI")).lower()
        title = plain(record.get("title"))
        if doi in known or title_already_listed(title, known_titles):
            continue
        paper = {
            "title": title,
            "venue": plain(record.get("container-title")),
            "year": year_of(record),
            "authors": "",
            "url": f"https://doi.org/{doi}",
        }
        category, block = new_entry(paper, record)
        if category not in ("journal", "conference"):
            continue
        updated = insert_publication(updated, category, block)
        known_titles.add(norm_title(title))
        known.add(doi)
        added.append((category, title, doi))

    if updated != source:
        PAGE.write_text(updated, encoding="utf-8")
        print(f"Website updated, newly verified Crossref publications: {len(added)}")
        for category, title, doi in added:
            print(f"  [{category}] {title} — https://doi.org/{doi}")
    else:
        print("Website publications already up to date.")


if __name__ == "__main__":
    main()
