#!/usr/bin/env bash
# Shared restore routine for encrypted backup and its isolated shell tests.
# Caller defines active_units and running from its pre-outage inventory.
homeaway_restore_active() {
  local failed=0 unit cid state
  for unit in "${active_units[@]}"; do
    if ! systemctl start "$unit"; then
      echo "ERROR: failed to restore host unit: $unit" >&2
      failed=1
    fi
  done
  for cid in "${running[@]}"; do
    state="$(docker inspect --format '{{.State.Running}}' "$cid" 2>/dev/null || true)"
    if [[ "$state" == false ]]; then
      if ! docker start "$cid" >/dev/null; then
        echo "ERROR: failed to restart container: $cid" >&2
        failed=1
      fi
    elif [[ "$state" != true ]]; then
      echo "ERROR: previously running container missing: $cid" >&2
      failed=1
    fi
  done
  return "$failed"
}
