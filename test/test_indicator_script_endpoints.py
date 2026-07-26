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


def test_fetch_returns_the_stored_ir_rather_than_recompiling(client):
    """Mutation proof for the whole point of this endpoint.

    A row's stored IR is replaced with a sentinel that no compiler would
    produce. If the route recompiled the source on read, the sentinel would be
    replaced by real IR; the contract requires it to come back verbatim. This is
    what makes "existing saved versions work without migration or
    recompilation" a fact rather than an assumption.
    """
    created = _create(client, "rsi-stored", RSI_SOURCE)
    sentinel = {"version": 1, "marker": "from-storage-not-a-recompile"}
    version = indicator_db.IndicatorScriptVersion.query.filter_by(
        id=created["version_id"]
    ).first()
    version.compiled_ir = sentinel
    indicator_db.db_session.commit()
    indicator_db.db_session.remove()

    fetched = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]
    assert fetched["compiled_ir"] == sentinel


def test_a_version_stored_without_ir_is_reported_as_null_not_recompiled(client):
    """Rows written before server-side compilation existed have compiled_ir
    NULL. They must read back as null so the client can say so, rather than
    being silently repaired by a recompile the reopen contract forbids."""
    created = _create(client, "rsi-legacy", RSI_SOURCE)
    version = indicator_db.IndicatorScriptVersion.query.filter_by(
        id=created["version_id"]
    ).first()
    version.compiled_ir = None
    indicator_db.db_session.commit()
    indicator_db.db_session.remove()

    fetched = client.get(f"/indicators/api/scripts/{created['id']}").get_json()["data"]
    assert fetched["compiled_ir"] is None
    assert fetched["source"] == RSI_SOURCE


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
