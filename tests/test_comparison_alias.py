"""The 2026-10-16 opening analysis was rejected for one citation (run 38912496933, archive artifact 12050917362,
response gen-1792159351-Soi4gADzHFEeyV1FYxbE, $0.11924): `watch_updates[1].evidence_ids` named the comparison
`cmp-latest-AAPL-intraday` beside the two prints it compares. The prompt forbids a comparison id as evidence and the
validator still does. A comparison is defined as its two operands, so where prior refs are admissible the alias is
rewritten to exactly those operands before validation, disclosed, and every check then runs on the result. Anywhere
else the alias stays fatal, and every rejection now names the record and the reference."""

import copy

import pytest
from test_continuity import carried_setup, trimmed
from test_pipeline import narrative

from market_brief.context import edition_profile
from market_brief.synthesize import COMPARISON_ALIAS, canonicalize_comparison_aliases, validate_narrative

CARRIED = "watch-sample-premarket-124500-tue-1"
ALIAS = "cmp-premarket-SPY-intraday"
OPERANDS = ["SPY-intraday", "premarket:SPY-intraday"]
# The incident's record as the analyst returned it (its reason verbatim), grafted onto the fixture's own prints.
INCIDENT_REASON = "AAPL holds positive and NVDA stays lower, though AAPL's lead has shrunk."


def opening(**records):
    value = trimmed(narrative(), edition_profile("OPEN_30M"))
    value.update(records)
    return value


def incident_update(evidence_ids):
    return dict(carried_id=CARRIED, assessment="weakened", reason=INCIDENT_REASON, evidence_ids=evidence_ids)


def test_the_incident_is_still_rejected_and_the_rejection_names_the_record_and_the_reference():
    packet, context, _ = carried_setup()
    value = opening(watch_updates=[incident_update(["SPY-intraday", "QQQ-daily", ALIAS])])
    with pytest.raises(ValueError, match=f"unsupplied evidence reference at watch_updates\\[0\\]: {ALIAS}$"):
        validate_narrative(value, packet, context)


def test_the_alias_becomes_its_two_operands_and_the_record_validates():
    packet, context, _ = carried_setup()
    value = opening(watch_updates=[incident_update(["SPY-intraday", "QQQ-daily", ALIAS])])
    original = copy.deepcopy(value)
    repaired, repairs = canonicalize_comparison_aliases(value, context)
    assert value == original  # the analyst's response is kept as returned
    assert repaired["watch_updates"][0]["evidence_ids"] == ["SPY-intraday", "QQQ-daily", "premarket:SPY-intraday"]
    assert repairs == [{"path": "watch_updates[0].evidence_ids", "aliases": {ALIAS: OPERANDS},
                        "from": ["SPY-intraday", "QQQ-daily", ALIAS],
                        "to": ["SPY-intraday", "QQQ-daily", "premarket:SPY-intraday"], "rule": COMPARISON_ALIAS}]
    assert validate_narrative(repaired, packet, context)
    # Nothing but the citation changed: the prose and every other field are the analyst's.
    stripped = copy.deepcopy(repaired)
    stripped["watch_updates"][0]["evidence_ids"] = original["watch_updates"][0]["evidence_ids"]
    assert stripped == original


def test_a_change_alias_is_rewritten_to_exactly_its_own_comparison():
    packet, context, _ = carried_setup()
    value = opening(changes=[dict(comparison_id=ALIAS, text="SPY turned since the premarket.",
                                  evidence_ids=[ALIAS])])
    repaired, repairs = canonicalize_comparison_aliases(value, context)
    assert repaired["changes"][0]["evidence_ids"] == OPERANDS
    assert [r["path"] for r in repairs] == ["changes[0].evidence_ids"]
    assert validate_narrative(repaired, packet, context)


@pytest.mark.parametrize("place", ["summary", "take", "banner", "character", "watch"])
def test_an_alias_outside_prior_admitting_records_is_left_for_the_validator(place):
    packet, context, _ = carried_setup()
    value = opening()
    record = {"summary": lambda v: v["summary"][0], "take": lambda v: v["take"], "banner": lambda v: v["banner"],
              "character": lambda v: v["character"], "watch": lambda v: v["watches"][0]}[place](value)
    record["evidence_ids"] = [*record["evidence_ids"], ALIAS]
    repaired, repairs = canonicalize_comparison_aliases(value, context)
    assert repairs == [] and repaired == value
    with pytest.raises(ValueError, match=ALIAS):
        validate_narrative(repaired, packet, context)


