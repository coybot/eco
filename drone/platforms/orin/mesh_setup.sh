#!/usr/bin/env bash
# Bring up an infrastructure-less Wi-Fi link for the team link (team_link.py):
# an 802.11s mesh point, falling back to IBSS (ad-hoc) when the radio cannot
# do mesh. No access point, no DHCP: teammates talk over IPv6 link-local
# multicast (team_link transport "ipv6_multicast"). See MESH.md.
#
#   sudo -E ./mesh_setup.sh <iface>            # bring up (idempotent)
#   sudo -E ./mesh_setup.sh <iface> --down     # tear down, hand back to NetworkManager
#   sudo -E ./mesh_setup.sh <iface> --status
#   ./mesh_setup.sh <iface> --dry-run          # print what would run, change nothing
#   add --force to proceed while coybot-network-manager is active
#
# Environment (sudo -E to pass it through):
#   MESH_ID          mesh ID / IBSS SSID, 1-32 chars (required to bring up)
#   MESH_PASSPHRASE  8-63 chars. SAE for mesh, WPA2-PSK (IBSS-RSN) for IBSS.
#                    Never printed. Lives only in /run/coybot-mesh (root, 0600).
#   MESH_CHANNEL     fixed channel, default 6 (2437 MHz). MESH_FREQ (MHz) overrides.
#   MESH_HT          HT20 (default) | NOHT | HT40+ | HT40-
#   MESH_MODE        auto (default: mesh if the phy supports it, else ibss) | mesh | ibss
#   MESH_ALLOW_OPEN  1 = allow a link with no link-layer encryption (no passphrase).
#                    team_link still authenticates every frame, but traffic is readable.
#   MESH_REGDOM      regulatory domain to set first, e.g. US
#   MESH_IPV4        optional address/prefix for the udp_broadcast transport, e.g. 192.0.2.11/24
#
# Every vehicle in the team must use the same MESH_ID, passphrase, channel and HT mode.
set -euo pipefail

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

IFACE=""
ACTION="up"
DRY_RUN=0
FORCE=0
for arg in "$@"; do
    case "$arg" in
        --down) ACTION="down" ;;
        --status) ACTION="status" ;;
        --dry-run) DRY_RUN=1 ;;
        --force) FORCE=1 ;;
        -h|--help) usage 0 ;;
        -*) echo "unknown option: $arg" >&2; usage 2 ;;
        *) [ -z "$IFACE" ] || { echo "one interface only" >&2; exit 2; }; IFACE="$arg" ;;
    esac
done
[ -n "$IFACE" ] || usage 2
case "$IFACE" in *[!A-Za-z0-9_.-]*) echo "bad interface name: $IFACE" >&2; exit 2 ;; esac

RUN_DIR="/run/coybot-mesh"
CONF="$RUN_DIR/$IFACE.conf"
PIDF="$RUN_DIR/$IFACE.pid"
LOGF="$RUN_DIR/$IFACE.log"
STATEF="$RUN_DIR/$IFACE.state"

log() { echo "[mesh_setup] $*"; }
die() { echo "[mesh_setup] ERROR: $*" >&2; exit 1; }
run() {
    if [ "$DRY_RUN" = 1 ]; then echo "+ $*"; else "$@"; fi
}
try() {   # best-effort: failures are fine (already in that state)
    if [ "$DRY_RUN" = 1 ]; then echo "+ $* || true"; else "$@" >/dev/null 2>&1 || true; fi
}
have() { command -v "$1" >/dev/null 2>&1; }

iface_exists() { [ -e "/sys/class/net/$IFACE" ]; }

