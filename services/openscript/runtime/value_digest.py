"""Stable checksum of a numeric series, byte-identical to the TS reference.

Mirror of `digestSeries` in the engine's `bench/lib/digest.ts`. The contract is
pinned by the shared fixture `fixtures/digest/series-digest.json`, replayed by
`test/test_openscript_digest_contract.py` here and by `tests/digest-contract.test.ts`
on the TS side, so the two implementations cannot drift silently.

Why a digest at all: cross-language VALUE parity had never been tested directly
(engine register C3). Diagnostics and IR shape are pinned byte-for-byte by the
shared corpora, but "the same script produces the same NUMBERS in the browser and
on the alert server" rested on nothing but hand-duplicated per-file assertions. A
6-dp digest covers an entire series in one line of fixture.

TWO RULES CARRY THE WHOLE CONTRACT, and both were established empirically rather
than assumed:

1. **Negative zero normalizes to zero.** JS `(-0).toFixed(6)` is "0.000000";
   Python `f"{-0.0:.6f}"` is "-0.000000". IEEE says the two values are equal, so
   a series containing -0 -- reachable via `ta.change` over a flat stretch, or
   `x * 0` with x negative -- would otherwise digest differently in the two
   runtimes for a value both agree is zero.

2. **NaN is encoded explicitly as `na`,** never formatted. Warmup and gap
   positions are part of the series identity and must not collapse into 0.

Everything else agrees already: 6-dp formatting was compared across the two
languages on ties (1.0000005, -0.9999995), magnitude extremes (1e20, 1e-20) and
realistic prices, and matched in every case. Python's banker's rounding and JS's
half-up differ only on EXACT decimal ties, which those f64 values are not.
"""

import hashlib
import math
from collections.abc import Iterable


def digest_series(values: Iterable[float]) -> str:
    """sha256 over 6-dp-rounded values; `na` for NaN. See the module docstring."""
    h = hashlib.sha256()
    for raw in values:
        v = float(raw)
        if math.isnan(v):
            h.update(b"na,")
            continue
        # Normalize negative zero -- rule 1. `v == 0` is True for both +0.0 and
        # -0.0, so this collapses the sign without a copysign check.
        if v == 0:
            v = 0.0
        h.update(f"{v:.6f},".encode())
    return h.hexdigest()
