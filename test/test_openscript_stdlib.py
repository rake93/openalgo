"""The bundled standard library -- Python side: drift guard and module rules.

Two jobs, and the first one is the load-bearing one.

DRIFT. ``stdlib_src.py`` is an EMBEDDING of the engine repo's ``stdlib/*.os``. If
it goes stale, the server compiles indicators against primitives that no longer
match the ones the browser compiles against -- silently, since both sides would
still compile clean and simply disagree about what a bullish FVG is. So the bytes
are compared against the sibling checkout, not merely the parsed result.

RULES. The registry's build-time rules are re-asserted here rather than trusted
from the TS side, because they are what makes a stdlib body safe to inline: a
body that could capture, declare an input, or emit an output would be a defect
the script author could neither see nor work around.

Why the embedding is a ``.py`` module at all: ``COMPILER_FINGERPRINT`` hashes
``rglob("*.py")``, so a primitive's body change moves the fingerprint and rides
the staleness refresh that already exists. Data files would sit outside it.
"""

import re
from pathlib import Path

import pytest

from services.openscript.openscript.builtins_table import KNOWN_NAMESPACES
from services.openscript.openscript.stdlib import (
    STDLIB,
    STDLIB_NAMESPACES,
    StdlibBuildError,
    build_stdlib,
    stdlib_arity,
    stdlib_has,
    stdlib_is_windowed,
    stdlib_symbols,
)
from services.openscript.openscript.stdlib_src import STDLIB_MODULES

MANIFEST = ["candle", "fvg", "ob", "rjb", "pivot", "bos"]

ENGINE_STDLIB = Path(__file__).resolve().parents[1].parent / "openalgo-openscript" / "stdlib"


def _build(source: str, name: str = "probe"):
    return lambda: build_stdlib([(name, source)])


# ── drift ──────────────────────────────────────────────────────────────────────


def test_the_embedded_stdlib_matches_the_engine_sources():
    """Byte-for-byte against the engine's authoritative ``.os`` modules.

    Skips only when the engine repo is genuinely absent; platform CI always checks
    it out at ENGINE_REF: main, so CI always compares.
    """
    if not ENGINE_STDLIB.is_dir():
        pytest.skip("engine repo not beside the platform")
    embedded = dict(STDLIB_MODULES)
    on_disk = sorted(p.stem for p in ENGINE_STDLIB.glob("*.os"))
    assert sorted(embedded) == on_disk, "stdlib modules added/removed on one side only"
    for name in on_disk:
        want = (ENGINE_STDLIB / f"{name}.os").read_text(encoding="utf-8").replace("\r\n", "\n")
        if not want.endswith("\n"):
            want += "\n"
        # The string compared here is the one that actually COMPILES on the
        # server -- strictly stronger than comparing copied data files.
        assert embedded[name] == want, f"{name}: embedded source differs from the engine's"


def test_the_module_count_and_order_are_exact():
    """Order is contract, not detail: a module may reference only modules before
    it, which is what makes dependency cycles unexpressible."""
    assert [name for name, _ in STDLIB_MODULES] == MANIFEST


def test_the_embedding_sits_under_the_compiler_fingerprint():
    """The whole reason it is a ``.py`` module. If this file ever moved outside
    the hashed package, a stdlib change would stop invalidating stored IR."""
    from services.openscript.openscript import stdlib_src

    package_root = Path(__file__).resolve().parents[1] / "services" / "openscript" / "openscript"
    embedded_path = Path(stdlib_src.__file__).resolve()
    assert embedded_path.suffix == ".py"
    assert package_root in embedded_path.parents


def test_no_module_is_empty_or_unterminated():
    # Non-vacuity: six empty strings would satisfy the byte comparison against
    # six empty files.
    for name, source in STDLIB_MODULES:
        assert source.strip(), f"{name} is empty"
        assert source.endswith("\n"), f"{name} is not newline-terminated"
        assert "\r" not in source, f"{name} carries CR bytes"


# ── the shipped registry ───────────────────────────────────────────────────────


def test_registers_exactly_the_manifest_namespaces():
    assert sorted(STDLIB) == sorted(MANIFEST)
    assert STDLIB_NAMESPACES == frozenset(MANIFEST)


