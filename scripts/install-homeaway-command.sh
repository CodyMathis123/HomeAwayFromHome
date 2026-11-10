#!/usr/bin/env bash
# Install one management command. No containers, host agents, secrets or
# application configuration are created or modified by this installer.
set -euo pipefail

usage() {
  echo "Usage: bash scripts/install-homeaway-command.sh --plan|--apply" >&2
}
if [[ "$#" -ne 1 ]]; then
  usage
  exit 2
fi
mode="$1"
if [[ "$mode" != "--plan" && "$mode" != "--apply" ]]; then
  usage
  exit 2
fi

root="$(cd "$(dirname "$0")/.." && pwd -P)"
script="$root/scripts/homeaway.py"
target=/usr/local/bin/homeaway
[[ -f "$script" && ! -L "$script" ]] || {
  echo "ERROR: Missing or symlinked scripts/homeaway.py" >&2; exit 1;
}
[[ -f "$root/compose.yaml" ]] || {
  echo "ERROR: Not a HomeAway checkout" >&2; exit 1;
}
command -v python3 >/dev/null || {
  echo "ERROR: python3 is required" >&2; exit 1;
}
if [[ -e "$target" || -L "$target" ]]; then
  if [[ -L "$target" ]] || ! grep -Fq '# homeaway-managed-command' "$target"; then
    echo "ERROR: Existing unrecognized $target; refusing to overwrite." >&2
    exit 1
  fi
fi

echo "Repo: $root"
echo "Command: $target"
echo "The command runs as your login user and invokes reviewed sudo helpers."
echo "No applications, services, Docker settings, or credentials will change."
if [[ "$mode" == "--plan" ]]; then
  echo "PLAN ONLY: use sudo bash scripts/install-homeaway-command.sh --apply"
  exit 0
fi
[[ "$EUID" -eq 0 ]] || {
  echo "ERROR: use sudo for --apply" >&2; exit 1;
}
# Use Bash's shell escaping; avoid interpreting checkout paths as code.
printf -v quoted '%q' "$script"
install -d -m 0755 /usr/local/bin
tmp="$(mktemp /usr/local/bin/.homeaway.XXXXXXXX)"
trap 'rm -f "$tmp"' EXIT
printf '#!/usr/bin/env bash\n# homeaway-managed-command\nexec /usr/bin/python3 %s "$@"\n' "$quoted" > "$tmp"
chmod 0755 "$tmp"
mv -f -- "$tmp" "$target"
trap - EXIT
echo "Installed $target (no services restarted)."
echo "Next: homeaway status"
