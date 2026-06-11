# Bucket 3 — LLM scraper (run locally/manually)

**Goal:** A Python pipeline that fetches each pool's schedule sources,
extracts structured swim sessions with Claude Haiku, validates the result,
and writes `data/schedules.json`. Runs from a laptop or manually in CI;
the weekly cron comes in Bucket 4.

Depends on: Bucket 1 (registry + schema), Bucket 2 (so output is visible).

## Stack

- Python 3.11+, `scraper/` directory.
- Deps (in `scraper/requirements.txt`): `anthropic`, `httpx`, `pydantic`,
  `pypdf` (PDF text extraction).
- Model: **`claude-haiku-4-5`** ($1/$5 per MTok — a full run over ~12 pools
  is well under $0.05). Auth via `ANTHROPIC_API_KEY` env var.

## Pipeline (per pool, from `pools.json` → `schedule_sources`)

### 1. Fetch

- `httpx` with a normal browser User-Agent, 30s timeout, 2 retries.
- `type: "html"` → strip to readable text: drop `<script>/<style>/<nav>`,
  collapse whitespace. Keep it simple — Haiku handles messy text; the goal
  is just to cut token count, not to parse.
- `type: "pdf"` → extract text with `pypdf`.
- Concatenate a pool's sources into one document (with source-URL headers).
- Truncate to ~50k characters; if truncated, log a warning.
- Fetch failure for all sources → mark pool `status: "stale"`, **keep the
  previous schedule entry verbatim**, continue with next pool.

### 2. Extract with Claude

Use the Python SDK's structured outputs (`client.messages.parse` with a
Pydantic model) so output is guaranteed-valid JSON:

```python
class Session(BaseModel):
    day: Literal["monday", ..., "sunday"]
    start: str  # "HH:MM" 24h
    end: str
    label: str  # verbatim text from the source
    category: Literal["rec", "lap", "other"]

class PoolSchedule(BaseModel):
    sessions: list[Session]
    season_note: str | None
    pool_appears_closed: bool
    extraction_confidence: Literal["high", "medium", "low"]
```

Prompt essentials (system prompt, keep stable for prompt caching across the
per-pool calls):
- Today's date and the pool's name/city (so the model picks the *current*
  season's schedule when a page lists several).
- Category rules verbatim from `data/SCHEMA.md`: `rec` = any swim a baby/
  family can attend (recreational, open, family, parent-tot); `lap` = lap
  only; `other` = lessons/aerobics/club/rentals.
- Expand recurrences ("M/W/F", "weekdays") into one session per day.
- If the page shows no current schedule, return empty sessions +
  `extraction_confidence: "low"` — never guess.

One API call per pool. `max_tokens` 4096 is plenty.

### 3. Validate & merge (this is the safety net — don't skimp)

For each extraction, run `scripts/validate.py` rules plus plausibility:
- Times valid, `start < end`, sessions within 05:00–22:00.
- Reject and keep previous data (mark `stale`) when: zero sessions where
  the previous run had >0 (unless `pool_appears_closed`), confidence `low`,
  or >40 sessions (probable hallucinated expansion).
- `pool_appears_closed: true` → `status: "closed"`, empty sessions.
- Accepted pools get `status: "ok"`, `last_verified: <today>`.

Merge into the existing `schedules.json` (never start from empty — a pool
missing from this run keeps its old entry), update `generated_at`, write.

### 4. Diff report

Print a human-readable summary to stdout: per pool — unchanged / updated
(with session-count delta) / stale / closed / failed. Bucket 4 puts this in
the commit message.

## CLI

```
python scraper/scrape.py            # all pools
python scraper/scrape.py --pool oakland-fremont
python scraper/scrape.py --dry-run  # extract + report, don't write
```

## Testing

- Save 2–3 fetched pages as fixtures in `scraper/fixtures/`; a
  `--from-fixtures` flag runs extraction against them (still calls the API,
  but reproducible inputs) so we can eyeball categorization quality.
- Run the full pipeline once for real; diff the result against Bucket 1's
  hand-seeded data for those 5 pools. Disagreements are the key quality
  signal — investigate each one (was the hand-seed wrong, or the model?).

## Acceptance criteria

- [ ] Full run completes even when some pools fail to fetch
- [ ] Output passes `scripts/validate.py`
- [ ] Extracted schedules for the 5 hand-seeded pools match reality (manual spot-check against the websites; note any model/categorization errors)
- [ ] Safety net verified: simulate a fetch failure and an empty extraction; confirm previous data is kept and marked `stale`
- [ ] Cost per full run logged (expect < $0.05)

## Checkpoint for owner review

Present: the diff between hand-seeded and scraped data, the per-pool status
report, any pools where extraction is unreliable (candidates for a
hand-maintained override), and measured cost per run.
