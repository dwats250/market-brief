"""The light budget profile, kept for tests after OPEN_30M moved to the opening profile (W43 S2e, OD-2).

Until W43 the opening-structure update was the only light edition, so tests reached "the light profile" through
`edition_profile("OPEN_30M")`. The light profile stays defined in `config/editions.json`; these helpers give it a
checkpoint again, explicitly, so its selection, bounds and headroom rules stay tested."""

import pytest

from market_brief import context as context_module

_CONFIG = context_module.editions_config


def light_config():
    config = _CONFIG()
    config["editions"]["OPEN_30M"] = dict(config["editions"]["OPEN_30M"], profile="light", words=[140, 220])
    return config


def light_profile():
    return context_module.edition_profile("OPEN_30M", light_config())


@pytest.fixture
def light_opening(monkeypatch):
    """Every edition_profile("OPEN_30M") in this test, including the CLI's and the validator's, is light."""
    monkeypatch.setattr(context_module, "editions_config", light_config)
