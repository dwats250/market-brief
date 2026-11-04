"""One scheduled Statistics Canada Labour Force Survey reader. Parsing only; fetching stays in collect.py.

Read as-released values from the dated Daily article. The WDS current table is deliberately not used because it may
contain revised history; this small reader emits no revision or consensus fields it cannot verify in that release.
"""

import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

SCHEDULE = "https://www150.statcan.gc.ca/n1/dai-quo/cal2-eng.htm"
DAILY = "https://www150.statcan.gc.ca/n1/daily-quotidien/{stamp}/dq{stamp}a-eng.htm"
EASTERN = ZoneInfo("America/Toronto")
MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


class StatisticsCanadaError(ValueError):
    """An official calendar or release page could not be safely interpreted."""


class SchedulePage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self.current = None
        self.kind = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "h3":
            self.current, self.kind = [], "date"
        elif tag == "div" and "rtitle" in (attrs.get("class") or "").split():
            self.current, self.kind = [], "title"

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if (tag == "h3" and self.kind == "date") or (tag == "div" and self.kind == "title"):
            self.blocks.append((self.kind, " ".join("".join(self.current).split())))
            self.current, self.kind = None, None


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag == "meta":
            attrs = dict(attrs)
            name = attrs.get("name")
            if name in {"dcterms.title", "dcterms.description", "dcterms.issued", "dcterms.modified"}:
                self.values.setdefault(name, []).append(attrs.get("content", ""))


def lfs_schedule(text, now, retrieved):
    """Return the listed LFS event or None; the official page must confirm its 8:30 a.m. Eastern convention."""
    if not re.search(
        r"Releases are published in\s*<em>The Daily</em> each working day at 8:30 a\.m\. Eastern", text, re.I
    ):
        raise StatisticsCanadaError("official release schedule lacks the 8:30 a.m. Eastern publication rule")
    page = SchedulePage()
    try:
        page.feed(text)
    except (AssertionError, ValueError):
        raise StatisticsCanadaError("malformed official release schedule") from None
    entries, day = [], None
    for kind, value in page.blocks:
        if kind == "date":
            match = re.fullmatch(r"(" + "|".join(MONTHS) + r")\s+(\d{1,2})", value)
            if not match:
                raise StatisticsCanadaError("schedule contains an unrecognized release date")
            month, number = MONTHS.index(match[1]) + 1, int(match[2])
            year = now.astimezone(EASTERN).year
            candidate = datetime(year, month, number, tzinfo=EASTERN).date()
            if candidate < now.astimezone(EASTERN).date() - timedelta(days=14):
                candidate = candidate.replace(year=year + 1)
            day = candidate
        elif kind == "title":
            match = re.fullmatch(r"Labour Force Survey,\s*(" + "|".join(MONTHS) + r")\s+(\d{4})", value)
            if match and day:
                period = f"{int(match[2]):04d}-{MONTHS.index(match[1]) + 1:02d}"
                scheduled = datetime(day.year, day.month, day.day, 8, 30, tzinfo=EASTERN)
                entries.append((scheduled, period))
    if len(entries) > 1:
        raise StatisticsCanadaError("official schedule lists multiple Labour Force Survey releases")
    if not entries:
        return None
    scheduled, period = entries[0]
    stamp = scheduled.strftime("%y%m%d")
    return dict(
        id=f"statcan-lfs-event-{scheduled:%Y%m%d}",
        title=f"Statistics Canada Labour Force Survey for {MONTHS[int(period[5:]) - 1]} {period[:4]}",
        source_id="statcan-calendar",
        published_at=None,
        checked_at=retrieved.isoformat(),
        scheduled_at=scheduled.astimezone(timezone.utc).isoformat(),
        status="SCHEDULED",
        period=period,
        url=DAILY.format(stamp=stamp),
    )


EMPLOYMENT = re.compile(
    r"Employment\s+(increased|rose|grew|declined|decreased|fell)\s+by\s+([\d,]+)\s+"
    r"(?:\([^)]*\)\s*)?in\s+(" + "|".join(MONTHS) + r")\b",
    re.I,
)
RATE = re.compile(
    r"The unemployment rate\s+(?:increased|rose|edged up|decreased|declined|fell|edged down)\s+"
    r"(?:\d{1,2}\.\d\s+percentage points?\s+)?to\s+(\d{1,2}\.\d)%",
    re.I,
)
RATE_UNCHANGED = re.compile(r"The unemployment rate\s+(?:was unchanged|held steady)\s+at\s+(\d{1,2}\.\d)%", re.I)


def lfs_actual(text, scheduled, expected_period):
    """Read the two Canadian headline values from the dated archived Daily release, never from revised table data."""
    page = Metadata()
    try:
        page.feed(text)
    except (AssertionError, ValueError):
        raise StatisticsCanadaError("malformed Statistics Canada release page") from None
    if any(
        len(page.values.get(key, [])) != 1
        for key in ("dcterms.title", "dcterms.description", "dcterms.issued", "dcterms.modified")
    ):
        raise StatisticsCanadaError("release page lacks unique dated headline metadata")
    title = page.values["dcterms.title"][0]
    title_match = re.fullmatch(
        r"The Daily\s*[—-]\s*Labour Force Survey,\s*(" + "|".join(MONTHS) + r")\s+(\d{4})", title
    )
    if not title_match:
        raise StatisticsCanadaError("page is not a Labour Force Survey Daily release")
    period = f"{int(title_match[2]):04d}-{MONTHS.index(title_match[1]) + 1:02d}"
    if period != expected_period:
        raise StatisticsCanadaError("release reports a different reference month than the official schedule")
    issued = page.values["dcterms.issued"][0]
    if issued != scheduled.astimezone(EASTERN).date().isoformat():
        raise StatisticsCanadaError("release page date does not match the official scheduled date")
    if page.values["dcterms.modified"][0] != issued:
        raise StatisticsCanadaError("release page was modified after its original publication date")
    description = page.values["dcterms.description"][0]
    employment, rates, unchanged = (
        EMPLOYMENT.findall(description),
        RATE.findall(description),
        RATE_UNCHANGED.findall(description),
    )
    if len(employment) != 1 or len(rates) + len(unchanged) != 1 or employment[0][2] != title_match[1]:
        raise StatisticsCanadaError(
            "release headline lacks one unambiguous Canadian employment change and unemployment rate"
        )
    direction, amount, _ = employment[0]
    change = int(amount.replace(",", "")) * (-1 if direction.lower() in {"declined", "decreased", "fell"} else 1)
    rate = float((rates or unchanged)[0])
    return dict(
        title="Statistics Canada Labour Force Survey",
        period=period,
        published_at=scheduled.astimezone(timezone.utc).isoformat(),
        values=dict(employment_change=change, unemployment_rate=rate),
    )
