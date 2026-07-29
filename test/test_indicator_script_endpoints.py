"""Script CRUD endpoints — the authoritative artifact a reopened indicator is
rebuilt from.

The reopen contract says a saved indicator is restored from the SERVER's
compiled IR and never from a browser recompile. That is only satisfiable if the
API actually hands the client that IR together with an identity stable enough to
re-fetch it. Until this suite existed, `_script_row` returned `source` and
`diagnostics` only: the server compiled and stored IR on every save, and then
never gave it back.

Everything here drives the real blueprint through a Flask test client against a
throwaway SQLite file — no mocked query layer, because the thing under test is
what the route serializes out of the database.
"""

import json
import os
import tempfile
from datetime import datetime

import pytest
import pytz
from flask import Flask
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_DB_FD)
_TEMP_DB_URL = f"sqlite:///{_DB_PATH}"

# `indicator_db` builds its engine at IMPORT time from DATABASE_URL, so a value
# has to be present for the import below to succeed at all. It is restored
# immediately afterwards: leaving it set leaks into every later test module in
# the same pytest session, which silently pointed the master-contract tests at
# this empty database.
_ORIGINAL_DB_URL = os.environ.get("DATABASE_URL")
os.environ["DATABASE_URL"] = _TEMP_DB_URL
try:
    from database import indicator_db
finally:
    if _ORIGINAL_DB_URL is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = _ORIGINAL_DB_URL

from services.openscript import openscript  # noqa: E402
from services.openscript.runtime.admit import admit_ir  # noqa: E402

RSI_SOURCE = (
    'indicator("RSI", overlay=false)\n'
    "len = input.int(14, \"Length\")\n"
    "r = ta.rsi(close, len)\n"
    'plot(r, "RSI")\n'
)

BROKEN_SOURCE = 'indicator("Broken", overlay=false)\nplot(nonexistent_thing, "X")\n'

# Compiles in the browser (TS) but NOT on the server: the Python port has no
# `request.security`. Kept here to pin what the endpoint does with a version
# whose server compile produced no IR.
HTF_SOURCE = (
    'indicator("HTF", overlay=true)\n'
    'h = request.security(syminfo.tickerid, "60", close)\n'
    'plot(h, "H")\n'
)


@pytest.fixture(scope="module")
def client():
    """Blueprint-only app over the temporary database.

    Only `indicators_bp` is registered — the routes under test are the subject,
    and pulling in the whole application would drag in broker plugins and the
    websocket proxy for no added coverage.

    The scoped session is bound to the temp engine here rather than relying on
    what `indicator_db` picked up at import: if an earlier test module already
    imported it, the module-level engine points at whatever DATABASE_URL was
    then, and this suite must never write into a real database. The original
    bind is restored and the temp engine disposed at teardown — a leaked engine
    holds its SQLite file descriptor for the life of the process.
    """
    engine = create_engine(
        _TEMP_DB_URL, poolclass=NullPool, connect_args={"check_same_thread": False}
    )
    original_bind = indicator_db.db_session.get_bind()
    indicator_db.db_session.remove()
    indicator_db.db_session.configure(bind=engine)
    indicator_db.Base.metadata.create_all(engine)

    app = Flask(__name__)
    app.secret_key = "test-key"
    from blueprints.indicators import indicators_bp

    app.register_blueprint(indicators_bp)

    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess["logged_in"] = True
            sess["user"] = "tester"
            # `is_session_valid` also requires a login_time inside the current
            # rollover window; "now" always is.
            sess["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
        yield c

    indicator_db.db_session.remove()
    indicator_db.db_session.configure(bind=original_bind)
    engine.dispose()
    try:
        os.unlink(_DB_PATH)
    except OSError:
        pass


def _create(client, name, source):
    res = client.post("/indicators/api/scripts", json={"name": name, "source": source})
    assert res.status_code == 201, res.get_data(as_text=True)
    return res.get_json()["data"]


# ── authoritative identity ───────────────────────────────────────────────────


def test_create_returns_full_identity_and_server_compiled_ir(client):
    row = _create(client, "rsi-identity", RSI_SOURCE)
    assert row["id"] > 0
    assert row["version_id"] > 0
    assert row["version_number"] == 1
    assert row["current_version_id"] == row["version_id"]
    assert len(row["source_hash"]) == 64
    assert row["compiler_version"] == "openscript-1.0"
    assert row["compiled_ir"] is not None
    assert row["compiled_ir"]["version"] == 1


def test_get_returns_the_same_identity_and_ir_as_create(client):
    created = _create(client, "rsi-refetch", RSI_SOURCE)
    res = client.get(f"/indicators/api/scripts/{created['id']}")
    assert res.status_code == 200
    fetched = res.get_json()["data"]
    for field in ("id", "version_id", "version_number", "source_hash", "compiler_version"):
        assert fetched[field] == created[field], field
    assert fetched["compiled_ir"] == created["compiled_ir"]


def test_refetching_the_same_version_is_byte_stable(client):
    """Identity has to survive repeated reads unchanged — a restored layout
    re-fetches on every reopen, and a value that drifts between reads would make
    the saved reference ambiguous."""
    created = _create(client, "rsi-stable", RSI_SOURCE)
    first = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]
    second = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)