def test_a_new_relationship_cannot_cite_prior_refs_so_its_alias_is_not_rewritten():
    packet, context, _ = carried_setup()
    value = opening()
    value["relationships"][0]["evidence_ids"] = [*value["relationships"][0]["evidence_ids"], ALIAS]
    assert value["relationships"][0]["carried_id"] is None
    repaired, repairs = canonicalize_comparison_aliases(value, context)
    assert repairs == []
    with pytest.raises(ValueError):
        validate_narrative(repaired, packet, context)


def test_a_comparison_without_both_supplied_operands_is_not_rewritten():
    packet, context, _ = carried_setup()
    # A light context lists unchanged comparisons by id and status only: no operands, nothing to rewrite to.
    compact = next(c for c in context["comparisons"] if "current_ref" not in c)
    value = opening(watch_updates=[incident_update(["SPY-intraday", compact["id"]])])
    repaired, repairs = canonicalize_comparison_aliases(value, context)
    assert repairs == []
    with pytest.raises(ValueError, match=f"at watch_updates\\[0\\]: {compact['id']}$"):
        validate_narrative(repaired, packet, context)


def test_unknown_ids_and_cold_starts_are_untouched_and_named():
    packet, context, _ = carried_setup()
    value = opening(watch_updates=[incident_update(["SPY-intraday", "cmp-invented-SPY"])])
    repaired, repairs = canonicalize_comparison_aliases(value, context)
    assert repairs == []
    with pytest.raises(ValueError, match="at watch_updates\\[0\\]: cmp-invented-SPY$"):
        validate_narrative(repaired, packet, context)
    cold = dict(context, prior_state=dict(status="cold_start", reason="none"))
    assert canonicalize_comparison_aliases(value, cold) == (value, [])


def test_an_unadmitted_prior_ref_is_named_too():
    packet, context, _ = carried_setup()
    value = opening(watch_updates=[incident_update(["SPY-intraday", "premarket:INVENTED-intraday"])])
    with pytest.raises(ValueError, match="prior evidence reference not admitted for this record at "
                                         "watch_updates\\[0\\]: premarket:INVENTED-intraday$"):
        validate_narrative(value, packet, context)


def paid_response(value, calls):
    import json

    def requester(payload, api_key, timeout=180):
        calls.append(payload)
        return {"id": "gen-test", "provider": "Anthropic", "model": "anthropic/claude-opus-5.5",
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.01},
                "choices": [{"finish_reason": "stop", "message": {"content": json.dumps(value)}}]}
    return requester


def test_the_paid_path_repairs_records_and_still_makes_exactly_one_call(capsys):
    from market_brief.synthesize import synthesize_openrouter
    packet, context, _ = carried_setup()
    value = opening(watch_updates=[incident_update(["SPY-intraday", "QQQ-daily", ALIAS])])
    calls = []
    output, model = synthesize_openrouter(packet, api_key="k", requester=paid_response(value, calls), context=context)
    assert len(calls) == 1
    assert output["watch_updates"][0]["evidence_ids"][-1] == "premarket:SPY-intraday"
    assert [(r["path"], r["aliases"]) for r in model["repairs"]] == [
        ("watch_updates[0].evidence_ids", {ALIAS: OPERANDS})]
    printed = capsys.readouterr().out
    assert f"Citation repaired (comparison-alias.v1): watch_updates[0].evidence_ids {ALIAS} -> SPY-intraday" in printed


def test_with_the_repair_disabled_the_paid_rejection_archives_the_response_as_returned(monkeypatch):
    from market_brief import synthesize
    monkeypatch.setattr(synthesize, "enabled_repairs", lambda config=None: set())
    packet, context, _ = carried_setup()
    value = opening(watch_updates=[incident_update(["SPY-intraday", "QQQ-daily", ALIAS])])
    calls = []
    with pytest.raises(synthesize.NarrativeRejected, match=f"at watch_updates\\[0\\]: {ALIAS};") as excinfo:
        synthesize.synthesize_openrouter(packet, api_key="k", requester=paid_response(value, calls), context=context)
    assert len(calls) == 1 and excinfo.value.narrative == value
    assert excinfo.value.attempt["repairs"] == []


def test_a_repair_that_does_not_rescue_the_response_archives_the_original():
    from market_brief import synthesize
    packet, context, _ = carried_setup()
    value = opening(watch_updates=[incident_update(["SPY-intraday", ALIAS])])
    value["summary"][0]["evidence_ids"] = [*value["summary"][0]["evidence_ids"], ALIAS]  # still fatal
    with pytest.raises(synthesize.NarrativeRejected, match=ALIAS) as excinfo:
        synthesize.synthesize_openrouter(packet, api_key="k", requester=paid_response(value, []), context=context)
    assert excinfo.value.narrative == value
    assert [r["path"] for r in excinfo.value.attempt["repairs"]] == ["watch_updates[0].evidence_ids"]


