"""Presentation truth: every renderer-owned label says what the datum is, which part of the session it
came from, and whether the words around it are this run's deterministic state or an earlier analysis.

The metric strings, evidence IDs, identities and comparisons underneath never change; only the reader
vocabulary the renderer puts on them does.
"""

import copy
import json
import re
import types

import pytest
from test_cadence import TUE, Day
from test_history_admission import packet_at, utc
from test_pipeline import fixture_packet, narrative

from market_brief import cli
from market_brief.continuity import interpretation_record
from market_brief.evidence import ROOT, evidence_catalog, finalize_coverage, normalize_observation
from market_brief.render import (
    change_column,
    compact_equity_rows,
    current_print_label,
    measure_label,
    metric_label,
    presentation,
    reader_metric_label,
    render,
)

PREMARKET_LABEL = "Premarket vs prior close"
INTRADAY_LABEL = "Intraday vs prior close"
NEAR_CLOSE_LABEL = "Near-close vs prior close"
AFTER_HOURS_LABEL = "After-hours vs prior close"
LATEST_LABEL = "Latest trade vs prior close"
PHASE_LABELS = (PREMARKET_LABEL, INTRADAY_LABEL, NEAR_CLOSE_LABEL, AFTER_HOURS_LABEL, LATEST_LABEL)
# Retired or never-emitted current-print wording. `Close vs prior close` is matched as a whole label so that
# `Near-close vs prior close` does not count.
RETIRED = ("Session-ending print vs prior close", "intraday vs prior close", "premarket vs prior close")
OFFICIAL_CLOSE = re.compile(r"(?<![\w-])[Cc]lose vs prior close")
PRINTS = (("SPY", -0.53), ("QQQ", -0.61), ("XLI", 0.40), ("GLD", -0.20))
SESSION = dict(open=f"{TUE}T13:30:00+00:00", close=f"{TUE}T20:00:00+00:00")


def with_prints(now, checkpoint, observed_at, prints=PRINTS):
    """The fixture packet at `now`, with current prints admitted through the real normalizer."""
    clock = utc(now)
    near_close = utc(SESSION["close"]) if checkpoint == "CLOSE_1M" else None
    packet = packet_at(clock, checkpoint=checkpoint, intraday=False)
    for symbol, value in prints:
        raw = dict(id=f"{symbol}-intraday", topic=symbol, metric="premarket return", value=value, unit="%",
                   baseline="latest trade versus previous regular close", frequency="intraday",
                   observed_at=observed_at, retrieved_at=clock.isoformat(), source_id="sample-prices",
                   status="AVAILABLE", reason="")
        row = normalize_observation(raw, clock, near_close)
        row["expected_freshness"] = "LIVE"
        packet["observations"].append(row)
    return finalize_coverage(packet)


def citing_prints(value=None):
    """The fixture narrative with its first read paragraph also citing SPY's and XLI's current prints."""
    value = copy.deepcopy(value or narrative())
    value["summary"][0]["evidence_ids"] = [*value["summary"][0]["evidence_ids"], "SPY-intraday", "XLI-intraday"]
    return value


def section(page, heading):
    return page.split(f"<h2>{heading}</h2>", 1)[1].split("</section>", 1)[0]


def surfaces(packet, value=None):
    """Every reader-facing label a current print receives, by path: § markers, table proofs, evidence
    ledger, snapshot figures (HTML) and chips (Markdown), the measure column, and the table captions."""
    value = citing_prints(value)
    view = presentation(packet, value)
    md, page = render(packet, value)
    session = packet["run"]["session"]
    prints = [row for row in packet["observations"] if row.get("frequency") == "intraday"]
    marker = [r["label"] for p in view["summary"] for r in p["refs"] if r["id"].endswith("-intraday")]
    proofs = [r["label"] for key in ("sectors", "cross_asset") for r in view[key]["proof"]
              if r["id"].endswith("-intraday")]
    ledger = [r["metric_label"] for r in view["evidence"] if r["id"].endswith("-intraday")]
    figures = [f["metric_label"] for f in view["chips"] if f["id"].endswith("-intraday")]
    measures = [measure_label(row, session) for row in prints]
    captions = [view["sectors"]["change_label"], view["cross_asset"]["change_label"]]
    html_figures = re.findall(r'<div class="figure"><span class="eyebrow">[^<]*? · ([^<]*)</span>', page)
    # The two tables that hold current prints here: sectors (XLI) and metals (GLD).
    html_captions = re.findall(r'<div class="caption">(?:[^<]*strongest to weakest|Metals structure)'
                               r'<span>([^<]*)</span>', page)
    md_chips = [line.split(" | ", 1)[0].split(" · ", 1)[1] for line in md.splitlines()
                if re.match(r"\| (SPY|QQQ|XLI|GLD) · ", line)]
    md_captions = [line for line in md.splitlines() if "strongest to weakest" in line or "METALS STRUCTURE" in line]
    return dict(marker=marker, proofs=proofs, ledger=ledger, figures=figures, measures=measures,
                captions=captions, html_figures=html_figures, html_captions=html_captions, md_chips=md_chips,
                md_captions=md_captions, page=page, md=md, view=view)


