"""Voice spec and The Take: narrative v2, one grounded falsifiable interpretation, advisory voice telemetry.

The Take is `take.text`, the single most useful interpretation the analyst could turn out to be wrong about,
backed by current evidence. It is the same class of record as the headline and the read: current evidence
only, grounded placeholders, no literal numbers, no trade instruction. It may be empty. Style telemetry
observes the prose and never decides anything.
"""

import copy
import hashlib
import json
import re

import pytest
from jsonschema import Draft202012Validator
from test_cadence import TUE, Day, interpretation_fragments
from test_continuity import carried_setup, trimmed
from test_contract import edition_response
from test_history_admission import packet_at, utc
from test_pipeline import fixture_packet, narrative
from test_schema_factoring import expand_local_refs

from market_brief import synthesize
from market_brief.context import analyst_context, edition_profile, editions_config, supplied_ids
from market_brief.continuity import (
    _hashed,
    admit_prior_state,
    bundle_path,
    carried_state,
    cited_ids,
    compare_all,
    edition_state,
    interpretation_record,
    load_bundle,
    write_bundle,
)
from market_brief.evidence import ROOT, evidence_catalog, model_packet
from market_brief.render import presentation, render
from market_brief.synthesize import (
    AVOID_PHRASES,
    NARRATIVE_SCHEMA,
    acceptance_schema,
    factored_transport_schema,
    narrative_schema,
    style_notes,
    synthesize_openrouter,
    transport_schema,
    validate_narrative,
)

# The fixtures' take: it compresses the stance the read already makes and claims nothing the sample cannot show.
TAKE = ("The tension is in the curve and metals, not in growth's 20-session lead over SPY, which stands at "
        "{{QQQ-spread20}}.")
TAKE_IDS = ["treasury-2y-change", "treasury-10y-change", "GDX-spread20", "QQQ-spread20"]
MISMATCH = "take text and evidence must be both present or both empty"
# The template's <style> block with the rates module (Rates & Reading Pass); The Take adds no CSS of its own.
STYLE_SHA256 = "5fe3ca6312e7be581893149e0b3fe742b0b68e1613b903d52c251f4003b57650"


def with_take(value=None, text=TAKE, ids=TAKE_IDS):
    value = copy.deepcopy(value if value is not None else narrative())
    value["take"] = {"text": text, "class": "INTERPRETATION", "evidence_ids": list(ids)}
    return value


def legacy_v1(value=None):
    """A narrative as the v1 contract produced it: no `take`, the v1 schema name."""
    value = copy.deepcopy(value if value is not None else narrative())
    value.pop("take", None)
    value["schema_version"] = "market-brief.narrative.v1"
    return value


def light():
    packet = packet_at(utc("2026-09-08T14:05:00+00:00"), checkpoint="OPEN_30M")
    profile = edition_profile("OPEN_30M")
    context = analyst_context(packet, profile)
    return packet, context, edition_response(profile, context)


# --- contract ------------------------------------------------------------------------------------

def test_narrative_contract_is_v2_with_a_required_take_right_after_summary():
    for profile in (None, edition_profile("PREMARKET"), edition_profile("OPEN_30M")):
        schema = NARRATIVE_SCHEMA if profile is None else narrative_schema(profile)
        assert schema["properties"]["schema_version"] == {"const": "market-brief.narrative.v2"}
        keys = list(schema["properties"])
        assert keys[keys.index("summary") + 1] == "take"
        assert "take" in schema["required"]
        take = schema["properties"]["take"]
        assert take["required"] == ["text", "class", "evidence_ids"] and take["additionalProperties"] is False
        assert take["properties"]["text"] == {"type": "string", "minLength": 0, "maxLength": 160}
        assert take["properties"]["class"] == {"const": "INTERPRETATION"}
        refs = take["properties"]["evidence_ids"]
        assert "minItems" not in refs and (refs["maxItems"], refs["uniqueItems"]) == (4, True)  # may be empty
        assert refs["items"] == schema["properties"]["banner"]["properties"]["evidence_ids"]["items"]


def test_the_take_crosses_the_wire_in_both_forms_and_expands_exactly():
    for profile in (None, edition_profile("PREMARKET"), edition_profile("OPEN_30M")):
        local = NARRATIVE_SCHEMA if profile is None else narrative_schema(profile)
        inline, factored = transport_schema(local), factored_transport_schema(local)
        assert expand_local_refs(factored) == inline
        wire = inline["properties"]["take"]
        assert wire["properties"]["text"]["description"] == "At most 160 characters."
        assert wire["properties"]["evidence_ids"]["description"] == "At most 4 items, no duplicates."
        assert list(inline["properties"])[list(inline["properties"]).index("summary") + 1] == "take"
        for value in (with_take(), with_take(text="", ids=[])):
            assert Draft202012Validator(inline).is_valid(value) and Draft202012Validator(factored).is_valid(value)
        missing = copy.deepcopy(with_take())
        del missing["take"]
        assert not Draft202012Validator(inline).is_valid(missing)
        assert not Draft202012Validator(factored).is_valid(missing)


def test_new_live_output_is_v2_only():
    with pytest.raises(ValueError, match="malformed narrative"):
        validate_narrative(legacy_v1(), fixture_packet())
    value = with_take()
    value["schema_version"] = "market-brief.narrative.v1"
    with pytest.raises(ValueError, match="malformed narrative at schema_version"):
        validate_narrative(value, fixture_packet())


# --- validator: empty and populated ------------------------------------------------------------------

@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_an_empty_take_validates_in_rich_and_light(text):
    assert validate_narrative(with_take(text=text, ids=[]), fixture_packet())
    packet, context, value = light()
    assert validate_narrative(with_take(value, text=text, ids=[]), packet, context)


