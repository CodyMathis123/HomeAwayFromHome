#!/usr/bin/env python3
"""Check Resend SMTP connectivity/auth without sending mail or exposing credentials.

Run as sudo from the Pi's repository. Does NOT touch Docker or live services.
"""
from __future__ import annotations
import os
from pathlib import Path
import smtplib
import ssl
import stat
import sys

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "secrets/control/lldap-smtp.env"
FIELDS = {
    "LLDAP_SMTP_OPTIONS__SERVER": "smtp.resend.com",
    "LLDAP_SMTP_OPTIONS__PORT": "465",
    "LLDAP_SMTP_OPTIONS__SMTP_ENCRYPTION": "TLS",
    "LLDAP_SMTP_OPTIONS__USER": "resend",
}

def load() -> dict:
    info = ENV.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise ValueError("SMTP env file must be regular, root-owned and mode 0600")
    if info.st_size < 50 or info.st_size > 8192:
        raise ValueError("SMTP env file has an unexpected size")
    values = {}
    for line in ENV.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        k, separator, v = line.partition("=")
        if separator and k:
            values[k] = v
    if any(values.get(k) != expected for k, expected in FIELDS.items()):
        raise ValueError("Expected Resend SMTP 465/TLS settings")
    if not values.get("LLDAP_SMTP_OPTIONS__PASSWORD", "").startswith("re_"):
        raise ValueError("Missing or invalid Resend API-key format")
    return values

def main() -> int:
    if os.geteuid() != 0:
        print("Run with sudo.", file=sys.stderr)
        return 2
    try:
        settings = load()
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.resend.com", 465,
                             context=context, timeout=12) as client:
            client.login("resend", settings["LLDAP_SMTP_OPTIONS__PASSWORD"])
        print("PASS: Resend SMTP TLS connection and authentication; no email sent.")
        return 0
    except (OSError, smtplib.SMTPException, ValueError, UnicodeError):
        print("FAIL: Resend SMTP connectivity, authentication, or local configuration; "
              "check provider status, outbound DNS/TLS and key. No secret was logged.",
              file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