def assert_every_path_reads(found, label):
    for path in ("marker", "proofs", "ledger", "figures", "measures", "html_figures", "md_chips"):
        assert found[path], path
        assert all(item.endswith(label) for item in found[path]), (path, found[path])
    for path in ("captions", "html_captions", "md_captions"):
        assert found[path] and all(label in item for item in found[path]), (path, found[path])
    for other in PHASE_LABELS:
        if other != label:
            for path in ("marker", "proofs", "ledger", "figures", "measures", "captions", "html_figures",
                         "html_captions", "md_chips", "md_captions"):
                assert not any(other in item for item in found[path]), (path, other)
            assert other not in found["page"] and other not in found["md"], other
    for retired in RETIRED:
        assert retired not in found["page"] and retired not in found["md"], retired
    assert not OFFICIAL_CLOSE.search(found["page"]) and not OFFICIAL_CLOSE.search(found["md"])


# --- A, B, C, D1: one helper, every path, the phase each row was observed in ---------------------------

def test_a_a_print_before_the_open_reads_premarket_on_every_path():
    packet = with_prints(f"{TUE}T13:00:00+00:00", "PREMARKET", f"{TUE}T12:58:00+00:00")
    found = surfaces(packet)
    assert_every_path_reads(found, PREMARKET_LABEL)
    assert "Intraday vs prior close" not in found["page"]


def test_b_a_print_during_the_session_reads_intraday_and_keeps_its_identity():
    before = with_prints(f"{TUE}T13:00:00+00:00", "PREMARKET", f"{TUE}T12:58:00+00:00")
    during = with_prints(f"{TUE}T14:01:00+00:00", "OPEN_30M", f"{TUE}T14:00:00+00:00")
    found = surfaces(during)
    assert_every_path_reads(found, INTRADAY_LABEL)
    assert "Premarket vs prior close" not in found["page"]
    for symbol, _ in PRINTS:
        early, late = (next(r for r in p["observations"] if r["id"] == f"{symbol}-intraday") for p in (before, during))
        assert early["metric"] == late["metric"] == "premarket return"  # the internal metric is unchanged
        assert early["identity"] == late["identity"]
        assert early["id"] == late["id"]


def test_c_a_provisional_session_ending_print_reads_near_close():
    packet = with_prints(f"{TUE}T20:38:00+00:00", "CLOSE_1M", f"{TUE}T19:59:58+00:00")
    assert {r["status"] for r in packet["observations"] if r["frequency"] == "intraday"} == {"PROVISIONAL"}
    found = surfaces(packet)
    assert_every_path_reads(found, NEAR_CLOSE_LABEL)
    # The separate small provisional note stays where it was already used.
    figures = found["page"].split('<div class="figures">', 1)[1].split("</div></div>", 1)[0]
    assert "<small>provisional</small>" in figures or "· provisional</small>" in figures
    assert found["view"]["sectors"]["change_label"] == f"{NEAR_CLOSE_LABEL} · provisional"
    assert "not official closing bars" in found["page"]


def test_d1_a_print_after_the_close_reads_after_hours():
    packet = with_prints(f"{TUE}T20:10:00+00:00", "CLOSE_1M", f"{TUE}T20:05:00+00:00")
    assert {r["status"] for r in packet["observations"] if r["frequency"] == "intraday"} == {"DELAYED"}
    assert_every_path_reads(surfaces(packet), AFTER_HOURS_LABEL)


# --- D2, D3, D4: unknown phase, mixed tables, never an official close label ---------------------------

@pytest.mark.parametrize("row,session", [
    (dict(observed_at="2026-09-04T19:59:00+00:00", status="AVAILABLE"), SESSION),  # an earlier session
    # 10:00 PM ET on Monday: the session's own UTC date, but the previous New York day.
    (dict(observed_at=f"{TUE}T02:00:00+00:00", status="AVAILABLE"), SESSION),
    (dict(observed_at="2026-12-08T00:30:00+00:00", status="AVAILABLE"),
     dict(open="2026-12-08T14:30:00+00:00", close="2026-12-08T21:00:00+00:00")),  # the same, in winter
    (dict(status="AVAILABLE"), SESSION),                                           # no clock
    (dict(observed_at=None, status="AVAILABLE"), SESSION),
    (dict(observed_at="not a time", status="AVAILABLE"), SESSION),                 # unparseable
    (dict(observed_at="2026-09-08", status="AVAILABLE"), SESSION),                 # a date, not a trade clock
    (dict(observed_at=f"{TUE}T14:00:00+00:00", status="AVAILABLE"), dict(close=SESSION["close"])),
    (dict(observed_at=f"{TUE}T14:00:00+00:00", status="AVAILABLE"), dict(open=SESSION["open"])),
    (dict(observed_at=f"{TUE}T14:00:00+00:00", status="AVAILABLE"), None),
])
def test_d2_a_print_whose_phase_cannot_be_placed_reads_latest_trade(row, session):
    row = dict(row, id="SPY-intraday", topic="SPY", metric="premarket return", frequency="intraday")
    assert current_print_label(row, session) == LATEST_LABEL
    assert reader_metric_label(row, session) == LATEST_LABEL
    assert metric_label(row, session) == LATEST_LABEL
    assert measure_label(row, session) == f"SPY · {LATEST_LABEL}"


