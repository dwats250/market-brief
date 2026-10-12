"""A checkpoint that was collected and accepted but never published stays the analytical anchor, and the page says
readers never saw it.

2026-10-12: the 6:32 AM PT OPEN_1M refresh was accepted and its continuity uploaded, then GitHub refused the push.
The 7:02 AM PT page compared against it correctly, but called it "the 6:32 AM PT refresh", a page no reader saw.
"""

import json

import pytest
from test_cadence import TUE, Day

from market_brief import cli
from market_brief.render import presentation, since_caption


@pytest.fixture
def day(monkeypatch, tmp_path):
    return Day(monkeypatch, tmp_path)


def publish(day, checkpoint):
    """What a successful push leaves in the next run's checkout: that run's page as main's publish/index.html."""
    (day.root / "publish").mkdir(exist_ok=True)
    (day.root / "publish/index.html").write_text(day.page(checkpoint))


def test_an_unpublished_refresh_is_still_the_anchor_and_reads_unpublished_all_day(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    publish(day, "PREMARKET")
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 0  # accepted; its push is refused, so main keeps PREMARKET
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0
    opening = json.loads((day.folder("OPEN_1M") / "evidence.json").read_text())
    structure = json.loads((day.folder("OPEN_30M") / "evidence.json").read_text())
    # The analytical continuity is unchanged: the unpublished refresh is still the latest anchor and comparison.
    assert structure["continuity"]["anchors"]["latest"]["run_id"] == opening["run"]["run_id"]
    assert structure["continuity"]["unpublished"] == ["latest"]
    context = json.loads((day.folder("OPEN_30M") / "analyst_context.json").read_text())
    assert "unpublished" not in json.dumps(context)  # the paid analyst's context is untouched
    bundle = day.bundle()
    view = presentation(structure, interpretation=bundle["interpretation"])
    assert view["since"]["label"] == "vs premarket and the unpublished 6:31 AM PT refresh"
    publish(day, "OPEN_30M")
    # Frozen with the interpretation, so a later refresh under it says the same.
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
    later = json.loads((day.folder("HOURLY_1300") / "evidence.json").read_text())
    view = presentation(later, interpretation=day.bundle()["interpretation"])
    assert view["since"]["label"] == ("vs premarket and the unpublished 6:31 AM PT refresh"
                                      " · through the 7:01 AM PT analysis")


def test_a_published_refresh_keeps_the_plain_wording(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    publish(day, "PREMARKET")
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 0
    publish(day, "OPEN_1M")
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0
    structure = json.loads((day.folder("OPEN_30M") / "evidence.json").read_text())
    assert structure["continuity"]["unpublished"] == []
    view = presentation(structure, interpretation=day.bundle()["interpretation"])
    assert view["since"]["label"] == "vs premarket and the 6:31 AM PT refresh"


@pytest.mark.parametrize("page,expected", [
    ('<html lang="en" data-evidence-cutoff="2026-09-08T13:00:00+00:00">', ["premarket", "latest"]),
    ('<html lang="en" data-evidence-cutoff="2026-09-08T13:31:00+00:00">', []),   # the anchor's own page
    ('<html lang="en" data-checkpoint="PREMARKET">', []),                        # a page from before the attribute
    ("", []),                                                                    # no page in the checkout
    ('<html lang="en" data-evidence-cutoff="not a time">', []),
])
def test_only_a_page_older_than_the_anchor_marks_it_unpublished(page, expected):
    anchors = dict(premarket=dict(evidence_cutoff="2026-09-08T13:00:05+00:00"),
                   latest=dict(evidence_cutoff="2026-09-08T13:31:00+00:00"))
    assert cli.unpublished_anchors(anchors, page) == expected
    assert cli.unpublished_anchors(dict(previous_close=dict(evidence_cutoff="2026-09-07T20:01:00+00:00")),
                                   page) == []


def test_the_caption_names_an_unpublished_premarket_too():
    anchors = dict(premarket=dict(evidence_cutoff="2026-09-08T13:00:05+00:00", checkpoint="PREMARKET"),
                   latest=dict(evidence_cutoff="2026-09-08T13:31:00+00:00", checkpoint="OPEN_1M"))
    assert since_caption(anchors, ["premarket"]) == "vs the unpublished premarket and the 6:31 AM PT refresh"
    assert since_caption(anchors) == "vs premarket and the 6:31 AM PT refresh"
