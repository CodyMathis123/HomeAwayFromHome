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
    return ["docker", "compose", "--env-file", str(env),
            "-f", str(root / "compose.cooling-only.yaml"), *command]


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
    token = (secret / "telemetry/token").read_text().strip()
    password = (secret / "grafana_admin_password").read_text().strip()
    assert conf["COOLING_STATE_DIR"] == str(state)
    with tempfile.TemporaryDirectory(prefix="cooling-https-check-") as folder:
        ca = Path(folder) / "root.crt"
        gateway_root(root, env, ca)
        wait_for_service(url, ca, password)
        anonymous, _ = request(url + "/api/org", ca)
        assert anonymous in (401, 403), f"Grafana org endpoint unauthenticated: {anonymous}"
        print("PASS: trusted TLS connects, Grafana requires independent authentication.")

        bad_auth, _ = request(
            url + "/ingest", ca, method="POST", body=b"invalid metric\n"
        )
        assert bad_auth == 401, f"Anonymous ingest was not denied: {bad_auth}"
        malformed, _ = request(
            url + "/ingest", ca, method="POST", body=b"invalid metric\n", token=token
        )
        assert malformed == 422, f"Authenticated malformed body was not denied: {malformed}"
        print("PASS: gateway routes require bearer token and enforce metric schema.")

        if not args.check_existing:
            sample = (
                f"sensordash_reading,host={HOST},"
                "sensor=/amdcpu/0/temperature/0,kind=temperature"
                f" value=46.25 {int(time.time() * 1000)}\n"
            ).encode()
            accepted, _ = request(url + "/ingest", ca, method="POST", body=sample,
                                  token=token)
            assert accepted == 204, f"Valid synthetic SensorDash sample was not accepted: {accepted}"
            print("PASS: synthetic SensorDash-format event accepted over verified HTTPS.")

        deadline = time.monotonic() + 60
        series = []
        while time.monotonic() < deadline:
            res = query_metrics(root, env)
            series = res["data"]["result"]
            if series:
                break
            time.sleep(2)
        assert series, "Sample not queryable from internal VictoriaMetrics"
        print("PASS: sample found by label in the internal VictoriaMetrics query API.")

        # Verify the real authenticated Grafana datasource query route.
        query = quote(METRIC, safe="")
        grafana_url = url + "/api/datasources/proxy/uid/homeaway-vm/api/v1/query?query=" + query
        stat, payload = request(grafana_url, ca, basic=password)
        assert stat == 200, f"Grafana datasource query failed: HTTP {stat}"
        result = json.loads(payload)
        assert result.get("status") == "success" and result["data"]["result"], (
            "Grafana datasource proxy could not read synthetic telemetry"
        )
        print("PASS: Grafana queried the real datasource and returned the synthetic reading.")

        dashboard_status, raw = request(
            url + "/api/search?type=dash-db", ca, basic=password
        )
        assert dashboard_status == 200, f"Grafana dashboard search HTTP {dashboard_status}"
        dashboards = json.loads(raw)
        titles = [item.get("title", "") for item in dashboards]
        assert any("SensorDash" in title for title in titles), (
            f"SensorDash dashboard was not provisioned; returned {titles}"
        )
        print("PASS: Grafana provisioned the SensorDash dashboard.")
        if args.check_existing:
            print("PASS: cooling data persisted across container stop/start.")

    ps = subprocess.run(
        ["docker", "ps", "-a", "--filter", "label=com.docker.compose.project=homeaway-cooling",
         "--format", "{{.Names}}"],
        check=True, capture_output=True, text=True,
    )
    assert len(ps.stdout.splitlines()) == 4, f"Unexpected cooling containers: {ps.stdout}"
    assert all(not x.startswith(("homeawayfromhome-pihole", "homeawayfromhome-wg"))
               for x in ps.stdout.splitlines())
    print("PASS: only four isolated cooling containers exist (no Pi-hole or WireGuard).")


if __name__ == "__main__":
    main()