def test_d2_phase_mapping_order_and_bounds():
    row = dict(id="SPY-intraday", topic="SPY", metric="premarket return", frequency="intraday", status="AVAILABLE")
    at = lambda clock, **extra: current_print_label(dict(row, observed_at=clock, **extra), SESSION)  # noqa: E731
    assert at(f"{TUE}T13:29:59+00:00") == PREMARKET_LABEL
    assert at(f"{TUE}T13:30:00+00:00") == INTRADAY_LABEL  # the open itself is inside the session
    assert at(f"{TUE}T20:00:00+00:00") == INTRADAY_LABEL  # so is the close (schedule.session_relation)
    assert at(f"{TUE}T20:00:01+00:00") == AFTER_HOURS_LABEL
    assert at(f"{TUE}T09:05:00-04:00") == PREMARKET_LABEL  # any offset, read on the New York clock
    # PROVISIONAL outranks everything, including a clock from another session.
    assert at(f"{TUE}T19:59:58+00:00", status="PROVISIONAL") == NEAR_CLOSE_LABEL
    assert at("2026-09-04T19:59:58+00:00", status="PROVISIONAL") == NEAR_CLOSE_LABEL
    # An intraday return keeps the same vocabulary; the metric string itself is never shown.
    assert reader_metric_label(dict(row, metric="intraday return", observed_at=f"{TUE}T14:00:00+00:00"),
                               SESSION) == INTRADAY_LABEL
    # Rows outside the current-print family keep their own labels.
    assert reader_metric_label(dict(metric="daily return", frequency="daily"), SESSION) == "Daily return"
    assert metric_label(dict(metric="daily yield change", frequency="daily"), SESSION) == "daily yield change"


def test_d2_a_frozen_row_keeps_the_phase_it_was_observed_in():
    """The interpretation's markers label the rows the analyst saw by their own clocks; the tables label
    this run's rows by theirs. An earlier-session frozen print is a latest trade, never "Intraday"."""
    synthesis = with_prints(f"{TUE}T13:00:00+00:00", "PREMARKET", f"{TUE}T12:58:00+00:00")
    synthesis["run"]["run_id"] = "live-premarket-130000-test"
    value = citing_prints()
    frozen = interpretation_record(synthesis, value)
    refresh = with_prints(f"{TUE}T17:01:00+00:00", "HOURLY_1300", f"{TUE}T17:00:00+00:00")
    refresh["run"]["run_id"] = "live-hourly_1300-170100-test"
    view = presentation(refresh, interpretation=frozen)
    assert view["carried"]
    marker = {r["id"]: r["label"] for r in view["summary"][0]["refs"]}
    assert marker["SPY-intraday"] == f"SPY · {PREMARKET_LABEL}"  # frozen at 5:58 AM PT, before the open
    assert all(r["metric_label"] == INTRADAY_LABEL for r in view["evidence"] if r["id"].endswith("-intraday"))
    assert view["sectors"]["change_label"] == INTRADAY_LABEL
    older = copy.deepcopy(frozen)
    older["evidence"]["SPY-intraday"]["observed_at"] = "2026-09-04T19:30:00+00:00"
    marker = {r["id"]: r["label"] for r in presentation(refresh, interpretation=older)["summary"][0]["refs"]}
    assert marker["SPY-intraday"] == f"SPY · {LATEST_LABEL}"


def markers(html):
    """(label, value, clock) for every proof line in a block of rendered HTML."""
    return re.findall(r'<span class="ref">(?:<a href="#evidence-[^"]+">|<i>)([^<]*)(?:</a>|</i>)<b>([^<]*)</b>'
                      r'<small>([^<]*)</small>', html)


def test_d2_prior_snapshot_refs_are_labeled_by_their_own_clock(day, monkeypatch):
    """Continuity refs (`premarket:`, `latest:`) reach the frozen record without a frequency or status. They
    are still current prints: a premarket anchor reads Premarket, an opening-refresh anchor Intraday, on the
    synthesis that cites them and on every refresh that carries it. The metric string is never the label."""
    real = cli.normalize_packet

    def moved_by_seven(raw, now, mode, checkpoint="PREMARKET"):
        if checkpoint == "OPEN_30M":
            raw = copy.deepcopy(raw)
            next(o for o in raw["observations"] if o["id"] == "SPY-intraday")["value"] = 0.21
        return real(raw, now, mode, checkpoint)
    monkeypatch.setattr(cli, "normalize_packet", moved_by_seven)

    def cite_anchors(live):
        live["changes"] = [
            dict(comparison_id="cmp-premarket-SPY-intraday",
                 text="SPY moved from {{premarket:SPY-intraday}} to {{SPY-intraday}} since the premarket.",
                 evidence_ids=["SPY-intraday", "premarket:SPY-intraday"]),
            dict(comparison_id="cmp-latest-SPY-intraday",
                 text="SPY moved from {{latest:SPY-intraday}} to {{SPY-intraday}} since the opening refresh.",
                 evidence_ids=["SPY-intraday", "latest:SPY-intraday"])]
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET") == 0  # prints at 6:00 AM PT, before the open
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 0    # prints at 6:31 AM PT, inside the session
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", mutate=cite_anchors) == 0
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
    expected = [(f"SPY · {INTRADAY_LABEL}", "+0.21 %", "7:01 AM PT"),
                (f"SPY · {PREMARKET_LABEL}", "-0.53 %", "6:00 AM PT"),
                (f"SPY · {INTRADAY_LABEL}", "+0.21 %", "7:01 AM PT"),
                (f"SPY · {INTRADAY_LABEL}", "-0.53 %", "6:31 AM PT")]
    for checkpoint in ("OPEN_30M", "HOURLY_1300"):
        page = day.page(checkpoint)
        since = page.split('<div class="since">', 1)[1].split("</div>", 1)[0]
        assert markers(since) == expected, checkpoint
        assert "Premarket return" not in page and "premarket return" not in page, checkpoint


