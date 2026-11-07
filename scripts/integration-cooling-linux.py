#!/usr/bin/env python3
"""Real Linux-side cooling backend smoke test (no SensorDash Windows needed).

Must run on an isolated CI runner with a freshly installed cooling package.
Sends syntactically valid synthetic SensorDash telemetry over verified HTTPS.
Does not expose, rotate or persist any user credentials.
"""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, build_opener, HTTPSHandler, ProxyHandler, urlopen

HOST = "ci-synthetic-cooling"
METRIC = f'sensordash_reading_value{{host="{HOST}"}}'


def docker(root: Path, env: Path, *command: str) -> list[str]:
    state = Path(next(
        line.split("=", 1)[1] for line in env.read_text().splitlines()
        if line.startswith("COOLING_STATE_DIR=")
    ))
    return ["docker", "compose", "--env-file", str(env),
            "--project-directory", str(root),
            "-f", str(state / "generated/compose.generated.json"), *command]


def request(url: str, ca: Path, *, method="GET", body=None, token=None, basic=None):
    headers = {}
    if body is not None:
        headers["Content-Type"] = "text/plain; charset=utf-8"
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    if basic is not None:
        credentials = base64.b64encode(("admin:" + basic).encode()).decode()
        headers["Authorization"] = "Basic " + credentials
    req = Request(url, data=body, method=method, headers=headers)
    context = ssl.create_default_context(cafile=str(ca))
    # The target is a local loopback test listener. Never route this
    # internal integration request through runner-provided HTTP(S) proxies.
    opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context))
    try:
        with opener.open(req, timeout=8) as response:
            return response.status, response.read()
    except HTTPError as ex:
        return ex.code, ex.read(8192)


def query_metrics(root: Path, env: Path) -> dict:
    code = (
        "import json,urllib.request;"
        f"u='http://victoria-metrics:8428/api/v1/query?query={quote(METRIC, safe='')}';"
        "print(urllib.request.urlopen(u,timeout=8).read().decode())"
    )
    response = subprocess.run(
        docker(root, env, "exec", "-T", "telemetry-ingest", "python", "-c", code),
        check=True, capture_output=True, text=True, timeout=18, cwd=root,
    )
    result = json.loads(response.stdout)
    assert result["status"] == "success", result
    return result


def gateway_root(root: Path, env: Path, destination: Path) -> None:
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        result = subprocess.run(
            docker(root, env, "ps", "-q", "gateway"),
            check=False, capture_output=True, text=True, cwd=root,
        )
        cid = result.stdout.strip()
        if cid:
            res = subprocess.run(
                ["docker", "cp", f"{cid}:/data/caddy/pki/authorities/local/root.crt",
                 str(destination)],
                capture_output=True, text=True, check=False, timeout=10,
            )
            if res.returncode == 0 and destination.is_file():
                return
        time.sleep(2)
    raise AssertionError("Caddy public root CA was not generated within 100 seconds.")


