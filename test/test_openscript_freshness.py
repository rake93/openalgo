"""M6 piece A — is this Flask process running the code currently on disk?

WHY THIS EXISTS. Trap T2: with `FLASK_DEBUG=False` there is no reloader, so a
change to the Python OpenScript service does not reach the server until someone
restarts `app.py` — and nothing says so. On 2026-07-30 a live check was
impossible because the Flask process predated the commits under test by ~14
hours, and the only symptom was results that made no sense. The fix is to make
an invisible condition a fact you can query.

THE MECHANISM is exact rather than heuristic: every fingerprint is computed ONCE
at import and pinned in a module constant. Recomputing from disk on demand and
comparing answers "has the code moved since this process started?" with no
guessing.

WHY PER-SUBTREE, and this is the finding that shaped the design. The obvious
implementation reuses the shipped `COMPILER_FINGERPRINT`, which is what the
pending register originally specified. It hashes ONLY
`services/openscript/openscript/` — the compiler front end. Measured over the 15
most recent commits touching `services/openscript`, SIX changed only `runtime/`
or the service layer and would not have moved it at all. A freshness check that
reports "fresh" for the plurality of real changes is a silent false negative,
which this project treats as worse than no check (the C4 lesson: semantic
acceptance without lowering was worse than neither). So all three subtrees are
fingerprinted separately, and `test_every_service_py_file_is_covered_by_exactly_one_subtree`
below is the permanent guard that the blind spot cannot come back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.openscript import freshness as fr  # noqa: E402
from services.openscript.compiler_service import COMPILER_FINGERPRINT  # noqa: E402

SERVICE_ROOT = Path(fr.__file__).resolve().parent


# -- the fingerprint primitive -------------------------------------------------------


def test_fingerprint_is_a_sha256_hex_digest():
    fp = fr.fingerprint_tree(SERVICE_ROOT / "openscript")
    assert isinstance(fp, str)
    assert len(fp) == 64
    int(fp, 16)  # raises if it is not hex


def test_fingerprint_is_stable_across_repeated_calls():
    root = SERVICE_ROOT / "openscript"
    assert fr.fingerprint_tree(root) == fr.fingerprint_tree(root)


def test_pycache_is_excluded_so_the_digest_does_not_depend_on_bytecode():
    # Otherwise the fingerprint moves the first time the interpreter writes
    # bytecode, i.e. for a reason unrelated to the compiler.
    root = SERVICE_ROOT / "openscript"
    assert any((root / "__pycache__").glob("*.pyc")), "no bytecode present; test is vacuous"
    assert fr.fingerprint_tree(root) == fr.fingerprint_tree(root)


def test_the_compiler_fingerprint_value_is_unchanged_by_this_refactor():
    """`compiler_fingerprint` is PERSISTED in every version's metadata_json and P2
    treats a mismatch as 'recompile this indicator'. Moving it would silently mark
    every stored indicator stale, so the refactor must reproduce the original
    algorithm bit for bit, not merely something equivalent-looking."""
    assert fr.fingerprint_tree(SERVICE_ROOT / "openscript") == COMPILER_FINGERPRINT


# -- the subtree map -----------------------------------------------------------------


def test_the_three_subtrees_are_named_and_distinct():
    assert set(fr.SUBTREES) == {"compiler", "runtime", "service"}


def test_every_service_py_file_is_covered_by_exactly_one_subtree():
    """THE GUARD AGAINST THE ORIGINAL BLIND SPOT.

    A file under services/openscript that no subtree fingerprints is a file whose
    change leaves the freshness check reporting 'fresh' while the process is
    stale. Exactly-one, not at-least-one: double counting would make one edit
    look like two stale subtrees and misdirect the restart message.
    """
    on_disk = {
        p.resolve()
        for p in SERVICE_ROOT.rglob("*.py")
        if "__pycache__" not in p.parts
    }
    assert on_disk, "no sources found; the test would be vacuous"

    counts = dict.fromkeys(on_disk, 0)
    for name in fr.SUBTREES:
        for p in fr.files_for(name):
            counts[p.resolve()] = counts.get(p.resolve(), 0) + 1

    uncovered = sorted(str(p.relative_to(SERVICE_ROOT)) for p, n in counts.items() if n == 0)
    doubled = sorted(str(p.relative_to(SERVICE_ROOT)) for p, n in counts.items() if n > 1)
    assert not uncovered, f"unfingerprinted, so their staleness is invisible: {uncovered}"
    assert not doubled, f"counted by more than one subtree: {doubled}"


def test_the_runtime_subtree_is_not_empty():
    # Non-vacuity: an empty runtime subtree would make its fingerprint constant
    # and the coverage test above would still pass on a typo'd path.
    assert len(fr.files_for("runtime")) > 0
    assert len(fr.files_for("compiler")) > 0
    assert len(fr.files_for("service")) > 0


def test_the_service_subtree_does_not_recurse_into_the_other_two():
    names = {p.name for p in fr.files_for("service")}
    assert "compiler_service.py" in names
    assert "lexer.py" not in names, "service must be top-level only, or it double counts"


# -- the freshness report ------------------------------------------------------------


def test_an_untouched_process_reports_fresh():
    report = fr.freshness()
    assert report["stale"] is False
    assert set(report["parts"]) == {"compiler", "runtime", "service"}
    for part in report["parts"].values():
        assert part["stale"] is False
        assert part["imported"] == part["live"]
    assert report["action"] == ""


@pytest.mark.parametrize(
    ("subtree", "relpath"),
    [
        ("compiler", "openscript/lexer.py"),
        ("runtime", "runtime/executor.py"),
        ("service", "compiler_service.py"),
    ],
)
def test_editing_a_file_makes_exactly_its_own_subtree_stale(subtree, relpath, monkeypatch):
    """The behaviour the endpoint exists for, proven per subtree.

    `runtime/executor.py` is the case the register's original narrow proposal
    would have missed entirely.
    """
    target = SERVICE_ROOT / relpath
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\n# freshness probe\n")
        report = fr.freshness()
        assert report["stale"] is True
        assert report["parts"][subtree]["stale"] is True
        assert report["parts"][subtree]["live"] != report["parts"][subtree]["imported"]
        for other in set(fr.SUBTREES) - {subtree}:
            assert report["parts"][other]["stale"] is False, f"{other} moved too"
        # The message must NAME the stale part -- a bare "restart" sends the
        # reader hunting, which is the cost this whole entry exists to remove.
        assert subtree in report["action"]
        assert "restart" in report["action"].lower()
    finally:
        target.write_bytes(original)

    assert fr.freshness()["stale"] is False, "restoring the file must clear staleness"


def test_the_report_never_leaks_a_filesystem_path():
    # The endpoint is session-authenticated but still user-facing; digests and a
    # subtree name are enough to act on.
    import json

    blob = json.dumps(fr.freshness())
    assert str(SERVICE_ROOT) not in blob
    assert "openalgo" not in blob.lower()


# -- the endpoint --------------------------------------------------------------------
#
# Blueprint-only app: this route touches no database, so binding one would add a
# fixture whose failure modes have nothing to do with what is under test.

ROUTE = "/indicators/api/openscript/freshness"


def _app():
    from flask import Flask

    from blueprints.indicators import indicators_bp

    app = Flask(__name__)
    app.secret_key = "test-key"
    app.register_blueprint(indicators_bp)
    return app


@pytest.fixture()
def client():
    from datetime import datetime

    import pytz

    app = _app()
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"] = "tester"
            # `is_session_valid` also requires a login_time inside the current
            # rollover window; "now" always is.
            sess["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
        yield c


def test_the_endpoint_reports_fresh_on_an_untouched_process(client):
    resp = client.get(ROUTE)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["stale"] is False
    assert set(body["parts"]) == {"compiler", "runtime", "service"}
    assert body["action"] == ""


def test_the_endpoint_reports_a_stale_runtime_and_names_it(client):
    """The case the register's original narrow proposal would have missed: a
    runtime-only edit, reported as fresh while the process is stale."""
    target = SERVICE_ROOT / "runtime" / "executor.py"
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\n# freshness probe\n")
        body = client.get(ROUTE).get_json()
        assert body["stale"] is True
        assert body["parts"]["runtime"]["stale"] is True
        assert body["parts"]["compiler"]["stale"] is False
        assert "runtime" in body["action"]
    finally:
        target.write_bytes(original)

    assert client.get(ROUTE).get_json()["stale"] is False


def test_the_endpoint_still_answers_200_when_stale(client):
    """"The process is out of date" is a successful answer to the question. A
    non-200 would make callers treat a working diagnostic as a broken endpoint,
    which is how a freshness check ends up ignored."""
    target = SERVICE_ROOT / "runtime" / "executor.py"
    original = target.read_bytes()
    try:
        target.write_bytes(original + b"\n# freshness probe\n")
        assert client.get(ROUTE).status_code == 200
    finally:
        target.write_bytes(original)


def test_the_endpoint_requires_a_session():
    # A diagnostic that reports internal build state must not be anonymous.
    with _app().test_client() as anon:
        assert anon.get(ROUTE).status_code != 200