def test_d2_a_previous_session_snapshot_reads_latest_trade():
    from test_continuity import PREMARKET_TUE, advance_bundle, empty_bundle, friday_close, run_packet, trimmed

    from market_brief.context import analyst_context, edition_profile
    from market_brief.continuity import admit_prior_state, compare_all, continuity_context
    from market_brief.synthesize import validate_narrative
    closing, handoff = friday_close()
    bundle = advance_bundle(empty_bundle(), closing, handoff, utc("2026-09-04T20:03:00+00:00"))
    packet = run_packet(PREMARKET_TUE, "sample-premarket-124500-tue", intraday_value=-0.53)
    prior = admit_prior_state(bundle, packet)
    comparisons = compare_all(prior, packet)
    profile = edition_profile("PREMARKET")
    context = dict(analyst_context(packet, profile, comparisons, prior),
                   **continuity_context(prior, comparisons, profile))
    carried = context["prior_state"]["watches"][0]
    value = trimmed(narrative(), profile)
    value["watch_updates"] = [dict(carried_id=carried["id"], assessment="unresolved",
                                   reason="No regular-session print yet.",
                                   evidence_ids=["SPY-intraday", "previous_close:SPY-intraday"])]
    validate_narrative(value, packet, context)
    view = presentation(packet, value, context)
    labels = {r["id"]: r["label"] for c in view["next"]["carried"] for r in c["refs"]}
    assert labels["previous_close:SPY-intraday"] == f"SPY · {LATEST_LABEL}"  # Friday's trade, not this session's
    assert labels["SPY-intraday"] == f"SPY · {PREMARKET_LABEL}"
    md, page = render(packet, value, context)
    assert "Premarket return" not in page and "Premarket return" not in md


def test_d2_carried_watch_values_keep_their_own_phase_through_a_provisional_close(day, monkeypatch):
    """A watch's criterion quotes a 6:00 AM PT premarket print. The 7:00 update had no QQQ print, so the
    interpretation carries the watch's own creation value; it reads Premarket at its own clock on the
    10:00 refresh and on a late close whose current prints are PROVISIONAL (never Intraday, never
    Near-close: the current row's clock and status are not the creation value's)."""
    real = cli.normalize_packet

    def without_qqq_at_seven(raw, now, mode, checkpoint="PREMARKET"):
        if checkpoint == "OPEN_30M":
            raw = dict(raw, observations=[o for o in raw["observations"] if o["id"] != "QQQ-intraday"])
        return real(raw, now, mode, checkpoint)
    monkeypatch.setattr(cli, "normalize_packet", without_qqq_at_seven)

    def quote_qqq(live):
        live["watches"][0].update(condition="QQQ holds its {{QQQ-intraday}} premarket move into the session.",
                                  evidence_ids=["QQQ-intraday"], horizon="SESSION")

    def no_reassessment(live):
        live["watch_updates"] = []
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", mutate=quote_qqq) == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", mutate=no_reassessment) == 0
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
    assert day.run(f"{TUE}T20:38:00+00:00", "CLOSE_1M", print_at=f"{TUE}T19:59:58+00:00") == 0
    close = json.loads((day.folder("CLOSE_1M") / "evidence.json").read_text())
    assert evidence_catalog(close)["QQQ-intraday"]["status"] == "PROVISIONAL"
    for checkpoint in ("HOURLY_1300", "CLOSE_1M"):
        page = day.page(checkpoint)
        watch = page.split("QQQ holds its -0.61 % premarket move", 1)[1].split("</details>", 1)[0]
        assert markers(watch) == [(f"QQQ · {PREMARKET_LABEL}", "-0.61 %", "6:00 AM PT")], checkpoint


