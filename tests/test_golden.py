"""Golden-file regression tests for build_sld.py.

Each case builds a lineup and compares a fingerprint of the modelspace against
a recorded one. A refactor that is meant to preserve geometry should leave
these silent; anything else prints what moved.
"""

import json

import pytest

from cases import CASES
from diff import describe
from fingerprint import FINGERPRINT_VERSION, fingerprint_dxf

IDS = [c.name for c in CASES]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Build every case once and fingerprint it.

    Module-scoped because each build shells out and imports a symbol library;
    doing that per-test would triple the runtime for no extra coverage.
    """
    out = {}
    tmp = tmp_path_factory.mktemp("sld")
    for case in CASES:
        dxf = tmp / f"{case.name}.dxf"
        case.build(dxf)
        out[case.name] = (dxf, fingerprint_dxf(dxf))
    return out


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_matches_golden(case, built):
    if not case.golden_path.exists():
        pytest.fail(
            f"no golden file for {case.name}. Record one with:\n"
            f"    python tests/regen_golden.py {case.name}")

    golden = json.loads(case.golden_path.read_text(encoding="utf-8"))
    _, current = built[case.name]

    if golden.get("fingerprint_version") != FINGERPRINT_VERSION:
        pytest.fail(
            f"{case.name}: golden was recorded by fingerprint version "
            f"{golden.get('fingerprint_version')}, this is "
            f"{FINGERPRINT_VERSION}. Re-record all cases:\n"
            f"    python tests/regen_golden.py")

    report = describe(golden, current, case.name)
    if report:
        pytest.fail(report)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_build_is_deterministic(case, built, tmp_path):
    """A second build of the same input must fingerprint identically.

    Without this, a real geometry regression could hide behind fingerprint
    noise -- and a golden file recorded from a nondeterministic build would
    fail at random, which trains people to regenerate goldens reflexively.
    """
    _, first = built[case.name]
    again = tmp_path / f"{case.name}_again.dxf"
    case.build(again)

    assert fingerprint_dxf(again)["entities"] == first["entities"], (
        f"{case.name}: two builds of identical input disagree, so the "
        f"generator is not deterministic and golden files cannot be trusted")


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_output_is_not_empty(case, built):
    """Guard the guard: an empty drawing must not silently match an empty golden."""
    _, fp = built[case.name]
    assert fp["entity_count"] > 0, f"{case.name} produced no entities"
    assert not fp["unrecognised_types"], (
        f"{case.name} emitted entity types the fingerprint does not cover: "
        f"{fp['unrecognised_types']}. Add them to fingerprint.entity_record, "
        f"otherwise changes to them go undetected.")
