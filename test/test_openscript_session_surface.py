"""The session surface end to end on the Python runtime — mirror of the
engine's `tests/executor.test.ts` session suites (literal + input-bound) and the
admission `field` gate.

The shared value fixtures (`fixtures/values/pos-session-*.json`) replay the
literal AND unbound-input lowerings over Mon-Fri data, but the week5m dataset
has no Saturday bar and its "23456" mask therefore never excludes anything — so
the dN ↔ days[N-1] index shift (the P8 backlog's named trap: `d7` must read
`days[6]`) is NOT covered by fixture replay. The Saturday-only override test
here is the one check that catches an off-by-one in that mapping, exactly as
its TS twin is on the engine side.
"""

import numpy as np
import pytest

from services.openscript import openscript
from services.openscript.runtime.admit import admit_ir
from services.openscript.runtime.executor import SessionInputError, execute_ir

# Epoch seconds for an IST (UTC+5:30) wall-clock instant — the same arithmetic
# the TS suite's `ist()` helper uses, so both mirrors sample identical instants.
_IST_OFFSET = 19_800


def _ist(y: int, mo: int, d: int, h: int, mi: int) -> int:
    import calendar as _cal
    import datetime as _dt

    return _cal.timegm(_dt.datetime(y, mo, d, h, mi).timetuple()) - _IST_OFFSET


# VERIFIED: 2026-08-03 is a Monday, 2026-08-04 a Tuesday, 2026-08-08 a Saturday.
# Shared by the literal AND input-bound suites below, so the two lowering paths
# are sampled at the SAME seven instants (mirrors the TS `sessionTimes`).
SESSION_TIMES = [
    _ist(2026, 8, 3, 9, 10),   # Mon pre-open  -> C 0
    _ist(2026, 8, 3, 9, 15),   # Mon open      -> C 1, F 1, B 1
    _ist(2026, 8, 3, 9, 20),   #               -> B 2
    _ist(2026, 8, 3, 15, 25),  # last bar      -> C 1
    _ist(2026, 8, 3, 15, 30),  # close, half-open -> C 0, B 0
    _ist(2026, 8, 4, 9, 15),   # Tue open      -> F 1, B 1
    _ist(2026, 8, 8, 9, 15),   # SATURDAY      -> mask excludes -> C 0
]


def _flat_dataset(times) -> dict:
    n = len(times)
    px = np.array([100.0 + i for i in range(n)])
    return {
        "time": np.asarray(times, dtype=float),
        "open": px.copy(),
        "high": px.copy(),
        "low": px.copy(),
        "close": px.copy(),
        "volume": np.ones(n),
    }


def _run_on_times(src: str, times, inputs=None) -> dict:
    result = openscript.compile(src)
    errors = [d for d in result.diagnostics if d.severity == "error"]
    assert not errors, [d.code for d in errors]
    assert result.ir is not None
    outputs = execute_ir(result.ir, _flat_dataset(times), inputs or {})
    return {o["title"]: list(o["values"]) for o in outputs if o.get("values") is not None}


LITERAL_SRC = (
    'indicator("s")\n'
    'plot(session.contains("0915-1530:23456") ? 1 : 0, "C")\n'
    'plot(session.first_bar("0915-1530:23456") ? 1 : 0, "F")\n'
    'plot(session.bars_in("0915-1530:23456"), "B")\n'
)

BOUND_SRC = (
    'indicator("s")\n'
    'sess = input.session("0915-1530:23456", title="Session")\n'
    'plot(session.contains(sess) ? 1 : 0, "C")\n'
    'plot(session.bars_in(sess), "B")\n'
)


def test_literal_contains_first_bar_bars_in_match_the_hand_derived_table():
    out = _run_on_times(LITERAL_SRC, SESSION_TIMES)
    assert out["C"] == [0, 1, 1, 1, 0, 1, 0]
    assert out["F"] == [0, 1, 0, 0, 0, 1, 0]
    assert out["B"] == [0, 1, 2, 3, 0, 1, 0]