def test_d3_a_table_whose_prints_span_phases_reads_latest_trade():
    def row(symbol, clock, status="DELAYED"):
        return dict(id=f"{symbol}-intraday", topic=symbol, metric="premarket return", value=0.3, unit="%",
                    baseline="latest trade versus previous regular close", frequency="intraday",
                    observed_at=clock, source_id="sample-prices", status=status)
    mixed = compact_equity_rows([row("XLI", f"{TUE}T13:25:00+00:00"), row("XLE", f"{TUE}T14:00:00+00:00")],
                                ["XLI", "XLE"])
    assert change_column(mixed, session=SESSION)[0] == LATEST_LABEL
    same = compact_equity_rows([row("XLI", f"{TUE}T13:45:00+00:00"), row("XLE", f"{TUE}T14:00:00+00:00")],
                               ["XLI", "XLE"])
    assert change_column(same, session=SESSION)[0] == INTRADAY_LABEL
    closing = compact_equity_rows([row("XLI", f"{TUE}T19:59:00+00:00", "PROVISIONAL"),
                                   row("XLE", f"{TUE}T20:05:00+00:00")], ["XLI", "XLE"])
    assert change_column(closing, session=SESSION)[0] == LATEST_LABEL  # no provisional note on a mixed table
    # Through the whole page: one caption for the table, and each row's own label in the proof lines.
    packet = packet_at(utc(f"{TUE}T14:01:00+00:00"), checkpoint="OPEN_30M", intraday=False)
    packet["observations"] += [row("XLI", f"{TUE}T13:25:00+00:00"), row("XLE", f"{TUE}T14:00:00+00:00")]
    view = presentation(packet, narrative())
    assert view["sectors"]["change_label"] == LATEST_LABEL
    proof = {r["id"]: r["label"] for r in view["sectors"]["proof"]}
    assert proof["XLI-intraday"].endswith(PREMARKET_LABEL) and proof["XLE-intraday"].endswith(INTRADAY_LABEL)
    md, page = render(packet, narrative())
    assert f"strongest to weakest<span>{LATEST_LABEL} · as of 7:00 AM PT" in page
    assert f"strongest to weakest · {LATEST_LABEL} · as of 7:00 AM PT" in md


def test_d4_no_page_or_example_edition_carries_an_official_close_label():
    pages = [render(with_prints(now, checkpoint, clock), citing_prints()) for now, checkpoint, clock in (
        (f"{TUE}T13:00:00+00:00", "PREMARKET", f"{TUE}T12:58:00+00:00"),
        (f"{TUE}T14:01:00+00:00", "OPEN_30M", f"{TUE}T14:00:00+00:00"),
        (f"{TUE}T20:38:00+00:00", "CLOSE_1M", f"{TUE}T19:59:58+00:00"),
        (f"{TUE}T20:10:00+00:00", "CLOSE_1M", f"{TUE}T20:05:00+00:00"))]
    examples = sorted((ROOT / "examples/editions").glob("*.*"))
    assert len(examples) == 10
    for text in [*(part for pair in pages for part in pair), *(path.read_text() for path in examples)]:
        assert not OFFICIAL_CLOSE.search(text)
        for retired in RETIRED:
            assert retired not in text, retired


# --- D, K: presentation only ---------------------------------------------------------------------------

@pytest.mark.parametrize("now,checkpoint,clock", [
    (f"{TUE}T13:00:00+00:00", "PREMARKET", f"{TUE}T12:58:00+00:00"),
    (f"{TUE}T14:01:00+00:00", "OPEN_30M", f"{TUE}T14:00:00+00:00"),
    (f"{TUE}T20:38:00+00:00", "CLOSE_1M", f"{TUE}T19:59:58+00:00"),
    (f"{TUE}T20:10:00+00:00", "CLOSE_1M", f"{TUE}T20:05:00+00:00"),
])
def test_d_rendering_mutates_nothing_it_is_given(now, checkpoint, clock):
    packet, value = with_prints(now, checkpoint, clock), citing_prints()
    packet["run"]["run_id"] = f"live-{checkpoint.lower()}-test"
    interpretation = interpretation_record(packet, value)
    refresh = with_prints(now, checkpoint, clock)
    refresh["run"]["run_id"] = "live-refresh-test"
    context = dict(prior_state=dict(status="cold_start", reason=""))
    before = copy.deepcopy((packet, value, interpretation, refresh, context))
    identities = {r["id"]: (r["metric"], r["identity"]) for r in packet["observations"] + packet["derived"]}
    presentation(packet, value, context)
    render(packet, value, context)
    presentation(refresh, interpretation=interpretation)
    render(refresh, interpretation=interpretation)
    assert (packet, value, interpretation, refresh, context) == before
    assert {r["id"]: (r["metric"], r["identity"]) for r in packet["observations"] + packet["derived"]} == identities
    assert all(r["metric"] == "premarket return" for r in packet["observations"] if r["frequency"] == "intraday")


NON_PRESENTATION = ("evidence.json", "analyst_context.json", "narrative.json", "edition_state.json",
                    "session_handoff.json")


