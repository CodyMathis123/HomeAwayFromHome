#!/usr/bin/env python3
"""Inspect Caddy's adapted JSON: old authenticated sites and new 503-only sites."""
import json
import sys
from pathlib import Path

OLD = (
    "homeaway.home.arpa",
    "auth.homeaway.home.arpa",
    "metrics.homeaway.home.arpa",
)
NEW = (
    "home.homeawayfromhome.house",
    "auth.homeawayfromhome.house",
    "metrics.homeawayfromhome.house",
)


def walk(item):
    if isinstance(item, dict):
        yield item
        for val in item.values():
            yield from walk(val)
    elif isinstance(item, list):
        for val in item:
            yield from walk(val)


def matching_routes(data, host):
    matches = []
    for element in walk(data.get("apps", {}).get("http", {})):
        for matcher in element.get("match", []):
            if isinstance(matcher, dict) and host in matcher.get("host", []):
                matches.append(element)
                break
    return matches


def validate(data):
    serialized = json.dumps(data)
    for host in OLD + NEW:
        assert matching_routes(data, host), "Missing Caddy host route: " + host
    assert "authelia:9091" in serialized, "Legacy forward-auth disappeared"
    assert "pc-control:8081" in serialized, "Legacy PC control upstream disappeared"
    assert "portal:8080" in serialized, "Legacy portal upstream disappeared"
    assert "grafana:3000" in serialized, "Legacy Grafana upstream disappeared"
    for host in NEW:
        routes = matching_routes(data, host)
        for route in routes:
            handlers = list(walk(route))
            assert any(x.get("handler") == "static_response" and
                       str(x.get("status_code")) == "503" for x in handlers), (
                "New hostname is not a disabled HTTP 503 placeholder: " + host)
            assert not any(x.get("handler") == "reverse_proxy" for x in handlers), (
                "New name must NOT reverse-proxy an app before auth migration: " + host)
    print("PASS: legacy auth/control routes remain, new names are static 503-only placeholders")


if __name__ == "__main__":
    assert len(sys.argv) == 2
    validate(json.loads(Path(sys.argv[1]).read_text()))