def test_source_hash_is_the_sha256_of_the_stored_source(client):
    import hashlib

    row = _create(client, "rsi-hash", RSI_SOURCE)
    assert row["source_hash"] == hashlib.sha256(RSI_SOURCE.encode("utf-8")).hexdigest()


def test_updating_the_source_advances_version_identity(client):
    created = _create(client, "rsi-versioned", RSI_SOURCE)
    changed = RSI_SOURCE.replace("14", "21")
    res = client.put(f"/indicators/api/scripts/{created['id']}", json={"source": changed})
    assert res.status_code == 200
    updated = res.get_json()["data"]
    assert updated["version_number"] == 2
    assert updated["version_id"] != created["version_id"]
    assert updated["source_hash"] != created["source_hash"]
    assert updated["compiled_ir"] != created["compiled_ir"]


def test_updating_without_a_source_change_keeps_the_same_version(client):
    created = _create(client, "rsi-noop", RSI_SOURCE)
    res = client.put(f"/indicators/api/scripts/{created['id']}", json={"source": RSI_SOURCE})
    updated = res.get_json()["data"]
    assert updated["version_id"] == created["version_id"]
    assert updated["version_number"] == 1


# ── the IR handed out is executable ──────────────────────────────────────────


def test_the_returned_ir_passes_admission(client):
    """The contract is not "an IR field exists" but "the runtime will run it"."""
    row = _create(client, "rsi-admit", RSI_SOURCE)
    assert admit_ir(row["compiled_ir"]) == []


def test_the_returned_ir_declares_the_scripts_inputs(client):
    """What the settings dialog derives its form from."""
    row = _create(client, "rsi-inputs", RSI_SOURCE)
    input_ids = [d["id"] for d in row["compiled_ir"]["inputs"]]
    assert "len" in input_ids


# ── the artifact comes from storage, not from a recompile ────────────────────


def _admissible_sentinel(source_ir: dict) -> dict:
    """A structurally valid IR carrying a marker no compiler would emit.

    It has to pass admission: an IR the runtime would refuse is repaired from
    the stored source (see the stale-IR tests below), so an inadmissible
    sentinel would prove the opposite of what these tests intend.
    """
    return {**source_ir, "marker": "from-storage-not-a-recompile"}


def test_fetch_returns_the_stored_ir_rather_than_recompiling(client):
    """Mutation proof for the whole point of this endpoint.

    A row's stored IR is replaced with a marked copy no compiler would produce.
    If the route recompiled the source on read, the marker would be gone; the
    contract requires the stored artifact to come back verbatim. This is what
    makes "existing saved versions work without migration or recompilation" a
    fact rather than an assumption.
    """
    created = _create(client, "rsi-stored", RSI_SOURCE)
    sentinel = _admissible_sentinel(created["compiled_ir"])
    version = indicator_db.IndicatorScriptVersion.query.filter_by(
        id=created["version_id"]
    ).first()
    version.compiled_ir = sentinel
    indicator_db.db_session.commit()
    indicator_db.db_session.remove()

    fetched = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]
    assert fetched["compiled_ir"] == sentinel


