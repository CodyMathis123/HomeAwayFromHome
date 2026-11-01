#!/usr/bin/env python3
"""Check a resolved *production opt-in* DNS-01 gateway overlay against baseline.

Two JSON files are produced by docker compose config --format json (base,
then base+overlay). No containers are started and no secret contents are read.
"""
from __future__ import annotations

import ipaddress
import json
import sys
from pathlib import Path


def mounts_for(service):
    return {entry["target"]: entry for entry in service.get("volumes", [])}


def check(baseline: dict, proposed: dict):
    assert baseline["name"] == proposed["name"] == "homeawayfromhome"
    before, after = baseline["services"], proposed["services"]
    assert set(before) == set(after), "Cannot add/remove services"
    for name in before:
        if name != "gateway":
            assert before[name] == after[name], "Non-gateway service changed: " + name
    assert baseline["networks"] == proposed["networks"], "No network changes"
    assert "cloudflare_dns01_token" not in baseline.get("secrets", {})
    expected_secrets = set(baseline.get("secrets", {})) | {"cloudflare_dns01_token"}
    assert set(proposed.get("secrets", {})) == expected_secrets

    original, gateway = before["gateway"], after["gateway"]
    assert original["image"] == "caddy:2.11.7-alpine"
    assert gateway["image"] == "homeaway-caddy-dns01:2.11.7-cloudflare-v0.2.4"
    assert gateway["entrypoint"] == ["/usr/local/bin/start-dns01"]
    assert gateway["build"]["dockerfile"] == "Dockerfile.dns01"
    assert gateway["build"]["context"].endswith("/control/gateway")
    allowed = {"image", "build", "entrypoint", "secrets", "volumes"}
    for key in set(original) | set(gateway):
        if key not in allowed:
            assert original.get(key) == gateway.get(key), "Gateway setting changed: " + key

    ports = gateway["ports"]
    assert len(ports) == 1, "Do not expose additional ports"
    assert ports[0]["published"] == "8443"
    assert ports[0]["target"] == 8443 and ports[0]["protocol"] == "tcp"
    assert ipaddress.ip_address(ports[0]["host_ip"]).is_private
    assert set(gateway["networks"]) == {"infra", "pc-control"}
    assert gateway["cap_drop"] == ["ALL"]
    assert gateway["cap_add"] == ["NET_BIND_SERVICE"]
    assert "no-new-privileges:true" in gateway.get("security_opt", [])
    assert not gateway.get("privileged")
    assert len(gateway.get("secrets", [])) == 1
    assert gateway["secrets"][0]["source"] == "cloudflare_dns01_token"
    assert "/var/run/docker.sock" not in json.dumps(gateway)

    old_mounts, new_mounts = mounts_for(original), mounts_for(gateway)
    assert set(new_mounts) == set(old_mounts) | {"/etc/caddy/Caddyfile.legacy"}
    assert set(old_mounts) == {"/etc/caddy/Caddyfile", "/data", "/config"}
    assert new_mounts["/etc/caddy/Caddyfile"]["source"].endswith(
        "/control/gateway/Caddyfile.dns01-production")
    assert new_mounts["/etc/caddy/Caddyfile"].get("read_only", False)
    legacy = new_mounts["/etc/caddy/Caddyfile.legacy"]
    assert legacy["source"] == old_mounts["/etc/caddy/Caddyfile"]["source"]
    assert legacy.get("read_only", False)
    for target in ("/data", "/config"):
        assert new_mounts[target] == old_mounts[target], "TLS state mount changed"

    secret_source = proposed["secrets"]["cloudflare_dns01_token"]["file"]
    assert secret_source.endswith("/secrets/control/cloudflare_dns01_token")
    assert all("cloudflare_dns01_token" not in json.dumps(service)
               for name, service in after.items() if name != "gateway")
    print("PASS: production DNS-01 adds only gateway binary/config/secret; all other services untouched")


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: check_dns01_production_resolved.py BASE.json PROPOSED.json")
    check(*(json.loads(Path(path).read_text()) for path in sys.argv[1:]))


if __name__ == "__main__":
    main()