def test_a_populated_take_with_supplied_current_refs_validates_in_rich_and_light():
    assert validate_narrative(with_take(), fixture_packet())
    packet, context, value = light()
    assert set(TAKE_IDS) <= supplied_ids(context)
    assert validate_narrative(with_take(value), packet, context)
    value = with_take(value, text="The opening print, SPY at {{SPY-intraday}}, is the one to test.",
                      ids=["SPY-intraday", "SPY-daily", "QQQ-daily", "treasury-10y-change"])
    assert validate_narrative(value, packet, context)


@pytest.mark.parametrize("text, ids", [
    (TAKE, []),                                   # text without evidence
    ("", TAKE_IDS),                               # evidence without text
    ("   ", ["QQQ-spread20"]),                    # whitespace is not text
])
def test_text_and_evidence_must_be_both_present_or_both_empty(text, ids):
    with pytest.raises(ValueError, match=MISMATCH):
        validate_narrative(with_take(text=text, ids=ids), fixture_packet())
    packet, context, value = light()
    with pytest.raises(ValueError, match=MISMATCH):
        validate_narrative(with_take(value, text=text, ids=ids), packet, context)


def test_take_evidence_bounds_are_the_contract_bounds():
    with pytest.raises(ValueError, match="malformed narrative at take.evidence_ids"):
        validate_narrative(with_take(ids=["NVDA-spread20", "NVDA-spread20"]), fixture_packet())
    with pytest.raises(ValueError, match="malformed narrative at take.class"):
        value = with_take()
        value["take"]["class"] = "OBSERVED"
        validate_narrative(value, fixture_packet())
    # The 160-character target is editorial: kept and noted within headroom, rejected beyond it.
    long_text = "Growth's edge rests on one mega-cap and not on participation. " * 6
    value = with_take(text=long_text[:200])
    assert validate_narrative(value, fixture_packet())
    notes = synthesize.editorial_notes(value, narrative_schema(edition_profile("PREMARKET")))
    assert dict(path="take.text", keyword="maxLength", limit=160, actual=200) in notes
    with pytest.raises(ValueError, match="malformed narrative at take.text"):
        validate_narrative(with_take(text=(long_text * 2)[:321]), fixture_packet())


# --- validator: the take cites current evidence only ---------------------------------------------------

@pytest.mark.parametrize("ref", ["invented-id", "attention-NVDA-spread", "sample-prices"])
def test_unknown_take_references_reject(ref):
    with pytest.raises(ValueError, match="unknown, unavailable, or unsupplied evidence reference"):
        validate_narrative(with_take(ids=["QQQ-spread20", ref]), fixture_packet())


def test_light_context_omitted_evidence_cannot_back_the_take():
    packet, context, value = light()
    omitted = sorted(set(evidence_catalog(model_packet(packet))) - supplied_ids(context))
    assert "NVDA-daily" in omitted
    with pytest.raises(ValueError, match="unsupplied"):
        validate_narrative(with_take(value, text="NVDA's daily move is the tell.", ids=["NVDA-daily"]),
                           packet, context)


def test_prior_refs_and_comparison_ids_cannot_back_the_take():
    packet, context, _ = carried_setup()
    value = trimmed(narrative(), edition_profile("OPEN_30M"))
    prior = {row["ref"] for row in context["prior_state"]["snapshots"]}
    comparisons = {c["id"] for c in context["comparisons"]}
    assert "premarket:SPY-intraday" in prior and "cmp-premarket-SPY-intraday" in comparisons
    # The same prior ref is admitted where continuity records may cite it.
    control = copy.deepcopy(value)
    control["changes"] = [dict(comparison_id="cmp-premarket-SPY-intraday",
                               text="SPY moved from {{premarket:SPY-intraday}} to {{SPY-intraday}}.",
                               evidence_ids=["SPY-intraday", "premarket:SPY-intraday"])]
    assert validate_narrative(with_take(control, text="SPY's turn is the read.", ids=["SPY-intraday"]),
                              packet, context)
    for ids, text in ((["SPY-intraday", "premarket:SPY-intraday"], "SPY turned from the premarket read."),
                      (["premarket:SPY-intraday"], "SPY held its premarket read."),
                      (["SPY-intraday", "cmp-premarket-SPY-intraday"], "SPY's move is the read."),
                      (["SPY-intraday", "premarket:SPY-intraday"],
                       "SPY went from {{premarket:SPY-intraday}} to {{SPY-intraday}}.")):
        with pytest.raises(ValueError, match="unknown, unavailable, or unsupplied evidence reference"):
            validate_narrative(with_take(value, text=text, ids=ids), packet, context)


# --- validator: grounding, digits, sample language, trade language ---------------------------------------

@pytest.mark.parametrize("text, ids, message", [
    ("Growth's edge is {{QQQ-spread20}} wide.", ["NVDA-spread20"], "numeric placeholder not grounded"),
    ("The survey, {{sample-event}}, sets the tone.", ["sample-event"], "numeric placeholder not grounded"),
    ("Growth's edge is {{invented-id}} wide.", ["QQQ-spread20"], "numeric placeholder not grounded"),
    ("Growth leads by 2 points over the index.", ["QQQ-spread20"], "literal numeric claim"),
    ("Growth's edge is {QQQ-spread20} wide.", ["QQQ-spread20"], "malformed evidence placeholder"),
    ("Growth leads today, not the index.", ["QQQ-spread20"], "current-language claim in sample narrative"),
    ("Growth is currently the only leader.", ["QQQ-spread20"], "current-language claim in sample narrative"),
])
def test_take_prose_obeys_the_existing_grounding_rules(text, ids, message):
    with pytest.raises(ValueError, match=message):
        validate_narrative(with_take(text=text, ids=ids), fixture_packet())


