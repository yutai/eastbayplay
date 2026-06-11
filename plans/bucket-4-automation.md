# Bucket 4 — Weekly automation via GitHub Actions

**Goal:** The scraper runs weekly (and on demand), commits fresh
`schedules.json` to `main`, and the Pages site updates automatically.
After this bucket the system is hands-off.

Depends on: Bucket 3 (scraper works manually).

## Workflow: `.github/workflows/scrape.yml`

- **Triggers:** `schedule:` weekly — Friday ~6 AM Pacific (`0 13 * * 5` UTC;
  note PDT/PST drift is fine for this use) so weekend data is fresh; plus
  `workflow_dispatch:` for manual runs.
- **Steps:**
  1. Checkout, setup Python 3.11, `pip install -r scraper/requirements.txt`.
  2. `python scraper/scrape.py` with `ANTHROPIC_API_KEY` from repo secrets.
  3. `python scripts/validate.py` — fail the job if invalid (don't commit).
  4. If `data/schedules.json` changed: commit with the diff report as the
     commit message body (e.g. `Update schedules: 3 updated, 1 stale`), push
     to `main`. Use the default `GITHUB_TOKEN` with
     `permissions: contents: write`; no PR — direct commit is fine for a
     2-person data repo.
  5. If nothing changed: exit green without committing.
- **Failure behavior:** if the scrape job fails, GitHub emails the owner
  (default notification). The site keeps serving the last good data —
  acceptable by design.
- Add `concurrency:` group so a manual run can't race the cron.

## Repo setup (owner does once, documented in README)

- Settings → Secrets and variables → Actions → add `ANTHROPIC_API_KEY`.
- Confirm Pages is serving from `main` (done in Bucket 2).
- Settings → Actions → General → Workflow permissions: Read and write
  (or rely on the workflow-level `permissions:` block — verify it suffices).

## Staleness surfacing (small site tweak)

In `app.js`: if `generated_at` is older than 14 days, show a banner
("Schedules haven't been refreshed since &lt;date&gt;") — catches a silently
disabled/failing workflow (GitHub disables cron on inactive repos after
60 days; a banner is our backstop).

## Acceptance criteria

- [ ] `workflow_dispatch` run completes end-to-end: scrape → validate → commit → Pages redeploys with new `generated_at`
- [ ] A run with no schedule changes exits green with no commit
- [ ] A forced validation failure does NOT commit
- [ ] Commit messages contain the per-pool diff report
- [ ] Stale-data banner appears when `generated_at` is artificially backdated
- [ ] README documents the secret + permissions setup

## Checkpoint for owner review

Present: link to a successful Actions run, the resulting commit, and the
live site showing the new `generated_at`. Owner adds the API key secret
before this bucket's implementation session if not already done.

After this checkpoint: v1 is done. Revisit `plans/backlog.md` for v2.
