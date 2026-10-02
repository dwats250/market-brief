"""Official BLS release values: the Employment Situation and the Consumer Price Index, read from BLS's fixed
current-edition pages once the admitted calendar says the release has happened. Nothing here fetches.

Two narrow readers, never a general scraper. The CPI's four values come from its Table A. The Employment Situation's
current-edition page holds only the release text, so its values come from BLS's standard sentences, each pinned by real
releases and checked against the others: every statement of a value must agree, the monthly earnings change must agree
with its cents and dollar level, each revision with its previous and revised estimates, and the combined revision with
their sum. A page is admitted only as the scheduled release: its official release time is the calendar event's and its
reference month the event's when the calendar names one. Anything else fails closed and shows no value.
"""

import re
from datetime import datetime, timezone
from html.parser import HTMLParser

from .evidence import ET, timestamp

EMPLOYMENT_SITUATION = "https://www.bls.gov/news.release/empsit.nr0.htm"
CONSUMER_PRICE_INDEX = "https://www.bls.gov/news.release/cpi.nr0.htm"
FAMILIES = {
    "empsit": dict(title="Employment Situation", url=EMPLOYMENT_SITUATION, source="bls-empsit",
                   name="BLS Employment Situation", heading="THE EMPLOYMENT SITUATION"),
    "cpi": dict(title="Consumer Price Index", url=CONSUMER_PRICE_INDEX, source="bls-cpi",
                name="BLS Consumer Price Index", heading="CONSUMER PRICE INDEX"),
}
RELEASE_PAGES = tuple(family["url"] for family in FAMILIES.values())
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTH = "(" + "|".join(MONTHS) + ")"
# The calendar names a release by its family alone (the ICS file) or with its reference month (the List View).
EVENT_TITLE = re.compile(rf"(Employment Situation|Consumer Price Index)(?: for {MONTH} (\d{{4}}))?")
PAGE_TITLE = re.compile(r"(Employment Situation|Consumer Price Index) Summary - (\d{4}) M(0[1-9]|1[0-2]) Results")
EMBARGO = re.compile(r"Transmission of material in this (?:news )?release is embargoed until (?:USDL-\d{2}-\d{3,5} )?"
                     rf"(\d{{1,2}}):([0-5]\d) (a\.m\.|p\.m\.) \(ET\) ({'|'.join(WEEKDAYS)}), {MONTH} (\d{{1,2}}), "
                     r"(\d{4})")
RELEASE_NUMBER = re.compile(r"\bUSDL-\d{2}-\d{3,5}\b")

# The Employment Situation's standard sentences. A direction word outside these lists is not read.
UP, DOWN = ("increased", "rose", "edged up", "grew"), ("decreased", "declined", "fell", "edged down")
HAVE_UP, HAVE_DOWN = ("increased", "risen", "grown"), ("decreased", "declined", "fallen")
VERB = "(" + "|".join(UP + DOWN) + ")"
SIGNED = r"([+\-\u2212]\d{1,3}(?:,\d{3})*|0)"  # BLS prints an unchanged month as (0)
COUNT = r"(\d{1,3}(?:,\d{3})*)"
PAYROLLS = (
    # The lead: "Both nonfarm payroll employment (+29,000) and the unemployment rate (4.2 percent) changed little in
    # September". The month closes the sentence; a decimal point is not a sentence end.
    re.compile(rf"nonfarm payroll employment \({SIGNED}\)(?:[^.]|\.\d){{0,160}}? in {MONTH}\b"),
    # "Total nonfarm payroll employment changed little in September (+29,000)"
    re.compile(rf"Total nonfarm payroll employment [a-z ]{{1,40}}? in {MONTH} \({SIGNED}\)"),
    # "Total nonfarm payroll employment increased by 162,000 in August"
    re.compile(rf"Total nonfarm payroll employment {VERB} by {COUNT} in {MONTH}\b"),
)
RATE = r"(\d{1,2}\.\d)"
UNEMPLOYMENT = (
    re.compile(rf"[Tt]he unemployment rate \({RATE} percent\)"),
    re.compile(rf"[Tt]he unemployment rate, at {RATE} percent,"),
    re.compile(r"[Tt]he unemployment rate (?:was unchanged|was little changed|was essentially unchanged|changed little"
               rf"|remained|held) at {RATE} percent"),
    re.compile(rf"[Tt]he unemployment rate (?:{VERB[1:-1]}|ticked up|ticked down) to {RATE} percent"),
    re.compile(rf"[Tt]he unemployment rate was {RATE} percent"),
)
EARNINGS_SUBJECT = "average hourly earnings for all employees on private nonfarm payrolls"
EARNINGS = (
    re.compile(rf"In {MONTH}, {EARNINGS_SUBJECT} {VERB} by (\d{{1,3}}) cents?, or (\d{{1,2}}\.\d) percent, "
               r"to \$(\d{1,3}\.\d\d)"),
    re.compile(rf"In {MONTH}, {EARNINGS_SUBJECT} (?:were|was) unchanged at \$(\d{{1,3}}\.\d\d)"),
)
EARNINGS_YEAR = re.compile(r"Over the (?:past 12 months|last 12 months|year), average hourly earnings have "
                           rf"({'|'.join(HAVE_UP + HAVE_DOWN)}) by (\d{{1,2}}\.\d) percent")