wpa_running() {
    [ -f "$PIDF" ] || return 1
    local pid; pid="$(cat "$PIDF" 2>/dev/null || true)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

stop_wpa() {
    if wpa_running; then
        run kill "$(cat "$PIDF")"
        [ "$DRY_RUN" = 1 ] || for _ in 1 2 3 4 5 6 7 8 9 10; do wpa_running || break; sleep 0.2; done
    fi
    try rm -f "$PIDF" "$CONF"
}

link_local() {
    ip -6 -o addr show dev "$IFACE" scope link 2>/dev/null | awk '{print $4}' | head -n1
}

# ---------------------------------------------------------------------------- status
if [ "$ACTION" = "status" ]; then
    iface_exists || die "no interface $IFACE"
    have iw && iw dev "$IFACE" info || true
    if wpa_running; then log "wpa_supplicant pid $(cat "$PIDF") (log $LOGF)"; else log "no wpa_supplicant for $IFACE"; fi
    log "link-local: $(link_local || true)"
    if have iw; then
        log "neighbours:"
        iw dev "$IFACE" station dump 2>/dev/null | awk '/^Station/{m=$2} /^\tsignal:/{print "  " m "  " $2 " dBm"}' || true
    fi
    [ -f "$STATEF" ] && log "configured: $(cut -d' ' -f1-4 "$STATEF")" || true
    exit 0
fi

if [ "$DRY_RUN" = 0 ] && [ "$(id -u)" != 0 ]; then
    die "needs root (sudo -E $0 $*)"
fi
if [ "$DRY_RUN" = 0 ]; then
    have iw || die "iw not installed (sudo apt install iw)"
    have ip || die "ip (iproute2) not installed"
    iface_exists || die "no interface $IFACE (ip link)"
fi

# ---------------------------------------------------------------------------- down
if [ "$ACTION" = "down" ]; then
    stop_wpa
    try iw dev "$IFACE" mesh leave
    try iw dev "$IFACE" ibss leave
    if [ -n "${MESH_IPV4:-}" ]; then try ip addr del "$MESH_IPV4" dev "$IFACE"; fi
    run ip link set "$IFACE" down
    try iw dev "$IFACE" set type managed
    run ip link set "$IFACE" up
    if have nmcli; then try nmcli device set "$IFACE" managed yes; fi
    try rm -f "$STATEF" "$LOGF"
    log "$IFACE back to managed mode"
    exit 0
fi

# ---------------------------------------------------------------------------- up: validate
MESH_ID="${MESH_ID:-}"
MESH_PASSPHRASE="${MESH_PASSPHRASE:-}"
MESH_HT="${MESH_HT:-HT20}"
MESH_MODE="${MESH_MODE:-auto}"
[ -n "$MESH_ID" ] || die "set MESH_ID"
[ "${#MESH_ID}" -le 32 ] || die "MESH_ID is longer than 32 characters"
case "$MESH_ID" in *[\"\\]*|*$'\n'*) die "MESH_ID may not contain quotes, backslashes or newlines" ;; esac
if [ -n "$MESH_PASSPHRASE" ]; then
    [ "${#MESH_PASSPHRASE}" -ge 8 ] && [ "${#MESH_PASSPHRASE}" -le 63 ] \
        || die "MESH_PASSPHRASE must be 8-63 characters"
    case "$MESH_PASSPHRASE" in *[\"\\]*|*$'\n'*) die "MESH_PASSPHRASE may not contain quotes, backslashes or newlines" ;; esac
elif [ "${MESH_ALLOW_OPEN:-0}" != 1 ]; then
    die "set MESH_PASSPHRASE (or MESH_ALLOW_OPEN=1 for an unencrypted link)"
fi
case "$MESH_HT" in HT20|NOHT|HT40+|HT40-) ;; *) die "MESH_HT must be HT20, NOHT, HT40+ or HT40-" ;; esac

if [ -n "${MESH_FREQ:-}" ]; then
    FREQ="$MESH_FREQ"
else
    CH="${MESH_CHANNEL:-6}"
    case "$CH" in ''|*[!0-9]*) die "MESH_CHANNEL must be a number" ;; esac
    if [ "$CH" -ge 1 ] && [ "$CH" -le 13 ]; then FREQ=$((2407 + 5 * CH))
    elif [ "$CH" -eq 14 ]; then FREQ=2484
    elif [ "$CH" -ge 32 ] && [ "$CH" -le 177 ]; then FREQ=$((5000 + 5 * CH))
    else die "MESH_CHANNEL $CH is not a 2.4/5 GHz channel"; fi
