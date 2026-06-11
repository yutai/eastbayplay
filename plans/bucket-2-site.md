# Bucket 2 — Static site on GitHub Pages

**Goal:** A mobile-first page that renders `data/schedules.json` as a pool
list with an "open for rec swim now / today" view, deployed on GitHub Pages.

Depends on: Bucket 1 (data files exist and validate).

## Constraints (from locked decisions)

- Plain HTML/CSS/JS. **No build step, no framework, no npm.** (D4)
- List view with open-now/today emphasis; no map. (D7)
- Site must render fine when data is stale or partial — never blank-page.

## Files

```
index.html      one page
app.js          fetch + render + time logic
style.css       mobile-first styles
data/           (from Bucket 1; served as static files)
```

## Behavior spec

### Main list (default view: "Today")

- One card per pool, sorted: **open-now first**, then pools with rec swim
  later today (soonest first), then the rest alphabetically.
- Card shows: pool name, city, today's **rec** sessions as time ranges
  (e.g. "1:00–3:00 PM Recreational Swim"), and a status badge:
  - 🟢 **Open now** — a `rec` session is in progress
  - 🕐 **Today at H:MM** — next rec session today
  - ⚪ **No rec swim today**
  - ⚠️ **Schedule may be outdated** — when `status` is `stale`/`unknown`
  - 🚫 **Closed** — when `status` is `closed`
- Pools with `status: "unknown"` and no sessions go in a collapsed
  "No schedule data yet" section at the bottom.

### Detail (tap a card — `<details>` element is fine)

- Full weekly grid for that pool: all 7 days, rec sessions prominent,
  `lap`/`other` sessions shown small/grey (D3: de-emphasized, not hidden,
  so we can sanity-check categorization). Show the verbatim `label`.
- `season_note`, `last_verified`, and a link to the pool's `info_url` and
  schedule source (for when we want to double-check).

### Time logic

- Compute "now" in `America/Los_Angeles` explicitly
  (`Intl.DateTimeFormat` with `timeZone` — do NOT trust the device TZ,
  and do NOT hand-roll UTC offsets, DST will bite).
- Header shows the current day/date and `generated_at` from the data file
  ("Schedules updated Jun 9").

### Robustness

- `fetch('data/schedules.json')` + `data/pools.json`; on failure show a
  friendly error with a retry, not a blank page.
- Pools present in `pools.json` but absent from `schedules.json` render in
  the "no data" section.

### Style

- Mobile-first, large touch targets, readable in sunlight (high contrast).
- No external fonts/CDNs — keep it dependency-free and fast.
- Lighthearted but minimal; this is a utility.

## Deployment

- GitHub Pages, **deploy from branch** (`main`, root). No Actions needed yet.
- Add a note to README: Settings → Pages → Source: `main` / root
  (one-time manual step for the owner).
- Everything must work under a subpath (`https://<user>.github.io/eastbayplay/`)
  — use **relative** URLs only.

## Acceptance criteria

- [ ] Opening `index.html` via a local static server shows the seeded pools, correctly sorted, with correct badges for the current time
- [ ] Open-now logic verified manually for at least 2 cases (during and outside a session — can temporarily stub "now" in a test)
- [ ] Weekly detail renders all sessions with rec emphasized
- [ ] Page works on a ~375px viewport
- [ ] Stale/unknown pools are visibly flagged
- [ ] README documents the Pages setup step

## Checkpoint for owner review

Present: the live (or locally served) page with a screenshot, plus how the
sort/badge logic behaves right now. Owner verifies: is this the page you'd
actually open on a Saturday morning? Anything missing before automation?
