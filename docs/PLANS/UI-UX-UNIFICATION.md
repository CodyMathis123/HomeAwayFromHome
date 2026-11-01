# HomeAway UI/UX unification — read-only shell and navigation

**Design/implementation status (November 1, 2026):** The current `Production` UI has a read-only HomeAway portal (also reachable through Caddy at the private HTTPS root), a separate authenticated `/control/` page for power, accounts and computers, Grafana dashboards, and native Pi-hole/wg-easy administration. This working model should be made coherent **without granting the portal new privileges**. This first PR adds a portal sidebar and interactive architecture explorer; later refinements are separately reviewable.

## Navigation and information architecture

| Destination | HomeAway navigation | Trust and data owner |
| --- | --- | --- |
| Overview | `/` | Read-only `portal` API; DNS response, wg-easy management listener (not VPN handshake), and Grafana TCP checks only |
| At a glance | `/#services` | Existing service cards with genuine external links |
| Architecture | `/architecture/` | Version-controlled static conceptual map; node links to descriptions, then existing tools. No discovery, no secrets, no actions |
| Live metrics | `/#metrics-heading` | Existing single lazily loaded read-only Grafana iframe; Grafana is the owner of graphs and history |
| Computers / Users | Validated `/api/control-config` target → `/control/` | Existing Caddy + Authelia and server-side LLDAP capabilities; never a portal API mutation |
| Pi-hole DNS | Native private `:8080/admin/` | Pi-hole owns DNS, query logs, and settings |
| Grafana | Native private `:3000/` | Grafana owns dashboards, authenticated configuration, and historical queries |
| WireGuard peers | No direct browser link | wg-easy `:51821` is **SSH-tunnel only**; don't link a remote phone to 127.0.0.1 or publish an admin port |
| Future Setup and Operations | Not yet placed in navigation | Phase 3/4 planning only; link when separately implemented and authorized |

### Architecture explorer contract

The map is **read-only** and describes logical system boundaries, not an auto-discovered topology. Its arrows are conceptual—not exact networking, VPN handshakes, PC reachability, or confirmed actions. Interactive SVG nodes jump to a keyboard-accessible component directory; that directory links to available native/authorized screens. Do not assume the presence of a daemon means it is healthy.

- The Windows PC owns SensorDash and local fan/PWM outputs; telemetry is observational and flows into the Pi's authenticated TLS intake, VictoriaMetrics and Grafana. The portal never edits SensorDash cooling settings.
- HomeAway PC control and inventory administration are under `/control/`, with Authelia, LLDAP and the dedicated narrowly privileged writer/host agents. The portal does not call those RPCs.
- The Pi's public readonly overview currently exposes a shared Grafana dashboard by explicit token; navigation must not implicitly expose Grafana admin access.
- SVG and service-directory links are native `<a>` anchors; no drag or command capability. The new page's files are packaged in the existing non-root, read-only portal container and served by the same CSP (no external JS, inline script or privileged browser API).

## UX audit plan, in small PRs

1. **Portal shell and architecture map (this PR)** — responsive desktop left rail / mobile horizontal links, route `/architecture/`, service descriptions and navigation; preserve the current `/control/` boundary. Verify all static pages return correct CSP and HTTP 405 for mutation methods.
2. **Authenticated `/control/` shell parity** — align branding/navigation between the two apps, link back to overview and architecture and retain authenticated per-role section links. Do not share authentication state with the public portal or iframe admin pages; no duplicate user inventory.
3. **Interaction and status review** — loading/empty/error feedback, action-requested vs action-completed semantics, screen-reader labels, keyboard focus, Safari phone layout, touch hit targets and scroll position. Maintain readable tables; no new high-frequency sensor polling.
4. **Phase 3 integration** — once its read-only diagnostics schema is implemented, display verified readiness/health badges without claiming DNS probe = functioning VPN, SSH port = PC power state, or blank Grafana chart = failed Windows host. Phase 4 may later add an authenticated Operations area with independently reviewed brokers.

## Acceptance before deployment

- `Production` unchanged until CI and code review; no automatic Pi deployment.
- New public portal routes handle only GET; `POST`/write verbs return 405; the container remains non-root/read-only and never mounts `docker.sock`, credentials, active PC inventory or SSH key material.
- No router/DNS/VPN/Grafana/Authelia configuration changes.
- Mobile, keyboard, external link destinations and role-specific `/control/` access are manually checked after the targeted `portal` rebuild. The browser diagram is a directory and documentation tool, not a replacement for Grafana.
