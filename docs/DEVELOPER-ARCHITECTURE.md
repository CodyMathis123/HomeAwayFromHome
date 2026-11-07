# Modular Linux backend: developer architecture and boundaries

**Design review record for experimental PR #104.** The intent is an
understandable, testable package, not a shell-script chain or a giant CLI.

## Separation of responsibilities

| Module | Single responsibility | Expected side effects |
| --- | --- | --- |
| `deploy/components/catalog.json` | Declarative features, requirements, suggestions and host constraints | None |
| `scripts/components.py` | Pure catalog validation, dependency closure and feature-to-service resolution | Read catalog only |
| `scripts/component_changes.py` | Pure impact and safety policy for transitions | None |
| `scripts/compose_generator.py` | Deterministically render selected Compose, scrape and HTTPS definitions | Only a separate explicitly called atomic writer |
| `scripts/init-cooling-standalone.py` | Fresh install, bounded credential and identity creation | Writes new state only |
| `scripts/backup_service.py` | Stopped-state encrypted archive and reference validation | Scoped Docker read/stop/start and backup output |
| `scripts/reconfigure_service.py` | Exclusive lock, checkpoint, scoped apply and best-effort rollback | Managed config and scoped Docker reconciliation |
| `scripts/cooling-stack.py` | CLI options and orchestration | Dispatches to the above modules |
| `scripts/build-cooling-release.py` | Release allowlist and identical tar/deb payloads | Writes release artifacts only |
| `portal/modular.*` | Independent authenticated read-only portal | Serves scoped feature/diagnostics responses |
| `monitoring/diagnostics/app.py` | Fixed read-only metrics probes, including modular mode | Queries internal VictoriaMetrics only |

The standalone monitoring project uses a single shared VictoriaMetrics,
Grafana and HTTPS gateway, with conditional telemetry, host exporter,
diagnostics and Portal components. This is feature composition, not
a separate hand-maintained Compose file for every possible combination.

## Rules for future features

1. Extend the catalog and its validation, but **never** infer that a
   previously untested component is automatically deployable.
2. Keep dependency resolution side-effect-free and deterministic.
   Dependency suggestions do not install services.
3. Implement independent service configuration in a new module or
   a small focused generator function. Avoid embedding application-specific
   deployment logic inside argument parsing.
4. Every new external listener, secret, Docker volume, capability, bind
   mount or host integration needs an explicit threat-model review.
   Use private networks and read-only mounts by default.
5. Never edit the user's full-household Production Compose or overwrite
   its secrets as a side effect of running modular CI.
6. Every new component needs tests covering selected-only, absent,
   combined, duplicate dependency and invalid selection scenarios.
7. Every mutable lifecycle action must have a read-only plan, typed
   confirmation, preflight, rollback strategy, audit-friendly local
   checkpoint and explicit data retention semantics.
8. Never invoke `docker compose down -v`, `docker volume prune`,
   host firewall/DNS migration or a network reroute in general package
   installation or component-removal paths.
9. Never rely on package post-install scripts to start or modify the
   monitoring service. Debian installation is code-only.
10. Require a scoped CI end-to-end test (including an actual failure
    scenario) for lifecycle changes. Physical Pi and Windows MSI tests
    are independent release gates.

We currently keep these small Python modules under `scripts/` to share
the existing repository tooling. As usage expands, moving them to an
importable `homeaway` Python package with a conventional `pyproject.toml`
and console entry point may be preferable. The existing flat layout is
a pragmatic prototype, not a claim that all packaging concerns are solved.

## Current constraints

- Component add/remove is intentionally limited to optional monitoring
  services; changing the cooling sender's identity is disabled.
- Encrypted full-state archives can be generated, but safe automated
  restoration is still future work.
- Rollback may require manual assistance if the Docker daemon fails or a
  future image changes its database schema.
- The Portal has its own authentication boundary instead of assuming
  Authelia/LLDAP exists; account integration remains a separate module.
- The project's first full 14-container Pi configuration is a future
  compatibility target, not a source of implicit dependencies.
