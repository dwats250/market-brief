"""GET-only official sources. No account discovery, arbitrary crawling, or trading routes."""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from .actuals import (
    FAMILIES,
    MONTHS,
    RELEASE_PAGES,
    WEEKDAYS,
    ReleaseError,
    check_release,
    due_releases,
    read_release,
    release_rows,
)
from .evidence import ET as EASTERN
from .evidence import timestamp
from .schedule import next_session_date

TREASURY = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
BLS = "https://www.bls.gov/schedule/news_release/bls.ics"
# The calendar file's fallback, BLS's official monthly List View. On BLS only these two forms and the two fixed
# current-edition release pages (`actuals.RELEASE_PAGES`) are ever requested.
BLS_LIST = re.compile(r"https://www\.bls\.gov/schedule/[0-9]{4}/(0[1-9]|1[0-2])_sched_list\.htm")
FED = "https://www.federalreserve.gov/feeds/press_all.xml"
CB = "https://dwats250.github.io/cuttingboard/contract.json"
ALPACA_DATA = "https://data.alpaca.markets"
ALPACA_BARS = f"{ALPACA_DATA}/v2/stocks/bars"
ALPACA_SNAPSHOTS = f"{ALPACA_DATA}/v2/stocks/snapshots"
HOSTS = {urlsplit(u).hostname for u in (TREASURY, BLS, FED, CB, ALPACA_DATA)}
ALPACA_UNIVERSE = (
    "SPY", "QQQ", "XLK", "XLF", "XLE", "XLI", "XLY", "XLP", "XLV", "XLU",
    "XLB", "XLRE", "XLC", "GLD", "GDX", "AAPL", "MSFT", "NVDA", "META", "AMZN", "GOOG",
)
USER_AGENT = "MarketBrief/0.1 personal research"
# BLS blocks robots that carry no way to contact their owner (www.bls.gov/bls/pss.htm), and its firewall also refuses
# an agent containing a URL. BLS_CONTACT, an email address, identifies BLS requests only and is never recorded.
CONTACT = re.compile(r"[\w.!#$%&'*+/=?^`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
                     r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+", re.ASCII)


class SourceError(ValueError):
    pass


def allowed(url):
    """An allowlisted host over HTTPS; on BLS, only the calendar file, the monthly List View pages and the two
    current-edition release pages."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in HOSTS:
        return False
    return (parts.hostname != urlsplit(BLS).hostname or url == BLS or url in RELEASE_PAGES
            or bool(BLS_LIST.fullmatch(url)))


def user_agent(url):
    contact = os.environ.get("BLS_CONTACT", "").strip()
    if urlsplit(url).hostname != urlsplit(BLS).hostname or not contact:
        return USER_AGENT
    if not CONTACT.fullmatch(contact):
        raise SourceError("BLS_CONTACT is not a plain email address")  # never echo the value
    return f"MarketBrief/0.1 ({contact})"


class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if new.scheme != "https" or new.hostname != old.hostname:
            raise SourceError("cross-host redirect rejected")
        if not allowed(newurl):
            raise SourceError("redirect outside allowlist")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, deadline):
    if not allowed(url):
        raise SourceError("source outside allowlist")
    agent = user_agent(url)
    opener = build_opener(SameHostRedirect())
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceError("collection deadline exceeded")
        try:
            request = Request(url, headers={"User-Agent": agent})
            with opener.open(request, timeout=min(15, remaining)) as response:
                data = response.read(1_000_001)
                if len(data) > 1_000_000:
                    raise SourceError("response size limit exceeded")
                return data.decode("utf-8-sig")
        except HTTPError as exc:
            if attempt == 0 and exc.code in {500, 502, 503, 504}:
                continue
            raise SourceError(f"HTTP {exc.code}") from None
        except (URLError, TimeoutError, OSError):
            if attempt:
                raise SourceError("network unavailable or timeout") from None
    raise SourceError("unavailable")


def source(ident, name, kind, url, now, status="AVAILABLE", reason="", **metadata):
    return dict(id=ident, name=name, kind=kind, url=url, retrieved_at=now.isoformat(),
                status=status, reason=reason, llm_allowed=True, retention_allowed=True,
                coverage_date=None, **metadata)


def alpaca_source(ident, name, now, feed, expected_freshness):
    return source(ident, name, "price", ALPACA_DATA, now, expected_freshness=expected_freshness,
                  provider="Alpaca Trading API", feed=feed, plan="Basic", data_delay="IEX real-time"
                  if feed == "iex" else "SIP delayed")


def xml_root(text):
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise SourceError("XML declarations rejected")
    return ET.fromstring(text)


def treasury_url(year):
    return f"{TREASURY}?data=daily_treasury_yield_curve&field_tdr_date_value={year}"


def treasury_entries(text, now):
    """One feed's entries dated before today (ET), as `(date, fields)`."""
    entries = []
    for entry in xml_root(text).findall("{*}entry"):
        fields = {e.tag.split("}")[-1]: e.text for e in entry.iter()}
        day = (fields.get("NEW_DATE") or "")[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and day < now.astimezone(EASTERN).date().isoformat():
            entries.append((day, fields))
    return entries


def yield_rows(entries, retrieved):
    """Level and daily change rows for the latest entry; the change is against the entry immediately before it.

    Entries merge by date (a later list wins a duplicate date). A change is a whole number of basis points,
    rounded here at derivation, so the reader, the ledger and the curve classifier all read the same integer.
    """
    rows = sorted(dict(entries).items())
    if not rows:
        raise SourceError("no prior daily yield observation")
    day, fields = rows[-1]
    result = []
    for term in (2, 5, 10, 30):
        raw = fields.get(f"BC_{term}YEAR")
        value = float(raw) if raw else None
        result.append(dict(id=f"treasury-{term}y", topic=f"US {term}Y", metric="daily par yield",
            value=value, unit="% yield", baseline="daily Treasury par curve, not an intraday quote",
            frequency="daily", observed_at=day, retrieved_at=retrieved.isoformat(),
            source_id="treasury", status="BACKGROUND", reason=""))
        if len(rows) >= 2 and raw and rows[-2][1].get(f"BC_{term}YEAR"):
            prior = float(rows[-2][1][f"BC_{term}YEAR"])
            result.append(dict(id=f"treasury-{term}y-change", topic=f"US {term}Y",
                metric="daily yield change", value=round((value-prior)*100), unit="bp",
                baseline=f"daily observation {rows[-2][0]}", frequency="daily", observed_at=day,
                retrieved_at=retrieved.isoformat(), source_id="treasury", status="BACKGROUND",
                reason=""))
    return result


def treasury_rows(text, now, retrieved):
    return yield_rows(treasury_entries(text, now), retrieved)


def calendar_events(text, now, retrieved):
    if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
        raise SourceError("malformed calendar")
    text = re.sub(r"\r?\n[ \t]", "", text)
    releases, dates = [], []
    admitted_dates = {now.astimezone(EASTERN).date().isoformat(), next_session_date(now)}
    for block in text.split("BEGIN:VEVENT")[1:]:
        if "END:VEVENT" not in block or "RRULE:" in block:
            raise SourceError("incomplete or recurring calendar unsupported")
        fields = dict(line.split(":", 1) for line in block.split("END:VEVENT")[0].splitlines()
                      if ":" in line)
        start_key = next((k for k in fields if k.startswith("DTSTART")), None)
        if not start_key or "SUMMARY" not in fields:
            raise SourceError("calendar event lacks time/title")
        value = fields[start_key]
        zone = timezone.utc if value.endswith("Z") else ics_zone(
            start_key.split("TZID=")[-1] if "TZID=" in start_key else "America/New_York")
        when = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=zone)
        dates.append(when.astimezone(EASTERN).date())
        if dates[-1].isoformat() not in admitted_dates:
            continue
        title = fields["SUMMARY"].replace("\\,", ",")
        releases.append((when, title, title))
    today = now.astimezone(EASTERN).date()
    if not dates or not min(dates) <= today <= max(dates):
        raise SourceError("calendar does not establish coverage for target date")
    return release_events(releases, retrieved)


# BLS's calendar file names its own VTIMEZONE `US-Eastern` (New York's rules), which is not an IANA key; ZoneInfo's
# KeyError for it escaped collection, so any successful fetch of the file would have stopped the whole run.
ICS_ZONES = {"US-Eastern": "America/New_York"}


def ics_zone(key):
    try:
        return ZoneInfo(ICS_ZONES.get(key, key))
    except (LookupError, ValueError, OSError):
        raise SourceError("unsupported calendar time zone") from None


def release_events(releases, retrieved):
    """`(time, title, release name)` as scheduled BLS events, one per title and time, numbered by time and then
    release name: the calendar file and the List View order a shared time differently (Real Earnings before CPI, or
    after), and a frozen `bls-event-<n>` must name the same release whichever path the next run read."""
    events = []
    for when, title, _ in sorted(releases, key=lambda release: (release[0], RELEASE_MARK.sub("", release[2]),
                                                                release[1])):
        scheduled_at = when.astimezone(timezone.utc).isoformat()
        if not any(event["title"] == title and event["scheduled_at"] == scheduled_at for event in events):
            events.append(dict(id=f"bls-event-{len(events)}", title=title, source_id="bls", published_at=None,
                               checked_at=retrieved.isoformat(), scheduled_at=scheduled_at, status="SCHEDULED"))
    return events


LIST_DATE = re.compile(rf"({'|'.join(WEEKDAYS)}), ({'|'.join(MONTHS)}) (\d{{1,2}}), (\d{{4}})")
LIST_TIME = re.compile(r"(0?[1-9]|1[0-2]):([0-5]\d) (AM|PM)")
LIST_HEADER = [("th", "Date"), ("th", "Time"), ("th", "Release")]
EASTERN_NOTE = re.compile(r"\bAll times\b[^<.]{0,40}\bEastern Time\b", re.I)
# BLS withdraws a listed release in the browser with a site-wide script, `Object.entries({'<date>': '<release>'})`
# (it did so for three February 2026 releases). Every entry must be one this reader understands, or the page fails.
WITHDRAWALS = re.compile(r"Object\.entries\(\{(.*?)\}\)", re.S)
WITHDRAWAL = re.compile(r"\s*'((?:[^'\\]|\\.)*)'\s*:\s*'((?:[^'\\]|\\.)*)'\s*(?:,|$)")
MONTH_VIEW_CELL = re.compile(r"d\d{4}")  # the Month View's cell IDs; the List View carries its own dated entries
RELEASE_MARK = re.compile(r"\s*\((?:P|R)\)$")  # the List View marks preliminary and revised releases; the file does not
BLS_SECONDS = 30  # the calendar file and its fallback share one source's time: two fifteen-second attempts
RELEASE_SECONDS = 20  # one release page: a fifteen-second attempt and a short retry


def schedule_url(year, month):
    return f"https://www.bls.gov/schedule/{year}/{month:02d}_sched_list.htm"


def list_date(text):
    """`Friday, October 2, 2026` (BLS also zero-pads the day) as a date whose weekday agrees."""
    match = LIST_DATE.fullmatch(text)
    if not match:
        raise SourceError("schedule row lacks a date")
    weekday, month, day, year = match.groups()
    try:
        value = date(int(year), MONTHS.index(month) + 1, int(day))
    except ValueError:
        raise SourceError("schedule date does not exist") from None
    if WEEKDAYS[value.weekday()] != weekday:
        raise SourceError("schedule date and weekday disagree")
    return value


def withdrawals(text):
    """The dates on which BLS's own script removes List View rows in the browser. Which rows it removes depends on
    script semantics this reader does not run, so a date named here is never read; an entry in any other form fails."""
    blocks = WITHDRAWALS.findall(text)
    if len(blocks) != text.count("Object.entries"):
        raise SourceError("unrecognized schedule withdrawal")
    withdrawn = set()
    for entries in blocks:
        if WITHDRAWAL.sub("", entries).strip():
            raise SourceError("unrecognized schedule withdrawal")
        for key, _ in WITHDRAWAL.findall(entries):
            if MONTH_VIEW_CELL.fullmatch(key):
                continue
            try:
                withdrawn.add(list_date(key))
            except SourceError:
                raise SourceError("unrecognized schedule withdrawal") from None
    return withdrawn


class ReleaseTable(HTMLParser):
    """The `release-list` table of a BLS List View page: each row's cells as `(tag, text, release name)`, the name
    being the cell's `<strong>` text, and the page's `<h1>` headings, which name its month. The page's visible text
    is kept (scripts and comments are not visible); a schedule date in it outside the release table, or a table end
    tag with no table open, means the table the reader sees is not the one parsed, and fails the page."""

    def __init__(self):
        super().__init__()
        self.tables, self.lists, self.closed, self.headings, self.visible = [], 0, 0, [], []
        self.rows, self.row, self.cell, self.heading, self.script = [], None, None, None, None

    def handle_starttag(self, tag, attrs):
        listing = bool(self.tables) and self.tables[-1]
        if tag in ("script", "style"):
            self.script = tag
        elif tag == "table":
            if listing:
                raise SourceError("schedule table nests a table")
            self.tables.append("release-list" in (dict(attrs).get("class") or "").split())
            self.lists += self.tables[-1]
        elif tag == "h1" and not listing:
            self.heading = []
        elif listing and tag == "tr":
            self.end_row()
            self.row = []
        elif listing and tag in ("td", "th") and self.row is not None:
            self.end_cell()
            self.cell = dict(tag=tag, text=[], name=[], strong=0)
        elif listing and tag == "strong" and self.cell is not None:
            self.cell["strong"] += 1

    def handle_endtag(self, tag):
        listing = bool(self.tables) and self.tables[-1]
        if tag == self.script:
            self.script = None
        elif tag == "table":
            if not self.tables:
                raise SourceError("schedule page closes a table it never opened")
            self.end_row()
            self.closed += self.tables.pop()
        elif tag == "h1" and self.heading is not None:
            self.headings.append(" ".join("".join(self.heading).split()))
            self.heading = None
        elif listing and tag in ("td", "th"):
            self.end_cell()
        elif listing and tag == "tr":
            self.end_row()
        elif listing and tag == "strong" and self.cell is not None and self.cell["strong"]:
            self.cell["strong"] -= 1

    def handle_data(self, data):
        if self.script:
            return
        self.visible.append(data)
        if self.heading is not None:
            self.heading.append(data)
        if self.cell is not None:
            self.cell["text"].append(data)
            if self.cell["strong"]:
                self.cell["name"].append(data)
        elif self.tables and self.tables[-1] and data.strip():
            raise SourceError("schedule table holds text outside its cells")
        elif LIST_DATE.search(" ".join(data.split())):
            raise SourceError("schedule dates appear outside the release table")

    def end_cell(self):
        if self.cell is not None:
            text, name = (" ".join("".join(self.cell[key]).split()) for key in ("text", "name"))
            self.row.append((self.cell["tag"], text, name))
            self.cell = None

    def end_row(self):
        self.end_cell()
        if self.row:
            self.rows.append(self.row)
        self.row = None


def list_releases(text, year, month):
    """The timed releases on one official List View page as `(New York time, title, release name)`, once the page
    proves it is that month's schedule: a heading names the month, its one release table has the Date, Time and
    Release columns and closes, every row is dated inside the month on a weekday that agrees, and the page states that
    its times are Eastern. A row without a time (a holiday) is no release."""
    table = ReleaseTable()
    table.feed(text)
    table.close()
    if table.lists != 1 or table.closed != 1 or table.tables or not table.rows:
        raise SourceError("schedule page lacks one complete release table")
    if f"{MONTHS[month - 1]} {year}" not in table.headings:
        raise SourceError("schedule page is not the requested month")
    if not EASTERN_NOTE.search(" ".join("".join(table.visible).split())):
        raise SourceError("schedule page does not state Eastern Time")
    header, *rows = table.rows
    if [cell[:2] for cell in header] != LIST_HEADER:
        raise SourceError("schedule table lacks the Date, Time and Release columns")
    releases = []
    for row in rows:
        if [cell[0] for cell in row] != ["td", "td", "td"]:
            raise SourceError("schedule row is not a date, a time and a release")
        (_, day_text, _), (_, clock_text, _), (_, title, name) = row
        day = list_date(day_text)
        if (day.year, day.month) != (year, month) or not title:
            raise SourceError("schedule row lies outside its month or names no release")
        if not clock_text:
            continue
        clock = LIST_TIME.fullmatch(clock_text)
        if not clock:
            raise SourceError("schedule row has no readable time")
        hour = int(clock[1]) % 12 + (12 if clock[3] == "PM" else 0)
        releases.append((datetime(day.year, day.month, day.day, hour, int(clock[2]), tzinfo=EASTERN), title, name))
    if not releases:
        raise SourceError("schedule page lists no releases")
    return releases


def failure(exc):
    return str(exc) if isinstance(exc, (SourceError, ReleaseError)) else f"malformed {type(exc).__name__}"


def bls_calendar(now, deadline, fetcher):
    """BLS releases scheduled today and at the next session: from the calendar file, else from the official monthly
    List View pages covering both dates. Returns `(events, url, reason, retrieved)`, the URL being the one actually
    read and the reason, on the fallback, why the file was not. When both fail, one SourceError names both."""
    deadline = min(deadline, time.monotonic() + BLS_SECONDS)
    try:
        body = fetcher(BLS, deadline)
        retrieved = datetime.now(timezone.utc)
        return calendar_events(body, now, retrieved), BLS, "", retrieved
    except (SourceError, ValueError, LookupError, UnicodeError, OverflowError) as exc:
        file_failure = failure(exc)
    days = (now.astimezone(EASTERN).date(), date.fromisoformat(next_session_date(now)))
    months = list(dict.fromkeys((day.year, day.month) for day in days))
    try:
        listed = []
        for year, month in months:
            text = fetcher(schedule_url(year, month), deadline)
            withdrawn = sorted(withdrawals(text).intersection(days))
            if withdrawn:
                raise SourceError(f"the page withdraws a release on {withdrawn[0].isoformat()}")
            listed += list_releases(text, year, month)
    # HTMLParser can still assert on input it was never meant to see; that is a malformed page, not a crash.
    except (SourceError, ValueError, LookupError, UnicodeError, OverflowError, AssertionError) as exc:
        raise SourceError(f"{file_failure}; monthly schedule: {failure(exc)}") from None
    retrieved = datetime.now(timezone.utc)
    events = release_events([release for release in listed if release[0].date() in days], retrieved)
    label = ", ".join(f"{year}-{month:02d}" for year, month in months)
    return events, schedule_url(*months[0]), f"monthly schedule {label}; calendar file: {file_failure}", retrieved


def release_actuals(events, now, deadline, fetcher):
    """The official values of each BLS release on today's admitted calendar whose scheduled time has passed: one read
    of its family's fixed current-edition page, admitted only as that scheduled release. Returns `(rows, records)`,
    one source record per release read (or refused); a page that cannot be admitted leaves the event to stand alone."""
    rows, records = [], []
    for family, event, period in due_releases(events, now):
        spec = FAMILIES[family]
        record = source(spec["source"], spec["name"], "release", spec["url"], now)
        record["coverage_date"] = now.astimezone(EASTERN).date().isoformat()
        try:
            if event is None:
                raise ReleaseError("the calendar lists this release more than once today")
            page = fetcher(spec["url"], min(deadline, time.monotonic() + RELEASE_SECONDS))
            retrieved = datetime.now(timezone.utc)
            release = read_release(family, page)
            check_release(release, event, period)
        except (SourceError, ValueError, LookupError, UnicodeError, OverflowError) as exc:
            record.update(status="UNAVAILABLE", reason=failure(exc))
        else:
            rows += release_rows(release, retrieved)
            # Revisions the release states but that did not read cleanly are left out; the record says so.
            record.update(retrieved_at=retrieved.isoformat(), **(dict(status="DEGRADED", reason=release["note"])
                                                                   if release["note"] else {}))
        records.append(record)
    return rows, records


def fed_context(text, now):
    root = xml_root(text)
    if root.tag != "rss" or root.find("channel") is None:
        raise SourceError("malformed RSS")
    result = []
    for item in root.findall("./channel/item"):
        title, published = item.findtext("title"), item.findtext("pubDate")
        if not title or not published:
            raise SourceError("RSS item lacks title/publication time")
        when = parsedate_to_datetime(published)
        if when.tzinfo is None:
            raise SourceError("RSS publication lacks timezone")
        if now - timedelta(days=2) <= when <= now:
            result.append(dict(id=f"fed-item-{len(result)}", title=title[:400], source_id="fed",
                               published_at=when.isoformat(), status="AVAILABLE"))
    return result[:6]


def cuttingboard_record(raw, now, captured):
    result = dict(status="INVALID", reason="malformed or unsupported public contract",
                  source=CB, captured_at=captured.isoformat(), adapter_version="v0")
    try:
        if raw.get("schema_version") != "v2":
            return result
        generated = timestamp(raw["generated_at"])
        if generated > now:
            return result
        if (now-generated).total_seconds() > 5400:
            return dict(result, status="STALE", reason="source generation older than ninety minutes")
        if raw.get("session_date") != now.astimezone(EASTERN).date().isoformat():
            return dict(result, status="STALE", reason="source belongs to another session")
        state = raw.get("system_state")
        if not isinstance(state, dict):
            return result
        values = {"outcome": raw.get("outcome"), "permission": state.get("permission")}
        if any(v is not None and (not isinstance(v, str) or len(v) > 80) for v in values.values()):
            return result
        if state.get("outcome") is not None and state["outcome"] != values["outcome"]:
            return result
        generation = raw.get("generation_id")
        if generation is not None and not isinstance(generation, str):
            return result
        return dict(result, status="AVAILABLE", reason="", schema_version="v2",
                    generated_at=generated.isoformat(), generation_id=generation, **values)
    except (ValueError, KeyError, TypeError, AttributeError):
        return result


def _alpaca_request(path, params, deadline, key_id, secret_key):
    query = urlencode(params, doseq=True)
    request = Request(f"{ALPACA_DATA}{path}?{query}", headers={
        "APCA-API-KEY-ID": key_id,
        "APCA-API-SECRET-KEY": secret_key,
        "User-Agent": "MarketBrief/0.1 personal research",
    })
    opener = build_opener(SameHostRedirect())
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise SourceError("Alpaca collection deadline exceeded")
    try:
        with opener.open(request, timeout=min(20, remaining)) as response:
            payload = response.read(2_000_001)
            if len(payload) > 2_000_000:
                raise SourceError("Alpaca response size limit exceeded")
            return json.loads(payload.decode("utf-8"))
    except HTTPError as exc:
        raise SourceError(f"Alpaca HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError, UnicodeError, json.JSONDecodeError):
        raise SourceError("Alpaca network or response failure") from None


def _bar_date(value):
    return timestamp(value).date().isoformat()


def _alpaca_history(payload, now, retrieved_at, source_id):
    histories = []
    for symbol, bars in payload.get("bars", {}).items():
        rows = [(bar.get("t"), bar.get("c")) for bar in bars
                if isinstance(bar, dict) and isinstance(bar.get("t"), str)
                and finite_number(bar.get("c"))]
        rows = [(date, close) for date, close in rows
                if date[:10] < now.date().isoformat()]
        rows.sort(key=lambda row: row[0])
        rows = rows[-65:]
        if not rows:
            continue
        dates = [_bar_date(date) for date, _ in rows]
        closes = [close for _, close in rows]
        histories.append(dict(id=f"history-{symbol}", symbol=symbol, dates=dates, closes=closes,
                              adjustment="split", session="regular_close", source_id=source_id,
                              retrieved_at=retrieved_at.isoformat()))
    return histories


def finite_number(value):
    return isinstance(value, (int, float)) and value == value and value not in (float("inf"), float("-inf"))


def _alpaca_intraday(payload, histories, now, retrieved_at, source_id):
    observations = []
    by_symbol = {row["symbol"]: row for row in histories}
    for symbol, snapshot in payload.items():
        trade = snapshot.get("latestTrade") or {}
        close = (snapshot.get("prevDailyBar") or {}).get("c")
        observed_at = trade.get("t")
        price = trade.get("p")
        if symbol not in by_symbol or not finite_number(price) or not finite_number(close) or not observed_at:
            continue
        when = timestamp(observed_at)
        if when.date() != now.date() or when > now:
            continue
        value = 100 * (price / close - 1) if close else None
        if value is None or not finite_number(value):
            continue
        observations.append(dict(id=f"{symbol}-intraday", topic=symbol, metric="premarket return",
            value=value, unit="%", baseline="Alpaca IEX latest trade versus previous regular close",
            frequency="intraday", observed_at=observed_at, retrieved_at=retrieved_at.isoformat(),
            source_id=source_id, status="AVAILABLE", reason="feed=IEX; latest trade timestamp"))
    return observations


def alpaca_probe(now, symbols=("SPY", "QQQ"), key_id=None, secret_key=None, fetcher=_alpaca_request):
    key_id = key_id or os.environ.get("APCA_API_KEY_ID")
    secret_key = secret_key or os.environ.get("APCA_API_SECRET_KEY")
    if not key_id or not secret_key:
        raise SourceError("Alpaca credentials are not configured")
    deadline = time.monotonic() + 45
    retrieved = datetime.now(timezone.utc)
    symbols = tuple(dict.fromkeys(symbols))
    snapshots = fetcher("/v2/stocks/snapshots", {"symbols": ",".join(symbols), "feed": "iex"},
                        deadline, key_id, secret_key)
    bars = fetcher("/v2/stocks/bars", {
        "symbols": ",".join(symbols), "timeframe": "1Day", "start": (now - timedelta(days=120)).date().isoformat(),
        "end": now.date().isoformat(), "limit": 10000 if len(symbols) > 2 else 200,
        "adjustment": "split", "feed": "iex", "sort": "asc",
    }, deadline, key_id, secret_key)
    histories = _alpaca_history(bars, now, retrieved, "alpaca-daily")
    return dict(authenticated=True, provider="Alpaca Trading API", plan="Basic", feed="IEX",
                snapshot_symbols=sorted(snapshots), historical_symbols=sorted(h["symbol"] for h in histories),
                historical_sessions={h["symbol"]: len(h["dates"]) for h in histories},
                historical_ranges={h["symbol"]: [h["dates"][0], h["dates"][-1]] for h in histories},
                intraday_symbols=sorted(r["topic"] for r in _alpaca_intraday(
                    snapshots, histories, now, retrieved, "alpaca-iex")),
                retrieved_at=retrieved.isoformat())


def alpaca_collect(now, symbols=ALPACA_UNIVERSE, key_id=None, secret_key=None):
    key_id = key_id or os.environ.get("APCA_API_KEY_ID")
    secret_key = secret_key or os.environ.get("APCA_API_SECRET_KEY")
    if not key_id or not secret_key:
        return dict(sources=[source("alpaca-daily", "Alpaca daily bars", "price", ALPACA_DATA, now,
            "UNAVAILABLE", "credentials are not configured", expected_freshness="PRIOR_CLOSE"),
            source("alpaca-iex", "Alpaca IEX current data", "quote", ALPACA_DATA, now,
            "UNAVAILABLE", "credentials are not configured", expected_freshness="LIVE")],
            observations=[], history=[])
    deadline = time.monotonic() + 90
    retrieved = datetime.now(timezone.utc)
    symbols = tuple(dict.fromkeys(symbols))
    daily = source("alpaca-daily", "Alpaca historical daily bars · IEX feed", "price", ALPACA_DATA,
                   now, expected_freshness="PRIOR_CLOSE", provider="Alpaca Trading API", feed="IEX",
                   plan="Basic", data_delay="historical daily; feed entitlement returned by account")
    intraday = source("alpaca-iex", "Alpaca current equity data · IEX feed", "quote", ALPACA_DATA,
                      now, expected_freshness="LIVE", provider="Alpaca Trading API", feed="IEX",
                      plan="Basic", data_delay="IEX real-time when timestamped current")
    try:
        bars = _alpaca_request("/v2/stocks/bars", {
            "symbols": ",".join(symbols), "timeframe": "1Day",
            "start": (now - timedelta(days=120)).date().isoformat(), "end": now.date().isoformat(),
            "limit": 10000, "adjustment": "split", "feed": "iex", "sort": "asc",
        }, deadline, key_id, secret_key)
        histories = _alpaca_history(bars, now, retrieved, "alpaca-daily")
        daily["coverage_date"] = max((h["dates"][-1] for h in histories), default=None)
        daily["coverage_symbols"] = len(histories)
    except SourceError as exc:
        daily.update(status="UNAVAILABLE", reason=str(exc))
        histories = []
    try:
        snapshots = _alpaca_request("/v2/stocks/snapshots", {
            "symbols": ",".join(symbols), "feed": "iex",
        }, deadline, key_id, secret_key)
        observations = _alpaca_intraday(snapshots, histories, now, retrieved, "alpaca-iex")
        intraday["coverage_symbols"] = len(observations)
        if not observations:
            intraday.update(status="UNAVAILABLE", reason="no current IEX trade timestamps returned")
    except SourceError as exc:
        intraday.update(status="UNAVAILABLE", reason=str(exc))
        observations = []
    return dict(sources=[daily, intraday], observations=observations, history=histories)


def collect_live(now, include_cuttingboard=False, fetcher=fetch):
    raw = dict(mode="LIVE", sources=[], observations=[], history=[], events=[], context_items=[])
    deadline = time.monotonic() + 120
    year = now.astimezone(EASTERN).year
    jobs = [
        ("treasury", "US Treasury", "economic_series", TREASURY, treasury_url(year)),
        ("bls", "BLS calendar", "calendar", BLS, BLS),
        ("fed", "Federal Reserve releases", "news", FED, FED),
    ]
    for ident, name, kind, url, request_url in jobs:
        record = source(ident, name, kind, url, now)
        try:
            if ident == "bls":  # the calendar file, else its official fallback pages: one source record either way
                events, record["url"], record["reason"], retrieved = bls_calendar(now, deadline, fetcher)
                record.update(retrieved_at=retrieved.isoformat(),
                              coverage_date=now.astimezone(EASTERN).date().isoformat())
                raw["events"].extend(events)
                raw["sources"].append(record)
                # The admitted calendar is the only trigger for a release page; no calendar, no release read.
                rows, records = release_actuals(events, now, deadline, fetcher)
                raw["observations"].extend(rows)
                raw["sources"].extend(records)
                continue
            body = fetcher(request_url, deadline)
            if ident == "treasury":
                entries = treasury_entries(body, now)
                if len(entries) < 2:
                    # Early January: this year's feed holds fewer than two entries before today, so the previous
                    # year's feed supplies the level or its prior entry. This year's entries win a duplicate date.
                    try:
                        entries = [*treasury_entries(fetcher(treasury_url(year - 1), deadline), now), *entries]
                    except (SourceError, ValueError, ET.ParseError, UnicodeError, OverflowError):
                        pass
            retrieved = datetime.now(timezone.utc)
            record["retrieved_at"] = retrieved.isoformat()
            if ident == "treasury":
                raw["observations"].extend(yield_rows(entries, retrieved))
            else:
                raw["context_items"].extend(fed_context(body, now))
        except (SourceError, ValueError, ET.ParseError, UnicodeError, OverflowError) as exc:
            record.update(status="UNAVAILABLE", reason=failure(exc))
        raw["sources"].append(record)
    for ident, name, url in (
        ("bea", "BEA calendar", "https://www.bea.gov/news/schedule"),
        ("fed-calendar", "Fed calendar", "https://www.federalreserve.gov/newsevents/calendar.htm"),
    ):
        raw["sources"].append(source(ident, name, "calendar", url, now, "UNAVAILABLE",
                                    "not automated in this slice; sourced input supported"))
    equity = alpaca_collect(now)
    raw["sources"].extend(equity["sources"])
    raw["observations"].extend(equity["observations"])
    raw["history"].extend(equity["history"])
    raw["cuttingboard"] = dict(status="UNAVAILABLE", reason="optional source not requested")
    if include_cuttingboard:
        captured = datetime.now(timezone.utc)
        try:
            payload = json.loads(fetcher(CB, deadline))
            raw["cuttingboard"] = cuttingboard_record(payload, now, captured)
        except (ValueError, SourceError):
            raw["cuttingboard"] = dict(status="UNAVAILABLE", reason="public GET failed",
                                       source=CB, captured_at=captured.isoformat(), adapter_version="v0")
    return raw
