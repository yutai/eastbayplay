# East Bay Play — Pool Rec-Swim Finder

A tiny website for two people (us) that answers one question fast:
**"Which East Bay pools have recreational (non-lap) swim today, and when?"**

Today we have to open Google Maps listings one by one and dig through city
websites to figure out when rec swim (the kind babies can attend) happens.
This tool replaces that slog.

## Goal & core decision

- **Core job:** Surface rec/family/open-swim hours for public pools from
  San Leandro up to Albany, in one mobile-friendly page, with an
  "open for rec swim now / today" view.
- **Users:** Just us two. No accounts, no analytics, no growth goals.
- **Success:** We stop checking Google Maps listing-by-listing.

## Locked decisions (verify against these before deviating)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | Scope v1 | **Pools only**; playgrounds are v2 backlog | Rec-swim hours are the actual pain |
| D2 | Coverage | Public pools, **San Leandro → Albany** (San Leandro, Alameda, Oakland, Piedmont, Emeryville, Berkeley, Albany) | Where we actually go |
| D3 | Swim types | **Any non-lap swim** counts (rec, open, family, parent-tot). Lap-only shown de-emphasized/hidden | "Where can a baby be in the water" |
| D4 | Form factor | **Static site on GitHub Pages**, plain HTML/CSS/JS, no build step | Zero cost, both of us bookmark it |
| D5 | Data freshness | **Automated scraping** via GitHub Actions, **weekly** cron + manual trigger | Schedules change seasonally |
| D6 | Extraction | **LLM extraction in CI** with Claude Haiku (`claude-haiku-4-5`) using structured outputs; `ANTHROPIC_API_KEY` in repo secrets | City sites are PDFs/HTML soup; hand parsers are too fragile |
| D7 | v1 view | **List + "open now / today" badge**, tap for full weekly schedule. No map. | Google Maps already does location |
| D8 | Process | Build one bucket at a time; **stop for owner review at each checkpoint** | Keep scope honest |

## Architecture

```
pools.json (hand-curated registry: name, city, source URLs, address)
        │
        ▼
scraper/ (Python)  ── fetch source pages ──► Claude Haiku extraction ──► validation
        │                                                                   │
        ▼                                                                   ▼
data/schedules.json  ◄────────────── committed weekly by GitHub Action ─────┘
        │
        ▼
index.html + app.js + style.css  (reads schedules.json client-side)
        │
        ▼
GitHub Pages (deploy from main branch)
```

Key properties:
- The **site never breaks when scraping fails** — it always renders the last
  good `schedules.json`, with a per-pool `last_verified` date shown so stale
  data is visible.
- The scraper **never overwrites good data with garbage**: validation rejects
  empty/implausible extractions and keeps the previous entry, flagging it.
- Timezone math (open-now badge) happens client-side in `America/Los_Angeles`.

## Buckets

| Bucket | Deliverable | Plan |
|--------|-------------|------|
| 1 | Pool registry + data schema + hand-seeded `schedules.json` | `plans/bucket-1-data-foundation.md` |
| 2 | Static site on GitHub Pages rendering the data | `plans/bucket-2-site.md` |
| 3 | LLM scraper (local/manual runs) producing `schedules.json` | `plans/bucket-3-scraper.md` |
| 4 | Weekly GitHub Action automating the scrape | `plans/bucket-4-automation.md` |
| — | v2 ideas (playgrounds, map, filters) | `plans/backlog.md` |

**Order matters:** 1 → 2 gives a useful site with manual data even if 3/4 slip.

## Working agreement for implementation sessions

- Implement exactly one bucket per session, from its plan file.
- At the end of a bucket, write a short CHECKPOINT summary (what was built,
  how to verify it, open questions) and **stop for owner review**.
- If a plan conflicts with reality (e.g., a pool website is unscrapable),
  note the deviation in the checkpoint rather than silently changing scope.
- Re-read the Locked Decisions table before making any design call.