def test_a_usable_stored_ir_is_never_recompiled(client):
    """The reopen contract's core rule, stated as the boundary of the repair.

    An artifact that admits is returned untouched, whatever else it contains —
    otherwise the "server IR, never a browser recompile" guarantee would quietly
    become "whatever the current compiler emits", and a saved layout could drift
    under the user without the version ever changing.

    **Scope narrowed by finding P2 (2026-07-29), deliberately.** "Never" now
    means "never while the compiler stands still". Recompiling is triggered by
    IR the runtime would refuse (P1) OR by a change of compiler BUILD, detected
    with a fingerprint (P2) — because an admissible-but-stale IR previously had
    no user-facing route to be refreshed at all, so a lowering fix never reached
    anything already saved.

    This test still passes unchanged, and that is the point: the version here was
    created by the CURRENT compiler, so its fingerprint matches and the sentinel
    survives. Drift under a standing compiler is still impossible; drift across a
    compiler upgrade is now repaired instead of frozen.
    """
    created = _create(client, "rsi-usable", RSI_SOURCE)
    sentinel = _admissible_sentinel(created["compiled_ir"])
    version = indicator_db.IndicatorScriptVersion.query.filter_by(
        id=created["version_id"]
    ).first()
    version.compiled_ir = sentinel
    indicator_db.db_session.commit()
    indicator_db.db_session.remove()

    fetched = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]
    assert fetched["compiled_ir"] == sentinel
    assert fetched["source"] == RSI_SOURCE


# ── IR stored by an older compiler ───────────────────────────────────────────


def _staleify(version_id: int) -> None:
    """Strip the negotiation header from a stored version's IR.

    Reproduces exactly what is in the wild: versions compiled before the Python
    ir-gen emitted `header` (commit 34977a88c). `version: 1` is still there, so
    the version check passes and admission fails on IR_MAJOR_MISMATCH instead.
    """
    version = indicator_db.IndicatorScriptVersion.query.filter_by(id=version_id).first()
    stale = {k: v for k, v in version.compiled_ir.items() if k != "header"}
    version.compiled_ir = stale
    indicator_db.db_session.commit()
    indicator_db.db_session.remove()


def test_a_header_less_stored_ir_would_be_refused_by_the_runtime(client):
    """The premise. Without this the repair tests could pass vacuously."""
    created = _create(client, "rsi-stale-premise", RSI_SOURCE)
    _staleify(created["version_id"])
    version = indicator_db.IndicatorScriptVersion.query.filter_by(
        id=created["version_id"]
    ).first()
    assert "IR_MAJOR_MISMATCH" in {e["code"] for e in admit_ir(version.compiled_ir)}
    indicator_db.db_session.remove()


def test_stale_stored_ir_is_repaired_on_fetch(client):
    """A version whose IR predates the current compiler is recompiled from its
    OWN stored source and comes back runnable.

    Without this every script saved before the header existed is permanently
    unaddable, and — worse — permanently unrestorable, because a saved layout
    pins the version id.
    """
    created = _create(client, "rsi-stale", RSI_SOURCE)
    _staleify(created["version_id"])

    fetched = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]

    assert admit_ir(fetched["compiled_ir"]) == []
    assert fetched["compiled_ir"]["header"]["major"] == 1


def test_the_repair_preserves_identity(client):
    """The source did not change, so nothing that identifies the version may.
    A layout pinned to this version has to keep resolving to it."""
    created = _create(client, "rsi-stale-identity", RSI_SOURCE)
    _staleify(created["version_id"])

    fetched = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]

    assert fetched["version_id"] == created["version_id"]
    assert fetched["version_number"] == created["version_number"]
    assert fetched["source_hash"] == created["source_hash"]
    assert fetched["source"] == RSI_SOURCE


def test_the_repair_persists(client):
    """Repaired once, not on every read — and visible to the next reader,
    including the version endpoint a restore goes through."""
    created = _create(client, "rsi-stale-persist", RSI_SOURCE)
    _staleify(created["version_id"])

    client.get(f"/indicators/api/scripts/{created['id']}")

    stored = indicator_db.IndicatorScriptVersion.query.filter_by(
        id=created["version_id"]
    ).first()
    assert admit_ir(stored.compiled_ir) == []
    indicator_db.db_session.remove()


