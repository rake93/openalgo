#!/usr/bin/env bash
#
# OpenAlgo — Oracle Cloud (OCI) Always Free bootstrap
# ---------------------------------------------------
# Prepares a FRESH Oracle Cloud "Ubuntu" ARM (Ampere A1) instance so that
# OpenAlgo's own installer (install/install-docker.sh) can run successfully.
#
# WHY THIS SCRIPT EXISTS
#   install-docker.sh already installs Docker, nginx, certbot, clones the repo,
#   builds the image and starts OpenAlgo. The ONE thing it does not handle is the
#   single biggest Oracle Cloud gotcha: OCI's Ubuntu images ship a host `iptables`
#   INPUT chain whose last rule REJECTs everything except SSH (port 22). Opening
#   80/443 in the OCI *Security List* (the cloud firewall) alone is NOT enough —
#   the packets still die at the host firewall, with a misleading
#   "connection refused / no route to host". Worse, an ACCEPT rule *appended*
#   after the REJECT is silently ignored, because the REJECT matches first.
#
#   This script inserts ACCEPT rules for tcp/80 and tcp/443 *before* that REJECT
#   rule and persists them across reboots. It is idempotent — safe to re-run.
#
# WHAT IT DOES NOT DO
#   - It does NOT touch the OCI Security List (the cloud-side firewall). That is a
#     one-time step in the OCI Console / CLI — see deploy/oracle/README.md.
#   - It does NOT install Docker or clone the app — install-docker.sh does that.
#
# USAGE (on the instance, as the default `ubuntu` user):
#   curl -fsSL https://raw.githubusercontent.com/rake93/openalgo/feat/oracle-deploy/deploy/oracle/bootstrap.sh | sudo bash
#     ...or, once these files are on your main branch, drop the branch segment.
#
#   # Fix the firewall, then immediately launch OpenAlgo's interactive installer:
#   curl -fsSL <url>/bootstrap.sh | sudo bash -s -- --run-installer
#
# ENV OVERRIDES
#   OPEN_PORTS            space-separated TCP ports to open   (default: "80 443")
#   OPENALGO_INSTALLER_URL  install-docker.sh URL used by --run-installer
#                         (default: upstream marketcalls/openalgo main)
#
set -euo pipefail

# ----------------------------------------------------------------------------- #
# pretty logging
# ----------------------------------------------------------------------------- #
if [ -t 1 ]; then
    C_BLUE=$'\033[0;34m'; C_GREEN=$'\033[0;32m'; C_YELLOW=$'\033[1;33m'
    C_RED=$'\033[0;31m'; C_NC=$'\033[0m'
else
    C_BLUE=""; C_GREEN=""; C_YELLOW=""; C_RED=""; C_NC=""
fi
log()  { printf '%s%s%s\n' "${2:-$C_NC}" "$1" "$C_NC"; }
info() { log "$1" "$C_BLUE"; }
ok()   { log "$1" "$C_GREEN"; }
warn() { log "$1" "$C_YELLOW"; }
die()  { log "ERROR: $1" "$C_RED" >&2; exit 1; }

# ----------------------------------------------------------------------------- #
# config
# ----------------------------------------------------------------------------- #
OPEN_PORTS="${OPEN_PORTS:-80 443}"
OPENALGO_INSTALLER_URL="${OPENALGO_INSTALLER_URL:-https://raw.githubusercontent.com/marketcalls/openalgo/main/install/install-docker.sh}"
RUN_INSTALLER=0

usage() {
    cat <<'USAGE'
OpenAlgo — Oracle Cloud Always Free bootstrap

Fixes the OCI host iptables firewall (opens 80/443 before the default REJECT rule)
so OpenAlgo's Docker installer can run. Idempotent.

Usage:
  sudo bash bootstrap.sh [--run-installer]

Options:
  --run-installer   After fixing the firewall, download and run OpenAlgo's
                    interactive Docker installer (install-docker.sh).
  -h, --help        Show this help.

Env overrides:
  OPEN_PORTS              TCP ports to open           (default: "80 443")
  OPENALGO_INSTALLER_URL  installer URL for --run-installer

Full runbook: deploy/oracle/README.md
USAGE
}

for arg in "$@"; do
    case "$arg" in
        --run-installer) RUN_INSTALLER=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "unknown argument: $arg (try --help)" ;;
    esac
done

echo
info "=============================================================="
info " OpenAlgo — Oracle Cloud Always Free bootstrap"
info "=============================================================="
echo

# ----------------------------------------------------------------------------- #
# preflight
# ----------------------------------------------------------------------------- #
[ "$(id -u)" -eq 0 ] || die "run as root (use: sudo bash bootstrap.sh)"

if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case "${ID:-}${ID_LIKE:-}" in
        *debian*|*ubuntu*) : ;;
        *) warn "This script targets OCI Ubuntu/Debian images (found: ${PRETTY_NAME:-unknown}). Continuing, but the iptables/persistence steps assume Debian-family tooling." ;;
    esac
