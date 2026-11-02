"""Quiet provenance: editorial footnotes for the reader, the full deterministic ledger underneath."""

import copy
import re

import pytest
from test_pipeline import fixture_packet, narrative
from test_render import Page

from market_brief.context import analyst_context
from market_brief.continuity import interpretation_record
from market_brief.evidence import evidence_catalog, normalize_observation, timestamp
from market_brief.render import reader_metric_label, render
from market_brief.synthesize import validate_narrative


def qqq_print(packet, observed_at="2026-09-08T12:44:00+00:00", value=0.87):
    now = timestamp(packet["run"]["target_time"])
    row = normalize_observation(dict(
        id="QQQ-intraday", topic="QQQ", metric="intraday return", value=value, unit="%",
        baseline="IEX latest trade versus previous regular close", frequency="intraday",
        observed_at=observed_at, retrieved_at=packet["run"]["target_time"],
        source_id="sample-prices", status="AVAILABLE", reason="fixture print"), now)
    packet["observations"].append(row)
    return row


def test_frozen_interpretation_citation_links_to_its_own_provenance_while_live_row_updates():
    original = fixture_packet()
    qqq_print(original)
    value = narrative()
    value["summary"][0]["evidence_ids"].append("QQQ-intraday")
    validate_narrative(value, original)
    frozen = interpretation_record(original, value)
    snapshot = copy.deepcopy(frozen["evidence"]["QQQ-intraday"])
    assert snapshot["source_id"] == "sample-prices"
    assert snapshot["baseline"] == "IEX latest trade versus previous regular close"

    refreshed = copy.deepcopy(original)
    current = next(row for row in refreshed["observations"] if row["id"] == "QQQ-intraday")
    current.update(value=-0.37, observed_at="2026-09-08T13:14:00+00:00",
                   retrieved_at="2026-09-08T13:15:00+00:00")
    refreshed["run"]["target_time"] = "2026-09-08T13:15:00+00:00"
    markdown, page = render(refreshed, interpretation=frozen)

    assert frozen["evidence"]["QQQ-intraday"] == snapshot
    assert 'href="#interpretation-evidence-QQQ-intraday"' in page
    cited = re.search(
        r'<span class="ref"><a href="#interpretation-evidence-QQQ-intraday">([^<]+)</a>'
        r'<b>([^<]+)</b><small>([^<]+)</small></span>', page)
    assert cited.groups() == ("QQQ · Premarket vs prior close", "+0.87%", "5:44 AM PT")
    frozen_row = page.split('id="interpretation-evidence-QQQ-intraday"', 1)[1].split("</div>", 1)[0]
    live_row = page.split('id="evidence-QQQ-intraday"', 1)[1].split("</div>", 1)[0]
    assert "+0.87%" in frozen_row and "5:44 AM PT" in frozen_row
    assert "IEX latest trade versus previous regular close" in frozen_row
    assert "sample-prices" in frozen_row
    assert "−0.37%" in live_row and "6:14 AM PT" in live_row
    assert "−0.37%" in markdown
    assert "[evidence](#interpretation-evidence-QQQ-intraday)" in markdown
    assert '<a id="interpretation-evidence-QQQ-intraday"></a>' in markdown
    assert '<a id="evidence-QQQ-intraday"></a>' in markdown


def test_carried_watch_citation_links_to_its_creation_time_snapshot():
    original = fixture_packet()
    original["run"]["target_time"] = "2026-09-08T13:00:00+00:00"
    created = qqq_print(original, observed_at="2026-09-08T12:58:00+00:00", value=0.87)
    frozen = interpretation_record(original, narrative())
    frozen["evidence"]["QQQ-intraday"] = copy.deepcopy(created)
    frozen["prior_state"] = dict(status="available", reason="", anchors={}, watches=[dict(
        id="watch-from-premarket", lifecycle="active", evaluability="assessable",
        hypothesis="QQQ holds {{QQQ-intraday}}.", confirmation="QQQ remains near {{QQQ-intraday}}.",
        contradiction="QQQ reverses below {{QQQ-intraday}}.",
        horizon=dict(phrase="Through the close", expires_at="2026-09-08T20:00:00+00:00"),
        evidence_refs=["QQQ-intraday"], values={"QQQ-intraday": copy.deepcopy(created)})])

    refreshed = copy.deepcopy(original)
    current = next(row for row in refreshed["observations"] if row["id"] == "QQQ-intraday")
    current.update(value=-0.37, observed_at="2026-09-08T13:14:00+00:00",
                   retrieved_at="2026-09-08T13:15:00+00:00")
    refreshed["run"]["run_id"] = "sample-hourly-refresh"
    refreshed["run"]["target_time"] = "2026-09-08T13:15:00+00:00"
    _, page = render(refreshed, interpretation=frozen)

    assert 'href="#watch-evidence-watch-from-premarket-QQQ-intraday">QQQ · Premarket vs prior close</a>' in page
    assert '<b>+0.87%</b><small>5:58 AM PT</small>' in page
    snapshot = page.split('id="watch-evidence-watch-from-premarket-QQQ-intraday"', 1)[1].split("</div>", 1)[0]
    assert "+0.87%" in snapshot and "5:58 AM PT" in snapshot
    assert "IEX latest trade versus previous regular close" in snapshot and "sample-prices" in snapshot
    live = page.split('id="evidence-QQQ-intraday"', 1)[1].split("</div>", 1)[0]
    assert "−0.37%" in live and "6:14 AM PT" in live


