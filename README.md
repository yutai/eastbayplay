# East Bay Play — Pool Rec-Swim Finder

A mobile-first single-page app that answers one question fast: **which East Bay pools have recreational (non-lap) swim today, and when?** It covers public pools from San Leandro to Albany (San Leandro, Alameda, Oakland, Piedmont, Emeryville, Berkeley, Albany) and shows an "open now / later today / no rec today" badge for each pool, sorted so the most useful options are always at the top. Tap any card to expand the full weekly schedule, with rec sessions prominently shown and lap/other sessions de-emphasized. Schedule data lives in `data/schedules.json` and is refreshed weekly via a GitHub Action; the page always renders the last good snapshot, with a `last_verified` date shown per pool so you can tell when data is fresh.

## GitHub Pages setup (one-time)

1. Push this repo to GitHub (if not already done).
2. Go to **Settings → Pages** in the repository.
3. Under **Source**, select **Deploy from a branch**.
4. Choose branch **`main`** and folder **`/ (root)`**, then click **Save**.
5. After a minute or two, the site will be live at `https://<your-username>.github.io/eastbayplay/`.

No build step or Actions workflow is needed for the site itself — GitHub Pages serves the static files directly from the repo root.

## Local development

```bash
cd eastbayplay
python3 -m http.server 8000
# open http://localhost:8000
```

To test time-override logic (useful for verifying open-now badges without waiting for the right time of day):

```
http://localhost:8000/?now=2026-06-13T14:00
```

The `now` parameter is interpreted as America/Los_Angeles wall time.

## Automation

The scraper runs automatically via GitHub Actions once the feature branch is merged to `main`. Until then the workflow file is in the repo but the cron/Pages integration activates only on `main`.

### Weekly scrape workflow (`.github/workflows/scrape.yml`)

- **Schedule:** Every Friday at ~6 AM Pacific (`0 13 * * 5` UTC). Also triggerable manually via **Actions → Scrape Pool Schedules → Run workflow**.
- **What it does:**
  1. Checks out the repo and installs scraper dependencies.
  2. Runs `python scraper/scrape.py`, capturing the per-pool diff report.
  3. Runs `python3 scripts/validate.py` — if validation fails, the job fails and nothing is committed.
  4. If `data/schedules.json` changed, commits it to `main` (with the diff report in the commit message body) and pushes. If nothing changed, the job exits green with no commit.
- **Concurrency:** A concurrency group (`scrape`) prevents a cron run and a manual run from racing each other.

### Required setup (one-time, done by repo owner)

1. **API key secret:** Settings → Secrets and variables → Actions → New repository secret → Name: `ANTHROPIC_API_KEY`, Value: your Anthropic API key. The scraper uses Claude Haiku (`claude-haiku-4-5`) for schedule extraction.
2. **Workflow permissions:** The workflow declares `permissions: contents: write` at the workflow level. This is sufficient for the default `GITHUB_TOKEN` to push commits. No additional repository-level settings change is required (the workflow-level declaration takes precedence).
3. **GitHub Pages:** Already configured in Bucket 2 to serve from `main` branch root. Pages redeploys automatically within ~1 minute of each commit to `main`.

### Failure behavior

- If the scrape or extraction fails, the job fails and GitHub sends an email notification to the repository owner (default GitHub behavior).
- The site keeps serving the last good `data/schedules.json` — stale data is always better than no data.
- If the workflow fails or is skipped repeatedly and `generated_at` falls more than 14 days behind today, the site shows a yellow banner: **"Schedules haven't been refreshed since \<date\>"** — this is the user-visible backstop.
- GitHub automatically disables cron workflows after 60 days of repository inactivity. The stale-data banner catches this case.

## Local scraping

Some pools use JavaScript-rendered pages (ActiveNet calendars, Squarespace
sites) that CI cannot reach without a real browser. Run this checklist on your
residential machine to pick up those pools:

```bash
git clone git@github.com:yutai/eastbayplay.git && cd eastbayplay
python3 -m venv .venv && source .venv/bin/activate
pip install -r scraper/requirements-local.txt
playwright install chromium
export ANTHROPIC_API_KEY=sk-ant-...
git pull                       # always pull first (CI commits weekly)
python scraper/scrape.py --render --dry-run   # review the report
python scraper/scrape.py --render             # write for real
git add data/schedules.json && git commit -m "Local scrape" && git push
```

Pools that require `--render` (marked `render: true` in `data/pools.json`):
- San Leandro (Farrelly Pool, Family Aquatic Center) — city rec site is JS-rendered
- Piedmont Community Pool — page content loads via JS
- Albany Aquatic Center — albanyaquaticcenter.com pool-schedule page + ActiveNet calendar

Pools that do **not** need `--render` (blocked by datacenter IP in CI, but fine from home):
- Oakland (deFremery, Fremont, Lions, Temescal, Larry Reid) — plain HTML, blocked by IP in CI
- Alameda (Emma Hood, Encinal) — plain HTML, blocked by IP in CI
- Berkeley (King Pool, West Campus Pool) — plain HTML + PDF, blocked by IP in CI

## Running tests

```bash
node scripts/test_app.mjs
```

## Structure

```
index.html          single-page app shell
app.js              fetch + render + time logic (no dependencies)
style.css           mobile-first styles
data/
  pools.json        hand-curated pool registry
  schedules.json    swim session data (updated by scraper)
  SCHEMA.md         data schema documentation
scripts/
  validate.py       validates schedules.json against schema
  test_app.mjs      unit tests for time/badge logic
plans/              implementation bucket specs
```
