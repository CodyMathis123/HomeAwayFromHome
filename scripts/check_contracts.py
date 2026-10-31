#!/usr/bin/env python3
"""Validate resolved Compose's public surface, not just text fragments."""
from __future__ import annotations

import ipaddress
import json
import subprocess
from pathlib import Path

from validate_config import parse_env


def check(config: dict, ip: str) -> None:
    services = config["services"]
    assert {"pihole", "wg-easy", "portal", "victoria-metrics", "grafana", "telemetry-ingest"} <= set(services)

    def published(service: str) -> list[dict]:
        return services[service].get("ports", [])

    def exactly(service: str, *, port: int, internal: int, proto: str,
                ip_address: str) -> bool:
        return any(
            p.get("published") == str(port) and p.get("target") == internal
            and p.get("protocol") == proto and p.get("host_ip") == ip_address
            for p in published(service)
        )

    for port, proto in [(53, "tcp"), (53, "udp"), (8080, "tcp")]:
        dest = 80 if port == 8080 else 53
        assert exactly("pihole", port=port, internal=dest, proto=proto, ip_address=ip)

    assert exactly("wg-easy", port=51821, internal=51821,
                   proto="tcp", ip_address="127.0.0.1")
    assert exactly("wg-easy", port=51820, internal=51820,
                   proto="udp", ip_address="") or any(
        p.get("published") == "51820" and p.get("target") == 51820
        and p.get("protocol") == "udp" and p.get("host_ip", "") in ("", "0.0.0.0")
        for p in published("wg-easy")
    )
    assert len(published("wg-easy")) == 2
    assert exactly("portal", port=8088, internal=8080, proto="tcp", ip_address=ip)
    assert exactly("grafana", port=3000, internal=3000, proto="tcp", ip_address=ip)
    assert exactly("telemetry-ingest", port=8429, internal=8443, proto="tcp", ip_address=ip)
    assert not published("victoria-metrics"), "Never publish VictoriaMetrics directly"
    for name in ("grafana", "telemetry-ingest", "victoria-metrics"):
        assert "metrics" in services[name].get("profiles", []), name
    assert len(published("telemetry-ingest")) == 1
    assert services["telemetry-ingest"].get("read_only") is True
    assert services["telemetry-ingest"].get("user") in ("65534:65534", None)
    assert "ALL" in services["telemetry-ingest"].get("cap_drop", [])
    assert "no-new-privileges:true" in services["telemetry-ingest"].get("security_opt", [])
    assert services["portal"].get("read_only") is True
    assert "ALL" in services["portal"].get("cap_drop", [])
    assert services["portal"].get("privileged", False) is False
    assert services["wg-easy"].get("environment", {}).get("INSECURE") == "true"
    assert services["wg-easy"].get("privileged", False) is False
    # The Pi 4 / kernel 6.18 live deployment needs nftables userspace in
    # wg-easy's own container; host iptables alternatives are insufficient.
    assert services["wg-easy"]["image"] == "homeaway-wg-easy-nft:15.5.0"
    assert services["wg-easy"]["build"]["dockerfile"] == "Dockerfile.wg-easy-nft"
    dockerfile = Path("Dockerfile.wg-easy-nft").read_text(encoding="utf-8")
    assert "FROM ghcr.io/wg-easy/wg-easy:15.5.0" in dockerfile
    assert "iptables-nft" in dockerfile and "ip6tables-nft" in dockerfile

    for name, service in services.items():
        assert not service.get("privileged", False), name
        assert not any("/var/run/docker.sock" in str(volume)
                       for volume in service.get("volumes", [])), name
        image = service.get("image")
        if image:
            assert ":" in image and not image.endswith(":latest"), name

    assert services["pihole"]["environment"].get("WEBPASSWORD_FILE") == "pihole_password", (
        "Pi-hole v6 expects Docker's secret name; it constructs /run/secrets/<name> itself"
    )
    assert len(services["pihole"].get("secrets", [])) == 1
    assert not services["portal"].get("secrets")
    assert len(services["grafana"].get("secrets", [])) == 1
    assert services["grafana"]["environment"]["GF_AUTH_ANONYMOUS_ENABLED"] == "false"
    assert ipaddress.ip_address(ip).is_private
    print("PASS: Compose ports, privilege, secrets and admin isolation")


def main() -> None:
    env = parse_env(Path(".env"))
    completed = subprocess.run(
        ["docker", "compose", "--profile", "metrics", "config", "--format", "json"],
        check=True, capture_output=True, text=True,
    )
    check(json.loads(completed.stdout), env["PI_LAN_IP"])


if __name__ == "__main__":
    main()