fi
case "$FREQ" in ''|*[!0-9]*) die "MESH_FREQ must be MHz, e.g. 2437" ;; esac

# coybot-network-manager flips the first Wi-Fi interface between client and AP
# mode on its own schedule; if that is this interface it will fight the mesh.
if [ "$DRY_RUN" = 0 ] && have systemctl && systemctl is-active --quiet coybot-network-manager 2>/dev/null; then
    if [ "$FORCE" != 1 ]; then
        die "coybot-network-manager is active and may reconfigure $IFACE. Use a second radio and pin
       network_manager.yaml 'interface:' to the other one, or stop that service; then re-run with --force."
    fi
    log "WARNING: coybot-network-manager is active (--force given)"
fi

# ---------------------------------------------------------------------------- up: pick mode
PHY=""
MODES=""
if iface_exists && [ -e "/sys/class/net/$IFACE/phy80211/name" ]; then
    PHY="$(cat "/sys/class/net/$IFACE/phy80211/name")"
    MODES="$(iw phy "$PHY" info 2>/dev/null | awk '/Supported interface modes:/{f=1;next} f&&/^\t\t\*/{sub(/^\t\t\* /,"");print;next} f{exit}')"
elif [ "$DRY_RUN" = 0 ]; then
    die "$IFACE is not an nl80211 Wi-Fi interface"
fi
supports() { printf '%s\n' "$MODES" | grep -qx "$1"; }

MODE="$MESH_MODE"
if [ "$MODE" = auto ]; then
    if [ -z "$MODES" ] || supports "mesh point"; then MODE=mesh
    elif supports "IBSS"; then MODE=ibss
    else die "$PHY supports neither mesh point nor IBSS: $(echo $MODES)"; fi
fi
case "$MODE" in
    mesh) [ -z "$MODES" ] || supports "mesh point" || die "$PHY does not support mesh point (try MESH_MODE=ibss)" ;;
    ibss) [ -z "$MODES" ] || supports "IBSS" || die "$PHY does not support IBSS" ;;
    *) die "MESH_MODE must be auto, mesh or ibss" ;;
esac
SEC="open"; [ -n "$MESH_PASSPHRASE" ] && SEC="$([ "$MODE" = mesh ] && echo sae || echo wpa2psk)"
[ "$SEC" = open ] && log "WARNING: no link-layer encryption (MESH_ALLOW_OPEN=1)"

# Desired-state fingerprint; the passphrase enters only as a hash.
PASS_HASH="$(printf '%s' "$MESH_PASSPHRASE" | sha256sum 2>/dev/null | cut -c1-16 || printf '%s' "$MESH_PASSPHRASE" | shasum -a 256 | cut -c1-16)"
WANT="mode=$MODE freq=$FREQ ht=$MESH_HT sec=$SEC id=$MESH_ID ipv4=${MESH_IPV4:-} pass=$PASS_HASH"

already_up() {
    [ -f "$STATEF" ] && [ "$(cat "$STATEF")" = "$WANT" ] || return 1
    local info; info="$(iw dev "$IFACE" info 2>/dev/null)" || return 1
    case "$MODE" in
        mesh) printf '%s' "$info" | grep -q "type mesh point" || return 1 ;;
        ibss) printf '%s' "$info" | grep -q "type IBSS" || return 1 ;;
    esac
    if [ "$SEC" != open ]; then wpa_running || return 1
    else printf '%s' "$info" | grep -q "($FREQ MHz)" || return 1; fi
}

if [ "$DRY_RUN" = 0 ] && already_up; then
    log "$IFACE already up: $MODE $SEC on $FREQ MHz $MESH_HT, link-local $(link_local)"
    exit 0
