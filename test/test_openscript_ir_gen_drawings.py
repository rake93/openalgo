"""OpenScript drawing lowering (Python) — mirrors the TS ir-gen drawing tests
(openalgo-openscript/tests/ir-gen.test.ts `plotlevel/plotzone drawing lowering`).

Compiles `plotlevel`/`plotzone` sources and asserts the emitted frozen `level`/
`zone` IR shape + the `drawing-streams` requiredFeatures flag. Shape-only on
purpose — execution is covered by the materializer fixtures and the
drawing-geometry replays (`test_openscript_drawing_geometry.py`). (This
docstring used to say a drawing IR "is rejected at admission"; that has been
false since Phase 1 Pri 4 flipped `drawing-streams` into SUPPORTED_FEATURES
and drawings began executing on both runtimes — register N11.)
"""

from services.openscript import openscript


def _ir(source: str) -> dict:
    result = openscript.compile(source)
    assert result.diagnostics == [], [d.code for d in result.diagnostics]
    assert result.ir is not None
    return result.ir


def test_plotlevel_lowers_to_level_and_declares_drawing_streams():
    p = _ir(
        'plotlevel(close > open, high, "R", color=color.red, width=2, style=line.style_dashed,'
        " offset=-1, right_pad=2, max_kept=3, label=\"R High\", label_latest_only=true)"
    )
    out = p["outputs"][0]
    assert out["kind"] == "level"
    assert out["title"] == "R"
    assert out["style"] == {"color": "#ef5350", "lineWidth": 2, "lineStyle": "dashed"}
    assert out["offset"] == -1
    assert out["rightPad"] == 2
    assert out["extend"] == "lastbar"
    assert out["maxKept"] == 3
    assert out["labelLatestOnly"] is True
    assert out["label"] == {"kind": "const", "value": "R High"}
    # lastbar carries neither terminate= nor bars=
    assert "terminate" not in out
    assert "bars" not in out
    # cond/price node ids point at real nodes (close>open binop, high source)
    assert p["nodes"][out["condNodeId"]]["op"] == "binop"
    assert p["nodes"][out["condNodeId"]]["operator"] == ">"
    assert p["nodes"][out["priceNodeId"]] == {"id": out["priceNodeId"], "op": "source", "source": "high"}
    assert "drawing-streams" in p["header"]["requiredFeatures"]


def test_extend_until_carries_terminate_not_bars():
    p = _ir(
        "plotlevel(ta.crossover(close, ta.sma(close, 20)), close,"
        " extend=extend.until, terminate=terminate.close_above)"
    )
    out = p["outputs"][0]
    assert out["kind"] == "level"
    assert out["extend"] == "until"
    assert out["terminate"] == "close_above"
    assert out["maxKept"] == 20
    assert "bars" not in out


def test_extend_bars_carries_bars_not_terminate():
    p = _ir("plotlevel(close > open, close, extend=extend.bars, bars=10)")
    out = p["outputs"][0]
    assert out["kind"] == "level"
    assert out["extend"] == "bars"
    assert out["bars"] == 10
    assert "terminate" not in out


def test_plotzone_lowers_to_zone():
    p = _ir(
        'plotzone(close < open, high, low, "OB", offset=-2, right_pad=1,'
        " extend=extend.until, terminate=terminate.touch, mitigated_color=color.gray,"
        " color=color.new(color.teal, 80), border_color=color.teal, border_style=line.style_dotted,"
        " max_kept=5, text=\"OB\")"
    )
    out = p["outputs"][0]
    assert out["kind"] == "zone"
    assert out["title"] == "OB"
    assert out["offset"] == -2
    assert out["rightPad"] == 1
    assert out["extend"] == "until"
    assert out["terminate"] == "touch"
    assert out["maxKept"] == 5
    assert out["mitigatedColor"] == "#787b86"
    assert out["text"] == {"kind": "const", "value": "OB"}
    assert out["style"] == {"color": "#26a69a33", "borderColor": "#26a69a", "borderStyle": "dotted"}
    assert p["nodes"][out["topNodeId"]]["source"] == "high"
    assert p["nodes"][out["bottomNodeId"]]["source"] == "low"
    assert "drawing-streams" in p["header"]["requiredFeatures"]


def test_max_kept_over_cap_clamps_and_warns_os5001():
    result = openscript.compile("plotlevel(close > open, close, max_kept=250)")
    assert [d.code for d in result.diagnostics] == ["OS5001"]
    assert result.diagnostics[0].severity == "warning"
    assert result.ir is not None
    assert result.ir["outputs"][0]["maxKept"] == 100
    assert "drawing-streams" in result.ir["header"]["requiredFeatures"]


def test_non_drawing_script_keeps_required_features_empty():
    p = _ir("plot(close)")
    assert p["header"]["requiredFeatures"] == []


# -- G8: drawing colors follow an input.color -----------------------------------------
#
# Drawings were the one family that dropped the binding id: the hex was baked at
# compile time and nothing replaced it, so the script compiled clean, the settings
# dialog showed the swatch, and dragging it did nothing. Mirrors the TS ir-gen
# tests; byte-identity of the resulting IR is enforced separately by
# test_openscript_ir_conformance against the engine's goldens.


def test_plotlevel_color_records_the_color_input_id():
    p = _ir('c = input.color(color.red, "C")\nplotlevel(close > open, close, "L", color=c)')
    style = p["outputs"][0]["style"]
    assert style["color"] == "#ef5350"
    assert style["colorInputId"] == "c"


def test_plotzone_binds_all_three_color_slots_independently():
    # Three DIFFERENT inputs: one shared id would satisfy a same-id assertion
    # while a per-slot mix-up went unnoticed.
    p = _ir(
        'a = input.color(color.red, "A")\n'
        'b = input.color(color.blue, "B")\n'
        'm = input.color(color.gray, "M")\n'
        'plotzone(close > open, high, low, "Z", color=a, border_color=b, mitigated_color=m,'
        " extend=extend.until, terminate=terminate.touch)"
    )
    out = p["outputs"][0]
    assert out["style"]["colorInputId"] == "a"
    assert out["style"]["borderColorInputId"] == "b"
    assert out["mitigatedColorInputId"] == "m"
    assert out["style"]["color"] == "#ef5350"
    assert out["style"]["borderColor"] == "#2962ff"
    assert out["mitigatedColor"] == "#787b86"


def test_a_const_draw_color_records_no_color_input_id():
    # Additive-optional (the labelSize precedent): ABSENT for a literal color, or
    # every stored artifact and every TS golden moves.
    p = _ir('plotlevel(close > open, close, "L", color=#ff0000)')
    style = p["outputs"][0]["style"]
    assert style["color"] == "#ff0000"
    assert "colorInputId" not in style


def test_an_input_color_in_border_or_mitigated_slots_is_not_os2017():
    # OS2017 used to allow `color=` only, so these were rejected outright -- the
    # settings surface was arbitrary rather than principled.
    result = openscript.compile(
        'b = input.color(color.blue, "B")\n'
        'm = input.color(color.gray, "M")\n'
        'plotzone(close > open, high, low, "Z", border_color=b, mitigated_color=m,'
        " extend=extend.until, terminate=terminate.touch)"
    )
    assert [d.code for d in result.diagnostics] == []


def test_an_input_color_outside_any_color_slot_is_still_os2017():
    # The widening must not become "anywhere": a color input used as a VALUE is
    # still an error, or the diagnostic stops meaning anything.
    result = openscript.compile('c = input.color(color.red, "C")\nplot(close + c)')
    assert "OS2017" in [d.code for d in result.diagnostics]
