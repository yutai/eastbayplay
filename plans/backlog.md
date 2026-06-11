# Backlog — v2 ideas (not planned, not committed)

Revisit only after v1 (Buckets 1–4) has been used for a few weekends and
still feels worth investing in.

- **Playgrounds** (original idea, deferred per D1): a curated list of
  favorite playgrounds, possibly paired with nearby pools for combined
  outings. Likely hand-curated data, no scraping — playground "hours" barely
  change. Decide what problem it actually solves before building.
- **Weekly planning grid:** pools × days matrix view for picking a day in
  advance (complement to the "today" list).
- **Admission info:** price, whether babies need swim diapers, parking notes
  — hand-curated fields on `pools.json`, shown on the detail view.
- **Map view:** only if list sorting ever feels insufficient.
- **Coverage extension:** El Cerrito Swim Center / Richmond Plunge (just
  north of Albany — came up during planning as nearby options).
- **Schedule-change notifications:** the Action already knows the diff;
  could email/notify when a favorite pool's rec hours change.
- **Manual override file:** `data/overrides.json` for pools the scraper
  can't handle reliably (flagged during Bucket 3) — hand-maintained entries
  that win over scraped data.
