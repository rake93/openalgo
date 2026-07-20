from services.openscript.openscript.finality import lub, source_finality, LOOKAHEAD_OPS


def test_lub():
    assert lub("historical-final", "confirmed") == "confirmed"
    assert lub("confirmed", "provisional") == "provisional"
    assert lub("provisional", "confirmed") == "provisional"


def test_source_finality():
    assert source_finality("close") == "confirmed"
    assert source_finality("hlc3") == "confirmed"
    assert source_finality("bar_index") == "historical-final"


def test_lookahead_ops():
    assert "ta.pivothigh" in LOOKAHEAD_OPS
    assert "ta.sma" not in LOOKAHEAD_OPS
