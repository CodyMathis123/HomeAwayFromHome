#!/usr/bin/env python3
"""One-time local-only Authelia bootstrap. Nothing is deployed or started."""
from __future__ import annotations
import argparse
import json
import os
import re
import secrets
import subprocess
from pathlib import Path

IMAGE = "authelia/authelia:4.39.28"
HOSTNAME_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
USER_RE = re.compile(r"^[a-z][a-z0-9_-]{1,30}$")
ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "data/control/authelia"


def safe_domain(value: str) -> str:
    if not HOSTNAME_RE.fullmatch(value) or len(value) > 253:
        raise ValueError("Use a valid DNS hostname, e.g. homeaway.home.arpa")
    return value


def build_configuration(home: str, auth: str, cookie_domain: str, session: str, jwt: str, encryption: str) -> str:
    h, a, domain = [json.dumps(safe_domain(s)) for s in (home, auth, cookie_domain)]
    return f"""# Generated locally; never commit or publish this file.
server:
  address: tcp://:9091
log:
  level: info
totp:
  issuer: HomeAway
authentication_backend:
  file:
    path: /config/users_database.yml
identity_validation:
  reset_password:
    jwt_secret: {json.dumps(jwt)}
access_control:
  default_policy: deny
  rules:
    - domain: {h}
      resources:
        - '^/control(?:/.*)?$'
      subject:
        - 'group:admins'
      policy: two_factor
session:
  secret: {json.dumps(session)}
  inactivity: 30m
  expiration: 8h
  remember_me: 1M
  cookies:
    - domain: {domain}
      authelia_url: {json.dumps("https://" + auth + ":8443")}
      default_redirection_url: {json.dumps("https://" + home + ":8443")}
regulation:
  max_retries: 4
  find_time: 5m
  ban_time: 15m
storage:
  encryption_key: {json.dumps(encryption)}
  local:
    path: /config/db.sqlite3
notifier:
  filesystem:
    filename: /config/notification.txt
"""


def create_user_hash() -> tuple[str, str]:
    completed = subprocess.run(
        ["docker", "run", "--rm", IMAGE, "authelia", "crypto", "hash", "generate",
         "argon2", "--random", "--random.length", "28", "--random.charset", "alphanumeric"],
        capture_output=True, text=True, timeout=120, check=True)
    output = completed.stdout
    digest = re.search(r"\$argon2id\$[^\s'\"]+", output)
    password = re.search(r"(?im)^\s*(?:random\s+)?password\s*:\s*(\S+)\s*$", output)
    if not digest or not password:
        raise RuntimeError("Could not parse generated password and digest. No account created.")
    return digest.group(0), password.group(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", default="homeaway.home.arpa")
    parser.add_argument("--auth", default="auth.homeaway.home.arpa")
    parser.add_argument("--cookie-domain", default="homeaway.home.arpa")
    parser.add_argument("--user", required=True)
    parser.add_argument("--email", required=True)
    args = parser.parse_args()
    for candidate in (args.home, args.auth, args.cookie_domain):
        safe_domain(candidate)
    if not USER_RE.fullmatch(args.user) or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", args.email):
        parser.error("Invalid user/email")
    DEST.mkdir(parents=True, exist_ok=True)
    config_file, users_file = DEST / "configuration.yml", DEST / "users_database.yml"
    if config_file.exists() or users_file.exists():
        parser.error("Existing Authelia configuration detected; never overwrite secrets or users")
    digest, password = create_user_hash()
    config = build_configuration(args.home, args.auth, args.cookie_domain,
                                 secrets.token_urlsafe(64), secrets.token_urlsafe(64),
                                 secrets.token_urlsafe(64))
    db = {"users": {args.user: {"disabled": False, "displayname": args.user,
                               "password": digest, "email": args.email, "groups": ["admins"]}}}
    # JSON is a subset of YAML: Authelia's users_database.yml accepts it.
    for target, data in ((config_file, config), (users_file, json.dumps(db, indent=2) + "\n")):
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as out:
            out.write(data)
    print("Created local Authelia configuration and one administrator account.")
    print("ONE-TIME INITIAL PASSWORD (save securely):", password)
    print("Before Compose deployment: sudo chown -R 65534:65534 data/control/authelia")
    print("Enable TOTP using the local /config/notification.txt registration message.")


if __name__ == "__main__":
    main()
