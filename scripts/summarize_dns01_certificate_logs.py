#!/usr/bin/env python3
"""Print only allowlisted Caddy certificate metadata from gateway log lines.

Do not echo untrusted/raw error strings, API tokens, account URLs, or headers.
"""
import json
import sys

HOSTS = (
    "home.homeawayfromhome.house",
    "auth.homeawayfromhome.house",
    "metrics.homeawayfromhome.house",
)


def summarize(lines):
    results = {host: "Not observed in recent log window" for host in HOSTS}
    for line in lines:
        pos = line.find("{")
        if pos < 0:
            continue
        try:
            entry = json.loads(line[pos:])
        except (ValueError, TypeError):
            continue
        identifier = entry.get("identifier")
        if identifier not in results:
            continue
        msg, level = entry.get("msg"), entry.get("level")
        if msg == "certificate obtained successfully" and (
            entry.get("issuer") == "acme-v02.api.letsencrypt.org-directory"
        ):
            results[identifier] = "Production certificate issued"
        elif msg == "authorization finalized" and entry.get("authz_status") == "valid":
            if results[identifier] != "Production certificate issued":
                results[identifier] = "DNS challenge passed; issuance not yet confirmed"
        elif level == "error" or msg in ("could not get certificate from issuer",
                                         "certificate obtain failed"):
            if results[identifier] != "Production certificate issued":
                results[identifier] = "Error reported; inspect gateway locally"
    return results


def main():
    results = summarize(sys.stdin)
    for host, status in results.items():
        print(host + ": " + status)
    if not all(v == "Production certificate issued" for v in results.values()):
        print("Review local Caddy logs or certificate files before declaring issuance complete.")


if __name__ == "__main__":
    main()
