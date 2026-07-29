"""Cross-language series-digest contract (engine register C3).

Value parity between the TS reference executor and this Python service has never
been tested directly: the shared corpora pin diagnostics and IR shape, and the
only numeric TS<->Python pins in existence are hand-duplicated per-file
assertions in the SuperTrend Cluster tests. `digest_series` is the primitive
that makes real value fixtures possible, so it is pinned first and on its own.

The fixture is authored by the TS side (the reference executor, per the spine's
"one IR, two DAG executors" topology) and replayed here byte-for-byte.

ENCODING (see the fixture's own `note`): a JSON number is itself; `null` means
NaN; the STRING "-0" means negative zero. That last one is not decoration --
`JSON.stringify(-0)` emits `0`, so a bare literal loses the sign and the
negative-zero cases would assert nothing at all. `test_the_fixture_is_not_vacuous`
below exists to keep that honest.
"""

import json
import math
from pathlib import Path

import pytest

from services.openscript.runtime.value_digest import digest_series

FIXTURE = (
    Path(__file__).resolve().parents[1].parent
    / "openalgo-openscript"
    / "fixtures"
    / "digest"
    / "series-digest.json"
)


def _load():
    if not FIXTURE.is_file():
        return None
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


FIXTURE_DATA = _load()

pytestmark = pytest.mark.skipif(
    FIXTURE_DATA is None,
    reason="digest contract fixture not found (engine repo not a sibling)",
)


def _decode(v):
    """JSON -> float, honouring the fixture's explicit NaN / negative-zero encoding."""
    if v is None:
        return float("nan")
    if isinstance(v, str):
        if v == "-0":
            return -0.0
        raise AssertionError(f"unknown sentinel in fixture: {v!r}")
    return float(v)


def _cases():
    return [pytest.param(c, id=c["name"].replace(" ", "-")) for c in FIXTURE_DATA["cases"]]


@pytest.mark.parametrize("case", _cases())
def test_python_digest_matches_the_ts_fixture(case):
    values = [_decode(v) for v in case["values"]]
    assert digest_series(values) == case["digest"], (
        f"{case['name']}: Python digest diverged from the TS reference. "
        "Cross-language value parity rests on this being byte-identical."
    )


def test_the_fixture_is_not_vacuous():
    """The negative-zero cases must actually carry a NEGATIVE zero.

    If the sentinel is ever dropped and the fixture regenerated with bare
    literals, every value would decode to +0.0, the cases would still PASS, and
    the divergence they exist to catch would be invisible. Two earlier tests in
    this project passed against a broken engine for exactly this reason.
    """
    signs = [
        math.copysign(1.0, _decode(v))
        for c in FIXTURE_DATA["cases"]
        for v in c["values"]
        if v == "-0"
    ]
    assert signs, "no negative-zero case survived encoding — the fixture proves nothing"
    assert all(s == -1.0 for s in signs), "a '-0' sentinel decoded to POSITIVE zero"


def test_nan_is_distinguishable_from_zero():
    """NaN must not digest the same as 0.0, or warmup bars and real zeros merge."""
    assert digest_series([float("nan")]) != digest_series([0.0])


def test_negative_zero_digests_as_positive_zero():
    """The whole point of the normalization, asserted directly rather than only
    via the fixture: IEEE says -0 == 0, so they must be one value here."""
    assert digest_series([-0.0]) == digest_series([0.0])
