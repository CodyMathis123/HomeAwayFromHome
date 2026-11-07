"""Declarative, read-only component selector and dependency resolver.

The catalog describes future features, but only an explicitly wired bundle
can be deployed. Resolution never invokes Docker or modifies host state.
"""
from __future__ import annotations

import json
from pathlib import Path
import re

CATALOG = Path(__file__).resolve().parents[1] / "deploy/components/catalog.json"
ID = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
STATUSES = {"prototype", "planned"}
# Only reviewed modules may be converted to deployable Compose services.
# Explicit expectations prevent a catalog edit from silently adding a
# privileged service or pulling an unreviewed container into a release.
MONITORING_FEATURES = frozenset({"cooling-monitoring", "linux-host-monitoring"})
OPTIONAL_FEATURES = frozenset({"monitoring-diagnostics", "home-portal"})
SUPPORTED_FEATURES = MONITORING_FEATURES | OPTIONAL_FEATURES
SHARED_SERVICES = frozenset({"gateway", "grafana", "victoria-metrics"})
FEATURE_SERVICES = {
    "cooling-monitoring": frozenset({"telemetry-ingest"}),
    "linux-host-monitoring": frozenset({"node-exporter"}),
    "monitoring-diagnostics": frozenset({"metrics-diagnostics"}),
    "home-portal": frozenset({"portal"}),
}

def _expected_services(closure: set[str], selected: set[str]) -> frozenset[str] | None:
    if not selected & MONITORING_FEATURES:
        return None
    if not selected <= SUPPORTED_FEATURES:
        return None
    if not closure <= (SUPPORTED_FEATURES | {"metrics-storage", "observability-ui"}):
        return None
    expected = set(SHARED_SERVICES)
    for feature in selected:
        expected.update(FEATURE_SERVICES[feature])
    # An optional portal can only be deployed with read-only diagnostics.
    if "home-portal" in selected and "monitoring-diagnostics" not in closure:
        return None
    if "monitoring-diagnostics" in closure:
        expected.update(FEATURE_SERVICES["monitoring-diagnostics"])
    return frozenset(expected)


class SelectionError(ValueError):
    """An invalid or not-yet-available feature selection."""


def parse_selection(value: str) -> tuple[str, ...]:
    if not isinstance(value, str) or not value.strip():
        raise SelectionError("Select at least one feature, e.g. cooling-monitoring.")
    ids = []
    for token in value.split(","):
        cid = token.strip()
        if not cid or not ID.fullmatch(cid):
            raise SelectionError(f"Invalid component ID: {cid!r}")
        if cid not in ids:
            ids.append(cid)
    return tuple(sorted(ids))


def load_catalog(path: Path = CATALOG) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("components"), list):
        raise SelectionError("Unsupported component catalog schema; expected schema 1.")
    components = {}
    for item in data["components"]:
        if not isinstance(item, dict):
            raise SelectionError("Every component must be an object.")
        cid = item.get("id")
        if not isinstance(cid, str) or not ID.fullmatch(cid) or cid in components:
            raise SelectionError(f"Duplicate or invalid component ID: {cid!r}")
        if item.get("status") not in STATUSES:
            raise SelectionError(f"Unsupported status for {cid}: {item.get('status')!r}")
        if not isinstance(item.get("label"), str) or not item["label"].strip():
            raise SelectionError(f"Missing display label for {cid}")
        if not isinstance(item.get("selectable", True), bool):
            raise SelectionError(f"Invalid selectable flag for {cid}")
        for field in ("requires", "suggests", "services", "host_requirements"):
            value = item.get(field)
            if not isinstance(value, list) or any(
                not isinstance(x, str) or not x.strip() for x in value
            ) or len(value) != len(set(value)):
                raise SelectionError(f"Invalid or duplicate {field} for {cid}")
        for field in ("requires", "suggests", "conflicts"):
            if field == "conflicts" and field not in item:
                continue
            if not isinstance(item[field], list) or any(
                not isinstance(x, str) or not ID.fullmatch(x) for x in item[field]
            ) or len(item[field]) != len(set(item[field])):
                raise SelectionError(f"Invalid {field} for {cid}")
        components[cid] = item

    for cid, item in components.items():
        for field in ("requires", "suggests", "conflicts"):
            for other in item.get(field, []):
                if other not in components:
                    raise SelectionError(f"{cid} references unknown {field}: {other}")
                if other == cid and field != "requires":
                    raise SelectionError(f"{cid} cannot {field} itself")

    # Validate *all* dependencies, even for currently unselected features.
    visiting, visited = set(), set()

    def walk(cid: str) -> None:
        if cid in visiting:
            raise SelectionError(f"Cyclic required dependency involving {cid}.")
        if cid in visited:
            return
        visiting.add(cid)
        for dep in components[cid]["requires"]:
            walk(dep)
        visiting.remove(cid)
        visited.add(cid)

    for cid in sorted(components):
        walk(cid)
    return components


