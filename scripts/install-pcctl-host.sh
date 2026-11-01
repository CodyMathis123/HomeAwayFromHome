#!/usr/bin/env bash
# Pi-side installer; does not alter a working legacy pcctl without --replace-legacy.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
apply=false
replace=false
for arg in "$@"; do
  case "$arg" in
    --apply) apply=true ;;
    --replace-legacy) replace=true ;;
    *) echo "Usage: sudo bash scripts/install-pcctl-host.sh --apply [--replace-legacy]" >&2; exit 2 ;;
  esac
done
echo "HomeAway PC host install: plan"
echo " - validates runtime inventory at /etc/homeaway-pcctl/hosts.json"
echo " - installs versioned pcctl modules, socket-activated non-root agent"
echo " - installs pcctl + pcctl-dispatch wrappers only if available or --replace-legacy"
echo " - never overwrites SSH keys, iPhone authorized_keys or Windows credentials"
if [[ "$apply" != true ]]; then
  echo "DRY RUN. Use --apply after reviewing scripts and backups."
  exit 0
fi
[[ "$(id -u)" == 0 ]] || { echo "Run as root" >&2; exit 1; }
command -v ssh >/dev/null || { echo "OpenSSH client required" >&2; exit 1; }
if ! id pcctl >/dev/null 2>&1; then
  useradd --system --user-group --create-home --shell /bin/bash pcctl
fi
install -d -m 0755 /etc/homeaway-pcctl /opt/homeaway-pcctl
inventory=/etc/homeaway-pcctl/hosts.json
if [[ ! -f "$inventory" ]]; then
  echo "First create $inventory using control/pcctl/hosts.example.json with real MACs." >&2
  exit 1
fi
HOMEAWAY_HOSTS_FILE="$inventory" python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from core import load_hosts; print("Validated",len(load_hosts()),"computers")' "$root/control/pcctl"
for file in core.py pcctl.py pcctl-dispatch.py agent.py; do
  install -m 0644 "$root/control/pcctl/$file" "/opt/homeaway-pcctl/$file"
done
for cmd in pcctl pcctl-dispatch; do
  target="/usr/local/bin/$cmd"
  if [[ -e "$target" || -L "$target" ]]; then
    if [[ "$replace" != true ]]; then
      echo "Existing $target preserved. To migrate it, rerun with --replace-legacy after backing up." >&2
      continue
    fi
    cp -a "$target" "$target.backup.$(date +%Y%m%d-%H%M%S)"
  fi
  [[ "$cmd" == "pcctl" ]] && source=pcctl.py || source=pcctl-dispatch.py
  cat > "$target" <<EOF
#!/usr/bin/env bash
exec /usr/bin/python3 -B /opt/homeaway-pcctl/$source "\$@"
EOF
  chmod 0755 "$target"
done
# Run-directory must be traversable by the pcctl Unix-socket group after reboot.
install -m 0644 "$root/control/pcctl/homeaway-pcctl.tmpfiles.conf" /etc/tmpfiles.d/homeaway-pcctl.conf
systemd-tmpfiles --create /etc/tmpfiles.d/homeaway-pcctl.conf
install -m 0644 "$root/control/pcctl/pcctl-agent.socket" /etc/systemd/system/pcctl-agent.socket
install -m 0644 "$root/control/pcctl/pcctl-agent.service" /etc/systemd/system/pcctl-agent.service
systemctl daemon-reload
systemctl enable --now pcctl-agent.socket
echo "Socket ready; PC controls remain restricted by inventory and existing keys."
echo "PCCTL_GID=$(id -g pcctl)  # add this to local .env for the unprivileged control container"
