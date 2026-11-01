#!/usr/bin/env python3
"""Static deployment barriers for the disabled-by-default account directory."""
from pathlib import Path

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
    assert "ports:" not in c, "No account service may publish host ports"
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
    print("PASS: account directory is opt-in, internal-only, and denies stale grants")

if __name__=="__main__":main()
