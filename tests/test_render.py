from html.parser import HTMLParser

from test_pipeline import fixture_packet, narrative

from market_brief.render import compact_equity_rows, measure_label, render


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.ids = set()
        self.refs = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script":
            self.scripts.append(values)
        if "id" in values:
            self.ids.add(values["id"])
        if tag == "a" and values.get("href", "").startswith("#"):
            self.refs.append(values["href"][1:])


def test_markdown_html_same_facts_mode_and_literal_halt():
    md, page = render(fixture_packet(), narrative())
    for value in ("FICTIONAL SAMPLE", "HALT", "-4.00 bp", "+1.00 bp", "INTERPRETATION",
                  "WATCH", "OBSERVED", "current pre-market direction unavailable"):
        assert value in md
        assert value.lower() in page.lower()
    parsed = Page()
    parsed.feed(page)
    assert len(parsed.scripts) == 1
    assert set(parsed.refs) <= parsed.ids
    assert "Content-Security-Policy" in page
    assert 'id="theme-choice"' in page
    assert page.index("What to watch") < page.index("Macro &amp; cross-asset")
    assert "direction-positive" in page and "direction-negative" in page
    assert 'class="number direction-neutral">3.86 % yield' in page


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
    assert "LIVE" in header
    assert "Tuesday, Sep 8 · 6:31 AM PT" in header
    assert "Last updated: 5:45 AM PT" in header
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
    assert "commissioning test, not the scheduled pre-market brief" in page
    assert "collection smoke test" not in page
    assert "COMMISSIONING RUN" in md


def test_live_commissioning_has_no_checkpoint_claim():
    packet = fixture_packet()
    packet["run"]["mode"] = "LIVE"
    packet["run"]["checkpoint"] = "COMMISSIONING"
    packet["run"]["commissioning"] = True
    value = narrative()
    value["mode"] = "LIVE"
    _, page = render(packet, value)
    header = page.split("<h1>", 1)[0]
    assert "LIVE COMMISSIONING" in header
    assert "PREMARKET" not in header
    assert "OPEN_1M" not in header
    assert "Collected at 5:45 AM PT" in header


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
    assert measure_label(rows[0]) == "Intraday vs prior close"


def test_render_deemphasizes_provenance_and_epistemic_boilerplate():
    _, page = render(fixture_packet(), narrative())
    assert page.count('class="cite"') < 40
    assert "Uncertainty:" not in page
    assert "Alternative:" not in page
    assert "Generated UTC:" in page
    assert "2026-09-08T" in page