def test_exposes_the_primitives_the_ports_depend_on():
    # Named, not counted: a count still passes if a primitive is renamed, and
    # these names are a frozen contract.
    for sym in [
        "candle.up", "candle.down",
        "fvg.up", "fvg.down", "fvg.up_top", "fvg.up_bottom", "fvg.up_mid",
        "fvg.down_top", "fvg.down_bottom", "fvg.down_mid",
        "ob.up", "ob.down", "ob.up_top", "ob.up_bottom", "ob.down_top", "ob.down_bottom",
        "rjb.down_wick", "rjb.down_signal", "rjb.up_wick", "rjb.up_signal",
        "pivot.last_high", "pivot.last_low",
        "bos.up", "bos.down", "bos.up_from", "bos.down_from",
    ]:
        ns, fn = sym.split(".")
        assert stdlib_has(ns, fn), f"{sym} missing"


def test_arity_matches_the_typescript_registry():
    assert stdlib_arity("candle", "up") == 0
    assert stdlib_arity("fvg", "up") == 1
    assert stdlib_arity("pivot", "last_high") == 2
    assert stdlib_arity("bos", "up_from") == 3
    assert stdlib_arity("fvg", "nope") is None


def test_namespaces_are_reachable_from_the_builtins_table():
    for ns in MANIFEST:
        assert ns in KNOWN_NAMESPACES


def test_windowed_propagation_matches_the_bodies():
    """`x := bos.up_from(x, ...)` inlines a self-reference into ta.crossover, which
    OS2013 exists to reject -- the guard cannot see through an opaque call, so the
    registry computes reachability instead."""
    assert stdlib_is_windowed("bos", "up") is True
    assert stdlib_is_windowed("pivot", "last_high") is True
    assert stdlib_is_windowed("candle", "up") is False
    assert stdlib_is_windowed("fvg", "up") is False


def test_symbol_count_is_stable():
    assert len(stdlib_symbols()) == 34


# ── module rules, each with the negative that proves it fires ─────────────────


def test_accepts_a_well_formed_module():
    # The same-module reference is QUALIFIED too. One rule for every stdlib
    # reference, not two, and no shipped module needs the exception.
    _build("f(a) => a + close\ng() => probe.f(1)\n")()


def test_rejects_a_non_function_statement():
    with pytest.raises(StdlibBuildError, match="only function declarations"):
        _build("x = close\nf() => x\n")()


def test_rejects_a_capture():
    with pytest.raises(StdlibBuildError, match="mystery"):
        _build("f(a) => a + mystery\n")()


def test_rejects_a_bare_cross_module_reference():
    with pytest.raises(StdlibBuildError, match="qualified"):
        _build("f() => up()\n")()


def test_rejects_forward_and_self_references_so_cycles_cannot_exist():
    with pytest.raises(StdlibBuildError, match=re.escape("probe.g")):
        _build("f() => probe.g()\ng() => close\n")()
    with pytest.raises(StdlibBuildError, match=re.escape("probe.f")):
        _build("f() => probe.f()\n")()


def test_rejects_an_input_declaration():
    with pytest.raises(StdlibBuildError, match="input"):
        _build('f() => input.int(5, "hidden")\n')()


def test_rejects_an_output_call():
    with pytest.raises(StdlibBuildError, match="output"):
        _build('f() => plot(close, "x")\n')()


def test_rejects_a_non_literal_history_offset():
    # The reason primitives are 0-anchored: _lower_hist cannot fold a parameter,
    # so this would be OS2006 pointing into source the user cannot open.
    with pytest.raises(StdlibBuildError, match="literal"):
        _build("f(n) => close[n]\n")()


def test_rejects_request_security():
    with pytest.raises(StdlibBuildError, match="request"):
        _build('f() => request.security(syminfo.tickerid, "D", close)\n')()


def test_allows_ta_math_kernels_nz_na_and_literal_offsets():
    # pivot.* is built entirely from these; forbidding them would empty the library.
    _build("f(l) => ta.valuewhen(not na(ta.pivothigh(high, l, l)), math.max(close, open[1]), 0)\n")()
