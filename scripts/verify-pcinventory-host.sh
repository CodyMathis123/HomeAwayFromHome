#!/usr/bin/env bash
# Operator-run, read-only smoke test for the optional Phase 2 host components.
# Never sends WOL/SSH power actions, changes inventory or prints credentials.
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo "Run as root: sudo bash $0" >&2; exit 2; }
for unit in homeaway-inventory.socket homeaway-pcverify.socket; do
  systemctl is-active --quiet "$unit" || {
    echo "FAIL: $unit is not active; do not enable management UI" >&2
    exit 1
  }
done
# Only inspect—not restart—the legacy agent controlling current PCs.
if ! systemctl is-active --quiet pcctl-agent.socket; then
  echo "WARNING: existing pcctl-agent.socket is not active; verify the legacy control path independently"
fi
for spec in \
  "/run/homeaway-pcinventory/agent.sock homeaway-inventory homeaway-inventory" \
  "/run/homeaway-pcverify/agent.sock pcctl homeaway-inventory"; do
  read -r path owner group <<< "$spec"
  [[ -S "$path" && ! -L "$path" ]] || { echo "FAIL: management socket missing or unsafe: $path" >&2; exit 1; }
  [[ "$(stat -c '%a %U %G' "$path")" == "660 $owner $group" ]] || {
    echo "FAIL: socket mode/owner/group mismatch for $path" >&2
    exit 1
  }
done

# Connect as the actual unprivileged inventory writer. A read-only list and
# an intentionally INVALID verifier operation exercise both Unix sockets
# and their ACLs without ever initiating SSH/network traffic.
runuser -u homeaway-inventory -- python3 -B - <<'PY'
import json
import socket

def rpc(path, command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(10)
        client.connect(path)
        client.sendall((json.dumps(command, separators=(",", ":")) + "\n").encode())
        payload = client.makefile("rb").readline(65537)
    if not payload.endswith(b"\n") or len(payload) > 65536:
        raise SystemExit("FAIL: invalid host-side socket response")
    return json.loads(payload)

inventory = rpc("/run/homeaway-pcinventory/agent.sock", {
    "actor": "smoke-check", "operation": "list", "payload": {}
})
if not inventory.get("ok") or not isinstance(inventory.get("result", {}).get("active"), list):
    raise SystemExit("FAIL: cannot read active inventory through management socket")
print("PASS: inventory socket accepted read-only list; active count:",
      len(inventory["result"]["active"]))

verifier = rpc("/run/homeaway-pcverify/agent.sock", {
    "operation": "invalid-smoke-operation", "candidate": {}
})
if verifier != {"verified": False, "code": "invalid_request"}:
    raise SystemExit("FAIL: verifier did not reject unrecognized operation")
print("PASS: verifier socket rejected invalid operation without SSH or network")
PY
echo "PASS: Phase 2 read-only host smoke; active PC inventory and power service unchanged"
