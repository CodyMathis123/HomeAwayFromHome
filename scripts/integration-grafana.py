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
EXPECTED = {"homeaway-sensordash", "homeaway-cooling", "homeaway-electrical"}
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
        except (urllib.error.URLError, TimeoutError, ValueError):
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
    print("PASS: Grafana boot, authentication and all three provisioned dashboards")


if __name__ == "__main__":
    main()