def describe(components: dict[str, dict]) -> list[dict]:
    return [
        {
            "id": cid,
            "label": item["label"],
            "status": item["status"],
            "selectable": item.get("selectable", True),
            "installable_now": (
                item.get("selectable", True)
                and item["status"] == "prototype"
                and cid in SUPPORTED_FEATURES
            ),
            "requires": sorted(item["requires"]),
            "suggests": sorted(item["suggests"]),
        }
        for cid, item in sorted(components.items())
    ]


def resolve(
    components: dict[str, dict], requested: tuple[str, ...], *, design=False
) -> dict:
    if not requested:
        raise SelectionError("At least one feature is required.")
    for cid in requested:
        if cid not in components:
            raise SelectionError(f"Unknown feature: {cid}. Run 'homeaway-cooling components'.")
        if not components[cid].get("selectable", True):
            raise SelectionError(
                f"{cid} is an internal capability; select a user-facing feature instead."
            )
    requested = tuple(sorted(set(requested)))
    closure = []
    seen = set()

    def visit(cid: str) -> None:
        if cid in seen:
            return
        for dep in sorted(components[cid]["requires"]):
            visit(dep)
        seen.add(cid)
        closure.append(cid)

    for cid in requested:
        visit(cid)
    for cid in closure:
        for other in components[cid].get("conflicts", []):
            if other in seen:
                raise SelectionError(f"Incompatible features: {cid} and {other}.")

    services = sorted({name for cid in closure for name in components[cid]["services"]})
    requirements = sorted({
        requirement for cid in closure
        for requirement in components[cid]["host_requirements"]
    })
    suggested = sorted({
        cid for source in closure for cid in components[source]["suggests"]
        if cid not in seen and components[cid].get("selectable", True)
    })
    unavailable = sorted(cid for cid in closure if components[cid]["status"] != "prototype")
    expected = _expected_services(set(closure), set(requested))
    deployable = not unavailable and expected is not None and expected == frozenset(services)
    plan = {
        "schema_version": 1,
        "selected_features": list(requested),
        "component_order": closure,
        "automatically_added": [cid for cid in closure if cid not in requested],
        "services": services,
        "host_requirements": requirements,
        "suggestions_not_selected": suggested,
        "planned_components": unavailable,
        "deployable": deployable,
        "deployment": "compose.generated.json" if deployable else None,
    }
    if not deployable and not design:
        if unavailable:
            raise SelectionError(
                "Not installable yet: " + ", ".join(unavailable) +
                ". Use 'plan --design' to inspect future dependencies only."
            )
        raise SelectionError(
            "This combination is not wired to a tested installer yet. "
            "Use 'plan --design' for a read-only dependency preview."
        )
    return plan


def plan_text(plan: dict) -> str:
    lines = [
        "Selected features: " + ", ".join(plan["selected_features"]),
        "Automatically required: " + (", ".join(plan["automatically_added"]) or "(none)"),
        "Resolved order: " + " -> ".join(plan["component_order"]),
        "Unique services: " + ", ".join(plan["services"]),
        "Host requirements: " + (", ".join(plan["host_requirements"]) or "(none)"),
        "Suggestions (not installed): " + (
            ", ".join(plan["suggestions_not_selected"]) or "(none)"
        ),
        "Deployment: " + (plan["deployment"] or "DESIGN ONLY; not deployable"),
    ]
    if plan["planned_components"]:
        lines.append("Still planned: " + ", ".join(plan["planned_components"]))
    return "\n".join(lines)


def plan_json(plan: dict) -> str:
    return json.dumps(plan, indent=2, sort_keys=True)
