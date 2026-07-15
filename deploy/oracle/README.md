# Deploy OpenAlgo on Oracle Cloud Always Free (Mumbai) — $0/month

A complete, always-on OpenAlgo deployment on Oracle Cloud Infrastructure (OCI)
**Always Free** tier, in the **Mumbai** region, for **₹0 / month**.

This is the cheapest way to run OpenAlgo that still satisfies every hard
requirement of a live trading platform:

| Requirement | How Oracle Always Free meets it |
|---|---|
| Always-on (no sleep) | A real VM — never spins down |
| Persistent storage | 200 GB Always Free block storage (your DB, tokens, orders survive) |
| Enough RAM | `VM.Standard.A1.Flex` — up to **2 OCPU / 12 GB** free |
| **Static IP for SEBI** | The instance's **reserved public IP** is a free static IP you register with your broker |
| India latency | **Mumbai** (`ap-mumbai-1`) region, close to NSE/BSE broker APIs |
| HTTPS + `wss://` | Free `sslip.io` hostname + Let's Encrypt (certbot) |

> **How this works:** OpenAlgo's own `install/install-docker.sh` already builds
> the Docker image (natively for ARM64), sets up **nginx** (which bridges
> `/ws` → the market-data WebSocket on `8765`), issues a **TLS certificate** with
> certbot, and starts everything. The only Oracle-specific gap it does not cover
> is the host firewall — that is what `deploy/oracle/bootstrap.sh` fixes.

---

## Before you start

- An Oracle Cloud account (the Always Free tier requires a card for identity
  verification but is **not** charged for Always Free resources).