ESTIMATE = r"([+\-\u2212]?\d{1,3}(?:,\d{3})*)"
REVISIONS = re.compile(
    rf"The change in total nonfarm payroll employment for {MONTH} was revised (up|down) by {COUNT}, from {ESTIMATE} "
    rf"to {ESTIMATE}, and the change for {MONTH} was revised (up|down) by {COUNT}, from {ESTIMATE} to {ESTIMATE}\. "
    rf"With these revisions, employment in {MONTH} and {MONTH} combined is {COUNT} (higher|lower) than previously "
    r"reported\.")
REVISION_MENTION = re.compile(r"\brevised (?:up|down) by\b|\bWith these revisions\b")
UNREAD_REVISIONS = "payroll revisions not read cleanly"
# Earnings: BLS rounds the cents change and the dollar level, and computes the percent from unrounded data.
EARNINGS_TOLERANCE = 0.08
# Bounds no real release crosses (payrolls in thousands of jobs; the rest in percent). A value outside one is a misread.
LIMITS = dict(payrolls=25_000, unemployment=30, earnings_mm=10, earnings_yy=25, revision=5_000,
              all_items_mm=5, all_items_yy=30, core_mm=5, core_yy=30)

# The CPI's Table A, in BLS's own words.
TABLE_A = "cpi_pressa"
TABLE_A_CAPTION = "Table A. Percent changes in CPI for All Urban Consumers (CPI-U): U.S. city average"
TABLE_A_GROUP = "Seasonally adjusted changes from preceding month"
# How Table A labels a month, as patterns: `Aug.`, `May`, and September as `Sep.` or `Sept.`.
SHORT_MONTHS = (r"Jan\.", r"Feb\.", r"Mar\.", r"Apr\.", "May", r"Jun\.", r"Jul\.", r"Aug\.", r"Sept?\.", r"Oct\.",
                r"Nov\.", r"Dec\.")
TABLE_VALUE = re.compile(r"-?\d{1,2}\.\d")
FOOTNOTE = re.compile(r"\s*\(\d+\)$")

# The evidence rows each release yields: (value key, evidence ID, metric, unit, baseline).
JOBS, PERCENT, CHANGE = "thousand jobs", "percent", "percent change"
MEASURES = {
    "empsit": (
        ("payrolls", "bls-empsit-payrolls", "nonfarm payroll change", JOBS,
         "total nonfarm, change from the previous month, seasonally adjusted"),
        ("unemployment", "bls-empsit-unemployment-rate", "unemployment rate", PERCENT,
         "share of the labor force, seasonally adjusted"),
        ("earnings_mm", "bls-empsit-earnings-mm", "average hourly earnings, monthly change", CHANGE,
         "private nonfarm, change from the previous month, seasonally adjusted"),
        ("earnings_yy", "bls-empsit-earnings-yy", "average hourly earnings, 12-month change", CHANGE,
         "private nonfarm, change over the past 12 months"),
    ),
    "cpi": (
        ("all_items_mm", "bls-cpi-all-items-mm", "CPI-U all items, monthly change", CHANGE,
         "U.S. city average, change from the previous month, seasonally adjusted"),
        ("all_items_yy", "bls-cpi-all-items-yy", "CPI-U all items, 12-month change", CHANGE,
         "U.S. city average, change over the past 12 months, not seasonally adjusted"),
        ("core_mm", "bls-cpi-core-mm", "CPI-U all items less food and energy, monthly change", CHANGE,
         "U.S. city average, change from the previous month, seasonally adjusted"),
        ("core_yy", "bls-cpi-core-yy", "CPI-U all items less food and energy, 12-month change", CHANGE,
         "U.S. city average, change over the past 12 months, not seasonally adjusted"),
    ),
}
REVISION, COMBINED = "payroll revision", "combined payroll revision"
READER_NAMES = dict(unemployment="unemployment rate", earnings_mm="monthly earnings change",
                    earnings_yy="12-month earnings change", all_items_mm="monthly CPI change",
                    all_items_yy="12-month CPI change", core_mm="monthly core CPI change",
                    core_yy="12-month core CPI change")


