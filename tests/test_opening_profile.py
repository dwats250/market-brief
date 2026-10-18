"""W43 S2e (owner ruling OD-2): OPEN_30M reads the full evidence record, with compact comparisons.

The opening profile is the rich catalog (every admitted row, nothing omitted) with the light profile's comparison
projection: full records only for `changed` comparisons, which are the only ones a narrative may interpret. Replayed
offline against the four W42 OPEN_30M runs (38409951093, 38576783285, 38747704585, 38912496933), the user message is
42,718-47,105 bytes against a 64,000 limit (rich as-is: 57,948-61,089); a synthetic CPI-day stress (every intraday
comparison changed, real yields and inflation compensation, RSP and IWM, six more events, doubled release rows,
three carried watches) is 53,389. Anything larger fails before the request and the checkpoint publishes as a
refresh (OD-1)."""

from test_continuity import carried_setup

from market_brief.context import analyst_context, edition_profile, supplied_ids
from market_brief.continuity import COMPARISON_FIELDS, admit_prior_state, compare_all, continuity_context
from market_brief.synthesize import construct_prompt, narrative_schema


def contexts():
    packet, _, bundle = carried_setup()
    prior = admit_prior_state(bundle, packet)
    comparisons = compare_all(prior, packet)
    built = {}
    for name, profile in (("opening", edition_profile("OPEN_30M")),
                          ("rich", dict(edition_profile("PREMARKET"), checkpoint="OPEN_30M"))):
        built[name] = dict(analyst_context(packet, profile, comparisons, prior),
                           **continuity_context(prior, comparisons, profile))
    return packet, comparisons, built


def test_open_30m_is_the_opening_profile_with_rich_bounds_and_its_own_word_range():
    profile = edition_profile("OPEN_30M")
    assert profile["profile"] == "opening" and profile["context"] == "full" and profile["comparisons"] == "changed"
    assert profile["words"] == [220, 320] and profile["input_limit_bytes"] == 64000
    assert profile["max_output_tokens"] == 7000
    assert (profile["summary_paragraphs"], profile["attention_items"], profile["watches"]) == (2, 3, 3)
    assert narrative_schema(profile) == narrative_schema(edition_profile("PREMARKET"))
    for duty in ("strongest and weakest sectors", "broad or concentrated", "by ID", "none in these sources",
                 "strongest rival", "previous business day"):
        assert duty in profile["guidance"], duty


def test_the_opening_reads_every_admitted_row_and_omits_nothing():
    _, _, built = contexts()
    assert built["opening"]["selection"] is None
    assert supplied_ids(built["opening"]) == supplied_ids(built["rich"])
    assert built["opening"]["catalog"] == built["rich"]["catalog"]


def test_only_changed_comparisons_carry_values_and_the_context_is_smaller_than_rich():
    packet, comparisons, built = contexts()
    opening = {c["id"]: c for c in built["opening"]["comparisons"]}
    assert set(opening) == {c["id"] for c in comparisons}
    for c in comparisons:
        expected = set(COMPARISON_FIELDS) if c["status"] == "changed" else {"id", "status"}
        assert set(opening[c["id"]]) == expected, c["id"]
    assert built["opening"]["comparison_summary"]["mode"] == "changed"
    sizes = {name: len(construct_prompt(packet, context=ctx)[1].encode()) for name, ctx in built.items()}
    assert sizes["opening"] < sizes["rich"]
