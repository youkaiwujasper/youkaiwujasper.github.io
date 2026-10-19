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
from urllib.parse import quote, urlencode, urljoin
from bs4 import BeautifulSoup
from urllib.request import Request, urlopen
import json

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "recent_research.html"
CANDIDATES = ROOT / "publication_candidates.md"
API = "https://api.crossref.org/works"
SCHOLAR_ID = "KqmBkNoAAAAJ"
SCHOLAR_URL = f"https://scholar.google.com/citations?user={SCHOLAR_ID}&hl=en&pagesize=100"
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


# Give intermittent network errors or rate limits time to clear. Repeated 403
# responses normally mean Scholar is blocking the runner; fail safely rather
# than bypassing the restriction or guessing which papers to publish.
SCHOLAR_RETRY_DELAYS = (20, 60)  # three total attempts, up to 80s wait


def fetch_scholar_page(url: str) -> BeautifulSoup:
    attempts = len(SCHOLAR_RETRY_DELAYS) + 1
    for attempt in range(1, attempts + 1):
        req = Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; academic-personal-site-check/1.0)",
            "Accept-Language": "en-US,en;q=0.9",
        })
        try:
            with urlopen(req, timeout=35) as response:
                return BeautifulSoup(response.read(), "html.parser")
        except HTTPError as exc:
            if exc.code not in (403, 429, 500, 502, 503, 504):
                raise RuntimeError(
                    f"Google Scholar returned HTTP {exc.code}; no website changes made."
                ) from exc
            reason = f"HTTP {exc.code}"
        except (URLError, TimeoutError) as exc:
            reason = str(exc)

        if attempt == attempts:
            raise RuntimeError(
                f"Google Scholar refused or failed after {attempts} attempts "
                f"(last error: {reason}). No website changes made."
            )
        delay = SCHOLAR_RETRY_DELAYS[attempt - 1]
        print(
            f"Google Scholar request {attempt}/{attempts} failed ({reason}); "
            f"retrying in {delay} seconds...",
            flush=True,
        )
        time.sleep(delay)
    raise AssertionError("unreachable")


def scholar_publications() -> list[dict]:
    """Fetch actual entries from the named Scholar profile, never author-name search.

    Scholar offers no public API and may refuse bots. A failed or empty fetch must
    abort the run so that missing data never authorizes an incorrect change.
    """
    all_papers = []
    for offset in range(0, 1000, 100):
        url = f"{SCHOLAR_URL}&cstart={offset}"
        soup = fetch_scholar_page(url)

        profile_name = soup.select_one("#gsc_prf_in")
        if not profile_name or "youkai wu" not in " ".join(
            profile_name.get_text(" ", strip=True).casefold().split()
        ):
            raise RuntimeError("Google Scholar author identity could not be verified; no update performed.")
        rows = soup.select("tr.gsc_a_tr")
        if offset == 0 and not rows:
            raise RuntimeError("Google Scholar returned no publication rows; no update performed.")
        for row in rows:
            anchor = row.select_one("a.gsc_a_at")
            if anchor is None:
                continue
            title = " ".join(anchor.get_text(" ", strip=True).split())
            if len(title) < 8:
                continue
            grays = row.select(".gs_gray")
            authors = grays[0].get_text(" ", strip=True) if grays else ""
            venue = grays[1].get_text(" ", strip=True) if len(grays) >= 2 else ""
            year_node = row.select_one(".gsc_a_y")
            year_text = year_node.get_text(" ", strip=True) if year_node else ""
            found = re.search(r"\b(?:19|20)\d{2}\b", year_text)
            year = int(found.group(0)) if found else None
            paper_url = urljoin("https://scholar.google.com", anchor.get("href") or "")
            all_papers.append({
                "title": title,
                "authors": authors,
                "venue": venue,
                "year": year,
                "url": paper_url,
            })
        if len(rows) < 100:
            break
    else:
        raise RuntimeError("Google Scholar pagination limit reached; no changes written.")
    if not all_papers:
        raise RuntimeError("Google Scholar profile empty or blocked; no changes written.")
    unique = {}
    for entry in all_papers:
        unique.setdefault(norm_title(entry["title"]), entry)
    return list(unique.values())


def crossref_match(title: str) -> dict | None:
    """Use Crossref only to enrich a publication already on the Scholar profile."""
    params = urlencode({"query.title": title, "rows": "8"})
    try:
        records = request_json(f"{API}?{params}")["message"]["items"]
    except (RuntimeError, KeyError):
        return None
    options = []
    for record in records:
        if record.get("type") not in {"journal-article", "proceedings-article", "posted-content"}:
            continue
        if norm_title(plain(record.get("title"))) != norm_title(title):
            continue
        if not exact_author(record):
            continue
        options.append(record)
    priority = {"journal-article": 0, "proceedings-article": 1, "posted-content": 2}
    return min(options, key=lambda r: priority.get(r["type"], 9)) if options else None


def exact_author(record: dict) -> bool:
    for author in record.get("author", []):
        given = plain(author.get("given")).casefold()
        family = plain(author.get("family")).casefold()
        if (given == "youkai" and family == "wu") or (given == "wu" and family == "youkai"):
            return True
    return False


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

    # Verify the exact user-provided Scholar profile FIRST. If blocked or
    # unexpectedly empty, abort instead of overwriting, adding, or merging.
    papers = scholar_publications()
    print(f"Verified Google Scholar profile {SCHOLAR_ID}: {len(papers)} publications.")

    known = {doi.lower() for doi in DOI_RE.findall(source)}
    records = {doi: metadata(doi) for doi in sorted(known)}
    updated = update_existing(source, records)
    known_titles = existing_titles(updated)

    added = []
    for paper in sorted(papers, key=lambda p: (p["year"] or 0, p["title"].casefold())):
        nt = norm_title(paper["title"])
        if nt in known_titles:
            continue
        record = crossref_match(paper["title"])
        doi = plain(record.get("DOI")).lower() if record else ""
        if doi and doi in known:
            continue
        category, block = new_entry(paper, record)
        updated = insert_publication(updated, category, block)
        known_titles.add(nt)
        if doi:
            known.add(doi)
        added.append((category, paper["title"]))

    if updated != source:
        PAGE.write_text(updated, encoding="utf-8")
        print(f"Updated website. New Scholar-listed publications: {len(added)}")
        for category, title in added:
            print(f"  [{category}] {title}")
    else:
        print("Website publications already up to date.")


if __name__ == "__main__":
    main()
