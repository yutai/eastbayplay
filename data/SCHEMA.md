# Data Schema

Two JSON files live in this directory:

- `pools.json` — hand-curated pool registry (rarely changes)
- `schedules.json` — swim session data (updated weekly by scraper)

---

## pools.json

Top-level object with a single key `"pools"`, an array of pool objects.

### Pool object

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | yes | Unique slug, e.g. `"oakland-fremont"`. Used as key in `schedules.json`. |
| `name` | string | yes | Official display name of the pool. |
| `city` | string | yes | City name, e.g. `"Oakland"`. |
| `address` | string | yes | Street address including city, state, zip. |
| `indoor` | boolean | yes | `true` if indoors (year-round), `false` if outdoor (often seasonal). |
| `operator` | string | yes | Organization that runs the pool. |
| `info_url` | string | yes | URL for the pool's main information page. |
| `schedule_sources` | array | yes | One or more source objects (see below). Used by scraper to fetch current schedules. |
| `notes` | string | no | Free text: seasonal notes, renovation status, contact info, scraping caveats, etc. |

### schedule_sources item

| Field | Type | Values |
|-------|------|--------|
| `url` | string | The URL the scraper should fetch. |
| `type` | string | `"html"` — rendered HTML page; `"pdf"` — PDF document. |

### Example

```json
{
  "id": "oakland-fremont",
  "name": "Fremont Pool",
  "city": "Oakland",
  "address": "4550 Foothill Blvd, Oakland, CA 94601",
  "indoor": false,
  "operator": "Oakland Parks, Recreation & Youth Development",
  "info_url": "https://www.oaklandca.gov/Community/Parks-Facilities/Pools/Fremont-Pool",
  "schedule_sources": [
    {"url": "https://www.oaklandca.gov/topics/pool-hours", "type": "html"}
  ],
  "notes": "Outdoor pool near Fremont High School."
}
```

---

## schedules.json

Top-level object with:
- `"generated_at"` — ISO 8601 UTC timestamp of when this file was produced.
- `"schedules"` — object mapping pool `id` → schedule object.

### Schedule object

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `last_verified` | string | `YYYY-MM-DD` | Date this pool's data was last successfully extracted. |
| `status` | string | `"ok"` \| `"stale"` \| `"closed"` \| `"unknown"` | `ok` = current good data; `stale` = scrape failed, showing old data; `closed` = pool confirmed closed; `unknown` = couldn't retrieve schedule. |
| `season_note` | string | free text | Human-readable note about the current season or schedule period. |
| `sessions` | array | — | Zero or more session objects (see below). Empty array is valid (e.g. for `"unknown"` status). |

### Session object

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `day` | string | `"monday"` \| `"tuesday"` \| `"wednesday"` \| `"thursday"` \| `"friday"` \| `"saturday"` \| `"sunday"` | Lowercase English day name. |
| `start` | string | `HH:MM` 24h | Session start time in local time (America/Los_Angeles). |
| `end` | string | `HH:MM` 24h | Session end time. Must be after `start`. |
| `label` | string | verbatim | Original label from the source schedule (e.g. `"Recreational Swim"`, `"Community Swim"`, `"Open Swim (Activity Pool)"`). Keep verbatim for auditability. |
| `category` | string | `"rec"` \| `"lap"` \| `"other"` | `rec` = anything a baby/family can attend (recreational/open/family/parent-tot swim); `lap` = lap swim only; `other` = lessons, water aerobics, club use, etc. |

### Rules

- `start < end` — validated by `scripts/validate.py`.
- Times are local (America/Los_Angeles); timezone math for "open now" badges is done client-side.
- A pool with `status: "unknown"` should have `sessions: []`.
- A pool with `status: "closed"` should have `sessions: []`.
- Multiple sessions on the same day are valid (e.g. morning and afternoon blocks).

### Example

```json
{
  "generated_at": "2026-06-11T00:00:00Z",
  "schedules": {
    "emeryville-eccl": {
      "last_verified": "2026-06-11",
      "status": "ok",
      "season_note": "Recreation Swim season: June 22 – Aug 14",
      "sessions": [
        {
          "day": "monday",
          "start": "13:00",
          "end": "15:00",
          "label": "Recreation Swim",
          "category": "rec"
        }
      ]
    }
  }
}
```
