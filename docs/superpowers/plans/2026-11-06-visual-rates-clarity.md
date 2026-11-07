# Visual Rates Clarity Implementation Plan

Goal: Make each Treasury maturity's verified daily change readable at phone widths.

Architecture: Use the existing rendered change string and existing stale/absent gates. Add neutral arrows and Higher/Lower/Unchanged labels in the existing Treasury table. No data, comparison, freshness, or evidence logic changes.

- [x] Add template-only direction labels and stronger basis-point typography.
- [x] Verify positive, negative, zero, absent and stale behavior with existing fixture data.
- [x] Run rates, rendering and evidence tests; measure phone and desktop overflow.
- [x] Capture fixture before/after screenshots, commit, push and open a draft PR.