class ReleaseError(ValueError):
    """Why a release page was not admitted, in words fit for the Sources drawer."""


def plain(text):
    return " ".join(text.split())


def period_name(period):
    year, month = period.split("-")
    return f"{MONTHS[int(month) - 1]} {year}"


def shifted(period, months):
    year, month = map(int, period.split("-"))
    index = year * 12 + month - 1 + months
    return f"{index // 12}-{index % 12 + 1:02d}"


class ReleasePage(HTMLParser):
    """The parts of a current-edition release page the readers use: its title, its one heading and, inside its one
    news block, its release texts (<pre>) and tables. Script and style text is never page text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.titles, self.headings, self.texts, self.tables = [], [], [], []
        self.blocks, self.divs, self.hidden = 0, [], 0
        self.target, self.table, self.in_text = None, None, False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        news = any(self.divs)
        if tag in ("script", "style"):
            self.hidden += 1
        elif tag == "title":
            self.titles.append([])
            self.target = self.titles[-1]
        elif tag == "h1":
            self.headings.append([])
            self.target = self.headings[-1]
        elif tag == "div":
            block = "normalnews" in (attrs.get("class") or "").split()
            self.blocks += block
            self.divs.append(block)
        elif tag == "pre" and news:
            if self.in_text or self.table is not None:
                raise ReleaseError("malformed release page: a release text inside another element")
            self.in_text = True
            self.texts.append([])
            self.target = self.texts[-1]
        elif tag == "table" and news:
            if self.table is not None or self.in_text:
                raise ReleaseError("malformed release page: a table inside another element")
            self.table = dict(id=attrs.get("id"), caption=[], rows=[], section=None)
            self.tables.append(self.table)
        elif self.table is not None:
            if tag == "caption":
                self.target = self.table["caption"]
            elif tag in ("thead", "tbody", "tfoot"):
                self.table["section"] = tag
            elif tag == "tr":
                self.table["rows"].append(dict(section=self.table["section"], cells=[]))
            elif tag in ("th", "td"):
                if not self.table["rows"]:
                    raise ReleaseError("malformed release page: a table cell outside a row")
                cell = dict(tag=tag, text=[], span=attrs.get("colspan") or "1")
                self.table["rows"][-1]["cells"].append(cell)
                self.target = cell["text"]
        if tag == "br" and self.target is not None:
            self.target.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)
        elif tag in ("title", "h1", "caption", "th", "td"):
            self.target = None
        elif tag == "pre" and self.in_text:
            self.in_text, self.target = False, None
        elif tag in ("thead", "tbody", "tfoot") and self.table is not None:
            self.table["section"] = None
        elif tag == "table" and self.table is not None:
            self.table = None
        elif tag == "div" and self.divs:
            self.divs.pop()

    def handle_data(self, data):
        if not self.hidden and self.target is not None:
            self.target.append(data)


def page_parts(page):
    parser = ReleasePage()
    try:
        parser.feed(page)
        parser.close()
    except ReleaseError:
        raise
    # HTMLParser can still assert on input it was never meant to see; that is a malformed page, not a crash.
    except (AssertionError, ValueError):
        raise ReleaseError("malformed release page") from None
    if parser.in_text or parser.table is not None:
        raise ReleaseError("malformed release page: a release text or table never closes")
    if parser.blocks != 1 or not parser.texts or len(parser.titles) != 1 or len(parser.headings) != 1:
        raise ReleaseError("malformed release page: not one title, one heading and one release text")
    return dict(title=plain("".join(parser.titles[0])), heading=plain("".join(parser.headings[0])),
                text="".join(parser.texts[0]), tables=parser.tables)


def thousands(text, limit, name):
    """`+29,000` → 29: BLS states payroll changes in whole thousands of jobs."""
    value = int(text.replace("\u2212", "-").replace(",", ""))
    if value % 1000:
        raise ReleaseError(f"a {name} not in whole thousands: {text}")
    if abs(value) > limit * 1000:
        raise ReleaseError(f"implausible {name}: {text}")
    return value // 1000


def bounded(value, name):
    if abs(value) > LIMITS[name] or (name == "unemployment" and value <= 0):
        raise ReleaseError(f"implausible {READER_NAMES[name]}: {value}")
    return value


def agree(name, values):
    """The one value every statement of a measure gives; none, or two different ones, fails closed."""
    if not values:
        raise ReleaseError(f"no {name} statement for the reference month")
    if len(set(values)) > 1:
        raise ReleaseError(f"the release states conflicting {name} values")
    return values[0]


def signed(verb):
    return -1 if verb in DOWN or verb in HAVE_DOWN else 1


def own_month(pattern, text, month, name):
    """Each match of a statement whose clause names no month but the reference month; a statement about another month
    (`... from August, when the unemployment rate was 4.3 percent`) refuses the release rather than stand in for it.
    The clause runs from the previous sentence break (at most sixty characters back) to the end of the sentence."""
    found = []
    for match in pattern.finditer(text):
        before = re.split(r"[.;] ", text[max(0, match.start() - 60):match.start()])[-1]
        after = re.split(r"\. (?=[A-Z(])", text[match.end():match.end() + 200], maxsplit=1)[0]
        if set(re.findall(MONTH, before + match.group(0) + after)) - {month}:
            raise ReleaseError(f"a {name} statement names another month")
        found.append(match.groups())
    return found


def employment(text, period):
    """The Employment Situation's required values and its stated payroll revisions, from its release text."""
    month = MONTHS[int(period[5:]) - 1]
    payrolls = [m[0] for m in PAYROLLS[0].findall(text) if m[1] == month]
    payrolls += [m[1] for m in PAYROLLS[1].findall(text) if m[0] == month]
    payrolls += [("-" if m[0] in DOWN else "+") + m[1] for m in PAYROLLS[2].findall(text) if m[2] == month]
    payrolls = agree("nonfarm payroll", [thousands(value, LIMITS["payrolls"], "payroll change") for value in payrolls])
    rates = [float(value) for pattern in UNEMPLOYMENT
             for value, in own_month(pattern, text, month, "unemployment rate")]
    unemployment = bounded(agree("unemployment rate", rates), "unemployment")
    changes = []
    for found, verb, cents, percent, level in EARNINGS[0].findall(text):
        if found != month:
            continue
        sign, cents, level = signed(verb), int(cents), float(level)
        if 100 * level - sign * cents <= 0:
            raise ReleaseError("the monthly earnings change disagrees with its cents and dollar level")
        implied = 100 * sign * cents / (100 * level - sign * cents)
        if abs(implied - sign * float(percent)) > EARNINGS_TOLERANCE:
            raise ReleaseError("the monthly earnings change disagrees with its cents and dollar level")
        changes.append(sign * float(percent))
    changes += [0.0 for found, _ in EARNINGS[1].findall(text) if found == month]
    earnings_mm = bounded(agree("monthly earnings", changes), "earnings_mm")
    yearly = [signed(verb) * float(percent)
              for verb, percent in own_month(EARNINGS_YEAR, text, month, "12-month earnings")]
    earnings_yy = bounded(agree("12-month earnings", yearly), "earnings_yy")
    values = dict(payrolls=payrolls, unemployment=unemployment, earnings_mm=earnings_mm, earnings_yy=earnings_yy)
    try:
        revisions, combined = payroll_revisions(text, period)
        note = ""
    except ValueError:  # a ReleaseError, or a number int() will not read
        revisions, combined, note = [], None, UNREAD_REVISIONS
    return dict(values=values, revisions=revisions, combined=combined, note=note)


