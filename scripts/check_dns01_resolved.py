#!/usr/bin/env python3
"""Validate the standalone DNS-01 staging compose project, without credentials.

The staging service must be unable to recreate or access the live gateway.
It must not publish ports, mount live cert state, or join production networks.
"""
import json
import sys


def check(config):
    services = config["services"]
    assert set(services) == {"dns01-staging"}, "Staging may not contain any production service"
    service = services["dns01-staging"]
    assert service.get("image") == "homeaway-caddy-dns01:2.11.7-cloudflare-v0.2.4"
    assert service.get("entrypoint") == ["/usr/local/bin/start-dns01"]
    assert service.get("restart") == "no"
    assert service.get("read_only") is True
    assert service.get("cap_drop") == ["ALL"]
    assert "no-new-privileges:true" in service.get("security_opt", [])
    assert not service.get("ports"), "DNS-01 never needs inbound ports"
    assert not service.get("privileged")
    assert not service.get("network_mode")
    assert set(service.get("networks", {})) == {"dns01-acme"}, "Only isolated outbound bridge"
    assert set(config.get("networks", {})) == {"dns01-acme"}
    assert not config["networks"]["dns01-acme"].get("internal", False), "ACME requires outbound access"
    assert not service.get("environment"), "Staging needs no production environment variables"
    assert {x["source"] for x in service.get("secrets", [])} == {"cloudflare_dns01_token"}
    assert set(config.get("secrets", {})) == {"cloudflare_dns01_token"}

    mounts = {m["target"]: m for m in service.get("volumes", [])}
    assert set(mounts) == {"/etc/caddy/Caddyfile", "/data", "/config"}
    assert mounts["/etc/caddy/Caddyfile"].get("read_only", False)
    assert mounts["/etc/caddy/Caddyfile"]["source"].endswith(
        "/control/gateway/Caddyfile.dns01-staging")
    for target in ("/data", "/config"):
        assert "/data/control/dns01-staging/" in mounts[target]["source"], (
            "Staging must not reuse live Caddy certificate/config state")
    assert "/var/run/docker.sock" not in json.dumps(service)
    assert "homeaway-pcctl" not in json.dumps(service)
    assert "data/control/caddy" not in json.dumps(service)
    print("PASS: DNS-01 staging isolated from all live HomeAway services")


if __name__ == "__main__":
    check(json.load(sys.stdin))