def test_the_version_endpoint_repairs_too(client):
    """Restore reads through here, so a stale version has to heal on this path
    as well — otherwise old layouts stay broken even once the script is addable."""
    created = _create(client, "rsi-stale-version-route", RSI_SOURCE)
    _staleify(created["version_id"])

    fetched = client.get(
        f"/indicators/api/scripts/{created['id']}/versions/{created['version_id']}"
    ).get_json()["data"]

    assert admit_ir(fetched["compiled_ir"]) == []


def test_a_version_with_no_ir_at_all_is_repaired(client):
    """`compiled_ir` NULL is the same problem in its most extreme form: stored
    before the server compiled at all."""
    created = _create(client, "rsi-null-ir", RSI_SOURCE)
    version = indicator_db.IndicatorScriptVersion.query.filter_by(
        id=created["version_id"]
    ).first()
    version.compiled_ir = None
    indicator_db.db_session.commit()
    indicator_db.db_session.remove()

    fetched = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]

    assert fetched["compiled_ir"] is not None
    assert admit_ir(fetched["compiled_ir"]) == []


def test_an_unrepairable_version_still_reports_null(client):
    """A source the server cannot compile has nothing to repair from. It must
    keep reading back as null so the client says so, rather than being papered
    over."""
    created = _create(client, "htf-unrepairable", HTF_SOURCE)

    fetched = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]

    assert fetched["compiled_ir"] is None
    assert fetched["diagnostics"]


# ── diagnostics and source behaviour stay compatible ─────────────────────────


def test_source_and_diagnostics_are_still_returned(client):
    row = _create(client, "rsi-compat", RSI_SOURCE)
    assert row["source"] == RSI_SOURCE
    assert row["diagnostics"] == []


def test_a_source_error_yields_diagnostics_and_no_ir(client):
    row = _create(client, "broken", BROKEN_SOURCE)
    assert row["compiled_ir"] is None
    assert row["diagnostics"], "a failing compile must report why"
    assert all("code" in d for d in row["diagnostics"])


def test_an_htf_script_saves_with_no_server_ir(client):
    """Pins the recorded compiler asymmetry at the API boundary: a script that
    compiles in the editor can still arrive here with no IR, so every consumer
    of `compiled_ir` must handle null."""
    row = _create(client, "htf", HTF_SOURCE)
    assert row["compiled_ir"] is None
    assert "OS2002" in {d["code"] for d in row["diagnostics"]}


# ── the list endpoint stays light ────────────────────────────────────────────


def test_list_carries_identity_but_not_the_payload(client):
    """The picker lists every saved script; shipping each one's IR and source
    would make that response grow without bound."""
    _create(client, "listed", RSI_SOURCE)
    rows = client.get("/indicators/api/scripts").get_json()["data"]
    assert rows
    for row in rows:
        assert "id" in row and "current_version_id" in row
        assert "compiled_ir" not in row
        assert "source" not in row


# ── the version endpoints ────────────────────────────────────────────────────


def test_single_version_fetch_returns_the_authoritative_ir(client):
    """Restoring a layout pinned to a specific version reads through here, so
    this endpoint must serve IR too — not just the current-version route."""
    created = _create(client, "rsi-version-fetch", RSI_SOURCE)
    res = client.get(
        f"/indicators/api/scripts/{created['id']}/versions/{created['version_id']}"
    )
    assert res.status_code == 200
    version = res.get_json()["data"]
    assert version["id"] == created["version_id"]
    assert version["source_hash"] == created["source_hash"]
    assert version["compiled_ir"] == created["compiled_ir"]
    assert version["source_code"] == RSI_SOURCE


def test_version_list_stays_light(client):
    created = _create(client, "rsi-version-list", RSI_SOURCE)
    rows = client.get(f"/indicators/api/scripts/{created['id']}/versions").get_json()["data"]
    assert rows
    for row in rows:
        assert "source_hash" in row
        assert "compiled_ir" not in row
        assert "source_code" not in row


