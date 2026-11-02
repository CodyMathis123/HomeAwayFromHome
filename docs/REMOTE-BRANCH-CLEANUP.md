# Safe remote branch cleanup

The Pi's unauthenticated GitHub REST requests can hit the API rate limit and
report a generic HTTPError. **This does not indicate a branch-deletion attempt.**

Use the reviewed GitHub snapshot committed alongside the cleanup helper.
It includes only exact branch heads whose corresponding same-repository PR
was confirmed merged when the snapshot was generated (2026-11-02).
No GitHub API token, package, or credential setup is required.

From a trusted machine with GitHub SSH access:

```bash
cd ~/HomeAwayFromHome
git fetch origin
git switch Production
git merge --ff-only origin/Production

python3 scripts/cleanup-merged-pr-branches.py --repo HomeAwayFromHome
python3 scripts/cleanup-merged-pr-branches.py --repo SensorDash
python3 scripts/cleanup-merged-pr-branches.py --repo walnut-loop-lab
```

The preview reads the saved eligibility snapshot **and** calls
\`git ls-remote --heads\` over the existing SSH connection to check each
branch SHA against the actual remote tip. It reports new, missing, moved,
unmerged, protected, and open-PR branches as excluded. A snapshot that
becomes stale is *conservative*: it never selects additional branches.

Only after reviewing the preview, run \`--apply\` for one repo at a time:

```bash
python3 scripts/cleanup-merged-pr-branches.py --repo HomeAwayFromHome --apply
```

Apply requires an exact typed confirmation. It rechecks all remote refs,
then uses a per-ref expected-SHA Git lease to reject concurrent changes.
Do not attempt to bulk-delete historical SensorDash validation branches or
the still-open LAN/mobile dashboard PR #12. Those need separate review.
This snapshot selects *merged-PR branch tips only*, not other branches that
happen to be ancestors of Production.

For a fresh optional live GitHub API review, use \`--live-api\`; it may need
a separately configured \`GITHUB_TOKEN\` or a wait for rate limit reset.
Avoid pasting tokens into chat, files, or shell history.

The script never starts/stops services, edits local branches, or modifies
Pi-hole, WireGuard, Authelia, Grafana, or SensorDash fan control.
