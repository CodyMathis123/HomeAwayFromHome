"""Pure, read-only installed-component transition planning.

A deliberately bounded lifecycle: the first supported live changes concern
only node-exporter, diagnostics and portal. Cooling telemetry has separate
certificate and sender requirements and cannot be toggled in this phase.
"""
from __future__ import annotations

from components import SelectionError, resolve

OPTIONAL = frozenset({"linux-host-monitoring", "monitoring-diagnostics", "home-portal"})
STICKY = frozenset({"cooling-monitoring"})
PRESERVED_VOLUMES = (
    "cooling-caddy-config", "cooling-caddy-data", "cooling-grafana",
    "cooling-metrics",
)


def transition(catalog: dict[str, dict], old: tuple[str, ...],
               new: tuple[str, ...]) -> dict:
    before = resolve(catalog, old)
    after = resolve(catalog, new)
    previous = set(before["selected_features"])
    desired = set(after["selected_features"])
    if previous == desired:
        raise SelectionError("Selection unchanged. No component change to apply.")
    if bool(previous & STICKY) != bool(desired & STICKY):
        raise SelectionError(
            "Enabling or disabling cooling-monitoring requires a separate "
            "certificate/token migration and is not supported by this workflow."
        )
    changed = previous ^ desired
    if changed - OPTIONAL:
        raise SelectionError(
            "Component change outside the reviewed optional-monitoring scope."
        )
    added = sorted(desired - previous)
    removed = sorted(previous - desired)
    old_services = set(before["services"])
    new_services = set(after["services"])
    return {
        "schema_version": 1,
        "before": before["selected_features"],
        "after": after["selected_features"],
        "add_features": added,
        "remove_features": removed,
        "add_services": sorted(new_services - old_services),
        "remove_services": sorted(old_services - new_services),
        "retained_services": sorted(old_services & new_services),
        "retained_volumes": list(PRESERVED_VOLUMES),
        "potential_disruption": (
            "Compose may briefly recreate the gateway and other changed "
            "containers; monitoring availability can be interrupted."
        ),
        "data_policy": (
            "Existing Docker named volumes and previously generated secrets "
            "are never deleted. Historical metrics remain retained."
        ),
        "requires_confirmation": True,
        "backup_recommended": True,
        "eligible": True,
    }
