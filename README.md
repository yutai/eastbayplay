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
