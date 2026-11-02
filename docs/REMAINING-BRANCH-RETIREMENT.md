# Remaining remote branch retirement — 2026-11-02

Goal: reduce HomeAwayFromHome and SensorDash to only the Production branch,
without losing historical Git commits or merging untested features.

## Audit results

- HomeAwayFromHome: 5 remaining non-Production branches. Three are old
  documentation/planning branches, one is the closed/unmerged Pi metrics
  diagnostic, and one is an already-merged cleanup branch.
- SensorDash: 19 \`validation/*\` branches retain divergent historical build
  and test development history. Do not infer their unique commits are redundant.
- SensorDash \`feature/lan-mobile-dashboard\` is **excluded**. PR #12 is still
  open, with 14 commits ahead and 108 behind Production at the audit. The PR
  explicitly says *do not merge* and requires separate owner disposition.

All other listed branches are pinned by exact audited SHA in
\`scripts/retire-legacy-branches-20261102.json\`.

## Plan and archival deletion

Use an ordinary login with GitHub SSH access. The read-only preview uses
\`git ls-remote\` exclusively. It does not call GitHub REST API.

```bash
cd ~/HomeAwayFromHome
git fetch origin
git switch Production
git merge --ff-only origin/Production

python3 scripts/retire-legacy-branches.py --repo HomeAwayFromHome
python3 scripts/retire-legacy-branches.py --repo SensorDash
```

Review all eligible and preserved refs. Apply separately, with interactive
typed confirmation:

```bash
python3 scripts/retire-legacy-branches.py --repo HomeAwayFromHome --apply
python3 scripts/retire-legacy-branches.py --repo SensorDash --apply
```

Apply makes a temporary bare clone, pushes one **lightweight archive tag per
retired branch**, verifies that GitHub hosts the tag at the reviewed SHA, then
deletes only that exact branch with \`--force-with-lease\`. If any branch has
moved, it is excluded or the operation stops. If deletion fails after a tag
was created, that branch stays intact; retrying will reuse a matching tag.
Running this script never changes services, Windows fan control, Pi-hole,
WireGuard, TLS, or the Pi's checked-out Production tree.

Archived history can be reached with:

```bash
git fetch origin --tags
git show archive/pre-prod-only-20261102/validation/chart-dates-20261029
```

## Last remaining blocker to Production-only

Once the 24 pinned historical branches are archived/deleted, the sole
remaining non-Production branch should be SensorDash
\`feature/lan-mobile-dashboard\` — **and the feature branch used to prepare
this very cleanup PR**, if GitHub has not removed it automatically.

For the SensorDash PR #12 branch:
1. Decide whether to defer this feature (recommended, since network security
   and live phone testing were explicitly left pending).
2. If deferring, preserve its exact branch SHA as a new remote archival tag,
   verify the tag, **close PR #12 unmerged**, then delete the head branch using
   a checked \`--force-with-lease\` deletion.
3. Do not merge solely to reduce branch count.

For the cleanup PR's own branch, delete it after merge using a current-SHA
lease or GitHub's merged-branch cleanup UI. Both should be handled separately
from the 24 pinned historical branches.

Protect Production (CI required, force pushes/deletion blocked) after final
cleanup. Never enable a rule that interferes with normal reviewed merges.