def test_k_the_saved_record_does_not_depend_on_the_renderer(monkeypatch, tmp_path):
    """Run the same production day twice, once with the real renderer and once with a renderer that
    writes nothing of substance. Every non-presentation artifact is byte-identical; metadata differs only
    in the page hashes."""
    def day(root, renderer):
        monkeypatch.setattr(cli, "render", renderer)
        counter = iter(range(1, 100))
        monkeypatch.setattr(cli, "uuid", types.SimpleNamespace(
            uuid4=lambda: types.SimpleNamespace(hex=f"{next(counter):08d}" + "0" * 24)))
        runner = Day(monkeypatch, root)
        assert runner.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
        assert runner.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0
        assert runner.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
        assert runner.run(f"{TUE}T20:03:00+00:00", "CLOSE_1M", print_at=f"{TUE}T19:59:58+00:00") == 0
        return runner
    real = day(tmp_path / "real", render)
    stub = day(tmp_path / "stub", lambda *args, **kwargs: ("", "<html></html>"))
    compared = 0
    for checkpoint in ("PREMARKET", "OPEN_30M", "HOURLY_1300", "CLOSE_1M"):
        a, b = real.folder(checkpoint), stub.folder(checkpoint)
        assert a.name == b.name
        for name in NON_PRESENTATION:
            if (a / name).exists() or (b / name).exists():
                assert (a / name).read_bytes() == (b / name).read_bytes(), (checkpoint, name)
                compared += 1
        ma, mb = (json.loads((folder / "metadata.json").read_text()) for folder in (a, b))
        assert {k: v for k, v in ma.items() if k not in {"markdown_hash", "html_hash"}} == \
               {k: v for k, v in mb.items() if k not in {"markdown_hash", "html_hash"}}
        assert ma["validation"] == "PASS"
    assert compared >= 10
    assert (tmp_path / "real/runs/continuity/bundle.json").read_bytes() == \
           (tmp_path / "stub/runs/continuity/bundle.json").read_bytes()


# --- E, E2, F: omissions and the optional Cuttingboard source ------------------------------------------

def coverage_items(page):
    drawer = page.split("<summary>Coverage limitations</summary>", 1)[1].split("</details>", 1)[0]
    return re.findall(r"<li>(.*?)</li>", drawer)


def md_coverage_items(md):
    block = md.split("## Sources & coverage", 1)[1].split("\n- [", 1)[0]
    return [line[2:] for line in block.splitlines() if line.startswith("- ")]


def test_e_optional_cuttingboard_absence_is_not_an_editorial_omission():
    packet = fixture_packet()
    packet["cuttingboard"] = {"status": "UNAVAILABLE", "reason": "optional source not requested"}
    packet["coverage"]["limitations"].append("Not automated: BEA calendar, live yields")
    value = narrative()
    assert not value["sections"]["cuttingboard"]
    view = presentation(packet, value)
    md, page = render(packet, value)
    assert not view["cuttingboard_section"]["visible"]  # the section itself still hides, as before
    assert "<h2>Cuttingboard context</h2>" not in page and "## Cuttingboard context" not in md
    for items in (view["limitations"], coverage_items(page), md_coverage_items(md)):
        assert items
        assert not any("Cuttingboard" in item for item in items), items
        assert not any("No admitted material" in item for item in items), items
    assert "No admitted material for" not in page and "No admitted material for" not in md


def test_e2_other_omitted_sections_read_not_in_this_edition():
    packet = fixture_packet()
    sectors = {"XLK", "XLF", "XLE", "XLI", "XLY", "XLP", "XLV", "XLU", "XLB", "XLRE", "XLC"}
    for key in ("observations", "derived"):
        packet[key] = [row for row in packet[key] if row.get("topic") not in sectors]
    packet["history"] = [row for row in packet["history"] if row["symbol"] not in sectors]
    packet["cuttingboard"] = {"status": "UNAVAILABLE", "reason": "optional source not requested"}
    view = presentation(packet, narrative())
    md, page = render(packet, narrative())
    assert "Not in this edition: Sector view" in view["limitations"]
    assert "Not in this edition: Sector view" in coverage_items(page)
    assert "Not in this edition: Sector view" in md_coverage_items(md)
    assert "<h2>Sector view</h2>" not in page


def technical(page):
    return page.split("<summary>Technical details</summary>", 1)[1].split("</details>", 1)[0]


def md_technical(md):
    return md.split("### Technical details", 1)[1]


@pytest.mark.parametrize("record,expected", [
    ({"status": "UNAVAILABLE", "reason": "optional source not requested"},
     "Cuttingboard: UNAVAILABLE — optional source not requested"),
    ({"status": "UNAVAILABLE", "reason": "not requested"}, "Cuttingboard: UNAVAILABLE — not requested"),
    ({"status": "STALE", "reason": "source generation older than ninety minutes",
      "captured_at": "2026-09-08T14:01:00+00:00", "adapter_version": "v0"},
     "Cuttingboard: STALE — source generation older than ninety minutes"),
    ({"status": "INVALID", "reason": "", "captured_at": "2026-09-08T14:01:00+00:00"}, "Cuttingboard: INVALID"),
    ({"status": "UNAVAILABLE", "reason": "   "}, "Cuttingboard: UNAVAILABLE"),
    ({"status": "UNAVAILABLE", "reason": None}, "Cuttingboard: UNAVAILABLE"),
    ({"status": "UNAVAILABLE", "reason": 403}, "Cuttingboard: UNAVAILABLE — 403"),  # rendering never raises
    ({"status": "UNAVAILABLE"}, "Cuttingboard: UNAVAILABLE"),
])
def test_f_cuttingboard_technical_status_states_what_happened(record, expected):
    packet = fixture_packet()
    packet["cuttingboard"] = record
    md, page = render(packet, narrative())
    html_line = next(item for item in re.findall(r"<li>(.*?)</li>", technical(page)) if item.startswith("Cuttingboard"))
    md_line = next(line for line in md_technical(md).splitlines() if line.startswith("Cuttingboard"))
    assert html_line == expected and md_line == expected  # HTML and Markdown agree
    for text in (technical(page), md_technical(md)):
        assert "None" not in text
        assert "generated ·" not in text and "generated ;" not in text and "schema ·" not in text


