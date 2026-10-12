"""Sunday Week Ahead prototype: a deterministic build from a saved evidence snapshot and an editorial file.

No network, no model call. The facts come from one dated snapshot folder (`inputs/` CSVs read once on
2026-10-11/12 plus `facts.json`, each row with its source, observation time and kind). The editorial lives in
`edition.json`. It writes figures only through `{evidence-id}` placeholders, and every placeholder and citation
must resolve or the build fails. The page reuses the production stylesheet (read from `templates/brief.html.j2`),
the collector's Treasury row builder, the curve module's spreads and move classifier, and the renderer's curve
chart. Nothing here is imported by production code or touches `publish/`.

    .venv/bin/python3 experiments/sunday/build.py experiments/sunday/2026-10-11
"""

import csv
import json
import math
import re
import statistics
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from market_brief import curve, render  # noqa: E402
from market_brief.collect import yield_rows  # noqa: E402

ET, PT = ZoneInfo("America/New_York"), ZoneInfo("America/Vancouver")
MINUS = "−"
TICKERS = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq-100",
    "RSP": "S&P 500 equal weight",
    "IWM": "Russell 2000",
    "XLK": "Technology",
    "XLC": "Communication Services",
    "XLY": "Consumer Discretionary",
    "XLF": "Financials",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLE": "Energy",
    "XLP": "Consumer Staples",
    "XLV": "Health Care",
    "XLU": "Utilities",
    "XLRE": "Real Estate",
    "GLD": "Gold fund",
    "GDX": "Gold miners",
    "TLT": "Treasuries 20Y+",
    "IEF": "Treasuries 7–10Y",
    "HYG": "High-yield credit",
    "LQD": "Investment-grade credit",
    "UUP": "Dollar fund",
    "USO": "Oil fund",
    "KRE": "Regional banks",
    "SMH": "Semiconductors",
    "XHB": "Homebuilders",
}
EQUITY = (
    "SPY",
    "QQQ",
    "RSP",
    "IWM",
    "XLK",
    "XLC",
    "XLY",
    "XLF",
    "XLI",
    "XLB",
    "XLE",
    "XLP",
    "XLV",
    "XLU",
    "XLRE",
    "KRE",
    "SMH",
    "XHB",
)
CROSS = ("TLT", "IEF", "LQD", "HYG", "UUP", "USO", "GLD", "GDX")
WEEK_START, WEEK_END, SEP1 = "2026-10-02", "2026-10-09", "2026-09-01"
PROSE_NUMBER = re.compile(r"[+−-]?\d+(?:\.\d+)?\s?(?:%|bp|pp)\b")
PLACEHOLDER = re.compile(r"\{([a-z0-9][a-z0-9-]*)\}")


# --- formatting (house style: +0.53%, −5.30 pp, −6 bp, 5.24%) ---------------------------------------------


def signed(value, digits, suffix=""):
    if round(value, digits) == 0:
        return f"{0:.{digits}f}{suffix}"
    text = f"{abs(value):,.{digits}f}{suffix}"
    return ("+" if value > 0 else MINUS) + text


def display(row):
    value, unit = row["value"], row["unit"]
    if unit == "status" or isinstance(value, str):
        return str(value)
    return {
        "% yield": lambda: f"{value:.2f}%",
        "bp": lambda: signed(value, 0, " bp"),
        "bp level": lambda: f"{value:.0f} bp",
        "pct change": lambda: signed(value, 2, "%"),
        "pp": lambda: signed(value, 2, " pp"),
        "%": lambda: f"{value:.1f}%",
        "USD": lambda: f"{value:,.2f}",
        "fx4": lambda: f"{value:.4f}",
        "index1": lambda: f"{value:.1f}",
        "pct change 1": lambda: signed(value, 1, "%"),
        "index": lambda: f"{value:,.2f}",
        "JPY": lambda: f"{value:.2f}",
        "K": lambda: f"{value:,.0f}K",
        "vol pts": lambda: f"{value:.2f}",
        "vol %": lambda: f"{value:.1f}%",
    }[unit]()


