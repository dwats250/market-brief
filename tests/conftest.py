"""Every test is offline: no OpenRouter request, no Claude CLI invocation, no paid synthesis."""

import shutil

import pytest

_real_which = shutil.which


@pytest.fixture(autouse=True)
def no_live_synthesis(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("MARKET_BRIEF_MODEL", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("tests must never open a connection to OpenRouter")

    monkeypatch.setattr("market_brief.synthesize.urlopen", forbidden)
    # The analyst CLI may be installed on a developer machine; tests must stub the runner explicitly.
    monkeypatch.setattr(shutil, "which",
                        lambda cmd, *args, **kwargs: None if cmd == "claude" else _real_which(cmd, *args, **kwargs))
