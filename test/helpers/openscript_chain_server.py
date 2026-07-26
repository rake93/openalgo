"""Drive the REAL scripts API through a Flask test client and print what it returns.

The browser half of the product-chain proof
(`frontend/src/lib/charts/product-chain.test.ts`) needs genuine server payloads:
a script actually persisted by `blueprints/indicators.py`, compiled by the
server's own Python compiler, and serialized by the real `_script_row`. Handing
the browser half a hand-written payload would prove nothing about the chain.

Reads `{"name": ..., "source": ..., "stale": false}` on stdin. Writes, on stdout,
`{"list": [...], "script": {...}, "version": {...}}` — the three responses the
picker, the add path, and the restore path respectively consume. Nothing else
goes to stdout so the caller can parse it directly.

With `"stale": true` the stored IR has its negotiation header stripped between
the save and the reads, reproducing a version compiled before the Python ir-gen
emitted one (34977a88c). That is not hypothetical: it is what was actually in a
running instance's database, and it made every affected script unaddable and its
layouts unrestorable. The chain has to hold over it.
"""

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pytz
from flask import Flask

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".db")
os.close(_DB_FD)
_ORIGINAL_DB_URL = os.environ.get("DATABASE_URL")
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"
try:
    from database import indicator_db
finally:
    if _ORIGINAL_DB_URL is None:
        os.environ.pop("DATABASE_URL", None)
    else:
        os.environ["DATABASE_URL"] = _ORIGINAL_DB_URL


def main() -> int:
    request = json.load(sys.stdin)

    indicator_db.Base.metadata.create_all(indicator_db.engine)
    app = Flask(__name__)
    app.secret_key = "chain-proof"
    from blueprints.indicators import indicators_bp

    app.register_blueprint(indicators_bp)

    try:
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["logged_in"] = True
                sess["user"] = "chain"
                sess["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()

            created = client.post(
                "/indicators/api/scripts",
                json={"name": request["name"], "source": request["source"]},
            )
            if created.status_code != 201:
                raise SystemExit(f"save failed: {created.get_data(as_text=True)}")
            script_id = created.get_json()["data"]["id"]
            version_id = created.get_json()["data"]["version_id"]

            if request.get("stale"):
                version = indicator_db.IndicatorScriptVersion.query.filter_by(
                    id=version_id
                ).first()
                version.compiled_ir = {
                    k: v for k, v in version.compiled_ir.items() if k != "header"
                }
                indicator_db.db_session.commit()
                indicator_db.db_session.remove()

            listing = client.get("/indicators/api/scripts").get_json()["data"]
            script = client.get(f"/indicators/api/scripts/{script_id}").get_json()["data"]
            version = client.get(
                f"/indicators/api/scripts/{script_id}/versions/{version_id}"
            ).get_json()["data"]

        json.dump({"list": listing, "script": script, "version": version}, sys.stdout)
        return 0
    finally:
        indicator_db.db_session.remove()
        indicator_db.engine.dispose()
        try:
            os.unlink(_DB_PATH)
        except OSError:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