def direction(row):
    if row["unit"] in ("pct change", "pct change 1", "pp", "bp") and isinstance(row["value"], (int, float)):
        return "positive" if row["value"] > 0 else "negative" if row["value"] < 0 else "neutral"
    return "neutral"


def when_label(value):
    if "T" in value:
        moment = datetime.fromisoformat(value)
        return f"{moment.astimezone(ET):%a, %b %-d, %-I:%M %p} ET"
    return f"{date.fromisoformat(value):%a, %b %-d}"


# --- deterministic evidence from the snapshot ---------------------------------------------------------------


def read_csv(path):
    with open(path, newline="") as handle:
        return list(csv.DictReader(handle))


def closes(inputs):
    table = {}
    for row in read_csv(inputs / "closes.csv"):
        table.setdefault(row["ticker"], {})[row["date"]] = float(row["close"])
    for row in read_csv(inputs / "etf_week.csv"):
        for day, key in ((WEEK_START, "close_2026_10_02"), (WEEK_END, "close_2026_10_09")):
            if re.fullmatch(r"\d+(\.\d+)?", row[key]):
                table.setdefault(row["ticker"], {})[day] = float(row[key])
    return table


def evidence_rows(folder):
    inputs, rows = folder / "inputs", []
    retrieved = datetime(2026, 10, 12, 7, 30, tzinfo=timezone.utc)

    def add(**row):
        row.setdefault("kind", "derived")
        row.setdefault("note", "")
        rows.append(row)

    # Treasury nominal par curve: the collector's own row builder, week start vs week end.
    nominal = {r["date"]: r for r in read_csv(inputs / "treasury_nominal.csv")}
    entries = [
        (day, {f"BC_{t}YEAR": nominal[day][f"{t} Yr"] for t in (2, 5, 10, 30)}) for day in (WEEK_START, WEEK_END)
    ]
    treasury = yield_rows(entries, retrieved)
    tenors = {}
    for row in treasury:
        row["status"], row["freshness"] = "BACKGROUND", "PRIOR_CLOSE"
        tenor = row["topic"].removeprefix("US ")
        tenors.setdefault(tenor, {})["change" if row["metric"] == curve.CHANGE else "level"] = row
        add(
            id=row["id"],
            group="U.S. Treasury par curve",
            label=row["topic"],
            metric="par yield" if row["metric"] == curve.LEVEL else "change since Fri, Oct 2",
            value=row["value"],
            unit="% yield" if row["metric"] == curve.LEVEL else "bp",
            observed_at=WEEK_END,
            kind="official",
            source_name="U.S. Treasury daily par yield curve",
            source_url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_yield_curve&field_tdr_date_value_month=202610",
        )
    spreads = curve.spread_rows(tenors)
    for row in spreads:
        add(
            id=row["id"],
            group="U.S. Treasury par curve",
            label=row["topic"],
            metric="spread" if row["unit"] != "bp" else "change since Fri, Oct 2",
            value=row["value"],
            unit="bp level" if row["unit"] != "bp" else "bp",
            observed_at=WEEK_END,
            kind="derived",
            source_name="U.S. Treasury daily par yield curve (derived)",
            source_url="",
            note="inputs " + ", ".join(row["input_ids"]),
        )
    by_id = {row["id"]: row for row in spreads}
    move = curve.classify(
        tenors["2Y"]["change"]["value"],
        tenors["10Y"]["change"]["value"],
        by_id.get("treasury-5s30s-change", {}).get("value"),
    )
    week_days = [d for d in sorted(nominal) if WEEK_START < d <= WEEK_END]
    for tenor, field in (("10y", "10 Yr"), ("30y", "30 Yr")):
        for edge, pick in (("high", max), ("low", min)):
            day = pick(week_days, key=lambda d: float(nominal[d][field]))
            add(
                id=f"treasury-{tenor}-w41-{edge}",
                group="U.S. Treasury par curve",
                label=f"US {tenor.upper()}",
                metric=f"{'highest' if edge == 'high' else 'lowest'} close, Oct 5–9",
                value=float(nominal[day][field]),
                unit="% yield",
                observed_at=day,
                kind="official",
                source_name="U.S. Treasury daily par yield curve",
                source_url="",
            )

    # Real (TIPS) par curve and par-curve breakevens.
    real = {r["date"]: r for r in read_csv(inputs / "treasury_real.csv")}
    for tenor in (5, 10, 30):
        level, before = float(real[WEEK_END][f"{tenor} Yr"]), float(real[WEEK_START][f"{tenor} Yr"])
        add(
            id=f"real-{tenor}y",
            group="U.S. Treasury real (TIPS) par curve",
            label=f"Real {tenor}Y",
            metric="real par yield",
            value=level,
            unit="% yield",
            observed_at=WEEK_END,
            kind="official",
            source_name="U.S. Treasury daily real par yield curve",
            source_url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/TextView?type=daily_treasury_real_yield_curve&field_tdr_date_value_month=202610",
        )
        add(
            id=f"real-{tenor}y-change",
            group="U.S. Treasury real (TIPS) par curve",
            label=f"Real {tenor}Y",
            metric="change since Fri, Oct 2",
            value=round((level - before) * 100),
            unit="bp",
            observed_at=WEEK_END,
            kind="official",
            source_name="U.S. Treasury daily real par yield curve",
            source_url="",
        )
    for tenor in (5, 10, 30):
        be = lambda day: float(nominal[day][f"{tenor} Yr"]) - float(real[day][f"{tenor} Yr"])  # noqa: E731
        add(
            id=f"be-{tenor}y",
            group="Par-curve breakeven (nominal − real)",
            label=f"Breakeven {tenor}Y",
            metric="nominal minus real par yield",
            value=round(be(WEEK_END), 2),
            unit="% yield",
            observed_at=WEEK_END,
            note="includes inflation risk and liquidity premia; not a pure inflation expectation",
            source_name="Derived from Treasury nominal and real par curves",
            source_url="",
        )
        add(
            id=f"be-{tenor}y-change",
            group="Par-curve breakeven (nominal − real)",
            label=f"Breakeven {tenor}Y",
            metric="change since Fri, Oct 2",
            value=round((be(WEEK_END) - be(WEEK_START)) * 100),
            unit="bp",
            observed_at=WEEK_END,
            source_name="Derived from Treasury nominal and real par curves",
            source_url="",
        )
    series = []
    for day in sorted(d for d in nominal if d >= SEP1):
        n10, r10 = float(nominal[day]["10 Yr"]), float(real[day]["10 Yr"])
        series.append(dict(day=day, nominal=n10, real=r10, be=round(n10 - r10, 2)))
    first, last = series[0], series[-1]
    for key, label in (("nominal", "US 10Y"), ("real", "Real 10Y"), ("be", "Breakeven 10Y")):
        add(
            id=f"since-sep1-{key}-10y",
            group="Since Sep 1",
            label=label,
            metric="change since Tue, Sep 1",
            value=round((last[key] - first[key]) * 100),
            unit="bp",
            observed_at=WEEK_END,
            kind="official" if key != "be" else "derived",
            source_name="U.S. Treasury nominal and real par curves",
            source_url="",
        )
    bes = [p["be"] for p in series]
    add(
        id="be-10y-range-low",
        group="Since Sep 1",
        label="Breakeven 10Y",
        metric="lowest since Sep 1",
        value=min(bes),
        unit="% yield",
        observed_at=WEEK_END,
        source_name="Derived from Treasury nominal and real par curves",
        source_url="",
    )
    add(
        id="be-10y-range-high",
        group="Since Sep 1",
        label="Breakeven 10Y",
        metric="highest since Sep 1",
        value=max(bes),
        unit="% yield",
        observed_at=WEEK_END,
        source_name="Derived from Treasury nominal and real par curves",
        source_url="",
    )

    # Equity and cross-asset week, vs SPY, and 20-session spreads where 21 closes exist.
    table = closes(inputs)
    week = {
        t: 100 * (table[t][WEEK_END] / table[t][WEEK_START] - 1)
        for t in table
        if WEEK_START in table[t] and WEEK_END in table[t]
    }
    for ticker, value in week.items():
        add(
            id=f"{ticker.lower()}-week",
            group=TICKERS.get(ticker, ticker),
            label=ticker,
            metric="week, Oct 2 → Oct 9 close",
            value=round(value, 2),
            unit="pct change",
            observed_at=WEEK_END,
            kind="close",
            source_name="stockanalysis.com daily closes",
            source_url=f"https://stockanalysis.com/etf/{ticker.lower()}/history/",
        )
        if ticker != "SPY":
            add(
                id=f"{ticker.lower()}-week-vs-spy",
                group=TICKERS.get(ticker, ticker),
                label=ticker,
                metric="week vs SPY",
                value=round(value - week["SPY"], 2),
                unit="pp",
                observed_at=WEEK_END,
                source_name="stockanalysis.com (derived)",
                source_url="",
            )
    dates = sorted(table["SPY"])
    if len(dates) >= 21:
        base = dates[-21]
        r20 = {t: 100 * (table[t][WEEK_END] / table[t][base] - 1) for t in table if base in table[t]}
        for ticker in r20:
            if ticker != "SPY":
                add(
                    id=f"{ticker.lower()}-20s-vs-spy",
                    group=TICKERS.get(ticker, ticker),
                    label=ticker,
                    metric="20-session return vs SPY",
                    value=round(r20[ticker] - r20["SPY"], 2),
                    unit="pp",
                    observed_at=WEEK_END,
                    source_name="stockanalysis.com (derived)",
                    source_url="",
                    note=f"window {dates[-20]} to {WEEK_END}; includes sessions leaving the window",
                )
        returns = [math.log(table["SPY"][d] / table["SPY"][p]) for p, d in zip(dates[-21:], dates[-20:])]
        add(
            id="spy-realized-20",
            group="Volatility",
            label="SPY",
            metric="20-session realized volatility, annualized",
            value=round(statistics.stdev(returns) * math.sqrt(252) * 100, 1),
            unit="vol %",
            observed_at=WEEK_END,
            source_name="stockanalysis.com closes (derived)",
            source_url="",
        )
    vix = {r["date"]: r for r in read_csv(inputs / "vix.csv")}
    for day, rid in ((WEEK_END, "vix"), (WEEK_START, "vix-prior-week")):
        add(
            id=rid,
            group="Volatility",
            label="VIX",
            metric="close" if rid == "vix" else "close, week start",
            value=float(vix[day]["close"]),
            unit="vol pts",
            observed_at=day,
            kind="close" if "fred" in vix[day]["source"] else "secondary",
            source_name="FRED VIXCLS (Cboe)" if "fred" in vix[day]["source"] else "Tapeboard recap (secondary)",
            source_url=vix[day]["source"],
        )

    facts = json.loads((folder / "facts.json").read_text())
    for row in facts["facts"]:
        add(**row)
    ids = [row["id"] for row in rows]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate evidence ids: {sorted(duplicates)}")
    for row in rows:
        if not row.get("observed_at") or not row.get("source_name"):
            raise ValueError(f"evidence {row['id']} lacks a time or a source")
        row["display"], row["direction"], row["when"] = display(row), direction(row), when_label(row["observed_at"])
    return rows, tenors, spreads, move, series, facts


