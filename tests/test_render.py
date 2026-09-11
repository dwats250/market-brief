from html.parser import HTMLParser

from test_pipeline import fixture_packet, narrative

from market_brief.render import compact_equity_rows, measure_label, render


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.ids = set()
        self.refs = []
        self.hidden = 0
        self.visible = []  # text outside details, style and script: what a reader sees at rest

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script":
            self.scripts.append(values)
        if "id" in values:
            self.ids.add(values["id"])
        if tag == "a" and values.get("href", "").startswith("#"):
            self.refs.append(values["href"][1:])
        if tag in ("details", "style", "script"):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ("details", "style", "script"):
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.visible.append(data)


def test_markdown_html_same_facts_mode_and_literal_halt():
    md, page = render(fixture_packet(), narrative())
    for value in ("FICTIONAL SAMPLE", "HALT", "-4.00 bp", "+1.00 bp", "INTERPRETATION",
                  "WATCH", "OBSERVED", "current prints unavailable"):
        assert value in md
        assert value.lower() in page.lower()
    parsed = Page()
    parsed.feed(page)
    assert len(parsed.scripts) == 1
    assert set(parsed.refs) <= parsed.ids
    assert "Content-Security-Policy" in page
    assert 'id="theme-choice"' in page
    assert page.index("What matters next") < page.index("Equity structure") < page.index("Macro &amp; rates")
    assert "direction-positive" in page and "direction-negative" in page
    assert 'class="number primary direction-neutral">3.86 % yield' in page


def test_model_html_is_escaped_in_both_formats():
    value = narrative()
    value["banner"]["title"] = '<script>alert("bad")</script>'
    md, page = render(fixture_packet(), value)
    assert '<script>alert("bad")</script>' not in page and '<script>alert("bad")</script>' not in md
    assert "&lt;script&gt;" in page


def test_render_deterministic_for_identical_records():
    assert render(fixture_packet(), narrative()) == render(fixture_packet(), narrative())


def test_live_header_uses_pacific_time_and_hides_plumbing():
    packet = fixture_packet()
    packet["run"]["mode"] = "LIVE"
    packet["run"]["checkpoint"] = "OPEN_1M"
    packet["run"]["session"]["scheduled_checkpoint_at"] = "2026-09-08T13:31:00+00:00"
    packet["run"]["session"]["meaningful_premarket"] = True
    value = narrative()
    value["mode"] = "LIVE"
    _, page = render(packet, value)
    header = page.split("<h1>", 1)[0]
    assert "LIVE · Open +1M edition · Tuesday, Sep 8 · as of 5:45 AM PT" in header
    assert header.count("PT") == 1  # one status/date/as-of line
    assert "Evidence cutoff" not in header
    assert "Generated 2026-" not in header
    assert "+00:00" not in header


def test_last_good_status_is_explicit():
    packet = fixture_packet()
    packet["run"]["display_status"] = "LAST GOOD BRIEF"
    _, page = render(packet, narrative())
    assert "LAST GOOD BRIEF" in page.split("<h1>", 1)[0]


def test_sample_commissioning_status_is_plain_language():
    packet = fixture_packet()
    packet["run"]["mode"] = "LIVE"
    packet["run"]["session"]["meaningful_premarket"] = False
    value = narrative()
    value["mode"] = "LIVE"
    md, page = render(packet, value)
    header = page.split("<h1>", 1)[0]
    assert "SAMPLE" in header and "COMMISSIONING RUN" in header
    assert "commissioning test, not the scheduled brief" in page
    assert page.count("COMMISSIONING RUN") == 1
    assert "collection smoke test" not in page
    assert "COMMISSIONING RUN" in md


def test_compact_equity_rows_use_current_observation_and_human_labels():
    rows = [
        dict(id="xle-intraday", topic="XLE", metric="premarket return", value=0.55,
             unit="%", frequency="intraday", status="AVAILABLE",
             observed_at="2026-09-08T17:30:00+00:00"),
        dict(id="xle-r20", topic="XLE", metric="twenty-session return", value=11.45,
             unit="%", frequency="daily", status="BACKGROUND", observed_at="2026-09-04"),
        dict(id="xle-spread", topic="XLE", metric="relative to SPY", value=11.83,
             unit="pp", frequency="daily", status="BACKGROUND", observed_at="2026-09-04"),
        dict(id="xle-sma", topic="XLE", metric="fifty-session average", value=59.20,
             unit="USD", frequency="daily", status="BACKGROUND", observed_at="2026-09-04"),
    ]
    row = compact_equity_rows(rows, ["XLE"])[0]
    assert row["label"] == "Energy"
    assert row["today"]["display"] == "+0.55 %"
    assert row["today"]["observed"] == "Tuesday, Sep 8 · 10:30 AM PT"
    assert measure_label(rows[0]) == "XLE · Intraday vs prior close"


def test_render_deemphasizes_provenance_and_epistemic_boilerplate():
    _, page = render(fixture_packet(), narrative())
    assert page.count('class="cite"') < 40
    assert "Uncertainty:" not in page
    assert "Alternative:" not in page
    assert "Generated UTC:" in page
    assert "2026-09-08T" in page


def test_citations_are_quiet_markers_and_the_evidence_chain_is_intact():
    """One closed marker per analytical block, no per-row link words, every cited ID anchored."""
    packet, value = fixture_packet(), narrative()
    _, page = render(packet, value)
    parsed = Page()
    parsed.feed(page)
    assert '<a class="cite"' not in page
    body = page.split("<body>", 1)[1].split('<details class="drawer"', 1)[0]
    assert body.count('<details class="cite">') >= 1 + len(value["summary"]) + len(value["watches"])
    visible = " ".join(parsed.visible)
    assert "Every cell is a ledger row" not in visible and "open the evidence ledger" not in visible
    assert ">evidence</a>" not in page  # the per-row and per-paragraph link word is gone
    assert page.count('<details class="cite proof">') == page.count("<table")  # one local proof per table
    cited = set(value["banner"]["evidence_ids"]) | set(value["character"]["evidence_ids"])
    for record in (*value["summary"], *value["watches"], *value["attention"],
                   *(p for key in value["sections"] for p in value["sections"][key])):
        cited |= set(record.get("evidence_ids", []))
    from market_brief.evidence import evidence_catalog
    catalog = evidence_catalog(packet)
    assert {f"evidence-{i}" for i in cited if i in catalog} <= parsed.ids
    assert {f"evidence-{i}" for i in catalog} <= parsed.ids  # every admitted usable row keeps its anchor
    assert "evidence-ledger" in parsed.ids and set(parsed.refs) <= parsed.ids
    assert 'data-session-date="2026-09-08"' in page and 'data-checkpoint="PREMARKET"' in page


def test_top_of_page_and_continuity_copy_read_as_product():
    packet, value = fixture_packet(), narrative()
    _, page = render(packet, value)
    top = page.split("<h1>", 1)[1].split("<section", 1)[0]
    assert '<span class="pill">' in top and value["character"]["text"][:30] in top
    assert value["banner"]["limitation"][:30] not in top  # ordinary caveat lives with the basis line
    assert page.count('<div class="figure">') == 3
    assert "premarket: absent" not in page.split('<details class="drawer"', 1)[0]
    assert "baseline read" in page
