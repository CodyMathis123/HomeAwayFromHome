#!/usr/bin/env python3
"""Validate fixed metrics-observer expressions against the ephemeral CI VM.

Run after integration-victoriametrics.py injected a synthetic SensorDash host.
Never connects to a user's Pi or external VictoriaMetrics instance.
"""
import importlib.util
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "ci_metrics_observer", ROOT / "monitoring/diagnostics/app.py")
observer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(observer)
observer.VM_QUERY = "http://127.0.0.1:18428/api/v1/query"


def main():
    probe_map = {item[0]: item for item in observer.PROBES}
    values = observer.read_vector(probe_map["telemetry.last"][2])
    hosts = observer.telemetry_hosts(values, time.time())
    assert any(item["host"] == "CI" and item["ageSeconds"] < 600
               for item in hosts), "Synthetic CI samples not found by 24h freshness query"
    disk = observer.read_vector(probe_map["vm.free"][2])
    check = observer.summarized_probe("vm.free", "VM data storage", "bytes",
                                       disk, observer.now_iso())
    assert check["state"] == "observed" and check["value"] > 0, check
    snap = observer.snapshot()
    assert set(item["id"] for item in snap["checks"]) == {
        "pi.scrape", "pi.cpu", "pi.memory", "pi.load", "pi.thermal", "vm.free"
    }
    assert any(item["host"] == "CI" for item in snap["telemetry"]["hosts"]), snap
    print("PASS: real VictoriaMetrics 24h last-sample query and fixed probe contract")


if __name__ == "__main__":
    main()
