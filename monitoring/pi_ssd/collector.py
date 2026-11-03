#!/usr/bin/env python3
"""Host-only, read-only SMART, filesystem, and kernel event metric collector.

Only numeric metrics are exported. No serial numbers, disk paths, or logs
leave the host; node-exporter receives an atomic read-only textfile.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

OUTPUT = Path("/var/lib/homeaway-ssd-metrics/pi-ssd.prom")
EVENTS = {
    "io_errors": re.compile(r"(?:I/O error|device offline error), dev sd[a-z]\b", re.I),
    "uas_aborts": re.compile(r"uas_eh_abort_handler|uas_eh_device_reset_handler start", re.I),
    "usb_resets": re.compile(r"reset SuperSpeed USB device", re.I),
    "host_controller_failures": re.compile(r"HC died|xHCI host controller not responding", re.I),
}
ATTRIBUTES = {
    5: "retired_blocks",
    171: "program_failures",
    172: "erase_failures",
    174: "unexpected_power_losses",
    187: "uncorrectable_errors",
    194: "temperature_celsius",
    196: "reallocation_events",
    241: "lifetime_writes_gib",
    242: "lifetime_reads_gib",
}

def command(*args, timeout=20):
    return subprocess.run(args, capture_output=True, text=True,
                          timeout=timeout, check=False)

def disk_for_root():
    result = command("findmnt", "-n", "-o", "SOURCE", "--target", "/")
    if result.returncode or not result.stdout.strip():
        raise RuntimeError("cannot resolve root source")
    root = os.path.realpath(result.stdout.strip())
    if not root.startswith("/dev/"):
        raise RuntimeError("root filesystem is not a block device")
    result = command("lsblk", "-n", "-o", "PKNAME", root)
    parent = result.stdout.strip()
    if result.returncode or not re.fullmatch(r"sd[a-z]+", parent):
        raise RuntimeError("root is not on a USB SATA disk")
    return "/dev/" + parent

def raw_number(row):
    raw = row.get("raw", {})
    n = raw.get("value") if isinstance(raw, dict) else None
    return n if isinstance(n, int) and n >= 0 else None

def parse_smart(data):
    status = data.get("smartctl", {}).get("exit_status", 0)
    if not isinstance(status, int) or status & 0b11:
        raise ValueError("smartctl could not query device")
    passed = data.get("smart_status", {}).get("passed")
    if not isinstance(passed, bool):
        raise ValueError("SMART overall status unavailable")
    result = {"homeaway_pi_ssd_smart_passed": float(passed)}
    rows = {row["id"]: row for row in data.get("ata_smart_attributes", {}).get("table", [])
            if isinstance(row, dict) and isinstance(row.get("id"), int)}
    for attr, name in ATTRIBUTES.items():
        if attr in rows:
            n = raw_number(rows[attr])
            if n is not None:
                result["homeaway_pi_ssd_" + name] = float(n)
    if 231 in rows:
        normalized = rows[231].get("value")
        if isinstance(normalized, int) and 0 <= normalized <= 253:
            # SandForce normalized indicator, NOT verified remaining-life percent.
            result["homeaway_pi_ssd_life_normalized"] = float(normalized)
    if 9 in rows:
        raw = rows[9].get("raw", {})
        s = raw.get("string", "") if isinstance(raw, dict) else ""
        hours = re.match(r"^\s*(\d+)h", s)
        n = int(hours.group(1)) if hours else raw_number(rows[9])
        if n is not None:
            result["homeaway_pi_ssd_power_on_hours"] = float(n)
    return result

def filesystem_metrics():
    st = os.statvfs("/")
    return {
        "homeaway_pi_root_filesystem_size_bytes": float(st.f_blocks * st.f_frsize),
        "homeaway_pi_root_filesystem_available_bytes": float(st.f_bavail * st.f_frsize),
        "homeaway_pi_root_filesystem_used_bytes": float(
            (st.f_blocks - st.f_bfree) * st.f_frsize),
    }

def kernel_metrics():
    result = command("journalctl", "-k", "-b", "--no-pager", "-o", "cat", timeout=30)
    if result.returncode:
        raise RuntimeError("kernel journal unavailable")
    # Since-boot gauges reset on reboot. Do not apply rate() to them.
    return {
        'homeaway_pi_ssd_kernel_events_since_boot{kind="' + name + '"}':
            float(len(pattern.findall(result.stdout)))
        for name, pattern in EVENTS.items()
    }

def render(metrics):
    lines = []
    for name, value in sorted(metrics.items()):
        if not isinstance(value, (int, float)) or not float("-inf") < value < float("inf"):
            raise ValueError("non-finite metric")
        lines.append(f"{name} {value:g}")
    return "\n".join(lines) + "\n"

def publish(path, body):
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise RuntimeError("metrics directory missing/unsafe")
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="ascii", dir=path.parent,
            prefix=".homeaway-ssd-", delete=False
        ) as stream:
            tmp = stream.name
            os.fchmod(stream.fileno(), 0o644)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)

def collect():
    metrics = {
        "homeaway_pi_ssd_collection_success": 0.0,
        "homeaway_pi_ssd_kernel_collection_success": 0.0,
        "homeaway_pi_ssd_last_attempt_timestamp_seconds": float(int(time.time())),
    }
    try:
        metrics.update(filesystem_metrics())
    except OSError:
        pass
    try:
        disk = disk_for_root()
        # Raw smartctl JSON is never logged or exported.
        res = command("smartctl", "-j", "-q", "noserial",
                      "-d", "sat", "-i", "-H", "-A", disk, timeout=25)
        metrics.update(parse_smart(json.loads(res.stdout)))
        metrics["homeaway_pi_ssd_collection_success"] = 1.0
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired):
        # No stale SMART numbers on errors. The explicit status becomes 0.
        pass
    try:
        metrics.update(kernel_metrics())
        metrics["homeaway_pi_ssd_kernel_collection_success"] = 1.0
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        pass
    return metrics

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    metrics = collect()
    publish(args.output, render(metrics))
    if metrics["homeaway_pi_ssd_collection_success"] != 1:
        print("SMART collection unavailable; exported collection_success=0", file=sys.stderr)
        return 1
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
