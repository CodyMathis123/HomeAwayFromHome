"""Fixed-target TLS certificate observations for HomeAway Phase 3.

Only reads certificate *public* metadata over private Docker networks.
No key mounts, container commands, arbitrary host selection or ACME actions.
Trust checks use the observer container's system roots; they do not prove
that a particular browser, device, or Windows collector trusts the chain.
"""
from __future__ import annotations

import os
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

from cryptography import x509

GATEWAY_TARGET = ("gateway", 8443)
TELEMETRY_TARGET = ("telemetry-ingest", 8443)
HOST_PATTERN = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
CERT_IDS = ("browser-home", "browser-auth", "browser-metrics", "telemetry-receiver")
TIMEOUT = 2.5


def safe_hostname(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 180:
        return None
    host = value.lower().strip()
    return host if HOST_PATTERN.fullmatch(host) else None


def targets():
    # The hostnames influence only SNI and expected certificate names;
    # TCP destinations remain the two fixed private service names above.
    return (
        ("browser-home", GATEWAY_TARGET, safe_hostname(os.getenv("HOMEAWAY_DOMAIN"))),
        ("browser-auth", GATEWAY_TARGET, safe_hostname(os.getenv("HOMEAWAY_AUTH_DOMAIN"))),
        ("browser-metrics", GATEWAY_TARGET, safe_hostname(os.getenv("HOMEAWAY_METRICS_DOMAIN"))),
        ("telemetry-receiver", TELEMETRY_TARGET, None),
    )


def get_leaf(target: tuple[str, int], server_name: str | None) -> bytes:
    # TLS connection deliberately does NOT enforce trust when extracting
    # public certificate metadata, so existing Caddy internal certs work.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection(target, timeout=TIMEOUT) as sock:
        with context.wrap_socket(sock, server_hostname=server_name) as channel:
            der = channel.getpeercert(binary_form=True)
    if not isinstance(der, bytes) or not 1 <= len(der) <= 32768:
        raise ValueError("No bounded leaf certificate")
    return der


def system_trust(target: tuple[str, int], server_name: str) -> str:
    try:
        context = ssl.create_default_context()
        with socket.create_connection(target, timeout=TIMEOUT) as sock:
            with context.wrap_socket(sock, server_hostname=server_name):
                return "verified"
    except ssl.SSLCertVerificationError:
        return "not_trusted"
    except (OSError, ssl.SSLError, TimeoutError):
        return "unavailable"


def short_name(name: x509.Name) -> str:
    # Issuer is public certificate metadata but keep its length bounded.
    value = name.rfc4514_string()
    return value[:160]


def certificate_names(cert: x509.Certificate) -> list[str]:
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        dns = [item for item in san.get_values_for_type(x509.DNSName)
               if isinstance(item, str) and len(item) <= 180]
        ips = [str(item) for item in san.get_values_for_type(x509.IPAddress)]
        return (dns + ips)[:24]
    except x509.ExtensionNotFound:
        return []


def hostname_matches(names: list[str], hostname: str) -> bool:
    # Informational SAN match only; never treated as independent proof of
    # certificate trust. Supports exact DNS names and one-label wildcards.
    # Python 3.12 removed ssl.match_hostname().
    host = hostname.lower()
    for name in names:
        candidate = name.lower()
        if candidate == host:
            return True
        if (candidate.startswith("*.") and candidate.count("*") == 1 and
                host.endswith(candidate[1:]) and
                host.count(".") == candidate.count(".")):
            return True
    return False


def observe(probe_id: str, target: tuple[str, int],
            hostname: str | None, now: float) -> dict:
    base = {
        "id": probe_id, "state": "unknown", "daysRemaining": None,
        "notAfter": None, "issuer": None, "subjectAltNames": [],
        "hostnameMatch": None, "publicTrust": "not_checked",
        "detail": "Certificate not checked",
    }
    if probe_id != "telemetry-receiver" and not hostname:
        base["detail"] = "Expected private browser hostname is missing or invalid"
        return base
    try:
        cert = x509.load_der_x509_certificate(get_leaf(target, hostname))
        not_after = cert.not_valid_after_utc
        not_before = cert.not_valid_before_utc
        current = datetime.fromtimestamp(now, timezone.utc)
        remaining = (not_after - current).total_seconds() / 86400
        names = certificate_names(cert)
        matches = hostname_matches(names, hostname) if hostname else None
        base.update({
            "state": "attention" if remaining < 14 or current < not_before or
                                   (matches is False) else "observed",
            "daysRemaining": round(remaining, 1),
            "notAfter": not_after.isoformat(),
            "issuer": short_name(cert.issuer),
            "subjectAltNames": names,
            "hostnameMatch": matches,
        })
        if probe_id != "telemetry-receiver":
            trust = system_trust(target, hostname)
            base["publicTrust"] = trust
            base["detail"] = (
                "Expired or expires in less than 14 days" if remaining < 14 else
                "Certificate not yet valid" if current < not_before else
                "Leaf certificate SAN does not match expected hostname" if not matches else
                "Leaf observed; system public roots validated, not client-device trust" if trust == "verified" else
                "Leaf observed; public trust not established (expected with Caddy internal CA)"
                if trust == "not_trusted" else
                "Leaf observed; public trust verification unavailable"
            )
        else:
            base["detail"] = (
                "Telemetry leaf expired or nearing expiration" if remaining < 14 else
                "Separate telemetry receiver leaf observed; Windows trust/CA and hostname not checked"
            )
    except (OSError, ValueError, TimeoutError, ssl.SSLError):
        base["detail"] = "TLS endpoint unavailable or leaf certificate invalid"
    return base


def snapshot() -> dict:
    now = time.time()
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = [pool.submit(observe, probe_id, address, name, now)
                for probe_id, address, name in targets()]
        certificates = [job.result() for job in jobs]
    return {
        "schemaVersion": 1,
        "checkedAt": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "source": "TLS handshake to fixed internal service endpoints",
        "certificates": certificates,
    }
