from html.parser import HTMLParser

from test_pipeline import fixture_packet, narrative

from market_brief.render import render


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
    assert not parsed.scripts
    assert set(parsed.refs) <= parsed.ids
    assert "Content-Security-Policy" in page


def test_model_html_is_escaped_in_both_formats():
    value = narrative()
    value["banner"]["title"] = '<script>alert("bad")</script>'
    md, page = render(fixture_packet(), value)
    assert "<script>" not in page and "<script>" not in md
    assert "&lt;script&gt;" in page


def test_render_deterministic_for_identical_records():
    assert render(fixture_packet(), narrative()) == render(fixture_packet(), narrative())
