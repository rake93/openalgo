"""The session-string grammar (session-surface design §3) — Python mirror of the
engine's `tests/session-string.test.ts` matrix, INCLUDING the exact-message
assertions.

Error-message texts are FROZEN by design (the two implementations replicate
them verbatim and shared fixtures pin the codes), so named rejects assert exact
text — a regex loose enough to match two different messages would let the two
implementations drift apart silently.

One case exists here that the TS file cannot have: a Unicode-digit session
string must be REJECTED. TS `\\d` is ASCII-only while Python's `\\d` matches any
Unicode decimal digit (and `int()` parses them), so a naive transliteration
would ACCEPT "٠٩١٥-١٥٣٠" that the engine rejects — the exact divergence the
`[0-9]` + `re.fullmatch` discipline in `session_string.py` exists to close.
"""

import pytest

from services.openscript.runtime.session_string import (
    ParsedSession,
    SessionParseError,
    parse_session_string,
)

# ── accepts (mirrors the TS `parseSessionString — accepts` block) ────────────


def test_parses_a_plain_window_all_days_implied():
    assert parse_session_string("0915-1530") == ParsedSession(
        open_minutes=9 * 60 + 15,
        close_minutes=15 * 60 + 30,
        days=(True, True, True, True, True, True, True),
    )


def test_parses_a_day_masked_window_mon_to_fri():
    assert parse_session_string("0915-1530:23456") == ParsedSession(
        open_minutes=555,
        close_minutes=930,
        days=(False, True, True, True, True, True, False),
    )


def test_parses_a_sparse_mask_and_midnight_open():
    assert parse_session_string("0000-2359:17") == ParsedSession(
        open_minutes=0,
        close_minutes=23 * 60 + 59,
        days=(True, False, False, False, False, False, True),
    )


def test_trims_surrounding_whitespace():
    assert parse_session_string(" 0915-1530\t") == ParsedSession(
        open_minutes=9 * 60 + 15,
        close_minutes=15 * 60 + 30,
        days=(True, True, True, True, True, True, True),
    )


def test_parses_a_single_day_mask_monday_only():
    assert parse_session_string("0915-1530:2") == ParsedSession(
        open_minutes=555,
        close_minutes=930,
        days=(False, True, False, False, False, False, False),
    )


def test_parses_the_full_seven_day_mask_same_as_omitted_days():
    assert parse_session_string("0915-1530:1234567") == ParsedSession(
        open_minutes=555,
        close_minutes=930,
        days=(True, True, True, True, True, True, True),
    )


# ── named rejects (exact frozen messages) ────────────────────────────────────


def _reject(raw: str, expected: str) -> None:
    r = parse_session_string(raw)
    assert isinstance(r, SessionParseError), f"{raw!r} must be rejected"
    assert r.error == expected


def test_rejects_the_midnight_wrap_by_name():
    _reject(
        "2300-0130",
        'session "2300-0130" crosses local midnight — not supported in v1.1 '
        "(openscript-session-surface-design.md §3)",
    )


def test_rejects_open_equal_to_close():
    _reject("0915-0915", 'session open must be before close, got "0915-0915"')


def test_rejects_bad_clock_digits():
    _reject("2560-1530", 'session times must be HHmm with HH 00-23 and mm 00-59, got "2560-1530"')
    _reject("0915-1575", 'session times must be HHmm with HH 00-23 and mm 00-59, got "0915-1575"')


def test_rejects_hh_24_the_boundary_is_00_23_not_00_24():
    _reject("2400-1530", 'session times must be HHmm with HH 00-23 and mm 00-59, got "2400-1530"')


def test_rejects_malformed_shapes():
    _reject("", 'session must be "HHmm-HHmm" or "HHmm-HHmm:days", got ""')
    _reject("0915", 'session must be "HHmm-HHmm" or "HHmm-HHmm:days", got "0915"')
    _reject("915-1530", 'session must be "HHmm-HHmm" or "HHmm-HHmm:days", got "915-1530"')
    _reject("0915-1530:", 'session days must be non-empty, got "0915-1530:"')


def test_rejects_day_digits_out_of_range_duplicated_or_out_of_order():
    _reject("0915-1530:08", 'session days are 1=Sunday..7=Saturday, got "0915-1530:08"')
    _reject(
        "0915-1530:22",
        'session days must be ascending, unique — canonical form e.g. "23456", got "0915-1530:22"',
    )
    _reject(
        "0915-1530:32",
        'session days must be ascending, unique — canonical form e.g. "23456", got "0915-1530:32"',
    )


# ── the Python-only trap case (unrepresentable in the TS suite) ──────────────


@pytest.mark.parametrize(
    "raw",
    [
        # Arabic-Indic digits throughout — `int()` would happily parse these.
        "٠٩١٥-١٥٣٠",
        # A single Devanagari digit smuggled into an otherwise-ASCII string.
        "0915-153०",
        # Fullwidth digits (U+FF10..) — `str.isdigit()` is True for all of them.
        "０９１５-１５３０",
    ],
)
def test_rejects_unicode_digits_that_python_re_d_would_accept(raw):
    """TS `\\d` is ASCII; Python `\\d` is not. The grammar must be ASCII-only."""
    r = parse_session_string(raw)
    assert isinstance(r, SessionParseError)
    assert r.error == f'session must be "HHmm-HHmm" or "HHmm-HHmm:days", got "{raw}"'
