"""The shipped pc-control image must include every referenced dashboard script.

The UI previously referenced accounts.js but the Dockerfile omitted it; browser
404 then prevented the feature-gated Users section from being displayed.
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
API = ROOT / "control/api"


class AccountAssets(unittest.TestCase):
    def test_dashboard_scripts_are_packaged_in_control_image(self):
        html = (API / "index.html").read_text()
        docker = (API / "Dockerfile").read_text()
        app = (API / "app.py").read_text()
        copy_line = next(
            (line for line in docker.splitlines()
             if line.startswith("COPY ") and line.endswith(" ./")),
            None)
        self.assertIsNotNone(copy_line)
        packaged = set(copy_line.split()[1:-1])
        self.assertIn("index.html", packaged)
        for script in re.findall(r'<script[^>]+src="/control/([^"]+)"', html):
            with self.subTest(script=script):
                self.assertTrue((API / script).is_file())
                self.assertIn(script, packaged, "Script omitted from Docker image")
                self.assertIn('"/control/' + script + '"', app,
                              "Control HTTP handler must serve packaged script")

    def test_feature_gated_users_markup_and_script_present(self):
        html = (API / "index.html").read_text()
        js = (API / "app.js").read_text()
        account_js = (API / "accounts.js").read_text()
        self.assertIn('id="accounts"', html)
        self.assertIn('src="/control/accounts.js"', html)
        self.assertIn("HomeAwayAccounts.start(identity)", js)
        self.assertIn("manageUsers", account_js)


    def test_pending_user_create_has_explicit_feedback(self):
        js = (API / "accounts.js").read_text()
        self.assertIn('Created pending user "', js)
        self.assertIn("if (created) form.reset()", js,
                      "Do not clear entered account fields after a failed request")
        self.assertIn("const refreshed = await refresh()", js)
        self.assertIn("Change succeeded, but the user list could not refresh", js)
        self.assertIn("Change failed:", js)
        self.assertIn("No HomeAway access · pending or revoked", js)

    def test_control_uses_shared_navigation_with_auth_boundaries_and_fallback(self):
        html = (API / "index.html").read_text()
        portal_nav = (ROOT / "portal/navigation.js").read_text()
        gateway = (ROOT / "control/gateway/Caddyfile").read_text()
        control = (API / "app.js").read_text()
        self.assertIn('src="/navigation.js"', html)
        self.assertLess(html.index('src="/navigation.js"'), html.index('src="/control/app.js"'))
        self.assertIn('id="app-navigation"', html)
        self.assertIn('<nav class="section-nav"', html,
                      "The local control menu must work if the portal is down")
        self.assertIn('id="nav-computers" href="#computer-admin" hidden', html)
        self.assertIn('id="nav-accounts" href="#accounts" hidden', html)
        self.assertIn("isControl ? 'Control page sections'", portal_nav)
        self.assertIn("'nav-computers', true", portal_nav)
        self.assertIn("'nav-accounts', true", portal_nav)
        self.assertIn("updateSectionNavigation(identity.capabilities)", control)
        self.assertIn("forward_auth authelia:9091", gateway)
        self.assertIn("reverse_proxy pc-control:8081", gateway)
        self.assertNotIn("reverse_proxy portal:8080 {", gateway)

    def test_authenticated_pages_use_shared_visual_theme_with_local_fallback(self):
        html = (API / "index.html").read_text()
        css = (API / "styles.css").read_text()
        gateway = (ROOT / "control/gateway/Caddyfile").read_text()
        shared = (ROOT / "portal/theme.css").read_text()
        self.assertIn('href="/control/styles.css"', html)
        self.assertIn('href="/theme.css"', html)
        self.assertLess(html.index('href="/control/styles.css"'), html.index('href="/theme.css"'))
        self.assertIn('class="masthead"', html)
        self.assertIn('class="brand"', html)
        self.assertIn('class="brand-mark"', html)
        self.assertIn('class="ambient" aria-hidden="true"', html)
        self.assertIn('id="identity"', html)
        self.assertIn("handle {", gateway)
        self.assertIn("reverse_proxy portal:8080", gateway)
        # If the portal is unavailable, local CSS still supplies essential UI.
        self.assertIn(".app-layout", css)
        self.assertIn(".app-sidebar", css)
        self.assertIn("--accent:#72e6ce", css)
        self.assertIn("border-radius: var(--radius-card)", shared)
        self.assertNotIn("password", shared.lower())

    def test_unified_control_navigation_and_stale_state(self):
        html = (API / "index.html").read_text()
        js = (API / "app.js").read_text()
        css = (API / "styles.css").read_text()
        self.assertIn('class="app-layout"', html)
        self.assertIn('class="app-sidebar"', html)
        self.assertIn('href="/architecture/"', html)
        self.assertIn('href="/#metrics-heading"', html)
        self.assertIn('href="/#services"', html)
        self.assertIn('id="nav-power" href="#hosts"', html)
        self.assertIn('aria-label="Primary navigation"', html)
        self.assertIn("window.addEventListener('hashchange', () => updateCurrentSection(true))", js)
        self.assertIn("link.setAttribute('aria-current', 'location')", js)
        self.assertIn("identity = null;", js)
        self.assertIn("hosts.dataset.stale = 'true'", js)
        self.assertIn("Previously shown results may be stale", js)
        self.assertIn('.hosts[data-stale="true"]', css)
        self.assertIn('grid-template-columns:228px minmax(0,1fr)', css)
        self.assertIn('position:sticky', css)

    def test_control_views_are_fragment_addressable_and_role_gated(self):
        html = (API / "index.html").read_text()
        js = (API / "app.js").read_text()
        css = (API / "styles.css").read_text()
        gateway = (ROOT / "control/gateway/Caddyfile").read_text()
        for name in ("hosts", "computer-admin", "accounts", "recent-activity"):
            self.assertIn('data-view-section="' + name + '"', html)
            self.assertIn('id="' + name + '"', html)
        self.assertIn('data-view-section="hosts"', html)
        self.assertIn("const VIEWS = new Set", js)
        self.assertIn("main.dataset.activeView = target", js)
        self.assertIn("!capabilities?.manageComputers", js)
        self.assertIn("!capabilities?.manageUsers", js)
        self.assertIn("!capabilities?.history", js)
        self.assertIn('id="nav-activity" href="#recent-activity" hidden', html)
        self.assertIn("'nav-activity', true", (ROOT / "portal/navigation.js").read_text())
        self.assertIn("history.replaceState(history.state, '', '/control/#hosts')", js)
        self.assertIn("window.HomeAwayNavigation?.sync()", js)
        self.assertIn("body.control-views-ready", css)
        self.assertIn("main[data-active-view=", css)
        self.assertIn("forward_auth authelia:9091", gateway)
        self.assertIn("reverse_proxy pc-control:8081", gateway)
        self.assertNotIn("router.js", html)

    def test_setup_readiness_uses_existing_readonly_apis_and_admin_gating(self):
        markup = (API / "index.html").read_text()
        js = (API / "readiness.js").read_text()
        shell = (API / "app.js").read_text()
        shared_nav = (ROOT / "portal/navigation.js").read_text()
        docker = (API / "Dockerfile").read_text()
        handler = (API / "app.py").read_text()
        self.assertIn('id="setup-readiness"', markup)
        self.assertIn('id="nav-readiness" href="#setup-readiness" hidden', markup)
        self.assertIn('src="/control/readiness.js"', markup)
        self.assertIn('readiness.js', docker)
        self.assertIn('"/control/readiness.js": ("readiness.js",', handler)
        for name in ("dns", "wireguard", "monitor", "hosts", "transport", "accounts", "computers"):
            self.assertIn('id="readiness-' + name + '"', markup)
        self.assertIn("const VIEWS = new Set", shell)
        self.assertIn("capabilities?.role !== 'admins'", shell)
        self.assertIn("'nav-readiness', true", shared_nav)
        self.assertIn("HomeAwayReadiness.start(identity)", shell)
        self.assertIn("HomeAwayReadiness.start(null)", shell)
        self.assertIn("HomeAwayReadiness.show()", shell)
        self.assertIn("readJson('/control/api/me')", js)
        self.assertIn("me?.capabilities?.role !== 'admins'", js)
        self.assertIn("readJson('/api/status')", js)
        self.assertIn("readJson('/control/api/hosts')", js)
        self.assertIn("await Promise.all(queries)", js)
        self.assertIn("location.hash = '#hosts'", js)
        self.assertNotIn("fetch('/control/api/admin/", js)
        self.assertNotIn("method: 'POST'", js)
        self.assertNotIn("docker.sock", js)

    def test_phase3_metrics_observer_is_scoped_and_admin_only(self):
        markup = (API / "index.html").read_text()
        js = (API / "readiness.js").read_text()
        backend = (API / "app.py").read_text()
        observer = (ROOT / "monitoring/diagnostics/app.py").read_text()
        overlay = (ROOT / "compose.control.yaml").read_text()
        deployment = (ROOT / "scripts/deploy-diagnostics.sh").read_text()
        for slug in ("pi-scrape", "pi-cpu", "pi-memory", "pi-load",
                     "pi-thermal", "vm-free", "telemetry"):
            self.assertIn('id="readiness-' + slug + '"', markup)
        self.assertIn("readJson('/control/api/admin/diagnostics')", js)
        self.assertIn("metricsStale()", js)
        self.assertIn("markStale(", js)
        self.assertIn("readiness-telemetry-hosts", js)
        self.assertIn('if path == "/control/api/admin/diagnostics":', backend)
        self.assertIn('if role != "admins":', backend)
        self.assertIn('def diagnostics_snapshot() -> dict:', backend)
        self.assertIn('HOMEAWAY_DIAGNOSTICS_URL', overlay)
        self.assertIn('networks: [infra, pc-control]', overlay)
        self.assertIn('profiles: [control]', overlay)
        self.assertIn('No credentials, host state, Docker socket', observer)
        self.assertIn("No client may send a MetricsQL expression", observer)
        self.assertIn("cached_snapshot()", observer)
        self.assertIn("up -d --no-deps --build metrics-diagnostics", deployment)

    def test_certificate_readiness_is_admin_only_and_private(self):
        markup = (API / "index.html").read_text()
        js = (API / "readiness.js").read_text()
        backend = (API / "app.py").read_text()
        observer = (ROOT / "monitoring/diagnostics/certificates.py").read_text()
        docker = (ROOT / "monitoring/diagnostics/Dockerfile").read_text()
        overlay = (ROOT / "compose.control.yaml").read_text()
        for name in ("browser-home", "browser-auth", "browser-metrics", "telemetry-receiver"):
            self.assertIn('id="readiness-cert-' + name + '"', markup)
            self.assertIn('id="readiness-cert-detail-' + name + '"', markup)
        self.assertIn("readJson('/control/api/admin/certificates')", js)
        self.assertIn('if path == "/control/api/admin/certificates":', backend)
        self.assertIn('if role != "admins":', backend)
        self.assertIn("CERT_DIAG_URL", backend)
        self.assertIn("cryptography==50.0.2", docker)
        self.assertIn("certificates.py", docker)
        self.assertIn('GATEWAY_TARGET = ("gateway", 8443)', observer)
        self.assertIn('TELEMETRY_TARGET = ("telemetry-ingest", 8443)', observer)
        self.assertNotIn("subprocess", observer)
        self.assertNotIn("docker.sock", observer)
        self.assertIn("HOMEAWAY_METRICS_DOMAIN:", overlay)

    def test_pending_accounts_require_an_explicit_role_choice(self):
        js = (API / "accounts.js").read_text()
        css = (API / "styles.css").read_text()
        self.assertIn("placeholder.selected = !roleValues.includes(user.role)", js)
        self.assertIn("save.disabled = true", js)
        self.assertIn("save.disabled = !select.value || user.role === select.value", js)
        self.assertIn("if (!select.value || user.role === select.value) return;", js)
        self.assertIn("select.setAttribute('aria-label', 'HomeAway role for '", js)
        self.assertIn("mutationPending = true", js)
        self.assertIn("mutationPending = false", js)
        self.assertIn("createButton.disabled = true", js)
        self.assertIn("createButton.disabled = false", js)
        self.assertIn('#accounts-message[data-state="error"]', css)

    def test_navigation_hides_admin_links_until_capabilities_arrive(self):
        html = (API / "index.html").read_text()
        js = (API / "app.js").read_text()
        css = (API / "styles.css").read_text()
        self.assertIn('aria-label="Control page sections"', html)
        self.assertIn('id="nav-computers" href="#computer-admin" hidden', html)
        self.assertIn('id="nav-accounts" href="#accounts" hidden', html)
        self.assertIn('id="recent-activity"', html)
        self.assertIn("updateSectionNavigation(identity.capabilities)", js)
        self.assertIn("updateSectionNavigation(null)", js)
        self.assertIn("!capabilities?.manageComputers", js)
        self.assertIn("!capabilities?.manageUsers", js)
        self.assertIn(".section-nav [hidden]{display:none}", css)
        self.assertIn("overflow-x:auto", css)


if __name__ == "__main__":
    unittest.main()
