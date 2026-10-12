"""The Week Ahead prototype builds offline from its saved snapshot, and every figure in its prose is evidence."""

import importlib.util
import json
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SUNDAY = ROOT / "experiments/sunday"


def builder():
    spec = importlib.util.spec_from_file_location("sunday_build", SUNDAY / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def snapshot(tmp_path):
    folder = tmp_path / "2026-10-11"
    shutil.copytree(SUNDAY / "2026-10-11", folder)
    return folder


def test_prototype_builds_offline_with_dated_sourced_evidence(snapshot):
    page = builder().build(snapshot).read_text()
    evidence = json.loads((snapshot / "evidence.json").read_text())
    assert all(row["observed_at"] and row["source_name"] and row["kind"] for row in evidence["rows"])
    assert "Real yields hold the long end high as CPI arrives" in page
    assert "Not collected" in page and "not published" in page.lower()
    # The week's 10Y decomposition comes from Treasury's nominal and real curves, not from prose.
    rows = {row["id"]: row for row in evidence["rows"]}
    assert (rows["since-sep1-nominal-10y"]["value"], rows["since-sep1-real-10y"]["value"]) == (45, 47)


def test_unknown_evidence_or_a_literal_figure_fails_the_build(snapshot):
    path = snapshot / "edition.json"
    edition = json.loads(path.read_text())
    edition["take"]["text"] = "Yields at {no-such-row}."
    path.write_text(json.dumps(edition))
    with pytest.raises(ValueError, match="unknown evidence id"):
        builder().build(snapshot)
    edition["take"]["text"] = "Yields rose 12 bp."
    path.write_text(json.dumps(edition))
    with pytest.raises(ValueError, match="placeholder"):
        builder().build(snapshot)


def test_prototype_never_writes_the_production_page(snapshot):
    before = (ROOT / "publish/index.html").read_bytes()
    builder().build(snapshot)
    assert (ROOT / "publish/index.html").read_bytes() == before