def test_bounded_labels_are_not_numeric_claims_in_the_take():
    value = with_take(text="QQQ leads SPY over 20 sessions while the 10Y firmed.",
                      ids=["QQQ-spread20", "treasury-10y-change"])
    assert validate_narrative(value, fixture_packet())


@pytest.mark.parametrize("text", [
    "A rates-driven sell-off, not a tech story.",
    "A rates-driven selloff, not a tech story.",
    "Two sell-offs in a row say rates, not tech.",
    "Selloffs like this are rates stories, not tech ones.",
    "The Sell-Off is about rates, not tech.",
])
def test_sell_off_as_a_market_noun_is_descriptive_prose(text):
    assert validate_narrative(with_take(text=text, ids=["treasury-10y-change", "QQQ-daily"]), fixture_packet())


@pytest.mark.parametrize("text", [
    "Buy the dip in growth.",
    "Sell growth into strength.",
    "A sell-off; sell the bounce in growth.",
    "The entry is the growth lead.",
    "Growth's target is the prior high.",
    "Sizing should follow the growth lead.",
    "Execute on the growth lead.",
    "Execution matters more than the growth lead.",
    "Growth deserves an order before the open.",
    "Sell off growth into the close.",
])
def test_actionable_trade_language_rejects_in_the_take(text):
    with pytest.raises(ValueError, match="trade language in the take"):
        validate_narrative(with_take(text=text, ids=["QQQ-spread20"]), fixture_packet())


def test_attention_reason_trade_rule_is_unchanged_by_the_take_exemption():
    for why in ("A sell-off in industrials would matter.", "Buy the cross."):
        value = with_take()
        value["attention"] = [dict(id="attention-NVDA-spread", why=why)]
        with pytest.raises(ValueError, match="trade language in attention reason"):
            validate_narrative(value, fixture_packet())


def test_a_provider_400_keeps_the_providers_own_bounded_message_and_nothing_else(monkeypatch):
    """G2.5 recorded only "Provider returned error": OpenRouter's `metadata.raw` carried the reason and was dropped."""
    import io
    from urllib.error import HTTPError
    raw = json.dumps({"type": "error", "request_id": "req_PRIVATE_SENTINEL", "error": {
        "type": "invalid_request_error",
        "message": "The compiled grammar is too large, which would cause performance issues.\u0007" + "x" * 400}})
    body = json.dumps({"error": {"code": 400, "message": "Provider returned error",
                                 "metadata": {"provider_name": "Anthropic", "raw": raw}},
                       "user_id": "PRIVATE_USER_SENTINEL"}).encode()
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(1)
        raise HTTPError(request.full_url, 400, "error", {}, io.BytesIO(body))

    monkeypatch.setattr(synthesize, "urlopen", fake_urlopen)
    with pytest.raises(ValueError, match="OpenRouter HTTP 400") as exc:
        synthesize_openrouter(fixture_packet(), api_key="secret")
    recorded = str(exc.value)
    assert calls == [1]
    assert "The compiled grammar is too large, which would cause performance issues." in recorded
    message = json.loads(recorded.split("diagnostic=", 1)[1])["error"]["metadata"]["provider_message"]
    assert len(message) == 300 and "\u0007" not in message and message.isprintable()
    for private in ("req_PRIVATE_SENTINEL", "PRIVATE_USER_SENTINEL", "invalid_request_error", "secret"):
        assert private not in recorded
    assert synthesize._provider_message("plain text body") is None
    assert synthesize._provider_message({"error": "not a dict"}) is None
    assert synthesize._provider_message(None) is None
    assert synthesize._provider_message('{"error": {"message": 5}}') is None
    assert synthesize._provider_message({"error": {"message": ["a"]}}) is None
    assert synthesize._provider_message({"error": {"message": "grammar\ttoo\nlarge"}}) == "grammar too large"
    assert synthesize._provider_message({"error": {"message": "  a \n\t b  "}}) == "a b"  # runs collapse
    assert synthesize._provider_message("[" * 20000) is None  # nesting past the JSON parser's recursion limit


def test_every_provider_error_shape_is_recorded_without_escaping_the_fail_closed_path(monkeypatch):
    import io
    from urllib.error import HTTPError
    # Under the 20,000-byte read limit, so the deep nesting reaches the provider-message parser itself.
    nested = json.dumps({"error": {"code": 400, "message": "Provider returned error",
                                   "metadata": {"provider_name": "Anthropic", "raw": "[" * 12000}}})
    assert len(nested) < 20_000
    surrogate = '{"error": {"code": 400, "message": "bad \\ud800 text", "metadata": {"provider_name": "An\\ud800"}}}'
    for body in (nested, "[" * 20000, surrogate):
        def fake_urlopen(request, timeout, body=body):
            raise HTTPError(request.full_url, 400, "error", {}, io.BytesIO(body.encode()))
        monkeypatch.setattr(synthesize, "urlopen", fake_urlopen)
        with pytest.raises(ValueError, match="OpenRouter HTTP 400") as exc:
            synthesize_openrouter(fixture_packet(), api_key="secret")
        str(exc.value).encode("utf-8")  # savable: no lone surrogate reaches metadata.json


def test_openrouter_labels_keep_their_original_truncation_for_the_no_endpoint_classifier():
    """Only the provider's own message is whitespace-normalized; OpenRouter's message is truncated as before, so
    which 404s count as "no endpoints" (and may fall back once) is unchanged."""
    import io
    from urllib.error import HTTPError
    for message, classified in (("No endpoints found for anthropic/claude-fable-5.1", True),
                                ("No\nendpoints found for x", False), (" " * 400 + "No endpoints found", False)):
        body = json.dumps({"error": {"code": 404, "message": message}}).encode()
        error = synthesize._safe_error(HTTPError("u", 404, "x", {}, io.BytesIO(body)))
        assert error["message"] == message[:300]
        assert synthesize._is_model_unavailable(error) is classified


