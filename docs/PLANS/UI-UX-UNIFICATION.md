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

## Shared design language across separate services

The portal and authenticated control API intentionally remain distinct containers: the portal is **read-only**, while `/control/` is protected by Authelia and server-side capabilities. Caddy makes them appear under one private HTTPS hostname; separate containers do **not** require inconsistent visual designs.

The shared CSS `portal/theme.css` is the common source for HomeAway's colors, page width, top branding, sidebar, content typography, card surfaces, buttons and mobile breakpoints. The existing same-origin Caddy routing serves `/theme.css` from the read-only portal on both overview/architecture and authenticated `/control/`. Each application still serves its own local CSS **before** the common theme as a fallback; this prevents a portal outage or network error from breaking an otherwise working PC-control page. To change the common appearance, edit `portal/theme.css` rather than independently adjusting the two frontends.

The shared stylesheet is a static asset, not a shared authentication context, API proxy, dynamic script, or Docker mount. Caddy continues to authorize `/control/` without exposing privileged operations to `/`. Full-page navigation across the two frontends still performs a document load; matched styling reduces visual discontinuity, but a future genuine single-page shell would need a separately reviewed frontend/router architecture with strict server-side authorization.

## Step 1 of single-frontend migration: persistent read-only views

The Portal now has one persistent browser document for **Overview** and
**Architecture** on the same origin. `portal/router.js` intercepts only these
two known read-only routes; the sidebar is preserved while each page's `main`
is cached in one content slot, with the inactive view hidden and inert. Browser
history, Back/Forward, normal same-page fragments and direct-link refreshes
remain supported. The Overview view pauses its service-status requests when
inactive; a return to it refreshes status without reconstructing the view
or needlessly discarding its prior content.

The same read-only `portal/navigation.js` now renders the left navigation for
both the portal and authenticated `/control/` page. The latter retains a static
role-hidden fallback menu and local CSS, so a temporary portal failure does not
break signed-in power controls. Only `control/app.js` may reveal administrator
links after receiving fresh `/control/api/me` permissions. The Caddy
`forward_auth` and separate backend capabilities remain authoritative.

**Still separate, intentionally:** Entering `/control/` is a full document
navigation through Authelia. The browser must not fetch privileged control
HTML or make power commands merely to keep the public read-only shell alive.
This PR does not claim a complete single-page frontend across authenticated
management. A subsequent reviewed phase can introduce a dedicated frontend
for private pages with separately authenticated APIs, but only after a
cross-origin/login-redirect, asset lifecycle, and fail-closed authorization
test plan. No new Caddy routes, exposed ports or Docker capabilities are
introduced in this step.

## Step 2: focused authenticated management views

The existing `/control/` document now renders **Power Controls, Computer
Management, Users & Permissions and Activity** as distinct, fragment-addressable
views (`#hosts`, `#computer-admin`, `#accounts`, `#recent-activity`).
The authenticated document, shared HomeAway navigation, and header remain
mounted while switching views; browser Back/Forward uses normal hash history.
Both direct links and initial sign-in return to the selected view when
authorized. Each section stays mounted, preserving unsaved input and current
inventory/role selection while navigating among views.

View changes are **not authorization**. Admin views remain hidden unless the
live `/control/api/me` capabilities allow them, and all account/computer
operations remain protected by Caddy's existing Authelia forward-auth plus
backend permission checks. A denied/revoked admin deep link falls back to
`#hosts`; access is never inferred from hash or nav markup. If JavaScript
is unavailable the original control document layout remains as a fallback,
with privileged sections still marked hidden.

A single persistent browser document for the entire HomeAway application
is **not implemented** yet: read-only portal views and authenticated control
are still separate documents. This preserves sign-in redirects, deny-by-default
routes and independence from read-only portal availability. Consolidating the
two documents requires separate auth/redirect and privacy review; it is not
safe to embed `/control/` into the unprivileged portal by bypassing
`X-Frame-Options` or to fetch privileged data for anonymous visitors.

## First Phase 3 evidence in the architecture explorer

The existing read-only `/architecture/` view gains three explicit live status
observations powered only by the **existing** `GET /api/status` endpoint.
There is no new probe process, privileged control API, Docker/host inventory
access, separate monitoring database, or background polling loop.

- **Pi-hole:** current response to the portal's DNS test query. A successful
  query does not prove that each household client uses that DNS resolver.
- **WireGuard:** reachability of wg-easy's private TCP management listener.
  It does *not* prove any client is connected or a tunnel has handshaken.
- **Grafana:** availability of its TCP listener. It may legitimately be stopped
  because the metrics profile is optional; success does not prove dashboards
  or sensor export are current.

The page refreshes when opened or returned to, and via an explicit Refresh
button; it does **not** add a high-frequency poller. Missing/failed status API
responses are displayed as **Unknown**, never colored as confirmed outages.
The architecture SVG remains static and conceptual. We intentionally do not
assign live status to components lacking a grounded probe: Authelia, LLDAP,
PC power state, Windows SensorDash, inventory writer, VictoriaMetrics ingestion
freshness, certificate trust, remote DNS/VPN or backup restoration.

This is a small observation-only foundation for Phase 3 S1. The planned
authenticated admin setup/readiness checks, privileged configuration broker,
and real end-to-end verification remain **unimplemented**.

## UX audit plan, in small PRs

1. **Portal shell and architecture map (implemented)** — responsive desktop left rail / mobile horizontal links, route `/architecture/`, service descriptions and navigation; preserve the current `/control/` boundary. Verify all static pages return correct CSP and HTTP 405 for mutation methods.
2. **Authenticated `/control/` shell parity (this PR)** — use the same left-rail / phone navigation to link back to Overview, Architecture and metrics, and keep management links tied to fresh server-reported capabilities. Failed identity lookups hide privileged UI; failed reachability refreshes mark old host results as stale. Do not share authentication state with the public portal or iframe admin pages; no duplicate user inventory.
3. **Interaction and status review** — loading/empty/error feedback, action-requested vs action-completed semantics, screen-reader labels, keyboard focus, Safari phone layout, touch hit targets and scroll position. Maintain readable tables; no new high-frequency sensor polling.
4. **Phase 3 integration** — once its read-only diagnostics schema is implemented, display verified readiness/health badges without claiming DNS probe = functioning VPN, SSH port = PC power state, or blank Grafana chart = failed Windows host. Phase 4 may later add an authenticated Operations area with independently reviewed brokers.

## Acceptance before deployment

- `Production` unchanged until CI and code review; no automatic Pi deployment.
- New public portal routes handle only GET; `POST`/write verbs return 405; the container remains non-root/read-only and never mounts `docker.sock`, credentials, active PC inventory or SSH key material.
- No router/DNS/VPN/Grafana/Authelia configuration changes.
- Mobile, keyboard, external link destinations and role-specific `/control/` access are manually checked after the targeted `portal` rebuild. The browser diagram is a directory and documentation tool, not a replacement for Grafana.
