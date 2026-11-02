#!/usr/bin/env python3
"""Check the actual opt-in production Compose stack without starting services.

Run with the resolved JSON of: base + control + accounts + computers +
production + account-recovery + authelia-smtp.
Synthetic secrets only in CI. No key values are printed.
"""
from __future__ import annotations
import json
import sys

def check(obj: dict) -> None:
    services=obj["services"]
    networks=obj["networks"]
    for name in ("gateway","lldap","authelia","account-broker","pc-control"):
        assert name in services, f"Missing service: {name}"

    gateway=services["gateway"]
    mounts=gateway.get("volumes",[])
    mount=[v for v in mounts if v.get("target")=="/etc/caddy/Caddyfile"]
    assert len(mount)==1, "Gateway needs exactly one Caddyfile mount"
    assert mount[0]["source"].endswith(
        "/control/gateway/Caddyfile.account-recovery"),"Wrong production Caddyfile"
    assert "infra" in gateway["networks"]
    assert "account-directory" in gateway["networks"]
    assert gateway["networks"]["infra"].get("gw_priority",0)>=100
    assert networks["account-directory"].get("internal") is True
    assert networks["pc-control"].get("internal") is True
    assert networks["account-ui"].get("internal") is not True
    assert networks["authelia-mail-egress"].get("internal") is not True

    authelia=services["authelia"]
    assert set(authelia["networks"])=={
        "pc-control","account-directory","authelia-mail-egress"}
    assert authelia["networks"]["authelia-mail-egress"].get("gw_priority",0)>=100
    assert "infra" not in authelia["networks"],"Authelia must not join infra"
    assert not authelia.get("ports"),"Authelia must not publish ports"
    env=authelia.get("environment",{})
    assert "AUTHELIA_CONFIG" not in env, "Authelia 4.39 ignores AUTHELIA_CONFIG"
    assert env.get("AUTHELIA_NOTIFIER_SMTP_ADDRESS")=="submissions://smtp.resend.com:465"
    assert env.get("AUTHELIA_NOTIFIER_SMTP_PASSWORD_FILE")=="/run/secrets/authelia_smtp_password"
    assert any(x["source"]=="authelia_smtp_password"
               for x in authelia.get("secrets",[]))

    lldap=services["lldap"]
    ports=lldap.get("ports",[])
    assert len(ports)==1 and ports[0].get("host_ip")=="127.0.0.1"
    assert ports[0].get("target")==17170
    assert set(lldap["networks"])=={"account-directory","account-ui"}
    assert lldap["environment"]["LLDAP_SMTP_OPTIONS__ENABLE_PASSWORD_RESET"]=="true"

    broker=services["account-broker"]
    assert set(broker["networks"])=={"account-directory","pc-control"}
    assert not broker.get("ports"),"Broker must not publish ports"
    assert not services["pc-control"].get("ports"),"Control API must remain private"
    assert set(services["pc-control"]["networks"])=={"pc-control"}
    for name in ("gateway","authelia","account-broker","pc-control"):
        s=services[name]
        assert not s.get("privileged",False),f"{name} must not be privileged"
        assert all("/var/run/docker.sock" not in str(v)
                   for v in s.get("volumes",[])),f"{name} must not use Docker socket"

def main() -> int:
    try:
        check(json.load(sys.stdin))
    except (AssertionError,KeyError,TypeError,ValueError) as e:
        print("FAIL: production overlay network/secret contract: "+str(e),file=sys.stderr)
        return 1
    print("PASS: production overlay network, gateway, SMTP and isolation contracts")
    return 0

if __name__=="__main__":
    raise SystemExit(main())
