#!/usr/bin/env bash
# Builds mosquitto/passwd from gcs/config.yaml's data_dir/auth.json (the same
# pairing tokens the HTTP API and drones use), plus a "gcs-server" account for
# the GCS process's own MQTT connection. Run this AFTER starting the GCS
# server at least once (so auth.json and drone tokens exist), and re-run it
# whenever a new drone registers or an operator is added.
#
# Usage:
#   ./gen_mosquitto_auth.sh [--data-dir ~/gcs-data] [--tls]
#
# --tls also generates a self-signed CA/cert/key under mosquitto/certs/ (same
# openssl invocation drone/common/local_control_api.py uses for its own
# self-signed cert), for the optional TLS listener in mosquitto.conf.
set -euo pipefail

DATA_DIR="${DATA_DIR:-$HOME/gcs-data}"
GEN_TLS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --tls) GEN_TLS=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 1 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTH_JSON="$DATA_DIR/auth.json"
PASSWD_FILE="$SCRIPT_DIR/mosquitto/passwd"

if [[ ! -f "$AUTH_JSON" ]]; then
  echo "error: $AUTH_JSON not found - run the GCS server once first (python3 -m gcs.server) so tokens exist" >&2
  exit 1
fi

if ! command -v mosquitto_passwd >/dev/null 2>&1; then
  echo "error: mosquitto_passwd not found - install mosquitto (e.g. 'brew install mosquitto')" >&2
  exit 1
fi

rm -f "$PASSWD_FILE"
touch "$PASSWD_FILE"

# gcs-server: the GCS process's own MQTT connection (matches mqtt.username in
# config.yaml - password is a fresh random token; put it back into
# config.yaml's mqtt.password field).
SERVER_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
mosquitto_passwd -b "$PASSWD_FILE" gcs-server "$SERVER_PASSWORD"
echo "gcs-server MQTT password (put this in config.yaml's mqtt.password): $SERVER_PASSWORD"

python3 - "$AUTH_JSON" "$PASSWD_FILE" <<'PYEOF'
import json
import subprocess
import sys

auth_path, passwd_path = sys.argv[1], sys.argv[2]
data = json.loads(open(auth_path).read())

for drone_id, token in data.get("drones", {}).items():
    subprocess.run(["mosquitto_passwd", "-b", passwd_path, drone_id, token], check=True)
    print(f"drone MQTT account: {drone_id}")

for name, token in data.get("operators", {}).items():
    account = f"operator-{name}"
    subprocess.run(["mosquitto_passwd", "-b", passwd_path, account, token], check=True)
    print(f"operator MQTT account: {account}")
PYEOF

echo "wrote $PASSWD_FILE"

if [[ "$GEN_TLS" -eq 1 ]]; then
  CERT_DIR="$SCRIPT_DIR/mosquitto/certs"
  mkdir -p "$CERT_DIR"
  if [[ ! -f "$CERT_DIR/ca.pem" ]]; then
    openssl req -x509 -newkey rsa:2048 -keyout "$CERT_DIR/ca.key" -out "$CERT_DIR/ca.pem" \
      -days 3650 -nodes -subj "/CN=gcs-local-ca"
  fi
  if [[ ! -f "$CERT_DIR/server.pem" ]]; then
    openssl req -newkey rsa:2048 -keyout "$CERT_DIR/server.key" -out "$CERT_DIR/server.csr" \
      -nodes -subj "/CN=gcs-mosquitto"
    openssl x509 -req -in "$CERT_DIR/server.csr" -CA "$CERT_DIR/ca.pem" -CAkey "$CERT_DIR/ca.key" \
      -CAcreateserial -out "$CERT_DIR/server.pem" -days 3650
    rm -f "$CERT_DIR/server.csr"
  fi
  echo "wrote self-signed CA/cert/key to $CERT_DIR - uncomment the TLS listener in mosquitto.conf"
  echo "and copy $CERT_DIR/ca.pem to each drone's certs/gcs-ca.pem (see drone/common/config.yaml.example)"
fi
