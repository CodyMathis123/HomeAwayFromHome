#!/usr/bin/env python3
"""Fail closed if the opt-in DNS-01 overlay changes the existing gateway boundary.

Reads docker compose --format json on stdin, without secrets or live services.
"""
import ipaddress
import json
import sys


def check(config):
    services = config["services"]
    gateway = services["gateway"]
    assert len(gateway.get("ports", [])) == 1, "Do not add ports"
    p = gateway["ports"][0]
    assert p["published"] == "8443" and p["target"] == 8443
    assert p["protocol"] == "tcp" and ipaddress.ip_address(p["host_ip"]).is_private
    assert set(gateway["networks"]) == {"infra", "pc-control"}
    assert gateway.get("cap_drop") == ["ALL"]
    assert gateway.get("cap_add") == ["NET_BIND_SERVICE"]
    assert "no-new-privileges:true" in gateway.get("security_opt", [])
    assert gateway.get("image") == "homeaway-caddy-dns01:2.11.7-cloudflare-v0.2.4"
    assert gateway.get("entrypoint") == ["/usr/local/bin/start-dns01"]
    assert gateway.get("secrets") and {
        x["source"] for x in gateway["secrets"]
    } == {"cloudflare_dns01_token"}
    assert not gateway.get("privileged")
    assert not any("/var/run/docker.sock" in str(x) for x in gateway.get("volumes", []))
    mounts = {x["target"]: x for x in gateway.get("volumes", [])}
    assert mounts["/etc/caddy/Caddyfile"]["read_only"]
    assert mounts["/etc/caddy/Caddyfile.legacy"]["read_only"]
    assert mounts["/etc/caddy/Caddyfile"]["source"].endswith(
        "/control/gateway/Caddyfile.dns01-staging")
    assert mounts["/etc/caddy/Caddyfile.legacy"]["source"].endswith(
        "/control/gateway/Caddyfile")
    assert "/data" in mounts and "/config" in mounts, "Preserve original Caddy TLS state"

    controller = services["pc-control"]
    assert set(controller["networks"]) == {"pc-control"}
    assert not controller.get("ports")
    assert "cloudflare_dns01_token" not in str(controller)
    portal = services["portal"]
    assert "pc-control" not in portal["networks"]
    assert "cloudflare_dns01_token" not in str(portal)
    for name in ("pihole", "wg-easy", "victoria-metrics", "telemetry-ingest"):
        assert "cloudflare_dns01_token" not in str(services[name])
    assert not any("cloudflare_dns01_token" in str(x)
                   for x in services["authelia"].get("secrets", []))
    print("PASS: DNS-01 staging changes only pinned gateway binary/config and Caddy-only secret")


if __name__ == "__main__":
    check(json.load(sys.stdin))
