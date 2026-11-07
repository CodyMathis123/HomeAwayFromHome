"""Deterministic, component-aware Docker Compose generation.

All outputs are data: no shell execution, image pulls, host networking changes,
Docker socket access, or privileged components. Source assets live in a
versioned package; per-install generated files live in an isolated state dir.
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import re
import tempfile

from components import SelectionError

ROOT = Path(__file__).resolve().parents[1]
DASHBOARDS = {
    "cooling-monitoring": (
        "sensordash.json", "cooling-systems.json", "reporting-summary.json",
        "electrical.json", "events.json",
    ),
    "linux-host-monitoring": ("linux-host.json",),
}
BASE_SERVICES = frozenset({"victoria-metrics", "grafana", "gateway"})
GENERATED = (
    "compose.generated.json", "scrape.generated.yml",
    "Caddyfile.generated", "dashboards.generated.yml",
)
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.-]{0,252}$")


def _mount(source: Path | str, target: str, *, read_only=True) -> dict:
    return {
        "type": "bind", "source": str(source), "target": target,
        "read_only": read_only,
    }


def settings(env_file: Path, state: Path) -> dict:
    if not env_file.is_file() or env_file.is_symlink():
        raise SelectionError(f"Missing or untrusted cooling config: {env_file}")
    options = {}
    for line in env_file.read_text(encoding="ascii").splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise SelectionError("Invalid generated cooling settings.")
        key, value = line.split("=", 1)
        if key in options:
            raise SelectionError(f"Duplicate cooling setting: {key}")
        options[key] = value
    try:
        ip = ipaddress.IPv4Address(options["COOLING_BIND_IP"])
        port = int(options["COOLING_HTTPS_PORT"])
        hostname = options["COOLING_TLS_NAME"]
        if ip.is_unspecified or ip.is_multicast or not 1024 <= port <= 65535:
            raise ValueError("Invalid bind IP or HTTPS port.")
        if not NAME_RE.fullmatch(hostname) or ".." in hostname:
            raise ValueError("Invalid TLS name.")
        if options.get("COOLING_STATE_DIR", str(state)) != str(state):
            raise ValueError("State path differs from configured install location.")
    except (KeyError, ValueError) as exc:
        raise SelectionError(f"Invalid cooling configuration: {exc}") from exc
    return {"ip": str(ip), "port": port, "hostname": hostname}


def render_caddy(*, cooling: bool, portal: bool = False) -> str:
    parts = [
        "{",
        "  admin off",
        "  default_sni {$COOLING_TLS_NAME}",
        "  skip_install_trust",
        "}",
        "",
        "https://{$COOLING_TLS_NAME}:8443 {",
        "  tls internal",
        "",
    ]
    if cooling:
        parts += [
            "  handle /ingest {",
            "    request_body {",
            "      max_size 256KB",
            "    }",
            "    reverse_proxy https://telemetry-ingest:8443 {",
            "      header_up -Cookie",
            "      transport http {",
            "        tls_trust_pool file /run/telemetry-ca.crt",
            "        tls_server_name {$COOLING_BIND_IP}",
            "      }",
            "    }",
            "  }",
            "",
        ]
    else:
        parts += [
            "  handle /ingest {",
            '    respond "Telemetry ingestion not installed" 404',
            "  }",
            "",
        ]
    if portal:
        parts += [
            "  handle /portal {",
            "    redir /portal/ 308",
            "  }",
            "  handle_path /portal/* {",
            "    reverse_proxy portal:8080",
            "  }",
            "",
        ]
    else:
        parts += [
            "  handle /portal {",
            '    respond "Portal not installed" 404',
            "  }",
            "  handle_path /portal/* {",
            '    respond "Portal not installed" 404',
            "  }",
            "",
        ]
    parts += [
        "  handle {",
        "    reverse_proxy grafana:3000",
        "  }",
        "}",
        "",
    ]
    return "\n".join(parts)


def render_dashboard_provider() -> str:
    # Standalone modules own their dashboard selection. When a feature is
    # removed, drop only the provisioned dashboard definition from Grafana;
    # VictoriaMetrics history and Grafana user state are never deleted.
    return (
        "apiVersion: 1\n"
        "providers:\n"
        "  - name: HomeAway Managed Dashboards\n"
        "    orgId: 1\n"
        "    folder: HomeAway\n"
        "    type: file\n"
        "    disableDeletion: false\n"
        "    updateIntervalSeconds: 10\n"
        "    allowUiUpdates: false\n"
        "    options:\n"
        "      path: /etc/grafana/dashboards\n"
    )


def render_scrape(*, host: bool) -> str:
    result = [
        "global:",
        "  scrape_interval: 15s",
        "  scrape_timeout: 10s",
        "scrape_configs:",
        "  - job_name: victoria-metrics",
        "    static_configs:",
        '      - targets: ["victoria-metrics:8428"]',
    ]
    if host:
        result += [
            "  - job_name: linux-host",
            "    static_configs:",
            '      - targets: ["node-exporter:9100"]',
        ]
    return "\n".join(result) + "\n"


def render(plan: dict, env_file: Path, state: Path, root: Path = ROOT) -> dict[str, str]:
    if not plan.get("deployable"):
        raise SelectionError("Design-only selections cannot generate runnable Compose.")
    selected = set(plan["selected_features"])
    cooling = "cooling-monitoring" in selected
    host = "linux-host-monitoring" in selected
    diagnostics = "monitoring-diagnostics" in plan["component_order"]
    portal = "home-portal" in selected
    allowed = set(DASHBOARDS) | {"monitoring-diagnostics", "home-portal"}
    if not (cooling or host) or selected - allowed:
        raise SelectionError("Selected features have no implemented Compose mapping.")
    opts = settings(env_file, state)
    generated = state / "generated"
    grafana_volumes = [
        "cooling-grafana:/var/lib/grafana",
        _mount(state / "secrets/grafana_admin_password", "/run/secrets/grafana_admin_password"),
        _mount(generated / "dashboards.generated.yml", "/etc/grafana/provisioning/dashboards/homeaway.yaml"),
        _mount(root / "grafana/provisioning/datasources/victoriametrics.yaml", "/etc/grafana/provisioning/datasources/homeaway.yaml"),
    ]
    for cid in sorted(selected):
        for dashboard in DASHBOARDS.get(cid, ()):
            grafana_volumes.append(
                _mount(root / "grafana/dashboards" / dashboard,
                       "/etc/grafana/dashboards/" + dashboard)
            )
    vm = {
        "image": "victoriametrics/victoria-metrics:v1.155.0",
        "restart": "unless-stopped",
        "command": [
            "-storageDataPath=/victoria-metrics-data",
            "-retentionPeriod=90d",
            "-promscrape.config=/etc/victoria-metrics/prometheus.yml",
        ],
        "volumes": [
            "cooling-metrics:/victoria-metrics-data",
            _mount(generated / "scrape.generated.yml", "/etc/victoria-metrics/prometheus.yml"),
        ],
        "networks": ["cooling-private"],
    }
    grafana = {
        "image": "grafana/grafana:13.3.0",
        "restart": "unless-stopped",
        "depends_on": ["victoria-metrics"],
        "environment": {
            "GF_SECURITY_ADMIN_USER": "admin",
            "GF_SECURITY_ADMIN_PASSWORD__FILE": "/run/secrets/grafana_admin_password",
            "GF_USERS_ALLOW_SIGN_UP": "false",
            "GF_AUTH_ANONYMOUS_ENABLED": "false",
            "GF_SECURITY_COOKIE_SECURE": "true",
            "GF_SECURITY_ALLOW_EMBEDDING": "false",
            "GF_SERVER_ROOT_URL": f'https://{opts["hostname"]}:{opts["port"]}',
        },
        "volumes": grafana_volumes,
        "networks": ["cooling-private"],
    }
    gateway = {
        "image": "caddy:2.11.7-alpine",
        "restart": "unless-stopped",
        "depends_on": ["grafana"] + (["telemetry-ingest"] if cooling else []) + (["portal"] if portal else []),
        "environment": {
            "COOLING_TLS_NAME": opts["hostname"],
            "COOLING_BIND_IP": opts["ip"],
        },
        "ports": [{"target": 8443, "published": opts["port"],
                   "host_ip": opts["ip"], "protocol": "tcp"}],
        "cap_drop": ["ALL"],
        "cap_add": ["NET_BIND_SERVICE"],
        "security_opt": ["no-new-privileges:true"],
        "volumes": [
            _mount(generated / "Caddyfile.generated", "/etc/caddy/Caddyfile"),
            "cooling-caddy-data:/data",
            "cooling-caddy-config:/config",
        ] + ([_mount(state / "secrets/telemetry/ca.crt", "/run/telemetry-ca.crt")] if cooling else []),
        "networks": ["cooling-public", "cooling-private"],
    }
    services = {"victoria-metrics": vm, "grafana": grafana, "gateway": gateway}
    if cooling:
        services["telemetry-ingest"] = {
            "build": {"context": str(root / "ingest")},
            "restart": "unless-stopped",
            "depends_on": ["victoria-metrics"],
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "volumes": [_mount(state / "secrets/telemetry", "/run/tls")],
            "networks": ["cooling-private"],
        }
    if host:
        services["node-exporter"] = {
            "image": "quay.io/prometheus/node-exporter:v1.12.1",
            "restart": "unless-stopped",
            "read_only": True,
            "user": "65534:65534",
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "command": [
                "--web.listen-address=:9100",
                "--path.procfs=/host/proc",
                "--path.sysfs=/host/sys",
                "--collector.disable-defaults",
                "--collector.cpu", "--collector.meminfo", "--collector.loadavg",
                "--collector.stat", "--collector.thermal_zone",
                "--collector.diskstats", "--collector.uname",
                "--collector.time", "--collector.vmstat",
            ],
            "volumes": [
                _mount("/proc", "/host/proc"),
                _mount("/sys", "/host/sys"),
            ],
            "networks": ["cooling-private"],
        }
    if diagnostics:
        services["metrics-diagnostics"] = {
            "build": {"context": str(root / "monitoring/diagnostics")},
            "restart": "unless-stopped",
            "depends_on": ["victoria-metrics"],
            "environment": {
                "HOMEAWAY_DIAGNOSTICS_MODE": "modular",
                "HOMEAWAY_SELECTED_FEATURES": ",".join(sorted(selected)),
            },
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "networks": ["cooling-private"],
        }
    if portal:
        services["portal"] = {
            "build": {
                "context": str(root / "portal"),
                "dockerfile": "Dockerfile.modular",
            },
            "restart": "unless-stopped",
            "depends_on": ["metrics-diagnostics"],
            "environment": {
                "HOMEAWAY_SELECTED_FEATURES": ",".join(sorted(set(plan["component_order"]) & allowed)),
                "HOMEAWAY_PORTAL_PASSWORD_FILE": "/run/secrets/portal_password",
            },
            "volumes": [
                _mount(state / "secrets/portal_password", "/run/secrets/portal_password"),
            ],
            "read_only": True,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "networks": ["cooling-private"],
        }
    if set(services) != set(plan["services"]):
        raise SelectionError(
            f"Generated service graph mismatch: expected {plan['services']}, "
            f"rendered {sorted(services)}"
        )
    project = {
        "name": "homeaway-cooling",
        "services": services,
        "networks": {
            "cooling-private": {"internal": True},
            "cooling-public": {"driver": "bridge"},
        },
        "volumes": {name: {} for name in (
            "cooling-metrics", "cooling-grafana",
            "cooling-caddy-data", "cooling-caddy-config",
        )},
    }
    return {
        "compose.generated.json": json.dumps(project, indent=2, sort_keys=True) + "\n",
        "scrape.generated.yml": render_scrape(host=host),
        "Caddyfile.generated": render_caddy(cooling=cooling, portal=portal),
        "dashboards.generated.yml": render_dashboard_provider(),
    }


def differences(rendered: dict[str, str], state: Path) -> list[str]:
    generated = state / "generated"
    changed = []
    for filename, content in rendered.items():
        path = generated / filename
        if path.is_symlink():
            raise SelectionError(f"Symlinked managed file prohibited: {path}")
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            changed.append(filename)
    return changed


def write(rendered: dict[str, str], state: Path) -> None:
    generated = state / "generated"
    if generated.is_symlink():
        raise SelectionError("Symlinked generated configuration directory is prohibited.")
    generated.mkdir(parents=True, exist_ok=True, mode=0o700)
    generated.chmod(0o700)
    for filename, content in rendered.items():
        if filename not in GENERATED:
            raise SelectionError(f"Not an approved generated file: {filename}")
        dest = generated / filename
        if dest.is_symlink():
            raise SelectionError(f"Symlinked managed file prohibited: {dest}")
        if dest.is_file() and dest.read_text(encoding="utf-8") == content:
            continue
        fd, tmp = tempfile.mkstemp(prefix=".homeaway-", dir=generated)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(content)
            os.chmod(tmp, 0o600)
            os.replace(tmp, dest)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