def payroll_revisions(text, period):
    """The two prior months' revised payroll changes and their combined revision, as BLS states them; ([], None) when
    the release states none. Each must agree with its own estimates, and the combined revision with their sum."""
    found = REVISIONS.findall(text)
    if not found:
        if REVISION_MENTION.search(text):
            raise ReleaseError(UNREAD_REVISIONS)
        return [], None
    if len(found) > 1:
        raise ReleaseError(UNREAD_REVISIONS)
    (first, first_way, first_by, first_from, first_to, second, second_way, second_by, second_from, second_to,
     named_first, named_second, combined, way) = found[0]
    months = [shifted(period, -2), shifted(period, -1)]
    names = [MONTHS[int(month[5:]) - 1] for month in months]
    if [first, second] != names or [named_first, named_second] != names:
        raise ReleaseError(UNREAD_REVISIONS)
    revisions = []
    for month, direction, by, previous, revised in ((months[0], first_way, first_by, first_from, first_to),
                                                    (months[1], second_way, second_by, second_from, second_to)):
        change = thousands(by, LIMITS["revision"], "revision") * (1 if direction == "up" else -1)
        previous, revised = (thousands(value, LIMITS["payrolls"], "payroll estimate") for value in (previous, revised))
        if revised - previous != change:
            raise ReleaseError(UNREAD_REVISIONS)
        revisions.append(dict(month=month, previous=previous, revised=revised, change=change))
    combined = thousands(combined, LIMITS["revision"], "revision") * (1 if way == "higher" else -1)
    if sum(revision["change"] for revision in revisions) != combined:
        raise ReleaseError(UNREAD_REVISIONS)
    return revisions, combined