- An SSH key pair (`ssh-keygen -t ed25519` if you don't have one).
- Your **broker API key + secret** (from your broker's developer portal).
- ~30 minutes.

---

## Step 1 — Create the Always Free ARM instance (Mumbai)

1. Sign in to the **OCI Console** → set your region (top-right) to
   **India West (Mumbai)** — `ap-mumbai-1`.
2. **Compute → Instances → Create instance.**
3. **Image and shape:**
   - **Image:** Canonical **Ubuntu 24.04** (or 22.04).
   - **Shape:** click *Change shape* → **Ampere** → **`VM.Standard.A1.Flex`** →
     set **2 OCPUs** and **12 GB** memory (the full free allowance as of 2026).
4. **Networking:** let it create a new VCN + public subnet, and **Assign a public
   IPv4 address = Yes**.
5. **SSH keys:** upload/paste your **public** key.
6. **Boot volume:** default is fine (you have 200 GB free to grow into).
7. **Create.**

> ⚠️ **"Out of host capacity" for A1?** This is common in popular regions. Retry
> after a few minutes/hours, or try the other India AD. Some people script the
> retry with the OCI CLI (`oci compute instance launch` in a loop).

Wait until the instance is **Running**, then note its **Public IP address**.

---

## Step 2 — Make the public IP static (reserve it)

The default public IP is **ephemeral** — it can change if you stop/start the
instance, which would break your broker's IP whitelist. Reserve it:

1. **Compute → Instances → your instance → Attached VNICs → the primary VNIC →
   IPv4 Addresses.**
2. On the primary private IP's public IP, choose **Edit** → **Reserved public IP**
   → *Reserve a new public IP* (or convert the ephemeral one).

Your public IP is now **static**. Use it everywhere below (call it `PUBLIC_IP`).

---

## Step 3 — Open ports 80 & 443 in the OCI Security List (cloud firewall)

This is the **first** of Oracle's two firewalls (the host firewall is Step 4).

1. **Networking → Virtual Cloud Networks → your VCN → Security Lists → the
   default security list.**
2. **Add Ingress Rules** — add two rules:

   | Stateless | Source CIDR | IP Protocol | Destination Port Range |
   |---|---|---|---|
   | No | `0.0.0.0/0` | TCP | `80` |
   | No | `0.0.0.0/0` | TCP | `443` |

(Port 22/SSH is already open from instance creation.)

> 🔒 Optional hardening: restrict SSH (port 22) ingress to *your* IP only, and
> leave 80/443 open to the world.

---

## Step 4 — SSH in and run the bootstrap (fixes the host firewall)

```bash
ssh ubuntu@PUBLIC_IP
```

Oracle's Ubuntu images enforce a **host `iptables`** firewall that REJECTs
everything except SSH — even after Step 3. `bootstrap.sh` inserts ACCEPT rules
for 80/443 *before* that REJECT and persists them:

```bash
curl -fsSL https://raw.githubusercontent.com/rake93/openalgo/feat/oracle-deploy/deploy/oracle/bootstrap.sh | sudo bash
```

*(Once these files are merged to your `main`, drop the `feat/oracle-deploy`
branch segment from the URL.)*

You should see `Host firewall is ready. Ports opened: 80 443`.

> **Why both firewalls?** Skipping Step 3 → connection times out. Skipping Step 4
> → connection refused / "no route to host". You need **both**.

---

## Step 5 — Install OpenAlgo (Docker + nginx + TLS, all automated)

Build your free TLS hostname from the public IP using **sslip.io** — it resolves
`PUBLIC_IP.sslip.io` straight to your IP, so certbot can issue a real certificate
with no domain purchase. Example: IP `140.238.1.2` → `140.238.1.2.sslip.io`.

Run OpenAlgo's official Docker installer:

```bash
curl -fsSL https://raw.githubusercontent.com/marketcalls/openalgo/main/install/install-docker.sh -o install-docker.sh
sudo bash install-docker.sh
```

It will prompt for:

| Prompt | Enter |
|---|---|
| Domain name | `PUBLIC_IP.sslip.io` (e.g. `140.238.1.2.sslip.io`) |
| Broker name | your broker, e.g. `zerodha`, `dhan`, `angel` |
| Broker API key / secret | from your broker's developer portal |
| Email (for SSL) | your email (Let's Encrypt expiry notices) |
| Enable Remote MCP? | `N` unless you use Claude/ChatGPT MCP |

The installer then: installs Docker + nginx + certbot, clones OpenAlgo to
`/opt/openalgo`, writes `.env` (auto-generating unique `APP_KEY` /
`API_KEY_PEPPER` / `FERNET_SALT`), **builds the ARM64 image** (~15–20 min on
2 OCPUs — grab a coffee), obtains the TLS cert, and starts everything.

**Set your broker's redirect/callback URL** (in the broker's developer portal) to:
```
https://PUBLIC_IP.sslip.io/<broker>/callback
```
The installer prints the exact URL for your chosen broker.

---

## Step 6 — Register the IP with your broker (SEBI static-IP mandate)

Since **2026-04-01**, brokers only accept transactional API orders from a
**whitelisted static IP**. In your broker's developer/API portal, add your
**reserved `PUBLIC_IP`** (from Step 2) to the API IP allowlist.

Until this is done, market data and login work, but **order placement will be
rejected** by the broker.

---

## Step 7 — Log in and verify

1. Open **`https://PUBLIC_IP.sslip.io`** — you should get a valid padlock (TLS).
2. Complete OpenAlgo first-run setup (create your admin login).
3. Connect your broker (OAuth/login flow).
4. Verify:
   - **Dashboard** loads and updates (Flask-SocketIO).
   - **Market data / Option Chain** streams live quotes → confirms the
     nginx `/ws` → `8765` bridge works.
   - Place a test order in **Analyzer/Sandbox** (no real money) before going live.

---

## Operations

**Daily broker token refresh (~03:00 IST):** Indian broker tokens expire daily.
Log in again each morning (or use the broker's auto-login/TOTP features where
supported). This is inherent to Indian brokers, not to this deployment.

**View logs**
```bash
cd /opt/openalgo && sudo docker compose logs -f --tail=100
```

**Restart / stop / start**
```bash
cd /opt/openalgo && sudo docker compose restart
```

**Upgrade to the latest OpenAlgo**
```bash
cd /opt/openalgo
sudo git pull
sudo docker compose down && sudo docker compose build && sudo docker compose up -d
```
Your data (in Docker named volumes) and `.env` are preserved across upgrades.

**Back up your data** (users, broker tokens, orders, sandbox, historical data):
```bash
# named volumes live under /var/lib/docker/volumes/openalgo_*
sudo tar czf ~/openalgo-backup-$(date +%F).tar.gz \
  -C /var/lib/docker/volumes openalgo_db openalgo_keys
sudo cp /opt/openalgo/.env ~/openalgo.env.bak   # contains APP_KEY / PEPPER / FERNET_SALT — keep it safe
```
> Keep `.env` safe: its `API_KEY_PEPPER` / `FERNET_SALT` are required to decrypt
> your stored broker tokens. Losing them means re-logging in every broker.

---

## Oracle-specific notes & gotchas

- **Free tier can change:** In **June 2026** Oracle silently halved the free ARM
  allowance from 4 OCPU/24 GB to **2 OCPU/12 GB** (no announcement). 12 GB is
  still ~6× OpenAlgo's needs. Watch your usage emails.
- **Idle reclaim:** Oracle may reclaim *idle* Always Free compute instances. A
  live OpenAlgo instance streaming market data and running strategies is not
  idle, so this normally won't trigger — but don't leave it fully stopped for
  long periods.
- **ARM64 build time:** the first `docker compose build` compiles the frontend +
  Python deps for aarch64; expect ~15–20 min on 2 OCPUs. Subsequent builds are
  cached and fast.
- **`sslip.io` rate limit:** `sslip.io` (and `nip.io`) share a large Let's Encrypt
  rate-limit pool that is *very rarely* exhausted. If certbot fails with "too many
  certificates", either retry later, swap `sslip.io` → `nip.io` in the hostname,
  or use a real domain.
- **Broker choice matters for crypto:** Delta Exchange (crypto) is 24/7 — set
  `DISABLE_SESSION_EXPIRY='true'` in `.env` (the installer does this automatically
  for crypto brokers).

---

## Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| Browser **times out** on `https://…` | OCI **Security List** ingress for 80/443 missing (Step 3). |
| Browser gets **connection refused / "no route to host"** | Host **iptables** not opened (re-run `bootstrap.sh`, Step 4). Rule must be **before** the REJECT. |
| **certbot fails** to issue cert | Hostname must resolve to this IP (use `PUBLIC_IP.sslip.io` exactly); ports 80/443 must be reachable from the internet (Steps 3+4) *before* running the installer. |
| Dashboard loads but **no live market data** | The `/ws` route isn't reaching `8765`. Check `sudo docker compose logs` and that the installer configured nginx (`/etc/nginx/sites-enabled/`). |
| **Orders rejected** by broker | Register the reserved `PUBLIC_IP` in the broker's IP allowlist (Step 6). |
| A1 **"out of host capacity"** at create time | Retry later / other AD (Step 1). |
| Public IP **changed** after stop/start | You didn't reserve it — do Step 2, then re-whitelist at the broker. |

---

## What this deployment gives you

✅ Full OpenAlgo: Unified Broker API, Python Strategy Host, Flow no-code builder,
Options Trading Suite, Sandbox/Analyzer, Telegram alerts, Remote MCP (optional) —
with **live market-data streaming**, real-time dashboard, and **live orders** via
your whitelisted static IP — all for **₹0/month**.

See also: [`docs/superpowers/specs/2026-07-15-oracle-cloud-deploy-design.md`](../../docs/superpowers/specs/2026-07-15-oracle-cloud-deploy-design.md)
for the design rationale and the full provider comparison.