def test_multiple_carried_watches_get_distinct_creation_time_evidence_destinations():
    packet = fixture_packet()
    now = timestamp(packet["run"]["target_time"])
    current = normalize_observation(dict(
        id="SPY-daily", topic="SPY", metric="daily return", value=0.31, unit="%",
        baseline="IEX latest trade versus previous regular close", frequency="intraday",
        observed_at="2026-09-08T13:14:00+00:00", retrieved_at=packet["run"]["target_time"],
        source_id="current-prices", status="AVAILABLE", reason="later interpretation"), now)
    packet["observations"].append(current)
    current_narrative = narrative()
    current_narrative["watches"] = []
    frozen = interpretation_record(packet, current_narrative)
    frozen["evidence"]["SPY-daily"] = copy.deepcopy(current)

    def creation(value, observed_at, source_id, baseline):
        return dict(id="SPY-daily", topic="SPY", metric="daily return", value=value, unit="%",
                    baseline=baseline, frequency="intraday", observed_at=observed_at,
                    source_id=source_id)

    older = creation(0.06, "2026-09-08T12:58:00+00:00", "opening-prices",
                     "IEX print versus previous regular close")
    later = creation(0.12, "2026-09-08T13:02:00+00:00", "midday-prices",
                     "IEX print versus previous regular close")
    frozen["prior_state"] = dict(status="available", reason="", anchors={}, watches=[
        dict(id="watch-open", lifecycle="active", evaluability="assessable",
             hypothesis="SPY holds {{SPY-daily}}.", confirmation="SPY remains near {{SPY-daily}}.",
             contradiction="SPY falls below {{SPY-daily}}.",
             horizon=dict(phrase="Through the close", expires_at="2026-09-08T20:00:00+00:00"),
             evidence_refs=["SPY-daily"], values={"SPY-daily": older}),
        dict(id="watch-midday", lifecycle="active", evaluability="assessable",
             hypothesis="SPY holds {{SPY-daily}}.", confirmation="SPY remains near {{SPY-daily}}.",
             contradiction="SPY falls below {{SPY-daily}}.",
             horizon=dict(phrase="Through the close", expires_at="2026-09-08T20:00:00+00:00"),
             evidence_refs=["SPY-daily"], values={"SPY-daily": later}),
        dict(id="watch-legacy", lifecycle="active", evaluability="assessable",
             hypothesis="Legacy SPY watch.", confirmation="", contradiction="",
             horizon=dict(phrase="Through the close", expires_at="2026-09-08T20:00:00+00:00"),
             evidence_refs=["SPY-daily"], values={})])
    refreshed = copy.deepcopy(packet)
    refreshed["run"]["run_id"] = "later-session-refresh"
    refreshed["run"]["target_time"] = "2026-09-08T13:15:00+00:00"
    markdown, page = render(refreshed, interpretation=frozen)

    for watch_id, value, clock, source in (
            ("watch-open", "+0.06%", "5:58 AM PT", "opening-prices"),
            ("watch-midday", "+0.12%", "6:02 AM PT", "midday-prices")):
        anchor = f"watch-evidence-{watch_id}-SPY-daily"
        assert f'href="#{anchor}">SPY · Daily return</a><b>{value}</b><small>{clock}</small>' in page
        assert f'id="{anchor}"' in page
        row = page.split(f'id="{anchor}"', 1)[1].split("</div>", 1)[0]
        assert value in row and clock in row and source in row
        assert f"[evidence](#{anchor})" in markdown
        assert f'<a id="{anchor}"></a>' in markdown
    assert "IEX print versus previous regular close" in page
    assert page.count('id="watch-evidence-') == 2
    assert 'href="#watch-evidence-watch-legacy-SPY-daily"' not in page
    assert "[evidence](#watch-evidence-watch-legacy-SPY-daily)" not in markdown