def test_f_an_available_cuttingboard_keeps_its_generation_details():
    packet = fixture_packet()
    assert packet["cuttingboard"]["status"] == "AVAILABLE"
    md, page = render(packet, narrative())
    expected = ("Cuttingboard: generated 2026-09-08T12:20:00+00:00 · captured 2026-09-08T12:45:00+00:00 · "
                "schema v2")
    assert f"<li>{expected}</li>" in technical(page)
    assert expected in md_technical(md).splitlines()
    assert "None" not in technical(page) and "None" not in md_technical(md)


# --- G, H, I: a caveat and an alternative explanation are labeled, and never merged -------------------

def with_macro_paragraph(uncertainty, alternative):
    value = narrative()
    value["sections"]["macro"][0].update(uncertainty=uncertainty, alternative=alternative)
    return value


def macro_notes(page, md):
    html = re.findall(r'<p class="fine">(Caveat|Could also be): (.*?)</p>', section(page, "Macro &amp; rates"))
    block = md.split("## Macro & rates", 1)[1].split("**TREASURY", 1)[0]
    markdown = re.findall(r"^(Caveat|Could also be): (.*)$", block, re.M)
    return html, markdown


def test_g_uncertainty_alone_reads_caveat():
    md, page = render(fixture_packet(), with_macro_paragraph("Dated rows only.", ""))
    assert macro_notes(page, md) == ([("Caveat", "Dated rows only.")], [("Caveat", "Dated rows only.")])
    assert "Could also be:" not in page and "Could also be:" not in md


def test_h_alternative_alone_reads_could_also_be():
    md, page = render(fixture_packet(), with_macro_paragraph("", "Duration exposure differs."))
    assert macro_notes(page, md) == ([("Could also be", "Duration exposure differs.")],
                                     [("Could also be", "Duration exposure differs.")])
    assert "Caveat:" not in page and "Caveat:" not in md


@pytest.mark.parametrize("uncertainty,alternative", [("", ""), ("   ", "\t "), ("", "  "), (" \n", "")])
def test_i_empty_or_blank_fields_render_no_label(uncertainty, alternative):
    md, page = render(fixture_packet(), with_macro_paragraph(uncertainty, alternative))
    for text in (page, md):
        assert "Caveat:" not in text and "Could also be:" not in text
    assert not re.search(r'<p class="fine">\s*</p>', page)  # no blank, unlabeled note either
    macro = md.split("## Macro & rates", 1)[1].split("**TREASURY", 1)[0]
    assert not re.search(r"\n[ \t]+\n", macro) and not re.search(r"\]\(#evidence-[^)]+\) +\n", macro)


def test_i_both_fields_render_caveat_first_each_with_its_own_meaning():
    value = with_macro_paragraph("  These are dated rows, {{treasury-2y-change}} on the 2Y.  ",
                                 "Duration exposure may explain the split. ")
    md, page = render(fixture_packet(), value)
    expected = [("Caveat", "These are dated rows, \u22124 bp on the 2Y."),
                ("Could also be", "Duration exposure may explain the split.")]
    assert macro_notes(page, md) == (expected, expected)
    view = presentation(fixture_packet(), value)
    paragraph = view["macro"]["paragraphs"][0]
    assert paragraph["uncertainty"] == expected[0][1] and paragraph["alternative"] == expected[1][1]
    assert "context" not in paragraph  # no merged, unlabeled line left in the view model
    # Unlabeled concatenation of the two is gone from both formats.
    joined = f"{expected[0][1]} {expected[1][1]}"
    assert joined not in page and joined not in md


# --- carried watches, clocks, and the frozen coverage caveat ------------------------------------------

@pytest.fixture
def day(monkeypatch, tmp_path):
    return Day(monkeypatch, tmp_path)


def clock_lines(page):
    return re.findall(r'<div class="clocks">(.*?)</div>', page)


def brief_md(day, checkpoint):
    return (day.folder(checkpoint) / "brief.md").read_text()


def test_j_a_carried_page_names_the_analysis_clock_and_the_refresh_clock_in_one_line(day):
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
    structure, refresh = day.page("OPEN_30M"), day.page("HOURLY_1300")
    assert clock_lines(structure) == ["As of 7:01 AM PT · Next update · 8:00 AM PT"]
    carried = ("Analysis anchored 7:01 AM PT · Observed record refreshed 10:00 AM PT · Next update · 11:00 AM PT")
    assert clock_lines(refresh) == [carried]  # exactly one clock element
    assert brief_md(day, "HOURLY_1300").splitlines()[3] == carried
    assert brief_md(day, "OPEN_30M").splitlines()[3] == "As of 7:01 AM PT · Next update · 8:00 AM PT"
    for page in (structure, refresh, brief_md(day, "HOURLY_1300"), brief_md(day, "OPEN_30M")):
        assert "Interpretation as of" not in page and "Data as of" not in page
    # The scheduler's idempotency markers are unchanged.
    assert f'data-session-date="{TUE}" data-checkpoint="HOURLY_1300"' in refresh
    assert f'data-session-date="{TUE}" data-checkpoint="OPEN_30M"' in structure
    # Each row still carries its own observation clock underneath the page-level clock.
    assert "as of 10:00 AM PT" in refresh and "Fri, Sep 4" in refresh