def table_label(period):
    """The pattern of Table A's label for the reference month: `Aug\\. 2026`."""
    return f"{SHORT_MONTHS[int(period[5:]) - 1]} {period[:4]}"


def cpi_values(tables, period):
    """Headline and core CPI-U, monthly (seasonally adjusted) and 12-month, from Table A's reference-month columns."""
    found = [table for table in tables if table["id"] == TABLE_A]
    if len(found) != 1:
        raise ReleaseError("the page has no single Table A")
    table = found[0]
    if plain("".join(table["caption"])) != TABLE_A_CAPTION:
        raise ReleaseError("Table A is not the CPI-U U.S. city average table")
    head = [row["cells"] for row in table["rows"] if row["section"] == "thead"]
    if len(head) != 2:
        raise ReleaseError("Table A's columns are not the expected ones")
    group = [(cell["tag"], plain("".join(cell["text"])), cell["span"]) for cell in head[0]]
    label = table_label(period)
    if (len(group) != 3 or group[0][:2] != ("th", "") or group[1] != ("th", TABLE_A_GROUP, "7")
            or not re.fullmatch(rf"Un- ?adjusted 12-mos\. ended {label}", group[2][1])):
        raise ReleaseError("Table A's columns are not the expected ones")
    months = [plain("".join(cell["text"])) for cell in head[1]]
    if len(months) != 7 or not re.fullmatch(label, months[-1]):
        raise ReleaseError("Table A's latest month is not the reference month")
    rows = {}
    for row in (row["cells"] for row in table["rows"] if row["section"] == "tbody"):
        if not row or row[0]["tag"] != "th":
            raise ReleaseError("Table A has a row without a label")
        name = FOOTNOTE.sub("", plain("".join(row[0]["text"])))
        if name in ("All items", "All items less food and energy"):
            if name in rows:
                raise ReleaseError(f"Table A repeats its {name} row")
            rows[name] = [plain("".join(cell["text"])) for cell in row[1:]]
    values = {}
    for key, name, column in (("all_items_mm", "All items", 6), ("all_items_yy", "All items", 7),
                              ("core_mm", "All items less food and energy", 6),
                              ("core_yy", "All items less food and energy", 7)):
        cells = rows.get(name)
        if cells is None or len(cells) != 8:
            raise ReleaseError(f"Table A has no complete {name} row")
        if not TABLE_VALUE.fullmatch(cells[column]):
            raise ReleaseError(f"Table A's {name} value is not a number")
        values[key] = bounded(float(cells[column]), key)
    return values


def identity(family, text):
    """A release text's official release time (its embargo line) and reference month (its heading)."""
    embargo = EMBARGO.findall(text)
    if len(embargo) != 1 or not RELEASE_NUMBER.search(text):
        raise ReleaseError("the release text has no single embargo line and release number")
    hour, minute, meridiem, weekday, month, day, year = embargo[0]
    try:
        released = datetime(int(year), MONTHS.index(month) + 1, int(day), int(hour) % 12 + 12 * (meridiem == "p.m."),
                            int(minute), tzinfo=ET)
    except ValueError:
        raise ReleaseError("the embargo line's date does not exist") from None
    if WEEKDAYS[released.weekday()] != weekday:
        raise ReleaseError("the embargo line's weekday does not match its date")
    upper = [month.upper() for month in MONTHS]
    headings = re.findall(rf"\b{FAMILIES[family]['heading']} (?:-{{1,2}}|\u2013) ({'|'.join(upper)}) (\d{{4}})\b", text)
    if len(headings) != 1:
        raise ReleaseError("the release text names no single reference month")
    return released, f"{headings[0][1]}-{upper.index(headings[0][0]) + 1:02d}"