# ── the server never trusts client IR ────────────────────────────────────────


def test_client_supplied_ir_is_ignored(client):
    """The server recompiles and stores its own. A client that posts IR must not
    be able to influence what is persisted."""
    forged = {"version": 1, "marker": "client-forged"}
    res = client.post(
        "/indicators/api/scripts",
        json={"name": "forged", "source": RSI_SOURCE, "compiled_ir": forged},
    )
    row = res.get_json()["data"]
    assert row["compiled_ir"] != forged
    # Compared through a JSON round-trip, which is what the client actually
    # receives: `meta.spans` is keyed by integer node id in process and by the
    # string form of it on the wire. Telemetry only — no executable field is
    # affected — but a raw `==` against the in-process IR would fail on it.
    expected = json.loads(json.dumps(openscript.compile(RSI_SOURCE).ir))
    assert row["compiled_ir"] == expected


# ── P2: stored IR that ADMITS but predates the current compiler ──────────────
#
# `_ensure_runnable_ir` repaired only IR the runtime would REFUSE. IR that still
# admits but was built by an older compiler was frozen forever, and re-saving
# unchanged source is a no-op, so there was NO user-facing route to refresh it.
# A lowering fix therefore never reached anything already saved.
#
# The gate is a COMPILER FINGERPRINT, not a diff: `compiler_version` is frozen
# per language revision ("openscript-1.0"), so it does not move when a lowering
# changes. The fingerprint hashes the compiler's own sources, so it moves
# whenever its output could.


def _set_fingerprint(version_id: int, fingerprint) -> None:
    """Force a version's recorded compiler fingerprint (None = pre-feature row)."""
    version = indicator_db.IndicatorScriptVersion.query.filter_by(id=version_id).first()
    meta = dict(version.metadata_json or {})
    if fingerprint is None:
        meta.pop("compiler_fingerprint", None)
    else:
        meta["compiler_fingerprint"] = fingerprint
    version.metadata_json = meta
    indicator_db.db_session.commit()
    indicator_db.db_session.remove()


def _set_ir(version_id: int, ir: dict) -> None:
    version = indicator_db.IndicatorScriptVersion.query.filter_by(id=version_id).first()
    version.compiled_ir = ir
    indicator_db.db_session.commit()
    indicator_db.db_session.remove()


def test_the_compiler_fingerprint_is_a_stable_content_hash():
    """The premise. A fingerprint that never moves would make the refresh dead
    code; one that moves per process would recompile the whole corpus on boot."""
    from services.openscript.compiler_service import COMPILER_FINGERPRINT, compile_source

    assert isinstance(COMPILER_FINGERPRINT, str)
    assert len(COMPILER_FINGERPRINT) == 64  # sha-256 hex
    assert compile_source("indicator(\"x\")\nplot(close)")["compiler_fingerprint"] == COMPILER_FINGERPRINT


def test_a_new_version_records_the_fingerprint_it_was_compiled_by(client):
    created = _create(client, "rsi-fp-stamp", RSI_SOURCE)
    from services.openscript.compiler_service import COMPILER_FINGERPRINT

    version = indicator_db.IndicatorScriptVersion.query.filter_by(
        id=created["version_id"]
    ).first()
    assert (version.metadata_json or {}).get("compiler_fingerprint") == COMPILER_FINGERPRINT


def test_stored_ir_from_an_older_compiler_is_refreshed_on_fetch(client):
    """The P2 fix. The IR admits, so the old repair left it alone forever."""
    created = _create(client, "rsi-stale-fp", RSI_SOURCE)
    sentinel = _admissible_sentinel(created["compiled_ir"])
    _set_ir(created["version_id"], sentinel)
    _set_fingerprint(created["version_id"], "0" * 64)  # a different compiler

    fetched = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]
    assert fetched["compiled_ir"] != sentinel, "stale IR was not refreshed"
    assert "marker" not in fetched["compiled_ir"]
    # Identity is untouched: a layout pinned to this version still resolves.
    assert fetched["version_id"] == created["version_id"]
    assert fetched["source_hash"] == created["source_hash"]
    assert fetched["source"] == RSI_SOURCE


