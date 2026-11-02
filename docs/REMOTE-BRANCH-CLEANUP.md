# Safe remote branch housekeeping

HomeAway maintains GitHub branch history across three repositories:
\`HomeAwayFromHome\`, \`SensorDash\`, and \`walnut-loop-lab\`.

Use the **read-only** branch cleanup script from a trusted machine that has
Git SSH credentials (normally the Pi). It calls the GitHub REST API to confirm
which same-repository PRs are **merged** and whether the matching remote branch
tip is *exactly* the PR head recorded by GitHub. It does **not** select:
\`Production\`, protected refs, open-PR branches, updated-after-merge branches,
unmerged/closed PRs, or historical branches with no matching merged PR.
It is intentionally conservative about squash merges.

\`\`\`bash
cd ~/HomeAwayFromHome
python3 scripts/cleanup-merged-pr-branches.py --repo HomeAwayFromHome
python3 scripts/cleanup-merged-pr-branches.py --repo SensorDash
python3 scripts/cleanup-merged-pr-branches.py --repo walnut-loop-lab
\`\`\`

Only after reviewing each plan, you may apply **one repository at a time**:

\`\`\`bash
python3 scripts/cleanup-merged-pr-branches.py --repo HomeAwayFromHome --apply
\`\`\`

\`--apply\` requires a typed confirmation with repository and candidate count.
It rechecks heads through \`git ls-remote\` over SSH and deletes each branch
using an **explicit expected-SHA Git lease**, so a branch changed since audit
will not be removed. Failures preserve the branch and are reported.

The GitHub API is readable without a token for public repositories. For a
private repo or to avoid rate limits, set \`GITHUB_TOKEN\` securely in the
operator environment; **never paste a token into a chat, shell history or
commit**. Git pushes authenticate through existing SSH configuration. Running
the script does not touch containers, the Pi's local branches or worktrees,
DNS, VPN or Windows SensorDash.

Keep the still-open SensorDash LAN/mobile PR branch until separately reviewed.
Non-PR historical validation branches and any divergent work remain for a
separate archival decision; do not bulk-delete them using pattern matching.
