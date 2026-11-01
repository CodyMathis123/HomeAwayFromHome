#!/usr/bin/env python3
"""Static deployment barriers for the disabled-by-default account directory."""
from pathlib import Path
import json
import os
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def main():
    c=(ROOT/"compose.accounts.yaml").read_text()
    gateway=(ROOT/"control/gateway/Caddyfile").read_text()
    backend=(ROOT/"control/api/app.py").read_text()
    broker=(ROOT/"control/accounts/broker.py").read_text()
    assert "profiles: [accounts]" in c
    assert "HOMEAWAY_ACCOUNT_ADMIN_ENABLED:" in c and ":-false" in c
    assert 'HOMEAWAY_ACCOUNT_ADMIN_ENABLED", "false"' in backend
    assert "HOMEAWAY_ACCOUNT_BROKER_URL" in backend
    assert "read_only: true" in c
    assert "no-new-privileges:true" in c
    assert "cap_drop: [ALL]" in c
    assert "account-directory:" in c and "internal: true" in c
    assert "homeaway_account_broker_token" in c
    assert "LLDAP_LDAP_USER_PASS_FILE" in c
    assert "LLDAP_KEY_SEED_FILE" in c
    assert "LLDAP_JWT_SECRET_FILE" in c
    assert "127.0.0.1:17170:17170/tcp" in c, "Directory UI must bind loopback only"
    assert "docker.sock" not in c and "privileged:" not in c
    assert "/config/users_database" not in c, "Never mount Authelia users writeable"
    assert "pc-control:" in c and "account-broker:" in c
    assert "LLDAP_LDAP_USER_PASS_FILE" not in c.split("  pc-control:",1)[1]
    assert "forward_auth authelia:9091" in gateway
    assert "copy_headers Remote-User>X-HomeAway-User Remote-Groups>X-HomeAway-Groups" in gateway
    assert 'role == "admins"' in backend
    assert 'broker_request(actor, "resolve"' in backend
    assert "read(131073)" in backend, "Backend response must be bounded"
    assert "fingerprint(target)!=revision" in broker
    assert "Cannot remove the last administrator" in broker
    assert 'if uid==actor or uid=="admin":' in broker
    env = {**os.environ,
           "HOMEAWAY_DOMAIN":"homeaway.home.arpa",
           "HOMEAWAY_AUTH_DOMAIN":"auth.homeaway.home.arpa",
           "HOMEAWAY_METRICS_DOMAIN":"metrics.homeaway.home.arpa",
           "PCCTL_GID":"999"}
    output = subprocess.run(
        ["docker","compose","-f","compose.yaml","-f","compose.control.yaml",
         "-f","compose.accounts.yaml","--profile","control",
         "--profile","accounts","--profile","metrics",
         "config","--format","json"],
        cwd=ROOT, env=env, check=True, capture_output=True, text=True)
    services=json.loads(output.stdout)["services"]
    assert set(services["authelia"]["networks"]) == {"pc-control","account-directory"}
    assert set(services["lldap"]["networks"]) == {"account-directory"}
    assert set(services["account-broker"]["networks"]) == {"pc-control","account-directory"}
    assert set(services["pc-control"]["networks"]) == {"pc-control"}
    ui_ports=services["lldap"].get("ports",[])
    assert len(ui_ports)==1 and ui_ports[0]["host_ip"]=="127.0.0.1"
    assert ui_ports[0]["target"]==17170 and ui_ports[0]["published"]=="17170"
    assert ui_ports[0]["protocol"]=="tcp"
    for name in ("account-broker","authelia","pc-control"):
        assert not services[name].get("ports"), name+" must not expose a host port"
    assert "1000" in [str(x) for x in services["account-broker"].get("group_add",[])]
    assert not services["lldap"].get("privileged", False)
    assert "lldap_admin_password" not in services["pc-control"].get("secrets",[])
    print("PASS: account directory is opt-in, SSH-loopback UI only, reachable from Authelia, and denies stale grants")

if __name__=="__main__":main()
