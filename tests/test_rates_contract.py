"""The analyst contract for rates (R11): bounded labels, prompt rules, and what the context carries, with the
narrative schema unchanged byte for byte."""

import copy
import hashlib

import pytest
from test_history_admission import packet_at, utc
from test_pipeline import fixture_packet, narrative

from market_brief import synthesize as s
from market_brief.context import analyst_context, edition_profile, supplied_ids
from market_brief.evidence import ROOT

# market-brief.narrative.v2 exactly as on main @ 14670b0: the local contract and the provider-enforced factored form.
SCHEMA_SHA256 = {
    "rich": ("b1001861fdf0feff837c92caaa11603c7c523c76c61a072fe07582928e9f8212",
             "73f3b474eb7f5b2069e49b4d32d418370aaba3e45689589297d5c27bab1687ce"),
    "light": ("0b759f44f5bbf01c12f8b5f10e0873e609fdd34f22fa9487717dbf70a5d3ce6e",
              "da1a84fd5ca28fcdc73573004789e072b418b8552ee10b989150df92f16d7043"),
}
PROMPT = (ROOT / "prompts/synthesis.md").read_text()


def digest(value):
    return hashlib.sha256(s.compact_json(value).encode()).hexdigest()


def test_the_narrative_schema_is_byte_identical():
    for profile, checkpoint in (("rich", "PREMARKET"), ("light", "OPEN_30M")):
        schema = s.narrative_schema(edition_profile(checkpoint))
        assert (digest(schema), digest(s.factored_transport_schema(schema))) == SCHEMA_SHA256[profile], profile
    assert s.NARRATIVE_SCHEMA["properties"]["schema_version"] == {"const": "market-brief.narrative.v2"}


# --- bounded labels -----------------------------------------------------------------------------------------

def with_macro(text):
    value = copy.deepcopy(narrative())
    value["sections"]["macro"] = [dict(text=text, evidence_ids=["treasury-2y-change", "treasury-10y-change"],
                                       uncertainty="", alternative="", **{"class": "INTERPRETATION"})]
    return value


@pytest.mark.parametrize("text", [
    "Treasuries rallied at the front end, and 2s10s steepened.",
    "The long end led: 5s30s flattened while 2s10s steepened.",
    "2s10s steepened; the 10-year and 30Y led.",
])
def test_the_curve_slopes_pass_the_digit_rule(text):
    assert s.validate_narrative(with_macro(text), fixture_packet())


@pytest.mark.parametrize("text", [
    "The 3s10s spread steepened.", "The 2s30s spread steepened.", "The 10s30s spread steepened.",
    "The 2S10S spread steepened.", "The 2s10 spread steepened.", "The 2-10s spread steepened.",
    "The 2s10s5 spread steepened.", "2s10s steepened by 5 bp.", "5s30s flattened 3 basis points.",
])
def test_other_digit_strings_still_reject(text):
    with pytest.raises(ValueError, match="literal numeric"):
        s.validate_narrative(with_macro(text), fixture_packet())


def test_attention_reasons_accept_the_slopes_too():
    assert s.ALLOWED_LABELS.sub("", "2s10s and 5s30s") == " and "


# --- the prompt -----------------------------------------------------------------------------------------------

def test_the_prompt_states_the_rates_convention_and_timing_tersely():
    numbers = PROMPT.split("NUMBERS:", 1)[1].split("\n\n", 1)[0]
    assert "2s10s, 5s30s" in numbers
    rates = PROMPT.split("RATES.", 1)[1].split("\n\n", 1)[0]
    flat = " ".join(rates.split())
    for phrase in ("official daily par curve from the previous business day", "not intraday yields",
                   "never present them as the cause of, reaction to, or explanation for current-session prints",
                   "never say yields are moving now", "`curve.release_note`", "the curve does not reflect it",
                   '"Treasuries sold off; yields rose"', '"Treasuries rallied; yields fell"', "front end, long end",
                   "steepener, flattener, bull and bear", "changes are bp", "`curve.label`, exactly",
                   "never invent one", "Do not characterize the belly", '"Consistent with", "sensitive to" and '
                   '"alongside"', "never tie the prior-day curve to a same-session move"):
        assert phrase in flat, phrase
    assert len(rates.encode()) < 900  # terse: every call pays for it


# --- the context ----------------------------------------------------------------------------------------------

def rates_rows(packet):
    return {row["id"] for group in packet["catalog"] for row in group["rows"] if group["topic"].startswith("US ")}


def test_both_profiles_carry_the_curve_rows_and_the_curve_record():
    for now, checkpoint in (("2026-09-08T12:45:00+00:00", "PREMARKET"), ("2026-09-08T14:05:00+00:00", "OPEN_30M")):
        packet = packet_at(utc(now), checkpoint=checkpoint)
        context = analyst_context(packet, edition_profile(checkpoint))
        assert {"treasury-2s10s", "treasury-2s10s-change", "treasury-2y", "treasury-10y-change"} <= rates_rows(context)
        curve = context["curve"]
        assert curve["label"] == "Bull steepener" and curve["inputs"] == ["treasury-2y-change", "treasury-10y-change"]
        assert set(curve) <= {"label", "sentence", "note", "pair", "inputs", "observed_at", "prior_observed_at",
                              "freshness", "reason", "release_note"}
        assert "curve" not in supplied_ids(context) and not any("curve" == ident for ident in supplied_ids(context))


def test_the_light_selection_keeps_30y_and_the_spreads_as_anchors():
    from market_brief.context import ANCHOR_TOPICS
    assert {"US 30Y", "US 2s10s", "US 5s30s"} <= set(ANCHOR_TOPICS)


def test_the_curve_record_cannot_be_cited():
    packet = fixture_packet()
    value = with_macro("The curve steepened.")
    value["sections"]["macro"][0]["evidence_ids"] = ["curve"]
    with pytest.raises(ValueError, match="unsupplied evidence reference: curve"):
        s.validate_narrative(value, packet)