def test_first_bar_day_changed_disjunct_survives_the_same_dayofmonth_trap():
    # "0000-2359" has NO day mask and covers the whole clock, so ONLY the
    # dayChanged disjunct can flip first_bar. Mar-1 and Apr-1 share the SAME
    # dayofmonth: without the month/year terms, Apr-1 would read as "still the
    # same session run". THE named mutation target (mirrors the TS twin).
    daily = [_ist(2026, 3, 1, 9, 15), _ist(2026, 4, 1, 9, 15)]
    out = _run_on_times(
        'indicator("s")\nplot(session.first_bar("0000-2359") ? 1 : 0, "F")\n', daily
    )
    assert out["F"] == [1, 1]


def test_unbound_input_default_reproduces_the_literal_table():
    out = _run_on_times(BOUND_SRC, SESSION_TIMES)
    assert out["C"] == [0, 1, 1, 1, 0, 1, 0]
    assert out["B"] == [0, 1, 2, 3, 0, 1, 0]


def test_bound_clock_override_moves_the_boundary_at_run_time():
    # Hand-derived under "0920-1530:23456": Mon 09:15 (in under the default) is
    # now OUT and Mon 09:20 is the session's FIRST bar; Tue 09:15 is out too.
    out = _run_on_times(BOUND_SRC, SESSION_TIMES, {"sess": "0920-1530:23456"})
    assert out["C"] == [0, 0, 1, 1, 0, 0, 0]
    assert out["B"] == [0, 0, 1, 2, 0, 0, 0]


def test_bound_saturday_only_mask_flips_the_mask_the_days_index_shift_trap():
    # ":7" admits ONLY Saturday: day 7 in the 1=Sunday..7=Saturday convention,
    # stored at days[6] by parse_session_string — so d7 must read days[6], the
    # one-off mapping nothing else in the suite (or the shared fixtures)
    # exercises. An off-by-one in the dN -> days[] mapping shifts every session
    # by a day and flips exactly these expectations.
    out = _run_on_times(BOUND_SRC, SESSION_TIMES, {"sess": "0915-1530:7"})
    assert out["C"] == [0, 0, 0, 0, 0, 0, 1]
    assert out["B"] == [0, 0, 0, 0, 0, 0, 1]


def test_a_malformed_bound_string_raises_os4005_naming_the_input():
    result = openscript.compile(BOUND_SRC)
    assert not [d for d in result.diagnostics if d.severity == "error"]
    assert result.ir is not None
    with pytest.raises(SessionInputError) as exc:
        execute_ir(result.ir, _flat_dataset(SESSION_TIMES), {"sess": "garbage"})
    assert exc.value.code == "OS4005"
    assert "'sess'" in str(exc.value)


# ── the admission `field` gate (mirror of the TS admit.test.ts checks) ───────


def _bound_ir() -> dict:
    result = openscript.compile(BOUND_SRC)
    assert result.ir is not None
    return result.ir


def test_admission_accepts_the_compiler_emitted_field_nodes():
    assert admit_ir(_bound_ir()) == []


def test_admission_rejects_a_field_on_a_non_session_input():
    ir = _bound_ir()
    ir["inputs"][0]["type"] = "string"
    errors = admit_ir(ir)
    assert any(
        e["code"] == "IR_BAD_INPUT_REF" and "binds non-session input" in e["message"]
        for e in errors
    )


def test_admission_rejects_a_field_name_outside_the_nine():
    ir = _bound_ir()
    forged = next(n for n in ir["nodes"] if n["op"] == "input" and n.get("field") is not None)
    forged["field"] = "d8"
    errors = admit_ir(ir)
    assert any(
        e["code"] == "IR_BAD_INPUT_REF" and "unknown session field 'd8'" in e["message"]
        for e in errors
    )
