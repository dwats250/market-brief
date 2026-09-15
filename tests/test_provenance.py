"""Quiet provenance: editorial footnotes for the reader, the full deterministic ledger underneath."""

import re

from test_pipeline import fixture_packet, narrative
from test_render import Page

from market_brief.evidence import evidence_catalog
from market_brief.render import reader_metric_label, render


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
    assert reader_metric_label(dict(metric="premarket return", frequency="intraday")) == "Premarket vs prior close"
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
        assert group_html.count('class="ledger"') >= 1


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
        assert re.search(r"<a href=\"#evidence-[^\"]+\">[^<]+</a>", row) or "<i>" in row
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
