"""NVIDIA's official press releases: dated issuer disclosures and the reported figures of a results release.

One exact feed URL (`NVIDIA_IR`), read with one GET by `collect.py`. Each recent entry becomes a compact context item
(title, bounded feed summary, publication instant or date, exact release URL), attributed to NVIDIA: a statement that
the company is investigating reports stays the company's statement, never a finding. A results release also yields
the few figures its own summary tables state (revenue and diluted EPS, GAAP and non-GAAP), as release observations
with their fiscal quarter, basis, publication time (or date alone, when the feed gives no clock) and source;
anything not read unambiguously yields no figures and leaves the release visible as a disclosure. No consensus,
no surprise, no price reaction, no article body.
"""

import hashlib
import re
from datetime import date, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

from .evidence import CONTEXT_WINDOW, ET

NVIDIA_IR = "https://nvidianews.nvidia.com/cats/press_release.xml"
SOURCE_ID = "nvidia-ir"
SOURCE_NAME = "NVIDIA official press releases"
ATTRIBUTION = "NVIDIA statement"
ARTICLE = re.compile(r"https://nvidianews\.nvidia\.com/news/[a-z0-9](?:[a-z0-9-]{0,198}[a-z0-9])?")
ITEM_LIMIT = 4
TITLE_LIMIT = 200
SUMMARY_LIMIT = 300
# RFC 822 without a clock (`Wed, 26 Aug 2026`) or an ISO date: a day is all the source gives.
DATE_ONLY = re.compile(r"(?:(Mon|Tue|Wed|Thu|Fri|Sat|Sun), )?(\d{1,2}) "
                       r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{4})")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
QUARTERS = {"First": 1, "Second": 2, "Third": 3, "Fourth": 4}
RESULTS_TITLE = re.compile(r"NVIDIA Announces Financial Results for (First|Second|Third|Fourth) Quarter"
                           r"(?: and)? Fiscal (\d{4})")
TABLE_CAPTION = "($ in millions, except earnings per share)"
# label in the summary table → (metric id part, metric, unit, value pattern). Only these rows are read.
FIGURES = {"Revenue": ("revenue", "revenue", "USD millions", re.compile(r"\$(\d{1,3}(?:,\d{3})+|\d{1,3})")),
           "Diluted earnings per share": ("diluted-eps", "diluted EPS", "USD per share",
                                          re.compile(r"(\()?\$(\d{1,3}\.\d{2})(?(1)\))"))}
REQUIRED = {("GAAP", "Revenue"), ("GAAP", "Diluted earnings per share"), ("non-GAAP", "Diluted earnings per share")}


class IssuerError(ValueError):
    pass


def text_of(markup):
    """Visible text of a small HTML fragment, whitespace collapsed."""
    parser = _Text()
    parser.feed(markup)
    parser.close()
    return " ".join("".join(parser.parts).split())


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        self.skip += tag in ("script", "style")

    def handle_endtag(self, tag):
        self.skip -= tag in ("script", "style") and self.skip > 0

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def bounded(text, limit):
    """Whole text when it fits; otherwise cut at a word boundary and marked with an ellipsis, never mid-word."""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:")
    return f"{cut}…"


def publication(value):
    """`("at", aware datetime)` for an RFC 822 instant with a zone, `("on", date)` when only a day is given."""
    value = " ".join((value or "").split())
    if not value:
        raise IssuerError("entry lacks a publication date")
    match = DATE_ONLY.fullmatch(value)
    if match or re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        weekday = match[1] if match else None
        try:
            result = (date(int(match[4]), MONTHS.index(match[3]) + 1, int(match[2])) if match
                      else date.fromisoformat(value))
        except ValueError:
            raise IssuerError("entry date does not exist") from None
        if weekday and WEEKDAYS[result.weekday()] != weekday:
            raise IssuerError("entry date and weekday disagree")
        return "on", result
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        raise IssuerError("entry publication time is malformed") from None
    if when.tzinfo is None:
        raise IssuerError("entry publication time lacks a time zone")
    return "at", when.astimezone(timezone.utc)


def item_id(url):
    """Stable across runs (a carried watch keeps naming the same release), unlike a position in the feed."""
    return f"{SOURCE_ID}-{hashlib.sha256(url.encode()).hexdigest()[:10]}"


def releases(root, now):
    """Admitted entries newest first, plus notes on what was not admitted. Raises IssuerError for a feed that is not
    this RSS; an individual malformed, future or invalid entry is left out and named, a stale one is simply older."""
    if root.tag != "rss" or root.find("channel") is None:
        raise IssuerError("malformed RSS")
    entries = root.findall("./channel/item")
    if not entries:
        raise IssuerError("RSS lists no entries")
    admitted, notes, seen = [], [], set()
    today = now.astimezone(ET).date()
    earliest = (now - CONTEXT_WINDOW)
    for entry in entries:
        title = " ".join((entry.findtext("title") or "").split())
        url = (entry.findtext("link") or "").strip()
        try:
            if not title:
                raise IssuerError("entry lacks a title")
            if not ARTICLE.fullmatch(url):
                raise IssuerError("entry link is not an NVIDIA newsroom release URL")
            precision, when = publication(entry.findtext("pubDate"))
        except IssuerError as exc:
            notes.append(f"{title or 'untitled entry'}: {exc}")
            continue
        if precision == "at" and when > now or precision == "on" and when > today:
            notes.append(f"{title}: publication after this run")
            continue
        if precision == "at" and when < earliest or precision == "on" and when < earliest.astimezone(ET).date():
            continue
        if url in seen:
            notes.append(f"{title}: duplicate release URL")
            continue
        seen.add(url)
        description = entry.findtext("description")
        summary = bounded(text_of(description), SUMMARY_LIMIT) if description else ""
        item = dict(id=item_id(url), title=bounded(title, TITLE_LIMIT), source_id=SOURCE_ID, url=url,
                    attribution=ATTRIBUTION, status="AVAILABLE")
        if summary:
            item["summary"] = summary
        item["published_at" if precision == "at" else "published_on"] = when.isoformat()
        admitted.append((when if precision == "at" else None, when, item, entry))
    # Newest first; a date-only entry sorts by its day.
    admitted.sort(key=lambda row: row[1].isoformat() if row[0] else row[1].isoformat() + "T23:59:59", reverse=True)
    if len(admitted) > ITEM_LIMIT:
        notes.append(f"{len(admitted) - ITEM_LIMIT} older release(s) inside the window not admitted "
                     f"(limit {ITEM_LIMIT})")
    return [(item, entry) for _, _, item, entry in admitted[:ITEM_LIMIT]], notes