def test_a_rejected_take_is_one_paid_call_and_no_retry():
    calls = []

    def requester(payload, api_key):
        calls.append(payload["model"])
        bad = with_take(ids=[])
        return {"id": "r", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(bad)}}]}

    with pytest.raises(synthesize.NarrativeRejected, match=MISMATCH):
        synthesize_openrouter(fixture_packet(), api_key="secret", requester=requester)
    assert calls == ["anthropic/claude-fable-5.1"]


# --- rendering -----------------------------------------------------------------------------------------

def read_block(page):
    return page.split('<div class="read">', 1)[1].split('<div class="figures">', 1)[0]


def test_the_take_renders_after_the_read_and_before_the_figures():
    packet, value = fixture_packet(), with_take()
    view = presentation(packet, value)
    assert view["take"]["text"] == ("The tension is in the curve and metals, not in growth's 20-session lead over "
                                    "SPY, which stands at +1.87 pp.")
    assert [r["id"] for r in view["take"]["refs"]] == TAKE_IDS
    md, page = render(packet, value)
    read = read_block(page)
    last_summary = view["summary"][-1]["text"]
    assert read.index(last_summary) < read.index("<b>The take:</b>")
    take = read.split("<b>The take:</b>", 1)[1]
    assert take.startswith(" The tension is in the curve and metals, not in growth&#39;s 20-session lead over SPY, "
                           "which stands at +1.87 pp. <details class=\"cite\">")
    marker = take.split("<details", 1)[1].split("</details>", 1)[0]
    assert re.findall(r'<span class="ref"><a href="#evidence-([^"]+)"', marker) == TAKE_IDS
    assert read.rstrip().endswith("</details></div></div>")  # the take is the last block inside `.read`
    # Markdown: the take is the paragraph after the read, then one ordinary blank line.
    lines = md.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("**The take:**"))
    assert lines[index] == ("**The take:** The tension is in the curve and metals, not in growth&#x27;s 20-session "
                            "lead over SPY, which stands at +1.87 pp. [evidence](#evidence-treasury-2y-change) "
                            "[evidence](#evidence-treasury-10y-change) [evidence](#evidence-GDX-spread20) "
                            "[evidence](#evidence-QQQ-spread20)")
    assert lines[index - 1] == "" and lines[index + 1] == "" and lines[index + 2] == "**OBSERVED SNAPSHOT**"
    assert lines[index - 2].startswith(view["summary"][-1]["text"][:40])


@pytest.mark.parametrize("empty", ["missing", "blank", "whitespace"])
def test_an_empty_or_missing_take_emits_nothing(empty):
    packet = fixture_packet()
    value = with_take(text="   " if empty == "whitespace" else "", ids=[])
    if empty == "missing":
        value = legacy_v1(value)
    assert presentation(packet, value)["take"] is None
    md, page = render(packet, value)
    assert "The take" not in md and "The take" not in page
    # Byte-identical to a page whose narrative never had a take: no element, label, or stray blank line.
    assert (md, page) == render(packet, legacy_v1())
    assert "\n\n\n" not in md.split("**OBSERVED SNAPSHOT**", 1)[0]


def test_the_template_adds_no_css():
    template = (ROOT / "templates/brief.html.j2").read_text()
    style = re.search(r"<style>.*?</style>", template, re.S).group(0)
    assert hashlib.sha256(style.encode()).hexdigest() == STYLE_SHA256
    _, page = render(fixture_packet(), with_take())
    assert re.search(r"<style>.*?</style>", page, re.S).group(0) == style


# --- continuity -----------------------------------------------------------------------------------------

def test_take_only_rows_are_frozen_with_the_interpretation():
    packet = fixture_packet()
    text, ids = "Industrials, {{XLI-daily}} on the day, are the cyclical check on growth.", ["XLI-daily"]
    without, with_ = with_take(text="", ids=[]), with_take(text=text, ids=ids)
    assert "XLI-daily" not in cited_ids(without, [], [])
    assert cited_ids(with_, [], []) - cited_ids(without, [], []) == {"XLI-daily"}
    frozen = interpretation_record(packet, with_)["evidence"]
    assert "XLI-daily" not in interpretation_record(packet, without)["evidence"]
    row = evidence_catalog(packet)["XLI-daily"]
    assert frozen["XLI-daily"]["value"] == row["value"] and frozen["XLI-daily"]["observed_at"] == row["observed_at"]


def test_cited_ids_and_the_record_tolerate_a_legacy_narrative_without_a_take():
    packet = fixture_packet()
    assert cited_ids(legacy_v1(), [], []) == cited_ids(with_take(text="", ids=[]), [], [])
    record = interpretation_record(packet, legacy_v1())
    assert "take" not in record["narrative"]
    md, page = render(packet, interpretation=record)
    assert "The take" not in md and "The take" not in page


def test_the_take_changes_no_non_interpretation_continuity_state():
    packet, context, bundle = carried_setup()
    value = trimmed(narrative(), edition_profile("OPEN_30M"))
    taken = with_take(value, text="SPY's turn from the premarket is the read to test.", ids=["SPY-intraday"])
    empty = with_take(value, text="", ids=[])
    for candidate in (taken, empty):
        validate_narrative(candidate, packet, context)
    prior = admit_prior_state(bundle, packet)
    comparisons = compare_all(prior, packet)
    states = [edition_state(packet, candidate, prior, comparisons, "ctx", "hash", "test")
              for candidate in (taken, empty, legacy_v1(value))]
    assert states[0] == states[1] == states[2]
    assert '"take"' not in json.dumps(states[0])
    records = [interpretation_record(packet, candidate, context) for candidate in (taken, empty)]
    carried = [carried_state(packet, prior, comparisons, record) for record in records]
    assert carried[0] == carried[1]
    assert '"take"' not in json.dumps(carried[0])
    # Only the interpretation record differs: the narrative itself and the rows it cites.
    assert records[0]["narrative"]["take"]["text"] and not records[1]["narrative"]["take"]["text"]