fi

log "bringing up $IFACE${PHY:+ ($PHY)}: $MODE, $SEC, $FREQ MHz $MESH_HT, id '$MESH_ID'"

# ---------------------------------------------------------------------------- up: configure
run mkdir -p "$RUN_DIR"
run chmod 700 "$RUN_DIR"
if have nmcli || [ "$DRY_RUN" = 1 ]; then try nmcli device set "$IFACE" managed no; fi
if [ -n "${MESH_REGDOM:-}" ]; then run iw reg set "$MESH_REGDOM"; fi
stop_wpa
try iw dev "$IFACE" mesh leave
try iw dev "$IFACE" ibss leave
run ip link set "$IFACE" down
if [ "$MODE" = mesh ]; then run iw dev "$IFACE" set type mp; else run iw dev "$IFACE" set type ibss; fi
try sysctl -w "net.ipv6.conf.$IFACE.disable_ipv6=0"
run ip link set "$IFACE" up
try iw dev "$IFACE" set power_save off

if [ "$SEC" = open ]; then
    HTARG="$MESH_HT"
    if [ "$MODE" = mesh ]; then
        run iw dev "$IFACE" mesh join "$MESH_ID" freq "$FREQ" "$HTARG"
    else
        run iw dev "$IFACE" ibss join "$MESH_ID" "$FREQ" "$HTARG" fixed-freq
    fi
else
    have wpa_supplicant || [ "$DRY_RUN" = 1 ] || die "wpa_supplicant not installed (needed for $SEC)"
    case "$MESH_HT" in
        NOHT) HTCONF="    disable_ht=1" ;;
        HT20) HTCONF="    disable_ht40=1" ;;
        *)    HTCONF="    ht40=1" ;;
    esac
    if [ "$MODE" = mesh ]; then
        NET="    mode=5
    key_mgmt=SAE
    mesh_fwding=1"
        GLOBAL="user_mpm=1"
    else
        NET="    mode=1
    proto=RSN
    key_mgmt=WPA-PSK
    pairwise=CCMP
    group=CCMP"
        GLOBAL="ap_scan=2"
    fi
    write_conf() {   # $1 = passphrase text to write
        cat <<EOF
# generated by mesh_setup.sh — do not edit; re-run the script instead
ctrl_interface=$RUN_DIR/wpa-$IFACE
$GLOBAL
${MESH_REGDOM:+country=$MESH_REGDOM}
network={
    ssid="$MESH_ID"
    frequency=$FREQ
$NET
$HTCONF
    psk="$1"
}
EOF
    }
    if [ "$DRY_RUN" = 1 ]; then
        echo "+ write $CONF (0600):"
        write_conf "<MESH_PASSPHRASE redacted>" | sed 's/^/    /'
    else
        ( umask 077; write_conf "$MESH_PASSPHRASE" > "$CONF" )
    fi
    run wpa_supplicant -B -D nl80211 -i "$IFACE" -c "$CONF" -P "$PIDF" -f "$LOGF"
fi

if [ -n "${MESH_IPV4:-}" ]; then run ip addr replace "$MESH_IPV4" dev "$IFACE"; fi

if [ "$DRY_RUN" = 1 ]; then
    echo "+ record state in $STATEF"
    exit 0
fi
printf '%s' "$WANT" > "$STATEF"
chmod 600 "$STATEF"

LL=""
for _ in $(seq 1 25); do LL="$(link_local)"; [ -n "$LL" ] && break; sleep 0.2; done
if [ "$SEC" != open ]; then
    sleep 1
    wpa_running || die "wpa_supplicant exited — see $LOGF (driver may not support $SEC in $MODE mode)"
fi
log "up: $IFACE $MODE $SEC $FREQ MHz, link-local ${LL:-<none yet>}"
log "team_link config: transport: ipv6_multicast, interface: $IFACE"