class _Tables(HTMLParser):
    """Every table in a release body as rows of cell texts."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.row, self.cell, self.depth = [], None, None, 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            if self.depth:
                raise IssuerError("release table nests a table")
            self.depth += 1
            self.tables.append([])
        elif self.depth and tag == "tr":
            self.row = []
        elif self.depth and tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag == "table" and self.depth:
            self.depth -= 1
        elif tag in ("td", "th") and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).replace("\xa0", " ").split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if any(self.row):
                self.tables[-1].append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def release_body(entry):
    """The entry's HTML body. The feed wraps it in a literal, escaped `<![CDATA[ … ]]>`; a real CDATA arrives bare."""
    body = (entry.findtext("content") or "").strip()
    if body.startswith("<![CDATA[") and body.endswith("]]>"):
        body = body[len("<![CDATA["):-len("]]>")]
    if not body:
        raise IssuerError("results release has no body")
    return body


def summary_table(tables, basis, column):
    """The one GAAP or Non-GAAP summary table: its basis heading, the caption and this quarter as the first column."""
    heading = "GAAP" if basis == "GAAP" else "Non-GAAP"
    found = [table for table in tables if table and table[0] == [heading]]
    if len(found) != 1:
        raise IssuerError(f"release does not hold exactly one {heading} summary table")
    table = found[0]
    if len(table) < 3 or table[1][:2] != [TABLE_CAPTION, column]:
        raise IssuerError(f"{heading} summary table does not state {column} in {TABLE_CAPTION}")
    return table[2:]


def results(item, entry, retrieved):
    """The reported revenue and diluted EPS of one results release, or `([], reason)` when it is not one that reads
    unambiguously. The fiscal quarter comes from the title and must head both summary tables."""
    match = RESULTS_TITLE.fullmatch(item["title"])
    if not match:
        return [], ""
    quarter, year = QUARTERS[match[1]], int(match[2])
    column = f"Q{quarter} FY{year % 100:02d}"
    try:
        tables = _Tables()
        tables.feed(release_body(entry))
        tables.close()
        figures = {}
        for basis in ("GAAP", "non-GAAP"):
            for row in summary_table(tables.tables, basis, column):
                if row[0] not in FIGURES:
                    continue
                if (basis, row[0]) in figures or len(row) < 2:
                    raise IssuerError(f"{basis} {row[0]} is not stated exactly once")
                part, metric, unit, pattern = FIGURES[row[0]]
                cell = pattern.fullmatch(row[1])
                if not cell:
                    raise IssuerError(f"{basis} {row[0]} for {column} does not read as a reported value")
                if unit == "USD per share":
                    value = float(cell[2]) * (-1 if cell[1] else 1)
                else:
                    value = int(cell[1].replace(",", ""))
                figures[(basis, row[0])] = (part, metric, unit, value)
        missing = REQUIRED - set(figures)
        if missing:
            raise IssuerError(f"{' and '.join(sorted(' '.join(key) for key in missing))} not found for {column}")
    except (IssuerError, AssertionError) as exc:
        return [], f"{item['title']}: figures not read ({exc}); shown as a disclosure only"
    period = f"FY{year}-Q{quarter}"
    rows = []
    for (basis, _), (part, metric, unit, value) in sorted(figures.items(), key=lambda kv: (kv[0][0] != "GAAP",
                                                                                           kv[1][0])):
        slug = basis.lower()
        rows.append(dict(id=f"nvda-fy{year}-q{quarter}-{slug}-{part}", topic="NVIDIA", metric=f"{basis} {metric}",
                         value=value, unit=unit,
                         baseline=f"as reported in NVIDIA's {column} results release; no consensus comparison",
                         # The release's own publication: its instant, or only its date when that is all the feed
                         # gives (never a clock inferred from NVIDIA's usual results time).
                         frequency="release", observed_at=item.get("published_at") or item["published_on"],
                         retrieved_at=retrieved.isoformat(),
                         source_id=SOURCE_ID, status="AVAILABLE", reason="", fiscal_period=period, basis=basis,
                         release_id=item["id"]))
    return rows, ""


def nvidia_releases(root, now, retrieved):
    """Admitted context items, result rows and notes (what was left out and why) from one parsed feed."""
    admitted, notes = releases(root, now)
    items, rows, periods = [], [], set()
    for item, entry in admitted:
        items.append(item)
        figures, note = results(item, entry, retrieved)
        if note:
            notes.append(note)
        if figures and figures[0]["fiscal_period"] in periods:
            notes.append(f"{item['title']}: a second results release for {figures[0]['fiscal_period']}; "
                         "figures not admitted")
            continue
        if figures:
            periods.add(figures[0]["fiscal_period"])
            rows += figures
    return items, rows, notes