def wait_for_service(url: str, ca: Path, basic: str) -> None:
    deadline = time.monotonic() + 100
    last = "not responding"
    while time.monotonic() < deadline:
        try:
            status, _ = request(url + "/api/org", ca, basic=basic)
            if status == 200:
                return
            last = f"Grafana authenticated API returned HTTP {status}"
        except (OSError, URLError) as exc:
            last = f"{type(exc).__name__}: {getattr(exc, 'reason', str(exc))}"
        time.sleep(2)
    raise AssertionError(f"HTTPS Grafana never became ready: {last}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path, default=Path("/opt/homeaway-cooling"))
    ap.add_argument("--env", type=Path, default=Path("/etc/homeaway-cooling/cooling.env"))
    ap.add_argument("--state", type=Path, default=Path("/var/lib/homeaway-cooling"))
    ap.add_argument("--check-existing", action="store_true",
                    help="Verify previously ingested data survived a down/up restart.")
    args = ap.parse_args()
    root, env, state = args.root.resolve(), args.env.resolve(), args.state.resolve()
    conf = dict(
        line.split("=", 1) for line in env.read_text(encoding="ascii").splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    host = conf["COOLING_TLS_NAME"]
    port = int(conf["COOLING_HTTPS_PORT"])
    url = f"https://{host}:{port}"
    secret = state / "secrets"
    selected = set(conf.get("COOLING_COMPONENTS", "cooling-monitoring").split(","))
    cooling = "cooling-monitoring" in selected
    linux_host = "linux-host-monitoring" in selected
    portal = "home-portal" in selected
    diagnostics = "monitoring-diagnostics" in selected or portal
    token = (secret / "telemetry/token").read_text().strip() if cooling else None
    password = (secret / "grafana_admin_password").read_text().strip()
    assert conf["COOLING_STATE_DIR"] == str(state)
    with tempfile.TemporaryDirectory(prefix="cooling-https-check-") as folder:
        ca = Path(folder) / "root.crt"
        gateway_root(root, env, ca)
        wait_for_service(url, ca, password)
        anonymous, _ = request(url + "/api/org", ca)
        assert anonymous in (401, 403), f"Grafana org endpoint unauthenticated: {anonymous}"
        print("PASS: trusted TLS connects, Grafana requires independent authentication.")

        if portal:
            anonymous_portal, _ = request(url + "/portal/", ca)
            assert anonymous_portal == 401, (
                f"Portal must reject requests without credentials: {anonymous_portal}"
            )
            password_file = secret / "portal_password"
            portal_password = password_file.read_text().strip()
            page, content = request(url + "/portal/", ca, basic=portal_password)
            assert page == 200 and b"Your monitoring" in content, (
                f"Authenticated component-aware portal failed: {page}"
            )
            code, payload = request(
                url + "/portal/api/features", ca, basic=portal_password
            )
            assert code == 200
            items = json.loads(payload)["selectedFeatures"]
            names = {item["id"] for item in items}
            assert selected <= names, (selected, names)
            assert not names & {"dns-filtering", "vpn", "computer-management"}
            code, payload = request(
                url + "/portal/api/diagnostics", ca, basic=portal_password
            )
            assert code == 200, f"Portal diagnostics unavailable: HTTP {code}"
            diag_summary = json.loads(payload)
            assert diag_summary.get("schemaVersion") == 1
            assert diag_summary.get("checks") or diag_summary.get("serviceChecks")
            print("PASS: separate portal login, selected-only features and diagnostics API.")
        else:
            absent, _ = request(url + "/portal/", ca)
            assert absent == 404, f"Unselected portal route was exposed: HTTP {absent}"
            assert not (secret / "portal_password").exists()
            print("PASS: unselected portal has no public HTTPS route or credentials.")

        if cooling:
            bad_auth, _ = request(
                url + "/ingest", ca, method="POST", body=b"invalid metric\n"
            )
            assert bad_auth == 401, f"Anonymous ingest was not denied: {bad_auth}"
            malformed, _ = request(
                url + "/ingest", ca, method="POST", body=b"invalid metric\n", token=token
            )
            assert malformed == 422, f"Authenticated malformed body was not denied: {malformed}"
            print("PASS: gateway routes require bearer token and enforce metric schema.")
        else:
            missing, _ = request(url + "/ingest", ca, method="POST", body=b"invalid\n")
            assert missing == 404, f"Unselected ingestion is exposed: HTTP {missing}"
            assert not (secret / "telemetry").exists()
            print("PASS: host-only install has no ingestion endpoint or collector credentials.")

        if cooling and not args.check_existing:
            sample = (
                f"sensordash_reading,host={HOST},"
                "sensor=/amdcpu/0/temperature/0,kind=temperature"
                f" value=46.25 {int(time.time() * 1000)}\n"
            ).encode()
            accepted, _ = request(url + "/ingest", ca, method="POST", body=sample,
                                  token=token)
            assert accepted == 204, f"Valid synthetic SensorDash sample was not accepted: {accepted}"
            print("PASS: synthetic SensorDash-format event accepted over verified HTTPS.")

        if cooling:
            deadline = time.monotonic() + 60
            series = []
            while time.monotonic() < deadline:
                res = query_metrics(root, env)
                series = res["data"]["result"]
                if series:
                    break
                time.sleep(2)
            assert series, "Sample not queryable from internal VictoriaMetrics"
            print("PASS: synthetic sample queryable from internal VictoriaMetrics.")

        # Every selected feature must be queryable through the authenticated
        # Grafana datasource, with shared VictoriaMetrics remaining private.
        queries = ([METRIC] if cooling else [])
        if linux_host:
            queries.extend(['up{job="linux-host"}', 'node_memory_MemTotal_bytes{job="linux-host"}'])
        for metric in queries:
            deadline = time.monotonic() + 75
            result = {}
            while time.monotonic() < deadline:
                query = quote(metric, safe="")
                grafana_url = url + "/api/datasources/proxy/uid/homeaway-vm/api/v1/query?query=" + query
                stat, payload = request(grafana_url, ca, basic=password)
                if stat == 200:
                    result = json.loads(payload)
                    matches = result.get("data", {}).get("result", [])
                    if matches and (metric != 'up{job="linux-host"}' or
                                    all(float(x["value"][1]) == 1 for x in matches)):
                        break
                time.sleep(2)
            assert stat == 200 and result.get("data", {}).get("result"), (
                f"Grafana datasource did not return selected metric {metric}: HTTP {stat}"
            )
        print("PASS: every selected monitoring feature is queryable through Grafana.")
        if diagnostics:
            # Fixed diagnostics observer is reachable only on the private
            # Compose network. It must not probe absent household services.
            inspector = (
                "import json,urllib.request;"
                "u='http://metrics-diagnostics:8095/v1/summary';"
                "print(urllib.request.urlopen(u,timeout=6).read().decode())"
            )
            result = subprocess.run(
                docker(root, env, "exec", "-T", "metrics-diagnostics",
                       "python", "-B", "-c", inspector),
                cwd=root, check=True, capture_output=True, text=True, timeout=15,
            )
            evidence = json.loads(result.stdout)
            checks = (evidence.get("checks", []) +
                      evidence.get("serviceChecks", []))
            names = {item["id"] for item in checks}
            assert "service.victoria" in names
            if linux_host:
                assert "service.linux-host" in names
            else:
                assert "service.linux-host" not in names
            assert not names & {"service.pihole", "service.wireguard", "pi.cpu"}
            if cooling:
                assert evidence.get("telemetry") is not None
            print("PASS: internal diagnostics probes include selected monitoring only.")
        else:
            assert not (secret / "portal_password").exists()
            print("PASS: no diagnostics observer in minimal monitoring installations.")

        dashboard_status, raw = request(
            url + "/api/search?type=dash-db", ca, basic=password
        )
        assert dashboard_status == 200, f"Grafana dashboard search HTTP {dashboard_status}"
        dashboards = json.loads(raw)
        titles = [item.get("title", "") for item in dashboards]
        if cooling:
            assert any("SensorDash" in title for title in titles), (
                f"SensorDash dashboard was not provisioned; returned {titles}"
            )
        else:
            assert not any("SensorDash" in title for title in titles), (
                f"Unselected SensorDash dashboard was provisioned: {titles}"
            )
        if linux_host:
            assert "Linux Host Monitoring" in titles, (
                f"Linux host dashboard was not provisioned: {titles}"
            )
        else:
            assert "Linux Host Monitoring" not in titles
        print("PASS: Grafana provisioned only selected feature dashboards.")
        if args.check_existing:
            print("PASS: cooling data persisted across container stop/start.")

    ps = subprocess.run(
        ["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=homeaway-cooling",
         "--format", "{{.Names}}"],
        check=True, capture_output=True, text=True,
    )
    expected = 3 + int(cooling) + int(linux_host) + int(diagnostics) + int(portal)
    assert len(ps.stdout.splitlines()) == expected, (
        f"Expected {expected} component-selected containers, got {ps.stdout}"
    )
    assert all(not x.startswith(("homeawayfromhome-pihole", "homeawayfromhome-wg"))
               for x in ps.stdout.splitlines())
    print(f"PASS: {expected} isolated monitoring containers (no Pi-hole or WireGuard).")


if __name__ == "__main__":
    main()
