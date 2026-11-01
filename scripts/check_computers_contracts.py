#!/usr/bin/env python3
"""Guard opt-in Computer Management's Compose and host privilege boundaries."""
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    env={**os.environ,
         "HOMEAWAY_DOMAIN":"homeaway.home.arpa",
         "HOMEAWAY_AUTH_DOMAIN":"auth.homeaway.home.arpa",
         "HOMEAWAY_METRICS_DOMAIN":"metrics.homeaway.home.arpa",
         "PCCTL_GID":"999",
         "PCINV_GID":"998"}
    cmd=["docker","compose","-f","compose.yaml","-f","compose.control.yaml",
         "-f","compose.accounts.yaml","-f","compose.computers.yaml",
         "--profile","control","--profile","accounts","--profile","metrics",
         "config","--format","json"]
    result=subprocess.run(cmd,cwd=ROOT,env=env,check=True,capture_output=True,text=True)
    cfg=json.loads(result.stdout)
    services=cfg["services"]
    control=services["pc-control"]
    assert control["user"]=="65534:65534"
    assert control["read_only"] is True
    assert not control.get("privileged",False)
    assert "ALL" in control["cap_drop"]
    assert "no-new-privileges:true" in control["security_opt"]
    assert not control.get("ports"),"Controller must not gain a published port"
    assert set(control["networks"])=={"pc-control"}
    gids=set(map(str,control["group_add"]))
    assert {"999","998"} <= gids,"Both independent sockets must have ACL groups"
    mounts={(m["source"],m["target"],m.get("read_only",False)) for m in control["volumes"]}
    assert ("/etc/homeaway-pcctl","/etc/homeaway-pcctl",True) in mounts
    assert ("/run/homeaway-pcctl","/run/homeaway-pcctl",True) in mounts
    assert ("/run/homeaway-pcinventory","/run/homeaway-pcinventory",True) in mounts
    assert not any("docker.sock" in str(m) or "/.ssh" in str(m) for m in control["volumes"])
    assert control["environment"]["HOMEAWAY_COMPUTER_ADMIN_ENABLED"]=="false"
    assert control["environment"]["HOMEAWAY_INVENTORY_SOCKET"]=="/run/homeaway-pcinventory/agent.sock"
    assert "pcinventory" not in services,"Writer must be a separate host-side service"
    for name in ("portal","gateway","authelia","account-broker","lldap"):
        service=services[name]
        assert all("homeaway-pcinventory" not in str(v) for v in service.get("volumes",[]))
    src=(ROOT/"control/api/app.py").read_text()
    assert "account_admin_enabled() and" in src
    assert 'role != "admins"' in src
    assert "computer_request(actor, operation, data)" in src
    assert 'operation = "audit-list" if path.endswith("/audit") else "list"' in src
    assert "computer_request(actor, operation, {})" in src
    assert '"/control/api/admin/computers/audit"' in src
    assert '"draft-create", "draft-discard", "rename"' in src
    systemd=(ROOT/"control/pcinventory/homeaway-inventory.service").read_text()
    assert "User=homeaway-inventory" in systemd
    assert "ProtectSystem=strict" in systemd
    assert "ReadWritePaths=/etc/homeaway-pcctl /var/lib/homeaway-pcinventory" in systemd
    assert "CapabilityBoundingSet=" in systemd
    assert "ProtectHome=true" in systemd
    handler=(ROOT/"control/pcinventory/agent.py").read_text()
    assert "subprocess.run(" not in handler and "os.system(" not in handler
    assert 'if op == "audit-list"' in handler
    assert "os.O_NOFOLLOW" in handler
    verify_socket=(ROOT/"control/pcverify/homeaway-pcverify.socket").read_text()
    verify_service=(ROOT/"control/pcverify/homeaway-pcverify.service").read_text()
    assert "SocketUser=pcctl" in verify_socket
    assert "SocketGroup=homeaway-inventory" in verify_socket
    assert "SocketMode=0660" in verify_socket
    assert "User=pcctl" in verify_service
    assert "ProtectHome=read-only" in verify_service
    verifier=(ROOT/"control/pcverify/agent.py").read_text()
    assert "HostKeyAlgorithms=ssh-ed25519" in verifier
    assert "StrictHostKeyChecking=yes" in verifier
    assert '"status"' in verifier
    assert '"shutdown"' not in verifier and '"sleep"' not in verifier
    print("PASS: computer administration opt-in, non-root writer socket and no browser inventory write/SSH key access")


if __name__=="__main__":
    main()
