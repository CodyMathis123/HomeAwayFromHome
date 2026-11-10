#!/usr/bin/env python3
"""Read-only inventory of Grafana Stat card provenance and presentation.

Run from any working directory. Scans *every* provisioned dashboard JSON in
this repository, not a hard-coded subset. Does not contact Grafana or modify
the files. Aggregate/single-source cards are informational, not forced to
display a source sensor.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS = ROOT / "grafana" / "dashboards"

# These cards intentionally show a changing across-sensor maximum as a
# sparkline. Pinning a single name to the card would falsely imply one sensor
# produced every historical sample. Keep the aggregate until a separate,
# time-aware source attribution design can preserve that history.
AGGREGATE_SPARKLINES = {
    ("overview.json", 1), ("overview.json", 2),
    ("overview.json", 3), ("overview.json", 4),
    ("overview.json", 7), ("overview.json", 8),
    ("overview.json", 10), ("overview.json", 11),
    ("portal-metrics.json", 1), ("portal-metrics.json", 2),
    ("portal-metrics.json", 3), ("portal-metrics.json", 4),
}


def inspect(root: Path = DASHBOARDS):
    findings = []
    for file in sorted(root.rglob("*.json")):
        obj = json.loads(file.read_text(encoding="utf-8"))
        for panel in obj.get("panels", []):
            if panel.get("type") != "stat":
                continue
            expressions = " ".join(t.get("expr", "") for t in panel.get("targets", []))
            options = panel.get("options", {})
            label = " ".join(t.get("legendFormat", "") for t in panel.get("targets", []))
            derived = "sensordash_reading_value" in expressions
            maximum = "topk(1," in expressions or "max(" in expressions
            key = (file.name, panel.get("id"))
            named = options.get("textMode") == "value_and_name" and "{{" in label
            if not derived or not maximum:
                category = "aggregate / other"
            elif "timestamp(" in expressions or "tlast_over_time(" in expressions:
                category = "sample freshness / no sensor attribution needed"
            elif " offset $__range" in expressions and " - " in expressions:
                category = "period comparison / no single winner"
            elif (file.name == "portal-metrics.json" and
                  'device="high_flow_NEXT"' in expressions and
                  ('name="Water_Temperature"' in expressions or
                   'name="Flow"' in expressions) and
                  'sensordash_sensor_info_value' in expressions):
                # Fixed physical NEXT measurement: only the host aggregation
                # can change. The source is explicit in the device/name match.
                category = "fixed NEXT sensor / host summary"
            elif key in AGGREGATE_SPARKLINES:
                category = "changing maximum sparkline / intentionally aggregated"
            elif named:
                centered = (options.get("orientation") == "vertical"
                            and options.get("wideLayout") is False
                            and options.get("justifyMode") == "center")
                normalized = 'label_transform(' in expressions
                category = ("attributed / standardized" if centered and normalized
                            else "attributed / review layout")
            else:
                category = "maximum / source not shown"
            findings.append((str(file.relative_to(ROOT)), panel.get("id"),
                             panel.get("title", ""), category))
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-all", action="store_true",
                        help="Include aggregate and other Stat panels")
    args = parser.parse_args()
    findings = inspect()
    for file, panel_id, title, category in findings:
        if args.show_all or category != "aggregate / other":
            print(f"{file} | {panel_id} | {title} | {category}")
    review = sum(row[3] in ("attributed / review layout",
                            "maximum / source not shown") for row in findings)
    print(f"Audited {len(findings)} Stat cards across "
          f"{len(set(row[0] for row in findings))} dashboards; "
          f"{review} candidates for design review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