def test_carried_watches_read_from_an_earlier_read():
    from test_continuity import carried_setup
    packet, context, _ = carried_setup()
    value = narrative()
    value["summary"], value["watches"], value["attention"] = value["summary"][:1], value["watches"][:1], []
    value["watch_updates"] = [dict(carried_id="watch-sample-premarket-124500-tue-1", assessment="unresolved",
                                   reason="The print has not settled.", evidence_ids=["SPY-intraday"])]
    md, page = render(packet, value, context)
    assert '<span class="meta">From an earlier read · unresolved · into the close</span>' in page
    assert "**FROM AN EARLIER READ · unresolved · Into the close**" in md
    assert "Carried ·" not in page and "CARRIED WATCH" not in md
    assert '<p class="fine">The print has not settled.</p>' in page  # the reason is untouched


LIMITATION = "The QQQ current print is unavailable at this read, so the growth benchmark is unconfirmed."


def test_n_a_frozen_availability_claim_never_outlives_its_edition(day, monkeypatch):
    """Current availability belongs to this run's deterministic record. The 7:00 analysis said QQQ had no
    current print; by 10:00 it has one, so the carried page must not repeat the old claim anywhere."""
    real = cli.normalize_packet

    def without_qqq_at_seven(raw, now, mode, checkpoint="PREMARKET"):
        if checkpoint == "OPEN_30M":
            raw = dict(raw, observations=[o for o in raw["observations"] if o["id"] != "QQQ-intraday"])
        return real(raw, now, mode, checkpoint)
    monkeypatch.setattr(cli, "normalize_packet", without_qqq_at_seven)

    def claim(live):
        live["banner"]["limitation"] = LIMITATION
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", mutate=claim) == 0
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
    assert day.bundle()["interpretation"]["narrative"]["banner"]["limitation"] == LIMITATION  # record unchanged

    # The originating synthesis still states its caveat, with its marker, in both formats.
    structure, structure_md = day.page("OPEN_30M"), brief_md(day, "OPEN_30M")
    sources = structure.split("<h2>Sources &amp; coverage</h2>", 1)[1].split('<details class="drawer">', 1)[0]
    assert LIMITATION in sources and '<details class="cite">' in sources
    assert structure_md.count(LIMITATION) == 2

    # The carried refresh withholds it everywhere, marker included, and shows the current QQQ print.
    refresh, refresh_md = day.page("HOURLY_1300"), brief_md(day, "HOURLY_1300")
    assert LIMITATION not in refresh and LIMITATION not in refresh_md
    assert "QQQ current print is unavailable" not in refresh and "QQQ current print is unavailable" not in refresh_md
    sources = refresh.split("<h2>Sources &amp; coverage</h2>", 1)[1].split('<details class="drawer">', 1)[0]
    assert sources.strip() == ""  # no orphaned marker where the caveat was
    evidence = json.loads((day.folder("HOURLY_1300") / "evidence.json").read_text())
    qqq = evidence_catalog(evidence)["QQQ-intraday"]
    assert qqq["status"] == "AVAILABLE"
    assert '<span class="eyebrow">QQQ · Intraday vs prior close</span><strong class="direction-negative">-0.61 %' \
        in refresh
    assert "| QQQ · Intraday vs prior close | -0.61 % |" in refresh_md
    # No renderer-owned current surface says QQQ (or current prints) is unavailable.
    assert "current prints unavailable" not in refresh.split('<details class="drawer"', 1)[0]
    assert not any("QQQ" in item for item in coverage_items(refresh))
    assert not any("QQQ" in item for item in md_coverage_items(refresh_md))
    # Current deterministic coverage is still shown on the carried page.
    assert coverage_items(refresh) and "<summary>Sources ·" in refresh


def test_n2_coverage_limitations_use_reader_words_without_touching_the_packet():
    packet = fixture_packet()
    packet["coverage"]["limitations"] += ["Not automated: BEA calendar, live yields",
                                          "BEA calendar: not automated in this slice; sourced input supported",
                                          "SPY: intraday observation older than twenty minutes"]
    original = list(packet["coverage"]["limitations"])
    view = presentation(packet, narrative())
    md, page = render(packet, narrative())
    for items in (view["limitations"], coverage_items(page), md_coverage_items(md)):
        assert "Not collected: BEA calendar, live yields" in items
        assert "BEA calendar: not collected" in items
        assert "SPY: intraday observation older than twenty minutes" in items  # other entries unchanged
        assert not any("not automated" in item.lower() for item in items)
    assert packet["coverage"]["limitations"] == original