# --- editorial: placeholders and citations resolve or the build fails ------------------------------------


def resolve(text, catalog, field):
    if PROSE_NUMBER.search(PLACEHOLDER.sub("", text)):
        raise ValueError(f"{field}: a figure must come from a placeholder, not literal text: {text!r}")

    def swap(match):
        if match[1] not in catalog:
            raise ValueError(f"{field}: unknown evidence id {match[1]}")
        return catalog[match[1]]["display"]

    return PLACEHOLDER.sub(swap, text)


def refs(ids, catalog, field):
    unknown = [i for i in ids if i not in catalog]
    if unknown:
        raise ValueError(f"{field}: unknown evidence ids {unknown}")
    out = []
    for i in ids:
        row, text = catalog[i], catalog[i]["unit"] == "status"
        label = f"{row['label']} · {row['metric']}" + (f": {row['display']}" if text else "")
        out.append(
            dict(
                id=i,
                label=label,
                display="" if text else row["display"],
                when=", ".join(row["when"].split(", ")[:2]),
                anchor=True,
            )
        )
    return out


def walk(node, catalog, path="edition"):
    """Every string resolves its placeholders; every `refs` list becomes proof lines."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            if key == "refs":
                out[key] = refs(value, catalog, f"{path}.refs")
            elif key in ("id", "calendar_id"):
                out[key] = value
            else:
                out[key] = walk(value, catalog, f"{path}.{key}")
        return out
    if isinstance(node, list):
        return [walk(item, catalog, f"{path}[{i}]") for i, item in enumerate(node)]
    if isinstance(node, str):
        return resolve(node, catalog, path)
    return node


# --- charts ---------------------------------------------------------------------------------------------


def since_chart(series):
    """Change since Sep 1 in bp for the real 10Y and the 10Y breakeven: one axis, two lines, labelled ends."""
    height, top, bottom = 150, 12, 120
    lines = {key: [round((p[key] - series[0][key]) * 100, 1) for p in series] for key in ("real", "be")}
    values = [v for line in lines.values() for v in line] + [0]
    low, high = math.floor(min(values) / 10) * 10, math.ceil(max(values) / 10) * 10

    def x(i):
        return f"{72 * i / (len(series) - 1):.2f}%"

    def y(v):
        return round(bottom - (v - low) / (high - low) * (bottom - top), 1)

    def segments(line):
        return [dict(x1=x(i), y1=y(a), x2=x(i + 1), y2=y(b)) for i, (a, b) in enumerate(zip(line, line[1:]))]

    ticks = [
        dict(y=y(v), text=signed(v, 0, " bp") if v else "0")
        for v in range(low, high + 1, 10 if high - low <= 40 else 20)
    ]
    return dict(
        height=height,
        real=segments(lines["real"]),
        be=segments(lines["be"]),
        ticks=ticks,
        zero=y(0),
        real_end=dict(x="74%", y=y(lines["real"][-1]), text=signed(lines["real"][-1], 0, " bp")),
        be_end=dict(x="74%", y=y(lines["be"][-1]), text=signed(lines["be"][-1], 0, " bp")),
        start=f"{date.fromisoformat(series[0]['day']):%b %-d}",
        end=f"{date.fromisoformat(series[-1]['day']):%b %-d}",
        bottom_label=bottom + 24,
    )


# --- page -----------------------------------------------------------------------------------------------


def stylesheet():
    template = (ROOT / "templates/brief.html.j2").read_text()
    return template[template.index("<style>") + len("<style>") : template.index("</style>")]


def build(folder):
    folder = Path(folder)
    rows, tenors, spreads, move, series, facts = evidence_rows(folder)
    catalog = {row["id"]: row for row in rows}
    edition = walk(json.loads((folder / "edition.json").read_text()), catalog)
    for item in edition["lab"]:
        hypothesis = catalog[f"hyp-{item['id'].lower()}"]
        item.update(
            statement=hypothesis["metric"],
            status=hypothesis["value"],
            source_id=hypothesis["id"],
            measure=hypothesis["note"],
        )
    calendar = {item["id"]: item for item in facts["calendar"]}
    days = []
    for event in edition["calendar"]:
        item = calendar[event["calendar_id"]]
        at = item["at"]
        if "T" in at:
            moment = datetime.fromisoformat(at)
            day, clock = (
                f"{moment.astimezone(ET):%A, %b %-d}",
                f"{moment.astimezone(ET):%-I:%M %p} ET · {moment.astimezone(PT):%-I:%M %p} PT",
            )
            if moment.astimezone(PT).date() != moment.astimezone(ET).date():
                clock += f" ({moment.astimezone(PT):%a})"
        else:
            day, clock = f"{date.fromisoformat(at):%A, %b %-d}", item.get("time_note", "All day")
        if not days or days[-1]["day"] != day:
            days.append(dict(day=day, events=[]))
        days[-1]["events"].append(dict(event, item=item, clock=clock))
    yields = [
        dict(maturity=t, level=catalog[f"treasury-{t.lower()}"], change=catalog[f"treasury-{t.lower()}-change"])
        for t in ("2Y", "5Y", "10Y", "30Y")
    ]
    real = [
        dict(
            maturity=f"{t}Y",
            level=catalog[f"real-{t}y"],
            change=catalog[f"real-{t}y-change"],
            be=catalog[f"be-{t}y"],
            be_change=catalog[f"be-{t}y-change"],
        )
        for t in (5, 10, 30)
    ]
    spread_view = [
        dict(
            name=name,
            definition=f"{long} minus {short}",
            level=catalog[f"treasury-{name}"],
            change=catalog[f"treasury-{name}-change"],
        )
        for name, short, long in curve.SPREADS
    ]
    nominal_chart = render.curve_chart(tenors, WEEK_END, WEEK_START, False)

    def week_rows(tickers):
        out = []
        for t in tickers:
            if f"{t.lower()}-week" in catalog:
                out.append(
                    dict(
                        symbol=t,
                        label=TICKERS.get(t, t),
                        week=catalog[f"{t.lower()}-week"],
                        vs=catalog.get(f"{t.lower()}-week-vs-spy"),
                        r20=catalog.get(f"{t.lower()}-20s-vs-spy"),
                    )
                )
        return out

    equity = week_rows(EQUITY)
    equity = equity[:4] + sorted(equity[4:], key=lambda r: -r["week"]["value"])
    groups = {}
    for row in rows:
        groups.setdefault(row["group"], []).append(row)
    sources = {}
    for row in rows:
        if row.get("source_url"):
            sources.setdefault(row["source_url"], dict(name=row["source_name"], url=row["source_url"], kinds=set()))[
                "kinds"
            ].add(row["kind"])
    for item in facts["calendar"]:
        sources.setdefault(item["source_url"], dict(name=item["source_name"], url=item["source_url"], kinds=set()))[
            "kinds"
        ].add("scheduled")
    view = dict(
        css=stylesheet(),
        edition=edition,
        days=days,
        yields=yields,
        real=real,
        spreads=spread_view,
        move=move,
        nominal_chart=nominal_chart,
        since=since_chart(series),
        equity=equity,
        cross=week_rows(CROSS),
        levels=[
            catalog[i]
            for i in ("wti-settle", "brent-settle", "gold-fut", "dxy", "usdjpy", "eurusd", "vix", "spy-realized-20")
        ],
        intl=[
            catalog[i]
            for i in (
                "intl-uk-10y",
                "intl-fr-10y",
                "intl-it-10y",
                "intl-de-10y",
                "intl-jp-10y",
                "intl-ca-10y",
                "intl-uk-30y",
                "intl-jp-30y",
            )
        ],
        us10=catalog["treasury-10y"],
        us30=catalog["treasury-30y"],
        auctions=[catalog[f"auction-{t}"] for t in ("3y", "10y", "30y")],
        evidence_groups=[dict(label=k, rows=v) for k, v in groups.items()],
        evidence_count=len(rows),
        sources=sorted(
            ({**s, "kinds": ", ".join(sorted(s["kinds"]))} for s in sources.values()), key=lambda s: s["name"]
        ),
        not_collected=facts["not_collected"],
        snapshot=facts["snapshot"],
        collected_utc=facts["collected_utc"],
        built_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    env = Environment(
        loader=FileSystemLoader(str(Path(__file__).parent)), autoescape=True, trim_blocks=True, lstrip_blocks=True
    )
    html = env.get_template("sunday.html.j2").render(**view)
    (folder / "index.html").write_text(html)
    public = [{k: v for k, v in row.items() if k not in ("display", "direction", "when")} for row in rows]
    (folder / "evidence.json").write_text(
        json.dumps(
            dict(
                snapshot=facts["snapshot"],
                collected_utc=facts["collected_utc"],
                rows=public,
                calendar=facts["calendar"],
                not_collected=facts["not_collected"],
            ),
            indent=1,
            ensure_ascii=False,
        )
    )
    return folder / "index.html"


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "2026-10-11"))
