"""Cross-language `bucket_key` / timeframe contract for HTF resampling (register C4).

This replays `fixtures/htf/bucket-key.json`, authored by the TS reference
implementation, and exists because the parity backlog left a standing instruction:

    "bucketKey ... NOT MIRRORED IN PYTHON ... No shared fixture can protect a
     function that exists on only one side, so a future Python resampler would
     reinvent the stride from scratch and could disagree with TypeScript silently.
     If a Python resampler is ever added, port bucketKey from the TypeScript
     source and add a shared bucket-key fixture in the same session."

C4 adds the resampler, so this is that fixture's Python half, in that session.

The two sharp edges the fixture targets:

  * THE DAY STRIDE. `min` keys are `dayNumber * 100_000 + slotOfDay`. A stride
    smaller than the largest `slotOfDay + 1` makes the same slot on consecutive
    days collide, silently merging a whole day of bars into its neighbour. The
    min/5 case spans two days at one slot precisely to catch that.
  * FLOOR vs TRUNCATE. `local_day_key` and the month ordinal floor. Python's `//`
    floors and `int()` truncates; they agree for positive operands and disagree for
    negative ones, so the fixture includes a negative UTC offset and a pre-1970
    instant. An implementation using `int()` passes every IST/UTC case and fails
    only there.
"""

import json
import math
from pathlib import Path

import pytest

from services.openscript.runtime.calendar import fixed_offset_calendar, session_calendar
from services.openscript.runtime.htf_resample import bucket_key
from services.openscript.runtime.timeframe import (
    Timeframe,
    infer_base_interval_seconds,
    parse_timeframe,
    timeframe_rank_seconds,
)

FIXTURE = (
    Path(__file__).resolve().parents[1].parent
    / "openalgo-openscript"
    / "fixtures"
    / "htf"
    / "bucket-key.json"
)


def _load():
    if not FIXTURE.is_file():
        return None
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


FX = _load()

pytestmark = pytest.mark.skipif(
    FX is None, reason="HTF bucket-key fixture not found (engine repo not a sibling)"
)


def _calendar(name):
    """Resolve a fixture calendar NAME through its recorded offset.

    The fixture carries the offsets so both sides build the same calendar from the
    same number; resolving a name to a hard-coded offset here would be a second
    source of truth.
    """
    spec = FX["calendars"][name]
    # Rebuilt from the RECORDED session, not a local table: a calendar that
    # dropped its session here would silently bucket from local midnight and the
    # case would pass against a calendar it was never generated under.
    if spec.get("sessionOpenSeconds") is not None:
        return session_calendar(spec["utcOffsetSeconds"], spec["sessionOpenSeconds"])
    return fixed_offset_calendar(spec["utcOffsetSeconds"])


def _tf(d):
    return Timeframe(unit=d["unit"], multiple=d["multiple"])


def _key_cases():
    return [pytest.param(c, id=c["timeframe"]["unit"] + str(c["timeframe"]["multiple"]) + "-" + c["calendar"])
            for c in FX["keyCases"]]


@pytest.mark.parametrize("case", _key_cases())
def test_bucket_key_matches_the_ts_fixture(case):
    cal = _calendar(case["calendar"])
    tf = _tf(case["timeframe"])
    for instant in case["instants"]:
        got = bucket_key(instant["t"], tf, cal)
        assert got == instant["key"], (
            f"{case['note']} / {instant['label']}: python {got} != TS {instant['key']}"
        )


def test_every_key_case_is_well_formed():
    """Mirrors the TS-side structural guard.

    Not paranoia: while the fixture was being authored, one case carried its
    timeframe as the STRING "M" rather than {unit, multiple}. `bucketKey` read
    `tf.unit`, got undefined, fell through its switch and returned undefined, so
    the fixture stored null keys and both sides "agreed" on nothing. The case
    meant to exercise the negative-epoch floor branch asserted nothing at all.
    """
    assert FX["keyCases"], "no key cases at all"
    for c in FX["keyCases"]:
        assert isinstance(c["timeframe"], dict), f"{c['note']}: timeframe must be an object"
        assert c["timeframe"]["unit"] in ("min", "D", "W", "M")
        assert c["timeframe"]["multiple"] > 0
        assert isinstance(c["reachableFromSource"], bool)
        assert c["calendar"] in FX["calendars"]
        assert c["instants"], f"{c['note']}: no instants"
        for instant in c["instants"]:
            assert isinstance(instant["key"], int), (
                f"{c['note']} / {instant['label']}: key is not an integer"
            )


def test_the_stride_case_really_spans_two_days_at_one_slot():
    """Non-vacuity for the stride guarantee itself."""
    case = next(
        c for c in FX["keyCases"]
        if c["timeframe"]["unit"] == "min" and c["timeframe"]["multiple"] == 5
    )
    keys = [i["key"] for i in case["instants"]]
    assert len(set(keys)) > 1, "all instants collapsed onto one key"
    first, last = case["instants"][0], case["instants"][-1]
    assert last["t"] - first["t"] == 86400, "the case no longer spans a day"
    assert last["key"] != first["key"], "same slot on the next day must be a different bucket"


@pytest.mark.parametrize("case", [pytest.param(c, id=repr(c["raw"])) for c in FX["parseCases"]])
def test_parse_timeframe_matches_the_ts_fixture(case):
    got = parse_timeframe(case["raw"])
    expected = case["timeframe"]
    if expected is None:
        assert got is None, f"parse_timeframe({case['raw']!r}) should be None, got {got}"
        return
    assert got is not None, f"parse_timeframe({case['raw']!r}) should parse"
    assert got.unit == expected["unit"]
    assert got.multiple == expected["multiple"]
    assert timeframe_rank_seconds(got) == case["rankSeconds"]


@pytest.mark.parametrize("case", [pytest.param(c, id=c["note"].replace(" ", "-")) for c in FX["inferCases"]])
def test_infer_base_interval_matches_the_ts_fixture(case):
    got = infer_base_interval_seconds(case["time"])
    expected = case["baseIntervalSeconds"]
    if expected is None:
        assert got is None, f"{case['note']}: expected None, got {got}"
    else:
        assert got is not None, f"{case['note']}: expected {expected}, got None"
        assert math.isclose(got, expected, rel_tol=0, abs_tol=1e-12), (
            f"{case['note']}: python {got} != TS {expected}"
        )


def test_multi_day_multiple_is_recorded_as_compiler_unreachable():
    """`parse_timeframe` accepts only D/1D, W/1W, M/1M, so a multiple>1 calendar
    timeframe cannot be produced from source -- yet `bucket_key` implements the
    arithmetic for it on both sides. The fixture flags which cases are reachable
    so a multiple>1 case is never mistaken for proof that the surface works.
    """
    assert parse_timeframe("2D") is None
    assert parse_timeframe("3W") is None
    assert parse_timeframe("6M") is None
    unreachable = [c for c in FX["keyCases"] if not c["reachableFromSource"]]
    assert unreachable, "the fixture no longer records the unreachable-multiple case"
    assert all(c["timeframe"]["multiple"] > 1 for c in unreachable)