# --- the production day: frozen take, legacy compatibility, cadence, telemetry ---------------------------

OPENING_TAKE = "SPY at {{SPY-intraday}} is a rates story until tech confirms it."


def opening_take(live):
    live["take"] = {"text": OPENING_TAKE, "class": "INTERPRETATION",
                    "evidence_ids": ["SPY-intraday", "treasury-10y-change"]}


def take_line(page):
    return page.split("<b>The take:</b>", 1)[1].split("</div>", 1)[0]


def test_a_carried_take_keeps_the_values_and_clock_its_analyst_saw(monkeypatch, tmp_path):
    day = Day(monkeypatch, tmp_path)
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M", mutate=opening_take) == 0
    later = (("SPY", 0.80), ("QQQ", 0.95), ("XLI", 0.40))
    assert day.run(f"{TUE}T17:01:00+00:00", "HOURLY_1300", prints=later) == 0
    assert day.calls == ["PREMARKET", "OPEN_30M"]
    structure, refresh = day.page("OPEN_30M"), day.page("HOURLY_1300")
    for page in (structure, refresh):
        line = take_line(page)
        assert "SPY at -0.53 % is a rates story until tech confirms it." in line
        assert "+0.80 %" not in line
        marker = re.search(r'href="#evidence-SPY-intraday">([^<]*)</a><b>([^<]*)</b><small>([^<]*)</small>', line)
        assert marker.groups() == ("SPY · Intraday vs prior close", "-0.53 %", "7:01 AM PT")
    ledger = refresh.split('id="evidence-SPY-intraday"', 1)[1].split("</div>", 1)[0]
    assert '<span class="value">+0.80 %</span>' in ledger
    assert interpretation_fragments(refresh) == interpretation_fragments(structure)
    assert "<b>The take:</b>" in interpretation_fragments(refresh)[0]
    frozen = day.bundle()["interpretation"]
    assert frozen["evidence"]["SPY-intraday"]["value"] == -0.53
    assert frozen["narrative"]["take"]["evidence_ids"] == ["SPY-intraday", "treasury-10y-change"]


def test_a_legacy_v1_interpretation_frozen_before_the_upgrade_still_refreshes(monkeypatch, tmp_path):
    day = Day(monkeypatch, tmp_path)
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    bundle = day.bundle()
    record = copy.deepcopy(bundle["interpretation"])
    record["narrative"] = legacy_v1(record["narrative"])
    bundle["interpretation"] = _hashed(record)
    write_bundle(bundle_path(tmp_path), bundle)
    assert load_bundle(bundle_path(tmp_path))[0]["interpretation"]["narrative"]["schema_version"].endswith("v1")
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 0
    assert day.calls == ["PREMARKET"]
    page = day.page("OPEN_1M")
    assert "The take" not in page and "Analysis anchored 6:00 AM PT" in page
    assert day.metadata("OPEN_1M")["validation"] == "PASS"
    # Continuity is not migrated: the frozen record stays exactly as it was written.
    assert day.bundle()["interpretation"] == bundle["interpretation"]


def test_the_take_appears_on_synthesis_and_carried_pages_and_the_day_still_calls_twice(monkeypatch, tmp_path):
    day = Day(monkeypatch, tmp_path)
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 0
    assert day.run(f"{TUE}T14:01:00+00:00", "OPEN_30M") == 0
    assert day.run(f"{TUE}T17:00:00+00:00", "HOURLY_1300") == 0
    assert day.run(f"{TUE}T20:03:00+00:00", "CLOSE_1M", print_at=f"{TUE}T19:59:58+00:00") == 0
    assert day.calls == ["PREMARKET", "OPEN_30M"]
    takes = {checkpoint: take_line(day.page(checkpoint))
             for checkpoint in ("PREMARKET", "OPEN_1M", "OPEN_30M", "HOURLY_1300", "CLOSE_1M")}
    assert takes["OPEN_1M"] == takes["PREMARKET"]
    assert takes["HOURLY_1300"] == takes["CLOSE_1M"] == takes["OPEN_30M"]
    for checkpoint in ("PREMARKET", "OPEN_30M"):
        assert day.metadata(checkpoint)["synthesis"]["calls"] == 1
    # The handoff carries no take: the next premarket starts from structured state, never old prose.
    assert '"take"' not in json.dumps(day.bundle()["close"])


# --- advisory style telemetry -----------------------------------------------------------------------------

SURFACES = (("banner", "title"), ("banner", "limitation"), ("summary", 0, "text"), ("summary", 0, "uncertainty"),
            ("summary", 0, "alternative"), ("take", "text"), ("sections", "macro", 0, "text"),
            ("sections", "macro", 0, "uncertainty"), ("sections", "macro", 0, "alternative"),
            ("attention", 0, "why"), ("watches", 0, "condition"), ("watches", 0, "confirmation"),
            ("watches", 0, "contradiction"), ("character", "text"), ("relationships", 0, "statement"),
            ("relationships", 0, "reason"), ("watch_updates", 0, "reason"), ("changes", 0, "text"))


PROSE = {"title", "limitation", "text", "uncertainty", "alternative", "why", "condition", "confirmation",
         "contradiction", "statement", "reason"}


