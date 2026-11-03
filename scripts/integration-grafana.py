#!/usr/bin/env python3
"""Prove Grafana itself reads the datasource and dashboards from the mounted files.

The CI-only Grafana runs on a GitHub Actions runner, not on a user's Pi.
"""
import base64
import json
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:13000"
EXPECTED = {"homeaway-overview", "homeaway-sensordash", "homeaway-cooling", "homeaway-electrical", "homeaway-dns-vpn", "homeaway-storage", "homeaway-events", "homeaway-system", "homeaway-portal"}
AUTH = "Basic " + base64.b64encode(b"admin:ci-only-grafana-password").decode("ascii")


def read(path: str, authorized: bool = True):
    headers = {"Authorization": AUTH} if authorized else {}
    request = urllib.request.Request(BASE + path, headers=headers)
    with urllib.request.urlopen(request, timeout=4) as response:
        return json.load(response)


def main() -> None:
    for attempt in range(90):
        try:
            response = read("/api/health", authorized=False)
            if response.get("database") == "ok":
                break
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            pass
        time.sleep(1)
    else:
        raise AssertionError("Grafana did not become healthy within the CI limit")

    for attempt in range(35):
        search = read("/api/search?type=dash-db")
        dashboards = {d.get("uid") for d in search if d.get("type") == "dash-db"}
        if EXPECTED <= dashboards:
            break
        time.sleep(1)
    else:
        raise AssertionError("Missing provisioned dashboards: " + repr(EXPECTED - dashboards))

    for uid in sorted(EXPECTED):
        document = read("/api/dashboards/uid/" + uid)
        dashboard = document["dashboard"]
        assert dashboard["uid"] == uid, uid
        assert dashboard["panels"], uid
        assert dashboard["editable"] is False, uid
        if uid == "homeaway-sensordash":
            variables = {v["name"]: v for v in dashboard["templating"]["list"]}
            panels = {p["id"]: p for p in dashboard["panels"]}
            for panel_id, name in (
                (111, "hardwareLatestTempSource"),
                (112, "hardwarePeakTempSource"),
            ):
                assert name in panels[panel_id]["title"], panels[panel_id]
                assert "${" + name + "}" in panels[panel_id]["title"], panels[panel_id]
                assert "subtitle" not in panels[panel_id], panels[panel_id]
                assert panels[panel_id]["options"]["textMode"] == "value", panels[panel_id]
                assert variables[name]["hide"] == 2, variables[name]
                assert variables[name]["refresh"] == 2, variables[name]
                assert variables[name]["query"]["query"].startswith("query_result("), variables[name]
        if uid == "homeaway-overview":
            variables = {v["name"]: v for v in dashboard["templating"]["list"]}
            panels = {p["id"]: p for p in dashboard["panels"]}
            for id, variable in ((5, "peakCpuSource"), (6, "peakGpuSource")):
                assert "${" + variable + "}" in panels[id]["title"], panels[id]
                assert "subtitle" not in panels[id], panels[id]
                assert panels[id]["options"]["textMode"] == "value", panels[id]
                assert panels[id]["options"]["graphMode"] == "area", panels[id]
                assert variables[variable]["hide"] == 2, variables[variable]
                assert variables[variable]["refresh"] == 2, variables[variable]
                assert "query_result(" in variables[variable]["query"]["query"]

    print("PASS: Grafana boot, authentication and all nine provisioned dashboards")


if __name__ == "__main__":
    main()