def test_the_repair_is_enabled_in_configuration():
    from market_brief.synthesize import enabled_repairs
    assert enabled_repairs() == {COMPARISON_ALIAS}


def test_the_page_and_its_markdown_disclose_the_rewrite():
    from market_brief.render import render
    packet, context, _ = carried_setup()
    value = opening(watch_updates=[incident_update(["SPY-intraday", "QQQ-daily", ALIAS])])
    repaired, repairs = canonicalize_comparison_aliases(value, context)
    markdown, page = render(packet, validate_narrative(repaired, packet, context), context, repairs=repairs)
    line = f"watch_updates[0]: {ALIAS} → SPY-intraday, premarket:SPY-intraday"
    assert "Citations rewritten from a comparison to the two observations it compares: " in page and line in page
    assert "Citations rewritten" in markdown and "→ SPY-intraday, premarket:SPY-intraday" in markdown
    _, plain = render(packet, validate_narrative(opening(), packet, context), context)
    assert "Citations rewritten" not in plain


def test_a_stale_refresh_says_how_many_prints_were_too_old_and_when_the_feed_stopped():
    """2026-10-15 HOURLY_1400 (run 38782152279): all 21 IEX prints were older than twenty minutes; the newest was
    stamped 16:56:33Z, an hour before the run. The refusal stands; its reason now says so."""
    from market_brief.cli import missing_prints_reason
    rows = [dict(id=f"S{i}-intraday", frequency="intraday", status="STALE",
                 observed_at="2026-10-15T16:56:33.617587305Z" if i == 0 else "2026-10-15T16:50:00Z")
            for i in range(21)]
    rows.append(dict(id="treasury-10y", frequency="daily", status="BACKGROUND", observed_at="2026-10-14"))
    assert missing_prints_reason(dict(observations=rows)) == (
        "refresh has no timestamped current prints: 21 intraday prints (21 stale; the newest stale print was "
        "stamped 12:56:33 PM ET / 9:56:33 AM PT); the last accepted page stands")
    assert "the price source returned none" in missing_prints_reason(dict(observations=rows[-1:]))
    odd = [dict(frequency="intraday", status="UNAVAILABLE", observed_at="2026-10-15T20:00:30Z"),
           dict(frequency="intraday", status="STALE", observed_at=12345),
           dict(frequency="intraday", status="INVALID", observed_at="not a time")]
    assert missing_prints_reason(dict(observations=odd)) == (
        "refresh has no timestamped current prints: 3 intraday prints (1 invalid, 1 stale, 1 unavailable); "
        "the last accepted page stands")


def test_malformed_or_repeated_citation_lists_are_left_exactly_as_returned():
    packet, context, _ = carried_setup()
    for refs in (["SPY-intraday", {"x": 1}, ALIAS], ["SPY-intraday", "SPY-intraday", ALIAS], [ALIAS, ALIAS]):
        value = opening(watch_updates=[incident_update(refs)])
        assert canonicalize_comparison_aliases(value, context) == (value, [])
        with pytest.raises(ValueError):
            validate_narrative(value, packet, context)
    assert canonicalize_comparison_aliases(opening(watch_updates="not a list"), context)[1] == []


def test_an_operand_already_cited_is_not_repeated_and_the_original_can_be_rebuilt():
    from market_brief.synthesize import as_returned
    packet, context, _ = carried_setup()
    value = opening(watch_updates=[incident_update([ALIAS, "QQQ-daily", "SPY-intraday"])])
    repaired, repairs = canonicalize_comparison_aliases(value, context)
    assert repaired["watch_updates"][0]["evidence_ids"] == ["premarket:SPY-intraday", "QQQ-daily", "SPY-intraday"]
    assert validate_narrative(repaired, packet, context)
    assert as_returned(repaired, repairs) == value


@pytest.mark.parametrize("setting", [None, "comparison-alias.v1", [1]])
def test_a_malformed_repair_setting_fails_before_any_request(setting, monkeypatch):
    from market_brief import synthesize
    config = synthesize.read_json(synthesize.ROOT / "config/editions.json")
    config["analyst"]["repairs"] = setting
    with pytest.raises(ValueError, match="analyst.repairs must be a list"):
        synthesize.enabled_repairs(config)
    monkeypatch.setattr(synthesize, "enabled_repairs", lambda config=None: (_ for _ in ()).throw(
        ValueError("analyst.repairs must be a list of rule names")))
    packet, context, _ = carried_setup()
    calls = []
    with pytest.raises(ValueError, match="analyst.repairs"):
        synthesize.synthesize_openrouter(packet, api_key="k", requester=paid_response(opening(), calls),
                                         context=context)
    assert calls == []
