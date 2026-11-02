#!/usr/bin/env python3
"""Verify resolved Compose security invariants for the opt-in control overlay."""
from __future__ import annotations
import ipaddress
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(config: dict, private_ip: str):
    services = config["services"]
    assert {"gateway", "authelia", "pc-control", "portal"} <= set(services)
    gateway = services["gateway"]
    # One private host port forwards to the established internal TLS listener.
    # Legacy :8443 must no longer be LAN-facing.
    ports = gateway.get("ports", [])
    actual = {(str(p["published"]), int(p["target"]), p["protocol"], p["host_ip"])
              for p in ports}
    expected = {("443", 8443, "tcp", private_ip)}
    assert len(ports) == 1 and actual == expected
    assert ipaddress.ip_address(private_ip).is_private
    # Official Caddy's file capability needs to be in its bounding set for exec.
    # Keep only NET_BIND_SERVICE; do not relax other gateway isolation.
    assert gateway.get("cap_drop") == ["ALL"]
    assert gateway.get("cap_add") == ["NET_BIND_SERVICE"]
    assert "no-new-privileges:true" in gateway.get("security_opt", [])
    for name in ("pc-control", "authelia"):
        service = services[name]
        assert not service.get("ports"), name + " must never publish a host port"
        assert service.get("user") == "65534:65534"
        assert not service.get("privileged", False)
        assert "ALL" in service.get("cap_drop", [])
        assert "no-new-privileges:true" in service.get("security_opt", [])
        assert len(service["networks"]) == 1
        assert "pc-control" in service["networks"]
        assert not any("/var/run/docker.sock" in str(v) for v in service.get("volumes", []))
    # The diagnostics observer is the sole new bridge between metrics and
    # authenticated control. No browser route or host port reaches it directly.
    observer = services["metrics-diagnostics"]
    assert not observer.get("ports")
    assert observer.get("user") == "65534:65534"
    assert observer.get("read_only") is True
    assert observer.get("cap_drop") == ["ALL"]
    assert "no-new-privileges:true" in observer.get("security_opt", [])
    assert set(observer["networks"]) == {"infra", "pc-control"}
    assert not observer.get("privileged", False)
    assert not observer.get("volumes"), "Observer cannot read Pi host volumes"
    assert not observer.get("secrets"), "Observer cannot access credentials"
    assert services["pc-control"]["environment"]["HOMEAWAY_DIAGNOSTICS_URL"] == (
        "http://metrics-diagnostics:8095/v1/summary"
    )
    controller = services["pc-control"]
    assert controller["read_only"] is True
    assert controller.get("group_add"), "Socket group is required"
    mounts = {(v["source"], v["target"], v.get("read_only", False)) for v in controller["volumes"]}
    assert ("/run/homeaway-pcctl", "/run/homeaway-pcctl", True) in mounts
    assert ("/etc/homeaway-pcctl", "/etc/homeaway-pcctl", True) in mounts
    assert not any("/.ssh" in str(v) or "id_ed25519" in str(v) for v in controller["volumes"])
    network = config["networks"]
    assert network["pc-control"]["internal"] is True
    assert "infra" in gateway["networks"] and "pc-control" in gateway["networks"]
    assert "pc-control" not in services["portal"]["networks"]
    assert not services["portal"].get("secrets")
    assert services["gateway"]["image"] == "caddy:2.11.7-alpine"
    assert services["authelia"]["image"] == "authelia/authelia:4.39.28"
    print("PASS: isolated, unprivileged PC control stack and private HTTPS port")


def main():
    env = {**os.environ, "HOMEAWAY_DOMAIN": "homeaway.home.arpa",
           "HOMEAWAY_AUTH_DOMAIN": "auth.homeaway.home.arpa",
           "HOMEAWAY_METRICS_DOMAIN": "metrics.homeaway.home.arpa",
           "PCCTL_GID": "999"}
    cfg = subprocess.run(["docker", "compose", "-f", "compose.yaml", "-f", "compose.control.yaml",
                          "--profile", "control", "--profile", "metrics",
                          "config", "--format", "json"], cwd=ROOT, env=env,
                         check=True, capture_output=True, text=True)
    config = json.loads(cfg.stdout)
    ip = env.get("PI_LAN_IP", "192.168.1.10")
    # Compose interpolates PI_LAN_IP from the CI .env file; use resolved port.
    published = config["services"]["gateway"]["ports"][0]
    check(config, published["host_ip"])


if __name__ == "__main__":
    main()