def test_unsupported_missing_and_stale_citations_remain_fail_closed():
    packet = fixture_packet()
    qqq_print(packet)
    value = narrative()
    value["summary"][0]["evidence_ids"] = ["invented:QQQ-intraday"]
    with pytest.raises(ValueError, match="unknown, unavailable, or unsupplied evidence reference"):
        validate_narrative(value, packet)

    # OPEN_30M's `latest:` record was present as a continuity comparison snapshot, but a summary is
    # current-condition prose and cannot cite that prior-session namespace.
    context = analyst_context(packet)
    context.update(prior_state=dict(status="available", snapshots=[dict(
        ref="latest:QQQ-intraday", topic="QQQ", metric="intraday return", value=0.5,
        unit="%", observed_at="2026-09-08T12:30:00+00:00")]),
        comparisons=[dict(id="cmp-latest-QQQ-intraday", status="changed",
                          prior_ref="latest:QQQ-intraday", current_ref="QQQ-intraday")])
    value["summary"][0]["evidence_ids"] = ["QQQ-intraday", "latest:QQQ-intraday"]
    with pytest.raises(ValueError, match="unknown, unavailable, or unsupplied evidence reference"):
        validate_narrative(value, packet, context)

    value["summary"][0]["evidence_ids"] = ["QQQ-intraday"]
    missing = fixture_packet()
    with pytest.raises(ValueError, match="unknown, unavailable, or unsupplied evidence reference"):
        validate_narrative(value, missing)

    stale = fixture_packet()
    qqq_print(stale, observed_at="2026-09-08T10:00:00+00:00")
    with pytest.raises(ValueError, match="unknown, unavailable, or unsupplied evidence reference"):
        validate_narrative(value, stale)


def bls_failure(packet):
    packet["sources"].append(dict(id="bls-live", name="BLS calendar", kind="calendar", url="https://www.bls.gov/schedule/",
                                  retrieved_at="2026-09-11T13:10:50.978377+00:00", status="UNAVAILABLE",
                                  reason="HTTP 403", llm_allowed=True, retention_allowed=True))
    return packet


def drawer(page, summary_start):
    """The HTML of one bottom drawer, located by the start of its summary text."""
    start = page.index("<summary>" + summary_start)
    following = page.find('<details class="drawer"', start)
    end = page.index("</section>", start) if following < 0 else following
    return page[page.rindex('<details class="drawer"', 0, start):end]


def test_every_admitted_row_keeps_its_anchor_and_every_narrative_reference_resolves():
    packet, value = fixture_packet(), narrative()
    _, page = render(packet, value)
    parsed = Page()
    parsed.feed(page)
    catalog = evidence_catalog(packet)
    assert {f"evidence-{i}" for i in catalog} <= parsed.ids
    cited = set(value["banner"]["evidence_ids"]) | set(value["character"]["evidence_ids"])
    for record in (*value["summary"], *value["watches"], *value["attention"],
                   *(p for key in value["sections"] for p in value["sections"][key])):
        cited |= set(record.get("evidence_ids", []))
    assert {f"evidence-{i}" for i in cited if i in catalog} <= parsed.ids
    assert set(parsed.refs) <= parsed.ids
    assert 'data-session-date="2026-09-08"' in page and 'data-checkpoint="PREMARKET"' in page
    assert "Content-Security-Policy" in page and page.count("<script>") == 1


def test_evidence_ids_are_anchors_not_reader_labels():
    packet = fixture_packet()
    _, page = render(packet, narrative())
    ledger = drawer(page, "Evidence ledger")
    for row_id in ("SPY-daily", "SPY-r20", "SPY-sma50", "SPY-dma50"):
        assert f'id="evidence-{row_id}"' in ledger
        assert f"<b>{row_id}</b>" not in ledger  # the ID is no longer the dominant label
    assert "Daily return" in ledger and "20-session return" in ledger and "vs 50DMA" in ledger
    assert "observed / published" not in ledger and "source sample-prices" not in ledger
    # Bare IDs stay out of the reader-facing marker text too.
    markers = re.findall(r'<details class="cite">(.*?)</details>', page, re.S)
    assert markers and not any(re.search(r">\s*[A-Z]+-(daily|r20|sma50|dma50|intraday)\b", m) for m in markers)


