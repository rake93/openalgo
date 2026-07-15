# Deploying OpenAlgo on Oracle Cloud Always Free (Mumbai) — Design

**Date:** 2026-07-15
**Status:** Approved
**Branch:** `feat/oracle-deploy`

## Goal

Give a self-hosted OpenAlgo operator a **$0/month, always-on, SEBI-compliant**
deployment on Oracle Cloud Infrastructure (OCI) Always Free tier in the **Mumbai**
region, using OpenAlgo's existing Docker tooling and adding only the
Oracle-specific glue.

## Why Oracle Cloud Always Free (vs. the alternatives evaluated)

OpenAlgo is a **stateful, always-on** trading platform:

- Holds a broker session + WebSocket market-data feed continuously.
- Runs scheduled Python strategies (APScheduler) and a daily ~03:00 IST token refresh.
- Persists users, broker auth tokens, orders, sandbox and historical data in
  SQLite + DuckDB **on local disk**.
- Must place transactional orders from a **static outbound IP** the broker
  whitelists (SEBI static-IP mandate, effective 2026-04-01).

These requirements eliminate every "free" PaaS tier that sleeps or has ephemeral
storage (Render/Railway/Koyeb free). Providers compared (July 2026):

| Option | $/mo | RAM | Region | Static IP | Verdict |
|---|---|---|---|---|---|
| **Oracle Cloud Always Free** | **0** | 12 GB (2 OCPU ARM) | **Mumbai** | free | **Chosen** — only free tier meeting every requirement |
| AWS Lightsail | 12 | 2 GB | Mumbai | free | Best paid alternative (Indian-card friendly) |
| Vultr / DigitalOcean | ~10–12 | 2 GB | Mumbai / Bangalore | free | Cheap, Indian-card friction |
| Fly.io | ~13 | 2 GB | Singapore | +$2 | Single-port PaaS, pricey India egress |
| Render Standard | 25 + IP add-on | 2 GB | Singapore | paid add-on | Easiest but priciest; IP not free |

**Key insight:** on any VPS the instance's own public IP *is* a free static IP
(satisfies SEBI at no cost), whereas managed PaaS charge for it. Oracle's Always
Free tier is the only one that is also **$0 and always-on with persistent disk**.

### Oracle-specific caveats (documented in the runbook)

1. **Free-tier terms can change unilaterally.** In June 2026 Oracle quietly
   halved the free ARM allowance (4 OCPU/24 GB → **2 OCPU/12 GB**). Still ~6× what
   OpenAlgo needs.
2. **A1 (Ampere) capacity** is frequently "out of capacity" in popular regions —
   retry provisioning.
3. **Idle-reclaim:** Oracle may reclaim *idle* Always Free instances. A live
   OpenAlgo instance streaming market data is not idle; documented anyway.
4. **ARM64 image:** `install-docker.sh` builds the image natively on the VM
   (`docker compose build`); all Python deps have aarch64 wheels and the base
   images are multi-arch. First build ~15–20 min on 2 OCPUs.

## Architecture on the VM

Reuse OpenAlgo's own `install/install-docker.sh`, which already provisions the
full production stack — we do **not** reinvent it:

```
Internet ──HTTPS/WSS──▶ nginx (host, :443, TLS via certbot)
                          ├─ /ws , /ws/     ─▶ 127.0.0.1:8765  (market-data WS proxy, in container)
                          ├─ /socket.io/    ─▶ gunicorn socket  (Flask-SocketIO, order/UI events)
                          └─ /               ─▶ gunicorn socket  (Flask app + React SPA)

OpenAlgo container (docker compose): gunicorn (eventlet, -w 1) + WS proxy (8765)
+ ZeroMQ bus (127.0.0.1:5555, loopback only). SQLite/DuckDB on named volumes.
```

nginx is what bridges `/ws` → `8765` — the reason a bare single-port PaaS would
need a custom in-container reverse proxy but a VPS does not.

## TLS without a purchased domain

`install-docker.sh` uses Let's Encrypt (certbot), which needs a resolvable
hostname. We use a **free wildcard-DNS service**: `<public-ip>.sslip.io` resolves
to the instance IP, and certbot issues a valid single-hostname cert via HTTP-01
(no wildcard needed). `nip.io` is the fallback if sslip.io's shared Let's Encrypt
rate-limit pool is temporarily exhausted.

## Deliverables (branch `feat/oracle-deploy`)

1. **`deploy/oracle/bootstrap.sh`** — idempotent VM prep for a fresh Ubuntu ARM
   instance:
   - system update; install Docker Engine + compose plugin + `iptables-persistent`;
   - **fix host iptables** — Oracle's Ubuntu images ship an `INPUT` chain that
     REJECTs everything except SSH; insert `ACCEPT` for tcp/80 and tcp/443
     **before** that REJECT rule (appending is silently ignored → misleading
     "no route to host"), then persist to `/etc/iptables/rules.v4`;
   - clone the repo to `/opt/openalgo` (repo + branch configurable via env);
   - hand off to `install/install-docker.sh` (kept interactive).
   - Does **not** touch the OCI Security List (cloud firewall) — that is a
     console/CLI step documented in the README.

2. **`deploy/oracle/README.md`** — the operator runbook, end to end:
   create A1 instance → **reserve** the public IP (static, for SEBI) → OCI
   Security List ingress 80/443 → run `bootstrap.sh` → run `install-docker.sh`
   with `<ip>.sslip.io` + broker creds → **register the reserved IP with the
   broker** → first login → daily token refresh, upgrades, backups,
   troubleshooting, and every Oracle gotcha above.

3. **`docs/INDEX.md`** — one-line pointer under "Install, deploy & operate".

## Non-goals

- No changes to the root `Dockerfile`, `start.sh`, `docker-compose.yaml`, or any
  existing installer — existing Docker/Railway/bare-metal users are untouched.
- No managed Postgres — OpenAlgo is natively SQLite + DuckDB on disk.
- No Render/Fly Blueprint (those were considered but not chosen).

## Security notes

- The reserved public IP is the single static egress IP registered with the
  broker; stolen credentials cannot be used from another host (SEBI mandate).
- ZeroMQ stays on loopback inside the container; nginx terminates TLS; only
  80/443 are exposed publicly (22 for SSH, ideally restricted to the operator's IP).
- `install-docker.sh` auto-generates unique `APP_KEY` / `API_KEY_PEPPER` /
  `FERNET_SALT`; these persist in the host `.env` and the Docker named volumes
  across restarts, so encrypted broker tokens survive redeploys.