def blank(value):
    """Every prose string emptied, so a count sees only the text a test writes."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PROSE and isinstance(child, str):
                value[key] = ""
            else:
                blank(child)
    elif isinstance(value, list):
        for child in value:
            blank(child)
    return value


def every_surface(text):
    value = blank(with_take())
    value["watch_updates"] = [dict(carried_id="watch-1", assessment="unresolved", reason="", evidence_ids=["x"])]
    value["changes"] = [dict(comparison_id="cmp-1", text="", evidence_ids=["x"])]
    for path in SURFACES:
        target = value
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = text
    return value


def test_avoid_list_is_fixed_and_ordered():
    assert AVOID_PHRASES == ("admitted", "packet", "notably", "evident", "suggesting", "rather than",
                             "broad but not", "character")
    notes = style_notes(narrative())
    assert list(notes["avoid_phrases"]) == list(AVOID_PHRASES)
    assert set(notes) == {"avoid_phrases", "avoid_phrase_total", "numeric_placeholders", "take"}
    assert set(notes["take"]) == {"present", "characters", "repeats_headline"}


def test_every_reader_prose_surface_is_counted_once():
    notes = style_notes(every_surface("Notably so."))
    assert notes["avoid_phrases"]["notably"] == len(SURFACES) == 18
    assert notes["avoid_phrase_total"] == 18
    assert notes["numeric_placeholders"] == 0


def test_exact_counts_are_case_insensitive_and_bounded_to_whole_phrases():
    value = blank(with_take())
    value["summary"][0]["text"] = ("Admitted rows, ADMITTED again; the packet, not packets; RATHER THAN rather  than; "
                                   "evident but not evidently; broad but not deep, broad but notable; character, "
                                   "characters, characterize; suggesting; notably.")
    notes = style_notes(value)
    assert notes["avoid_phrases"] == {"admitted": 2, "packet": 1, "notably": 1, "evident": 1, "suggesting": 1,
                                      "rather than": 2, "broad but not": 1, "character": 1}
    assert notes["avoid_phrase_total"] == 10


def test_identifiers_labels_and_placeholders_are_not_prose():
    value = blank(with_take(text="", ids=["character-packet"]))
    value["take"]["text"] = "Edge at {{character-packet}} holds."
    value["schema_version"] = "market-brief.narrative.v2"
    value["banner"]["evidence_ids"] = ["admitted-row", "packet-row"]
    value["summary"][0]["text"] = "Growth leads {{notably-evident}} and {{rather-than}}."
    value["summary"][0]["class"] = "INTERPRETATION"
    value["attention"][0]["id"] = "attention-admitted-packet"
    value["watches"][0]["horizon"] = "EVENT(evident-suggesting)"
    value["relationships"][0]["instruments"] = ["packet", "character"]
    value["relationships"][0]["carried_id"] = "rel-character"
    value["watch_updates"] = [dict(carried_id="watch-admitted", assessment="unresolved", reason="Untested.",
                                   evidence_ids=["previous_close:packet"])]
    value["changes"] = [dict(comparison_id="cmp-character", text="Moved.", evidence_ids=["admitted"])]
    notes = style_notes(value)
    assert notes["avoid_phrase_total"] == 0 and set(notes["avoid_phrases"].values()) == {0}
    assert notes["numeric_placeholders"] == 3


def test_numeric_placeholders_and_take_telemetry():
    value = with_take()
    notes = style_notes(value)
    placeholders = sum(len(re.findall(r"\{\{[^{}]+\}\}", text)) for text in json.dumps(value).split('"'))
    assert notes["numeric_placeholders"] == placeholders == 5
    stated = value["take"]["text"]
    assert notes["take"] == dict(present=True, characters=len(stated), repeats_headline=False)
    assert style_notes(with_take(text="", ids=[]))["take"] == dict(present=False, characters=0, repeats_headline=False)
    assert style_notes(legacy_v1())["take"] == dict(present=False, characters=0, repeats_headline=False)
    echo = with_take(text="  growth has the lead: confirmation is STILL missing!  ", ids=["QQQ-spread20"])
    assert style_notes(echo)["take"] == dict(present=True, characters=len(echo["take"]["text"].strip()),
                                             repeats_headline=True)


def test_style_notes_is_pure():
    value = every_surface("Notably, rather than broad but not.")
    before = copy.deepcopy(value)
    style_notes(value)
    assert value == before


UGLY = "Notably, the admitted packet is evident, suggesting a broad but not deep character rather than a trend."


def ugly(live):
    """Every avoid phrase in live prose, while the narrative stays grounded and valid."""
    live["summary"][0]["text"] = UGLY
    live["character"]["text"] = "Notably " + live["character"]["text"]
    live["take"] = {"text": "Rather than breadth, the packet shows one leader.", "class": "INTERPRETATION",
                    "evidence_ids": ["QQQ-spread20"]}


def test_ugly_voice_never_changes_acceptance_publication_or_the_call_count(monkeypatch, tmp_path):
    clean, rough = Day(monkeypatch, tmp_path / "clean"), Day(monkeypatch, tmp_path / "rough")
    assert clean.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert rough.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False, mutate=ugly) == 0
    for day in (clean, rough):
        assert day.calls == ["PREMARKET"] and day.published == ["PREMARKET"]
        metadata = day.metadata("PREMARKET")
        assert metadata["validation"] == "PASS" and metadata["synthesis"] == dict(kind="synthesis", calls=1)
        keys = list(metadata)
        assert keys.index("style") == keys.index("editorial") + 1
        saved = json.loads((day.folder("PREMARKET") / "narrative.json").read_text())
        assert saved == day.narratives["PREMARKET"]
        assert metadata["style"] == style_notes(saved)
    style = rough.metadata("PREMARKET")["style"]
    assert style["avoid_phrase_total"] >= 10 and all(style["avoid_phrases"].values())
    assert clean.metadata("PREMARKET")["style"]["avoid_phrase_total"] < style["avoid_phrase_total"]
    assert UGLY in rough.page("PREMARKET")


def test_refresh_and_close_metadata_carry_no_style_telemetry(monkeypatch, tmp_path):
    day = Day(monkeypatch, tmp_path)
    assert day.run(f"{TUE}T13:00:00+00:00", "PREMARKET", intraday=False) == 0
    assert day.run(f"{TUE}T13:31:00+00:00", "OPEN_1M") == 0
    assert day.run(f"{TUE}T20:03:00+00:00", "CLOSE_1M", print_at=f"{TUE}T19:59:58+00:00") == 0
    assert "style" in day.metadata("PREMARKET")
    assert "style" not in day.metadata("OPEN_1M") and "style" not in day.metadata("CLOSE_1M")


def test_ugly_voice_is_one_openrouter_call_with_the_narrative_unchanged():
    calls, value = [], with_take()
    value["summary"][0]["text"] = UGLY

    def requester(payload, api_key):
        calls.append(1)
        return {"id": "r", "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}

    accepted, _ = synthesize_openrouter(fixture_packet(), api_key="secret", requester=requester)
    assert calls == [1] and accepted == value


# --- prompt, schema size and budgets ---------------------------------------------------------------------

PROMPT = (ROOT / "prompts/synthesis.md").read_text()


def test_prompt_is_v0_3_and_smaller_than_v0_2():
    assert PROMPT.splitlines()[0] == "# Market Brief synthesis v0.3"
    size = len(PROMPT.encode())
    assert size < 9649 and size <= 9300


def test_the_voice_leads_the_prompt():
    head = " ".join(PROMPT.encode()[:1200].decode(errors="ignore").split())
    for phrase in ("sharp desk colleague", "discretionary trader", "macro intuition", "Lead with the claim",
                   "Short sentences, one idea each", "approved evidence placeholders", "zero is fine",
                   "never invent a cause", "blind spot once", "without pretending certainty"):
        assert phrase in head, phrase


def test_the_prompt_steers_away_from_contract_words_without_a_blacklist_gate():
    lowered = PROMPT.lower()
    for phrase in AVOID_PHRASES:
        assert f'"{phrase}"' in lowered or f"“{phrase}”" in lowered, phrase


def test_the_prompt_defines_the_take():
    lowered = " ".join(PROMPT.lower().split())
    assert "the single most useful interpretation in this brief that you could turn out to be wrong about" \
        in lowered
    for phrase in ("one short sentence", "current evidence", "do not repeat the headline", "leave it empty",
                   "never fill it", "not a prediction", "not trade advice"):
        assert phrase in lowered, phrase


def test_the_prompt_says_what_the_validator_checks_literally_in_the_take():
    take = " ".join(PROMPT.split("THE TAKE:", 1)[1].split("\n\n", 1)[0].split())
    assert "checked literally" in take and "(sell-off is fine)" in take
    for word in synthesize.TRADE_LANGUAGE.pattern.split("(", 1)[1].split(")", 1)[0].split("|"):
        assert re.search(rf"\b{word}\b", take), word


def test_the_prior_ref_namespace_keeps_its_concrete_example_and_no_identifier_is_wrapped():
    """A zero-cost replay of the 2026-09-24 premarket, run while the example had been trimmed, cited
    `anchor:previous_close:QQQ-intraday` in four continuity records: the bare template was read literally. The
    example is load-bearing, and a code span broken across lines would teach a broken identifier."""
    continuity = " ".join(PROMPT.split("CONTINUITY.", 1)[1].split())
    assert "namespaced `anchor:evidence-id` (for example `premarket:SPY-intraday`)" in continuity
    assert all("\n" not in span for span in re.findall(r"`[^`]*`", PROMPT))
    assert not any(line.endswith("-") for line in PROMPT.splitlines())


def test_the_take_rules_out_an_alternative_only_on_supplied_evidence():
    """G3, from a zero-cost replay of the 2026-09-24 premarket: the take said "not isolated stock news" although
    no news is collected. Absence of evidence rules nothing out; only supplied evidence can exclude."""
    take = " ".join(PROMPT.split("THE TAKE:", 1)[1].split("\n\n", 1)[0].split())
    assert 'Claim nothing the evidence cannot show: write "not X" only when supplied evidence shows X false' in take
    assert "and missing data rules nothing out" in take


def test_the_prompt_says_attention_reasons_get_the_same_word_check_without_the_exemption():
    records = " ".join(PROMPT.split("RECORDS.", 1)[1].split("\n\n", 1)[0].split())
    assert "`why` under the take's word check with no sell-off exemption" in records


def test_the_prompt_says_placeholders_carry_their_own_sign_and_unit():
    numbers = " ".join(PROMPT.split("NUMBERS:", 1)[1].split("\n\n", 1)[0].split())
    assert "with its sign and unit, so write no unit, % sign, or up/down word beside it" in numbers
    budget = " ".join(PROMPT.split("BUDGET.", 1)[1].split("\n\n", 1)[0].split())
    assert "a backstop, not a target" in budget


def test_the_prompt_keeps_every_semantic_truth_rule():
    lowered = " ".join(PROMPT.lower().split())
    for phrase in ("recommendations", "targets", "positions", "entries", "exits", "orders",
                   "never enumerate", "tables are the record", "invent", "alternative explanation",
                   "a small basket is not market breadth", "price moving after an event is not proof",
                   "prior-close", "timestamped current", "provisional", "not an official closing bar",
                   "current evidence first", "`changes`, `relationships`, and `watch_updates`", "`cmp-...`",
                   "`comparison_id`", "omitted facts cannot be cited", "sample", "never present sample data as live",
                   "once, in the banner `limitation`", "indeterminate", "improving/deteriorating",
                   "prior state is structured earlier analysis", "never evidence", "no_new_observation",
                   "no second attempt", "discarded", "missing breadth, news, fx, or live rates stay unknown",
                   "never claim complete news or event coverage",
                   "empty uncertainty and alternative strings are correct without a specific new point"):
        assert phrase in lowered, phrase


def schema_shape(schema):
    """Distinct compiled structure of a factored schema: every `$defs` body counted once, each `$ref` one node."""
    counts = dict(nodes=0, objects=0, properties=0)

    def walk(node):
        if not isinstance(node, dict):
            return
        counts["nodes"] += 1
        if "$ref" in node:
            return
        if node.get("type") == "object":
            counts["objects"] += 1
            counts["properties"] += len(node["properties"])
        for child in (*node.get("properties", {}).values(), *node.get("anyOf", []), *node.get("$defs", {}).values()):
            walk(child)
        walk(node.get("items"))
    walk(schema)
    return counts


# The largest provider schema Anthropic compiled fresh and accepted (69c225d, production 2026-09-18). G2.5 on
# 2026-09-25 (5,497 B, 75 nodes, 12 objects, 58 properties) was rejected; a 5,510-byte design ceiling let it through.
ACCEPTED_ENVELOPE = dict(bytes=5295, nodes=72, objects=11, properties=55)


def test_the_provider_schema_stays_inside_the_envelope_anthropic_has_accepted():
    sizes = {}
    for name, local in (("FULL", NARRATIVE_SCHEMA), ("PREMARKET", narrative_schema(edition_profile("PREMARKET"))),
                        ("OPEN_30M", narrative_schema(edition_profile("OPEN_30M")))):
        inline = synthesize.compact_json(transport_schema(local))
        factored = factored_transport_schema(local)
        assert expand_local_refs(factored) == json.loads(inline)
        shape = dict(bytes=len(synthesize.compact_json(factored).encode()), **schema_shape(factored))
        sizes[name] = (len(inline.encode()), shape)
        for measure, ceiling in ACCEPTED_ENVELOPE.items():
            assert shape[measure] <= ceiling, (name, measure, shape[measure], ceiling)
    print("inline bytes / factored shape:", sizes)


def test_the_g25_rejected_schema_shape_is_outside_the_envelope(monkeypatch):
    """The guard would have caught the rejected schema: each paragraph text bound back on its own field."""
    local = narrative_schema(edition_profile("PREMARKET"))
    wire = transport_schema(local)
    for array in [wire["properties"]["summary"], *wire["properties"]["sections"]["properties"].values()]:
        array["description"], bound = array["description"].split(" Each text: ", 1)
        array["items"]["properties"]["text"]["description"] = bound
    monkeypatch.setattr(synthesize, "transport_schema", lambda schema: json.loads(json.dumps(wire)))
    rejected = synthesize.factored_transport_schema(local)
    shape = dict(bytes=len(synthesize.compact_json(rejected).encode()), **schema_shape(rejected))
    assert shape == dict(bytes=5497, nodes=75, objects=12, properties=58)
    assert any(shape[measure] > ceiling for measure, ceiling in ACCEPTED_ENVELOPE.items())


def test_summary_and_section_paragraphs_are_one_wire_node_with_every_text_bound_described():
    for profile in (None, edition_profile("PREMARKET"), edition_profile("OPEN_30M")):
        local = NARRATIVE_SCHEMA if profile is None else narrative_schema(profile)
        wire = transport_schema(local)
        arrays = {"summary": wire["properties"]["summary"],
                  **{key: value for key, value in wire["properties"]["sections"]["properties"].items()}}
        items = {json.dumps(array["items"], sort_keys=True) for array in arrays.values()}
        assert len(items) == 1  # one node: summary and every section paragraph
        assert next(iter(arrays.values()))["items"]["properties"]["text"] == {"type": "string",
                                                                              "description": "Non-empty."}
        summary = local["properties"]["summary"]
        assert arrays["summary"]["description"] == (
            f"At most {summary['maxItems']} items. Each text: At most "
            f"{summary['items']['properties']['text']['maxLength']} characters, non-empty.")
        for key in ("macro", "equities", "attention", "events"):
            assert arrays[key]["description"] == "At most 1 items. Each text: At most 240 characters, non-empty."
        assert arrays["cuttingboard"]["description"] == \
            "At most 0 items. Each text: At most 240 characters, non-empty."
        factored = factored_transport_schema(local)
        paragraph_keys = {"text", "class", "evidence_ids", "uncertainty", "alternative"}
        found = []

        def collect(node):
            if isinstance(node, dict):
                if node.get("type") == "object" and set(node.get("properties", {})) == paragraph_keys:
                    found.append(node)
                for value in node.values():
                    collect(value)
            elif isinstance(node, list):
                for value in node:
                    collect(value)
        collect(factored)
        assert len(found) == 1  # the provider compiles one paragraph rule for the summary and every section


def test_budgets_are_the_authoritative_edition_limits():
    config = editions_config()
    rich, light_ = config["profiles"]["rich"], config["profiles"]["light"]
    assert (rich["input_limit_bytes"], rich["max_output_tokens"]) == (64000, 7000)
    assert (light_["input_limit_bytes"], light_["max_output_tokens"]) == (40000, 4500)


@pytest.mark.parametrize("now, checkpoint", [("2026-09-08T12:45:00+00:00", "PREMARKET"),
                                             ("2026-09-08T14:05:00+00:00", "OPEN_30M")])
def test_a_complete_response_with_a_take_fits_each_edition(now, checkpoint):
    packet = packet_at(utc(now), checkpoint=checkpoint)
    profile = edition_profile(checkpoint)
    context = analyst_context(packet, profile)
    value = with_take(edition_response(profile, context))
    assert validate_narrative(value, packet, context)
    _, user = synthesize.construct_prompt(packet, context=context)
    payload = json.loads(user)
    assert "take" in payload["output_schema"]["properties"]
    assert len(user.encode()) <= profile["input_limit_bytes"]
    assert acceptance_schema(narrative_schema(profile))["properties"]["take"]["properties"]["text"]["maxLength"] == 320
