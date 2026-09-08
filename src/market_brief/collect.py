"""GET-only official sources. No account discovery, arbitrary crawling, or trading routes."""

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from .evidence import ET as EASTERN
from .evidence import timestamp

TREASURY = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
BLS = "https://www.bls.gov/schedule/news_release/bls.ics"
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


class SourceError(ValueError):
    pass


class SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urlsplit(req.full_url), urlsplit(newurl)
        if new.scheme != "https" or new.hostname != old.hostname:
            raise SourceError("cross-host redirect rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch(url, deadline):
    if urlsplit(url).hostname not in HOSTS or urlsplit(url).scheme != "https":
        raise SourceError("source outside allowlist")
    opener = build_opener(SameHostRedirect())
    for attempt in range(2):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SourceError("collection deadline exceeded")
        try:
            request = Request(url, headers={"User-Agent": "MarketBrief/0.1 personal research"})
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


def treasury_rows(text, now, retrieved):
    rows = []
    for entry in xml_root(text).findall("{*}entry"):
        fields = {e.tag.split("}")[-1]: e.text for e in entry.iter()}
        day = (fields.get("NEW_DATE") or "")[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) and day < now.astimezone(EASTERN).date().isoformat():
            rows.append((day, fields))
    if not rows:
        raise SourceError("no prior daily yield observation")
    rows.sort(key=lambda x: x[0])
    day, fields = rows[-1]
    result = []
    for term in (2, 5, 10):
        raw = fields.get(f"BC_{term}YEAR")
        value = float(raw) if raw else None
        result.append(dict(id=f"treasury-{term}y", topic=f"US {term}Y", metric="daily par yield",
            value=value, unit="% yield", baseline="daily Treasury par curve, not an intraday quote",
            frequency="daily", observed_at=day, retrieved_at=retrieved.isoformat(),
            source_id="treasury", status="BACKGROUND", reason=""))
        if len(rows) >= 2 and raw and rows[-2][1].get(f"BC_{term}YEAR"):
            prior = float(rows[-2][1][f"BC_{term}YEAR"])
            result.append(dict(id=f"treasury-{term}y-change", topic=f"US {term}Y",
                metric="daily yield change", value=(value-prior)*100, unit="bp",
                baseline=f"daily observation {rows[-2][0]}", frequency="daily", observed_at=day,
                retrieved_at=retrieved.isoformat(), source_id="treasury", status="BACKGROUND",
                reason=""))
    return result


def calendar_events(text, now, retrieved):
    if "BEGIN:VCALENDAR" not in text or "END:VCALENDAR" not in text:
        raise SourceError("malformed calendar")
    text = re.sub(r"\r?\n[ \t]", "", text)
    events, dates = [], []
    for block in text.split("BEGIN:VEVENT")[1:]:
        if "END:VEVENT" not in block or "RRULE:" in block:
            raise SourceError("incomplete or recurring calendar unsupported")
        fields = dict(line.split(":", 1) for line in block.split("END:VEVENT")[0].splitlines()
                      if ":" in line)
        start_key = next((k for k in fields if k.startswith("DTSTART")), None)
        if not start_key or "SUMMARY" not in fields:
            raise SourceError("calendar event lacks time/title")
        value = fields[start_key]
        zone = timezone.utc if value.endswith("Z") else ZoneInfo(
            start_key.split("TZID=")[-1] if "TZID=" in start_key else "America/New_York")
        when = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S").replace(tzinfo=zone)
        dates.append(when.astimezone(EASTERN).date())
        if dates[-1] != now.astimezone(EASTERN).date():
            continue
        events.append(dict(id=f"bls-event-{len(events)}", title=fields["SUMMARY"].replace("\\,", ","),
            source_id="bls", published_at=None, checked_at=retrieved.isoformat(),
            scheduled_at=when.astimezone(timezone.utc).isoformat(), status="SCHEDULED"))
    today = now.astimezone(EASTERN).date()
    if not dates or not min(dates) <= today <= max(dates):
        raise SourceError("calendar does not establish coverage for target date")
    return events


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
    jobs = [
        ("treasury", "US Treasury", "economic_series", TREASURY,
         f"{TREASURY}?data=daily_treasury_yield_curve&field_tdr_date_value={now.year}"),
        ("bls", "BLS calendar", "calendar", BLS, BLS),
        ("fed", "Federal Reserve releases", "news", FED, FED),
    ]
    for ident, name, kind, url, request_url in jobs:
        record = source(ident, name, kind, url, now)
        try:
            body = fetcher(request_url, deadline)
            retrieved = datetime.now(timezone.utc)
            record["retrieved_at"] = retrieved.isoformat()
            if ident == "treasury":
                raw["observations"].extend(treasury_rows(body, now, retrieved))
            elif ident == "bls":
                raw["events"].extend(calendar_events(body, now, retrieved))
                record["coverage_date"] = now.astimezone(EASTERN).date().isoformat()
            else:
                raw["context_items"].extend(fed_context(body, now))
        except (SourceError, ValueError, ET.ParseError, UnicodeError, OverflowError) as exc:
            reason = str(exc) if isinstance(exc, SourceError) else f"malformed {type(exc).__name__}"
            record.update(status="UNAVAILABLE", reason=reason)
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