def release_text(family, text):
    """A release read from its text alone: its identity and, for the Employment Situation, its values."""
    text = plain(text)
    released, period = identity(family, text)
    release = dict(family=family, title=FAMILIES[family]["title"], period=period, released_at=released, values={},
                   revisions=[], combined=None, note="")
    if family == "empsit":
        release.update(employment(text, period))
    return release


def read_release(family, page):
    """One current-edition release page, read as `family`'s release, or ReleaseError. The page's title and its
    release text must name the same month before any value is read."""
    spec = FAMILIES[family]
    parts = page_parts(page)
    title = PAGE_TITLE.fullmatch(parts["title"])
    if not title or title[1] != spec["title"] or parts["heading"] != f"{spec['title']} Summary":
        raise ReleaseError(f"not the {spec['title']} release page")
    if identity(family, plain(parts["text"]))[1] != f"{title[2]}-{title[3]}":
        raise ReleaseError("the page's title and its release text name different months")
    release = release_text(family, parts["text"])
    if family == "cpi":
        release["values"] = cpi_values(parts["tables"], release["period"])
    return release


def due_releases(events, now):
    """Today's admitted releases of the two families whose scheduled time has passed, as (family, event, the reference
    month the calendar names or None). A family listed twice today yields (family, None, None): which page is which
    cannot be told."""
    today, due = now.astimezone(ET).date(), {}
    for event in events:
        match = EVENT_TITLE.fullmatch(event.get("title") or "")
        if not match:
            continue
        when = timestamp(event["scheduled_at"])
        if when.astimezone(ET).date() != today or when > now:
            continue
        family = next(key for key, spec in FAMILIES.items() if spec["title"] == match[1])
        period = f"{match[3]}-{MONTHS.index(match[2]) + 1:02d}" if match[2] else None
        due.setdefault(family, []).append((event, period))
    return [(family, *found[0]) if len(found) == 1 else (family, None, None) for family, found in due.items()]


def long_date(when):
    return f"{MONTHS[when.month - 1]} {when.day}, {when.year}"


def check_release(release, event, period):
    """The page is the scheduled release: its official release time is the event's, and its reference month is the
    one the calendar names (when it names one) and precedes the release."""
    scheduled = timestamp(event["scheduled_at"])
    if release["released_at"] < scheduled:
        raise ReleaseError(f"the page still shows the {long_date(release['released_at'])} release")
    if release["released_at"] != scheduled:
        raise ReleaseError(f"the page shows a release of {long_date(release['released_at'])}, not the scheduled one")
    if period and release["period"] != period:
        raise ReleaseError(f"the page reports {period_name(release['period'])}, "
                           f"not the scheduled {period_name(period)}")
    if release["period"] >= f"{release['released_at']:%Y-%m}":
        raise ReleaseError("the page's reference month does not precede its release")


def release_rows(release, retrieved):
    """The release's evidence rows: dated by its official release time and tied to its reference month."""
    spec = FAMILIES[release["family"]]
    common = dict(topic=release["title"], frequency="release", retrieved_at=retrieved.isoformat(),
                  observed_at=release["released_at"].astimezone(timezone.utc).isoformat(), source_id=spec["source"],
                  status="AVAILABLE", reason="", reference_period=release["period"])
    rows = [dict(common, id=ident, metric=metric, value=release["values"][key], unit=unit, baseline=baseline)
            for key, ident, metric, unit, baseline in MEASURES[release["family"]]]
    for revision in release["revisions"]:
        rows.append(dict(common, id=f"bls-empsit-revision-{revision['month']}", metric=REVISION,
                         value=revision["change"], unit=JOBS, revised_month=revision["month"],
                         revised_from=revision["previous"], revised_to=revision["revised"],
                         baseline="change to the revised month's previously published estimate"))
    if release["combined"] is not None:
        rows.append(dict(common, id="bls-empsit-revision-combined", metric=COMBINED, value=release["combined"],
                         unit=JOBS, baseline="the revised months together, change from previously reported"))
    return rows