def test_a_refreshed_version_records_the_new_fingerprint(client):
    """Otherwise every fetch would recompile forever."""
    from services.openscript.compiler_service import COMPILER_FINGERPRINT

    created = _create(client, "rsi-restamp", RSI_SOURCE)
    _set_fingerprint(created["version_id"], "0" * 64)
    client.get(f"/indicators/api/scripts/{created['id']}")

    version = indicator_db.IndicatorScriptVersion.query.filter_by(
        id=created["version_id"]
    ).first()
    assert (version.metadata_json or {}).get("compiler_fingerprint") == COMPILER_FINGERPRINT


def test_a_version_predating_the_fingerprint_is_treated_as_stale(client):
    """The existing corpus carries no fingerprint at all."""
    created = _create(client, "rsi-no-fp", RSI_SOURCE)
    sentinel = _admissible_sentinel(created["compiled_ir"])
    _set_ir(created["version_id"], sentinel)
    _set_fingerprint(created["version_id"], None)

    fetched = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]
    assert "marker" not in fetched["compiled_ir"]


def test_a_source_that_no_longer_compiles_keeps_its_working_ir(client):
    """Never make a saved indicator worse than it was.

    If the compiler tightened (an accepted construct became an error), the fresh
    compile yields no IR. The stored artifact still runs, so it is kept -- and
    the fingerprint is NOT stamped, so the refresh retries rather than recording
    a lie about which compiler produced it.
    """
    created = _create(client, "rsi-uncompilable", RSI_SOURCE)
    working_ir = created["compiled_ir"]
    version = indicator_db.IndicatorScriptVersion.query.filter_by(
        id=created["version_id"]
    ).first()
    version.source_code = "indicator(\"x\")\nplot(this_is_not_defined)"  # errors now
    meta = dict(version.metadata_json or {})
    meta["compiler_fingerprint"] = "0" * 64
    version.metadata_json = meta
    indicator_db.db_session.commit()
    indicator_db.db_session.remove()

    fetched = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]
    assert fetched["compiled_ir"] == working_ir, "a working artifact was destroyed"
    version = indicator_db.IndicatorScriptVersion.query.filter_by(
        id=created["version_id"]
    ).first()
    assert (version.metadata_json or {}).get("compiler_fingerprint") == "0" * 64


def test_a_script_saved_before_the_os2010_flip_keeps_working(client):
    """P2 and FU-1 composing — the migration story, end to end.

    `plotlevel(..., bogus_arg=5)` compiled cleanly for years (the argument was
    silently dropped), then shipped as an OS2010 warning, and is now an error.
    A user who saved such a script must not find their chart broken by a
    compiler upgrade they did not ask for.

    The recompile fails, so the refresh keeps the stored IR and does not stamp
    the fingerprint. The indicator keeps rendering; re-saving is what forces the
    author to fix the argument. Without the "never make it worse" branch this
    would blank the artifact and the chart with it.
    """
    created = _create(client, "legacy-bogus-arg", RSI_SOURCE)
    working_ir = created["compiled_ir"]
    version = indicator_db.IndicatorScriptVersion.query.filter_by(
        id=created["version_id"]
    ).first()
    # Exactly what such a row looks like: source the CURRENT compiler refuses,
    # alongside IR that a previous compiler built and the runtime still admits.
    legacy_source = 'indicator("x")\nplotlevel(close > open, close, bogus_arg=5)'
    version.source_code = legacy_source
    meta = dict(version.metadata_json or {})
    meta["compiler_fingerprint"] = "0" * 64
    version.metadata_json = meta
    indicator_db.db_session.commit()
    indicator_db.db_session.remove()

    fetched = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]
    assert fetched["compiled_ir"] == working_ir, "an upgrade broke a saved indicator"

    # Premise guard: the source really is refused now, so this is not vacuous.
    from services.openscript.compiler_service import compile_source

    recompiled = compile_source(legacy_source)
    assert recompiled["ir"] is None
    assert [d["code"] for d in recompiled["diagnostics"]] == ["OS2010"]
