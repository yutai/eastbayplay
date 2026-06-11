# Bucket 1 — Data foundation: pool registry, schema, seed data

**Goal:** A complete, hand-verified inventory of public pools from San Leandro
to Albany, a JSON schema for schedules, and one hand-seeded `schedules.json`
so Bucket 2 has real data to render.

No site, no scraper, no LLM in this bucket.

## Tasks

### 1. Research the pool inventory (use web search)

Find every **publicly accessible** pool in: San Leandro, Alameda, Oakland,
Piedmont, Emeryville, Berkeley, Albany. Include city/municipal pools; include
YMCA-style pools only if they offer drop-in rec swim to non-members.

Candidate starting list (verify each — names, status, and URLs change;
some pools close seasonally or for renovation):

- **San Leandro:** San Leandro Family Aquatic Center (Farrelly Pool?)
- **Alameda:** Emma Hood Swim Center, Encinal Swim Center (run by Alameda rec / swim clubs — verify public rec hours exist)
- **Oakland (Oakland Parks, Rec & Youth Development):** Fremont Pool, Lions Pool (Dimond), deFremery Pool, Temescal Pool, East Oakland Sports Center pool, Live Oak Pool (check if open)
- **Piedmont:** Piedmont Community Pool
- **Emeryville:** Emeryville Community Pool (ECCL)
- **Berkeley:** West Campus Pool, King Pool, Willard Pool (reopened?)
- **Albany:** Albany Community Pool

For each pool record: official schedule page URL (the page that actually
lists swim hours — often different from the pool's landing page), plus any
PDF schedule link. This is the most valuable research output: the scraper in
Bucket 3 fetches exactly these URLs.

### 2. Define the schema

Two files:

**`data/pools.json`** — hand-curated, rarely changes:

```json
{
  "pools": [
    {
      "id": "oakland-fremont",
      "name": "Fremont Pool",
      "city": "Oakland",
      "address": "4550 Foothill Blvd, Oakland, CA 94601",
      "indoor": true,
      "operator": "Oakland Parks, Recreation & Youth Development",
      "info_url": "https://...",
      "schedule_sources": [
        {"url": "https://...", "type": "html"}
      ],
      "notes": "Optional free text (e.g. 'closed for renovation until ...')"
    }
  ]
}
```

`schedule_sources` is a list because some pools split schedules across pages
or PDFs. `type` is `"html"` or `"pdf"`.

**`data/schedules.json`** — generated (hand-written in this bucket):

```json
{
  "generated_at": "2026-06-11T00:00:00Z",
  "schedules": {
    "oakland-fremont": {
      "last_verified": "2026-06-11",
      "status": "ok",
      "season_note": "Summer schedule, June 8 – Aug 14",
      "sessions": [
        {
          "day": "saturday",
          "start": "13:00",
          "end": "15:00",
          "label": "Recreational Swim",
          "category": "rec"
        }
      ]
    }
  }
}
```

Rules:
- `day`: lowercase English day name. `start`/`end`: 24h `HH:MM` local time.
- `category`: `"rec"` (anything a baby can attend: rec/open/family/parent-tot)
  | `"lap"` | `"other"` (lessons, water aerobics, club use).
- `status`: `"ok"` | `"stale"` (scrape failed, showing old data) |
  `"closed"` (pool confirmed closed) | `"unknown"`.
- Keep the original `label` text verbatim so we can sanity-check the
  categorization on the site.

### 3. Seed the data

- Fill `data/pools.json` with every pool found in task 1.
- Hand-fill `data/schedules.json` for **at least 5 pools** (prioritize
  Oakland + Berkeley) by reading their schedule pages. Use `category: "rec"`
  judgment per decision D3. Others may be `status: "unknown"` with empty
  sessions.
- Add `data/SCHEMA.md` documenting the two formats (roughly this file's
  section 2, kept current).

### 4. Validation script (small)

`scripts/validate.py` (stdlib only): checks both JSON files parse, every
schedule key exists in `pools.json`, times are valid `HH:MM` with
`start < end`, days/categories/statuses are from the allowed enums.
Exit non-zero on failure. This becomes the CI gate later.

## Acceptance criteria

- [ ] `data/pools.json` lists all public pools San Leandro → Albany with working schedule-source URLs (spot-check each URL resolves)
- [ ] `data/schedules.json` has real, current hours for ≥5 pools
- [ ] `data/SCHEMA.md` exists and matches the actual files
- [ ] `python3 scripts/validate.py` passes
- [ ] No site/scraper code written

## Checkpoint for owner review

Present: the pool list (any pools you didn't expect? any missing?), the
5+ seeded schedules (do the rec-swim categorizations look right?), and any
pools whose websites look hard to scrape (flag for Bucket 3 planning).