fi

ARCH="$(uname -m)"
if [ "$ARCH" != "aarch64" ] && [ "$ARCH" != "arm64" ]; then
    warn "CPU arch is '$ARCH', not ARM64. Oracle Always Free is Ampere A1 (aarch64); this still works on x86 shapes, just not the free tier."
fi

command -v iptables >/dev/null 2>&1 || die "iptables not found — unexpected on an OCI Ubuntu image."

# ----------------------------------------------------------------------------- #
# 1. ensure rules survive reboot (netfilter-persistent / iptables-persistent)
# ----------------------------------------------------------------------------- #
info "[1/3] Ensuring iptables-persistent is installed..."
if ! command -v netfilter-persistent >/dev/null 2>&1; then
    # Preseed so the package does not prompt to save current rules interactively.
    echo iptables-persistent iptables-persistent/autosave_v4 boolean true | debconf-set-selections
    echo iptables-persistent iptables-persistent/autosave_v6 boolean true | debconf-set-selections
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y iptables-persistent
    ok "  • installed iptables-persistent"
else
    ok "  • already present"
fi

# ----------------------------------------------------------------------------- #
# 2. open the requested ports on the host INPUT chain, BEFORE the REJECT rule
# ----------------------------------------------------------------------------- #
# ensure_port <iptables-binary> <port>
ensure_port() {
    local ipt="$1" port="$2"
    # Idempotency: skip if an identical ACCEPT rule already exists.
    if "$ipt" -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
        ok "  • ${ipt} tcp/${port} already allowed"
        return 0
    fi
    # Find the first REJECT/DROP rule in INPUT and insert just above it, so the
    # new ACCEPT is evaluated first. Appending after REJECT would be ignored.
    local reject_line
    reject_line="$("$ipt" -L INPUT --line-numbers -n 2>/dev/null | awk '$2=="REJECT" || $2=="DROP" {print $1; exit}')"
    if [ -n "$reject_line" ]; then
        "$ipt" -I INPUT "$reject_line" -p tcp --dport "$port" -j ACCEPT
        ok "  • ${ipt} tcp/${port} allowed (inserted before REJECT at line ${reject_line})"
    else
        "$ipt" -A INPUT -p tcp --dport "$port" -j ACCEPT
        ok "  • ${ipt} tcp/${port} allowed (appended — no REJECT rule found)"
    fi
}

info "[2/3] Opening host firewall ports: ${OPEN_PORTS}"
for port in $OPEN_PORTS; do
    ensure_port iptables "$port"
    # Mirror onto IPv6 as best-effort only (Always Free instances are IPv4 by
    # default). Run in a subshell with errexit off so a v6 quirk can never abort
    # the essential IPv4 setup above.
    if command -v ip6tables >/dev/null 2>&1; then
        ( set +e; ensure_port ip6tables "$port" ) || true
    fi
done

# ----------------------------------------------------------------------------- #
# 3. persist
# ----------------------------------------------------------------------------- #
info "[3/3] Persisting rules across reboots..."
netfilter-persistent save >/dev/null 2>&1 || die "failed to persist iptables rules"
ok "  • saved to /etc/iptables/rules.v{4,6}"

echo
ok "Host firewall is ready. Ports opened: ${OPEN_PORTS}"
echo

# ----------------------------------------------------------------------------- #
# next steps
# ----------------------------------------------------------------------------- #
warn "IMPORTANT — this only fixed the HOST firewall (iptables)."
warn "You must ALSO open the same ports in the OCI Security List (cloud firewall):"
warn "  OCI Console → Networking → VCN → your subnet → Security List →"
warn "  Add Ingress Rules:  Source 0.0.0.0/0  |  TCP  |  Dest ports 80 and 443"
echo

if [ "$RUN_INSTALLER" -eq 1 ]; then
    info "Launching OpenAlgo's Docker installer (interactive)..."
    info "It will ask for: your hostname (e.g. <public-ip>.sslip.io), broker name,"
    info "broker API key/secret, and an email for the TLS certificate."
    echo
    tmp="$(mktemp)"
    trap 'rm -f "$tmp"' EXIT
    curl -fsSL "$OPENALGO_INSTALLER_URL" -o "$tmp" || die "failed to download installer from $OPENALGO_INSTALLER_URL"
    bash "$tmp"
else
    info "Next: run OpenAlgo's Docker installer (interactive)."
    info "It installs Docker + nginx + certbot, clones OpenAlgo, builds the ARM"
    info "image and starts everything with HTTPS."
    echo
    cat <<EOF
  curl -fsSL ${OPENALGO_INSTALLER_URL} -o install-docker.sh
  sudo bash install-docker.sh

When prompted for the domain, use your free sslip.io hostname built from the
instance's PUBLIC IP, e.g. for 140.238.1.2  ->  140.238.1.2.sslip.io

Full runbook: deploy/oracle/README.md
EOF
fi
echo
ok "Done."
