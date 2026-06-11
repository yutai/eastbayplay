# Bucket 5 — Local browser scraping + extraction fixes

**Goal:** Make the blocked/JS-rendered pools scrapeable by running the scraper
locally (residential IP + headless Chrome), and fix the two extraction-quality
issues found in the first live CI run.

Context from the first live run (commit `1e3671f`):
- 5 Oakland + 2 Alameda pools: HTTP 403 from Actions runners (datacenter-IP
  bot blocking). Likely fine from a residential IP, possibly without Chrome.
- San Leandro ×2, Piedmont, Albany: fetch 200 but the schedule isn't in the
  raw HTML (JS-rendered / ActiveNet calendar). Needs a real browser.
- Berkeley King: legitimate 43-session schedule rejected by the 40-session cap
  (West Campus legitimately has 37 — the cap is too tight).
- Berkeley West Campus: model accepted the *expired* Spring PDF (range ended
  June 7) with high confidence.

## Design

### 1. Per-source render flag in `pools.json`

Add optional `"render": true` to entries in `schedule_sources` for sources
known to need a real browser (Albany ActiveNet; add San Leandro / Piedmont /
Albany based on judgment — they fetched 200 but yielded nothing). Schema
update in `data/SCHEMA.md`.

### 2. `--render` mode in the scraper (local-only)

- New flag `--render` on `scrape.py`. When set, sources with `"render": true`
  are fetched via **Playwright chromium** (headless): goto URL, wait for
  network idle (with a hard timeout ~30s), take `page.content()`, then strip
  to text exactly like the html path. Import playwright lazily **inside** the
  render path so the module works without it installed.
- Without `--render` (i.e., in CI), render-marked sources are **skipped** with
  a log line. If ALL of a pool's sources are skipped, the pool's previous
  entry is left **untouched** (outcome `SKIPPED` in the report) — NOT marked
  stale, since a local run is the designated owner of those pools.
- Playwright goes in a separate `scraper/requirements-local.txt`
  (`-r requirements.txt` + `playwright`); CI requirements unchanged. README
  documents `playwright install chromium`.

### 3. Stop re-staling locally-scraped pools from CI

Current behavior: every CI fetch failure (the weekly Oakland 403s) flips the
pool to `stale` even if a local run verified it yesterday. Change: on fetch
failure / rejected extraction, mark `stale` **only if `last_verified` is more
than 14 days old**; otherwise keep the previous entry and its status, with
outcome `KEPT (recently verified)` in the report. Keep-previous-data behavior
is unchanged — this only affects the status flag.

### 4. Extraction-quality fixes

- `MAX_SESSIONS`: 40 → **60**.
- Add `schedule_valid_through: str | None` ("YYYY-MM-DD" if the source states
  an end date) to the `PoolSchedule` Pydantic model. Prompt: extract it when
  present; merge logic: if `schedule_valid_through` < today, **reject** as
  expired (keep previous per the safety net). Also strengthen the prompt:
  "If the only schedule on the page has already ended, return empty sessions
  with confidence low — do not extract an expired schedule."

### 5. Owner's local-run checklist (README section "Local scraping")

```
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

## Testing (in the sandbox — no residential IP, maybe no Chromium)

- Unit tests (extend `scripts/test_scraper.py`): expired
  `schedule_valid_through` rejected; 43 sessions now accepted, >60 rejected;
  fetch failure with recent `last_verified` keeps status `ok`; fetch failure
  with old `last_verified` marks `stale`; render-marked sources skipped
  without `--render`, and all-sources-skipped leaves the entry untouched.
- Render path: try `pip install playwright && playwright install chromium`;
  if the sandbox allows it, smoke-test rendering against a local test HTML
  file with JS-injected content (`file://` or a local http server). If
  install fails, mock the playwright call and note it untested.
- Everything existing must stay green: `validate.py`, `test_scraper.py`,
  `test_app.mjs`.

## Acceptance criteria

- [ ] CI behavior unchanged for non-render sources; render sources skipped cleanly with `SKIPPED`/`KEPT` reporting
- [ ] `--render` works (live-tested if Chromium is installable in the sandbox, else mocked + flagged for the owner's first local run)
- [ ] Cap at 60; expired-schedule rejection implemented and unit-tested
- [ ] Re-staling fix unit-tested (recent `last_verified` survives a CI fetch failure)
- [ ] README "Local scraping" section with the exact checklist
- [ ] All existing test suites pass

## Checkpoint for owner review

Present: what changed, test results, which parts remain unverified until the
owner's first local run. The real acceptance test happens on the owner's
machine: run the checklist, see how many of the 12 failing pools now extract.