def test_reader_metric_labels_come_from_metric_metadata():
    assert reader_metric_label(dict(metric="daily return")) == "Daily return"
    assert reader_metric_label(dict(metric="twenty-session return")) == "20-session return"
    assert reader_metric_label(dict(metric="fifty-session average")) == "50-session average"
    assert reader_metric_label(dict(metric="distance from 50DMA")) == "vs 50DMA"
    assert reader_metric_label(dict(metric="relative to SPY")) == "vs SPY · 20 sessions"
    # A current print's label comes from its own clock against the session: without either it is only the
    # latest trade; the metric string (`premarket return`) is identity and never the label.
    assert reader_metric_label(dict(metric="premarket return", frequency="intraday")) == "Latest trade vs prior close"
    session = dict(open="2026-09-08T13:30:00+00:00", close="2026-09-08T20:00:00+00:00")
    before_open = dict(metric="premarket return", frequency="intraday", observed_at="2026-09-08T12:58:00+00:00")
    assert reader_metric_label(before_open, session) == "Premarket vs prior close"
    assert reader_metric_label(dict(before_open, observed_at="2026-09-08T14:00:00+00:00"), session) \
        == "Intraday vs prior close"
    assert reader_metric_label(dict(metric="daily par yield")) == "Daily par yield"
    assert reader_metric_label(dict(metric="daily yield change")) == "Daily change"


def test_each_instrument_group_is_independently_disclosed():
    packet = fixture_packet()
    _, page = render(packet, narrative())
    ledger = drawer(page, "Evidence ledger")
    groups = [re.sub(r"<[^>]+>", "", g)
              for g in re.findall(r'<details class="ledger-group"[^>]*><summary>(.*?)</summary>', ledger)]
    topics = {r.get("topic") or r.get("title") or r["id"] for r in evidence_catalog(packet).values()}
    assert len(groups) == len(topics)
    assert any(re.match(r"SPY · \d+ observations", g) for g in groups)
    assert not any("open" in tag for tag in re.findall(r'<details class="ledger-group"[^>]*>', ledger))
    # A ledger row lives inside exactly one instrument group.
    for group_html in re.findall(r'<details class="ledger-group".*?</details>', ledger, re.S):
        assert len(re.findall(r'class="ledger(?: item)?"', group_html)) >= 1  # a headline row is `ledger item`


def test_table_provenance_is_a_local_summary_not_a_dump():
    packet = fixture_packet()
    md, page = render(packet, narrative())
    assert "Every cell is a ledger row" not in page and "Every cell is a ledger row" not in md
    assert "open the evidence ledger" not in page
    tables = page.count("<table")
    assert page.count('<details class="cite proof">') == tables
    proofs = re.findall(r'<details class="cite proof">(.*?)</details>', page, re.S)
    for proof in proofs:
        assert "View exact values" in proof
        assert 'href="#evidence-' in proof  # every listed value links to its ledger row
        assert 'href="#evidence-ledger"' in proof  # the global ledger is still one step away
    assert "Vs SPY" not in page


def test_marker_shows_local_proof_with_reader_labels_values_and_freshness():
    packet = fixture_packet()
    _, page = render(packet, narrative())
    marker = re.search(r'<details class="cite">(.*?)</details>', page, re.S).group(1)
    rows = re.findall(r'<span class="ref">(.*?)</span>', marker, re.S)
    assert rows, marker
    for row in rows:
        assert re.search(r"<a href=\"#(?:interpretation-)?evidence-[^\"]+\">[^<]+</a>", row) or "<i>" in row
    assert "<b>" in marker  # the exact number sits with each line
    assert "Full evidence ledger" in marker


def test_source_failures_stay_visible_while_retrieval_plumbing_moves_down():
    packet = bls_failure(fixture_packet())
    _, page = render(packet, narrative())
    sources = drawer(page, "Sources ·")
    assert "BLS calendar" in sources and "Unavailable" in sources and "HTTP 403" in sources
    assert "Retrieved 2026-" not in sources and "+00:00" not in sources
    assert 'class="source-status unavailable"' in sources
    technical = page.split("Technical details", 1)[1]
    assert "bls-live" in technical and "2026-09-11T13:10:50" in technical
    summary = re.search(r"<summary>Sources · .*?</summary>", page).group(0)
    assert "1 unavailable" in summary


def test_technical_details_keep_deep_operational_metadata():
    packet = fixture_packet()
    _, page = render(packet, narrative())
    technical = drawer(page, "Technical details")
    for item in ("Generated UTC:", "Evidence cutoff UTC:", "Checkpoint:", "Bootstrap:", "Calendar:",
                 "Continuity:", "Sources retrieved:"):
        assert item in technical, item
    assert "PREMARKET" in technical
