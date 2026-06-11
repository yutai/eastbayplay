#!/usr/bin/env python3
"""
scraper/scrape.py — East Bay Play pool schedule scraper.

Fetches schedule sources for each pool in data/pools.json, extracts structured
swim sessions using Claude Haiku via the Anthropic Python SDK's structured
outputs (client.messages.parse), validates the result, and writes
data/schedules.json.

Usage:
    python scraper/scrape.py                     # all pools
    python scraper/scrape.py --pool oakland-fremont
    python scraper/scrape.py --dry-run           # extract + report, don't write
    python scraper/scrape.py --from-fixtures     # use scraper/fixtures/ content
    python scraper/scrape.py --render            # use headless Chrome for render: true sources
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Literal, Optional

import httpx
from anthropic import Anthropic
from pydantic import BaseModel

# ── Paths ─────────────────────────────────────────────────────────────────────

ROOT = Path(__file__).parent.parent
POOLS_PATH = ROOT / "data" / "pools.json"
SCHEDULES_PATH = ROOT / "data" / "schedules.json"
FIXTURES_DIR = Path(__file__).parent / "fixtures"

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 4096
MAX_CHARS = 50_000  # truncate fetched content beyond this
MAX_SESSIONS = 60   # reject if extraction returns more (probable hallucination)
RECENT_DAYS = 14    # keep status "ok" if last_verified is within this many days

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s  %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("scraper")

# ── Pydantic models ───────────────────────────────────────────────────────────


class Session(BaseModel):
    day: Literal[
        "monday", "tuesday", "wednesday", "thursday",
        "friday", "saturday", "sunday"
    ]
    start: str   # "HH:MM" 24h
    end: str
    label: str   # verbatim text from the source
    category: Literal["rec", "lap", "other"]


class PoolSchedule(BaseModel):
    sessions: list[Session]
    season_note: Optional[str]
    pool_appears_closed: bool
    extraction_confidence: Literal["high", "medium", "low"]
    schedule_valid_through: Optional[str] = None  # "YYYY-MM-DD" if source states an end date


# ── Fetch helpers ─────────────────────────────────────────────────────────────


def fetch_html(url: str, client: httpx.Client) -> str:
    """Fetch a URL, strip script/style/nav tags, collapse whitespace."""
    log.info("  Fetching HTML: %s", url)
    response = client.get(url, headers=BROWSER_HEADERS, follow_redirects=True)
    response.raise_for_status()
    html = response.text

    # Strip script, style, nav, header, footer blocks
    for tag in ("script", "style", "nav", "header", "footer", "noscript"):
        html = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>",
            " ",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
    # Strip remaining HTML tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def fetch_pdf(url: str, client: httpx.Client) -> str:
    """Fetch a PDF URL and extract text with pypdf."""
    import io
    import pypdf

    log.info("  Fetching PDF: %s", url)
    response = client.get(url, headers=BROWSER_HEADERS, follow_redirects=True)
    response.raise_for_status()
    reader = pypdf.PdfReader(io.BytesIO(response.content))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def fetch_render(url: str) -> str:
    """
    Fetch a JS-rendered URL using Playwright headless Chromium.
    Waits for network idle, then strips HTML to text.
    Playwright is imported lazily so the module works without it installed.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright not installed — run: pip install playwright && playwright install chromium"
        )

    log.info("  Fetching (headless Chrome): %s", url)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30_000)
            html = page.content()
        finally:
            browser.close()

    # Same stripping as fetch_html
    for tag in ("script", "style", "nav", "header", "footer", "noscript"):
        html = re.sub(
            rf"<{tag}[^>]*>.*?</{tag}>",
            " ",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )
    html = re.sub(r"<[^>]+>", " ", html)
    html = re.sub(r"[ \t]+", " ", html)
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def fetch_sources(
    pool: dict,
    client: httpx.Client,
    fixtures_dir: Optional[Path] = None,
    use_render: bool = False,
) -> tuple[Optional[str], bool]:
    """
    Fetch all schedule_sources for a pool and concatenate them.

    Returns (content, all_skipped) where:
    - content is None if all sources failed (caller marks pool stale).
    - all_skipped is True when every source was skipped (render-only pool
      running without --render); caller should use SKIPPED outcome and
      leave the previous entry completely untouched.

    Optionally reads from fixtures_dir instead of the network.
    """
    parts = []
    any_success = False
    skipped_count = 0
    total_count = 0

    for src in pool.get("schedule_sources", []):
        url = src["url"]
        src_type = src["type"]
        needs_render = src.get("render", False)
        total_count += 1

        # --- fixture override ---
        if fixtures_dir is not None:
            fixture = _find_fixture(pool["id"], url, fixtures_dir)
            if fixture:
                log.info("  Using fixture for %s: %s", pool["id"], fixture.name)
                parts.append(f"[Source: {url}]\n{fixture.read_text()}")
                any_success = True
                continue
            else:
                log.warning("  No fixture found for pool=%s url=%s", pool["id"], url)
                continue

        # --- render-flagged source without --render: skip ---
        if needs_render and not use_render:
            log.info(
                "  Skipping render source (pass --render to fetch): %s",
                url,
            )
            skipped_count += 1
            continue

        # --- live fetch ---
        try:
            if needs_render and use_render:
                text = fetch_render(url)
            elif src_type == "html":
                text = fetch_html(url, client)
            elif src_type == "pdf":
                text = fetch_pdf(url, client)
            else:
                log.warning("  Unknown source type '%s' for %s", src_type, url)
                continue
            parts.append(f"[Source: {url}]\n{text}")
            any_success = True
        except httpx.HTTPStatusError as e:
            log.warning(
                "  HTTP %d fetching %s for pool=%s (likely blocked)",
                e.response.status_code,
                url,
                pool["id"],
            )
        except Exception as e:
            log.warning(
                "  Fetch failed for %s pool=%s: %s",
                url,
                pool["id"],
                e,
            )

    all_skipped = (skipped_count == total_count and total_count > 0)

    if not any_success:
        return None, all_skipped

    combined = "\n\n---\n\n".join(parts)
    if len(combined) > MAX_CHARS:
        log.warning(
            "  Content for %s truncated from %d to %d chars",
            pool["id"],
            len(combined),
            MAX_CHARS,
        )
        combined = combined[:MAX_CHARS]
    return combined, False


def _find_fixture(pool_id: str, url: str, fixtures_dir: Path) -> Optional[Path]:
    """Look for a fixture file matching pool_id."""
    candidates = list(fixtures_dir.glob(f"{pool_id}*"))
    if candidates:
        return candidates[0]
    return None


# ── Extraction ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a swim-schedule extraction assistant for the East Bay Play website.
The user message states today's date and which pool is being analyzed.

Your task: extract all public swim sessions from the source text in the user
message and return them in a structured format.

CATEGORY RULES (from data/SCHEMA.md):
- "rec"   = any swim a baby or family can attend: recreational swim, open swim,
             family swim, parent-tot swim, community swim, aquatic playtime.
- "lap"   = lap swim ONLY (lane swimming for fitness, not open to all ages).
- "other" = swim lessons, water aerobics, aqua fitness, club/team use, rentals,
             any event that is not open drop-in swim.

RECURRENCE RULES:
- Expand recurrences into one session per day.
  e.g. "M/W/F 1–3 pm" → three separate sessions (monday, wednesday, friday).
  e.g. "weekdays 6–8 am" → five sessions (monday through friday).
- Use 24-hour HH:MM format for times (e.g. 13:30, not 1:30pm).

SEASON / SCHEDULE SELECTION:
- If the page shows multiple seasonal schedules, pick only the CURRENT season
  based on today's date (given in the user message).
- If no current schedule is available, return empty sessions and set
  extraction_confidence to "low". Never guess or invent sessions.
- If the only schedule on the page has already ended (its end date is before
  today), return empty sessions with confidence "low" — do not extract an
  expired schedule.

SCHEDULE END DATE:
- If the source explicitly states a schedule end date (e.g. "through June 7",
  "valid until September 1"), set schedule_valid_through to that date in
  YYYY-MM-DD format. If no end date is stated, leave it null.

CONFIDENCE:
- "high"   = schedule clearly present, dates current, unambiguous.
- "medium" = schedule present but dates unclear or page lists "coming soon".
- "low"    = no usable schedule found; returning empty sessions.

POOL CLOSED:
- Set pool_appears_closed=true only if the page explicitly states the pool is
  closed for renovation, permanent closure, or the entire season.
""".strip()


def extract_schedule(
    pool: dict,
    content: str,
    anthropic_client: Anthropic,
    today: str,
) -> tuple[PoolSchedule, dict]:
    """
    Call Claude Haiku with structured output. Returns (PoolSchedule, usage_dict).
    Raises on API error.
    """
    # Pool-specific context lives in the user message so the system prompt
    # stays byte-identical across the per-pool calls (prompt-cache friendly).
    system = SYSTEM_PROMPT
    user_msg = (
        f"Today's date: {today}\n"
        f"Pool: \"{pool['name']}\" in {pool['city']}\n\n"
        f"SCHEDULE SOURCE TEXT:\n\n{content}"
    )

    response = anthropic_client.messages.parse(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
        output_format=PoolSchedule,
    )

    usage = {
        "input_tokens": response.usage.input_tokens,
        "output_tokens": response.usage.output_tokens,
        "cache_creation_input_tokens": getattr(
            response.usage, "cache_creation_input_tokens", 0
        ),
        "cache_read_input_tokens": getattr(
            response.usage, "cache_read_input_tokens", 0
        ),
    }

    return response.parsed_output, usage


# ── Validation & merge ────────────────────────────────────────────────────────

TIME_RE = re.compile(r"^(\d{2}):(\d{2})$")


def _parse_time(t: str) -> Optional[tuple[int, int]]:
    m = TIME_RE.match(t)
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    if h > 23 or mn > 59:
        return None
    return (h, mn)


def _time_minutes(t: tuple[int, int]) -> int:
    return t[0] * 60 + t[1]


def _is_recently_verified(previous: Optional[dict], today: str) -> bool:
    """Return True if last_verified is within RECENT_DAYS of today."""
    if not previous:
        return False
    lv = previous.get("last_verified", "")
    try:
        lv_date = date.fromisoformat(lv)
        today_date = date.fromisoformat(today)
        return (today_date - lv_date).days <= RECENT_DAYS
    except ValueError:
        return False


def validate_sessions(sessions: list[Session]) -> list[str]:
    """Return a list of validation error strings (empty = ok)."""
    errors = []
    for i, sess in enumerate(sessions):
        st = _parse_time(sess.start)
        et = _parse_time(sess.end)
        if st is None:
            errors.append(f"session[{i}] invalid start '{sess.start}'")
            continue
        if et is None:
            errors.append(f"session[{i}] invalid end '{sess.end}'")
            continue
        sm, em = _time_minutes(st), _time_minutes(et)
        if sm >= em:
            errors.append(
                f"session[{i}] start '{sess.start}' >= end '{sess.end}'"
            )
        if sm < 5 * 60 or em > 22 * 60:
            errors.append(
                f"session[{i}] times {sess.start}–{sess.end} outside 05:00–22:00"
            )
        if not sess.label:
            errors.append(f"session[{i}] empty label")
    return errors


def decide_merge(
    pool_id: str,
    extracted: PoolSchedule,
    previous: Optional[dict],
    today: str,
) -> dict:
    """
    Apply safety-net rules and return the final schedule entry.

    Rules (in priority order):
    1. pool_appears_closed → status "closed", empty sessions, update last_verified.
    2. extraction_confidence "low" → reject, keep previous (mark stale only if
       last_verified > RECENT_DAYS old; otherwise keep status unchanged).
    3. schedule_valid_through < today → reject as expired (same keep logic).
    4. Session count > MAX_SESSIONS → reject (probable hallucination).
    5. Zero sessions extracted AND previous had >0 sessions → reject.
    6. Validation errors in extracted sessions → reject.
    7. Otherwise accept: status "ok", last_verified = today.
    """
    prev_sessions = (previous or {}).get("sessions", [])
    prev_had_sessions = len(prev_sessions) > 0
    recently_verified = _is_recently_verified(previous, today)

    def _keep_previous(reason: str) -> dict:
        """Keep previous entry; mark stale only if not recently verified."""
        if previous:
            entry = dict(previous)
            if not recently_verified and entry.get("status") == "ok":
                entry["status"] = "stale"
                log.warning(
                    "  %s: %s → marking stale (last_verified > %d days ago)",
                    pool_id, reason, RECENT_DAYS,
                )
            else:
                log.warning(
                    "  %s: %s → keeping previous (recently verified, status unchanged)",
                    pool_id, reason,
                )
            return entry
        return _unknown_entry(today)

    # Rule 1: closed
    if extracted.pool_appears_closed:
        log.info("  %s: pool appears closed → status=closed", pool_id)
        return {
            "last_verified": today,
            "status": "closed",
            "season_note": extracted.season_note or "",
            "sessions": [],
        }

    # Rule 2: low confidence
    if extracted.extraction_confidence == "low":
        return _keep_previous("low confidence")

    # Rule 3: expired schedule
    if extracted.schedule_valid_through:
        try:
            through_date = date.fromisoformat(extracted.schedule_valid_through)
            today_date = date.fromisoformat(today)
            if through_date < today_date:
                log.warning(
                    "  %s: schedule expired (valid_through=%s, today=%s)",
                    pool_id,
                    extracted.schedule_valid_through,
                    today,
                )
                return _keep_previous(
                    f"expired schedule (valid_through={extracted.schedule_valid_through})"
                )
        except ValueError:
            log.warning(
                "  %s: invalid schedule_valid_through '%s', ignoring",
                pool_id,
                extracted.schedule_valid_through,
            )

    # Rule 4: too many sessions
    if len(extracted.sessions) > MAX_SESSIONS:
        log.warning(
            "  %s: %d sessions > %d max → rejecting (possible hallucination)",
            pool_id,
            len(extracted.sessions),
            MAX_SESSIONS,
        )
        return _keep_previous(f"{len(extracted.sessions)} sessions > {MAX_SESSIONS} max")

    # Rule 5: zero sessions when previous had sessions
    if len(extracted.sessions) == 0 and prev_had_sessions:
        log.warning(
            "  %s: extracted 0 sessions but previous had %d → keeping previous",
            pool_id,
            len(prev_sessions),
        )
        return _keep_previous("0 sessions extracted but previous had sessions")

    # Rule 6: session validation
    errors = validate_sessions(extracted.sessions)
    if errors:
        log.warning(
            "  %s: session validation failed (%d errors) → keeping previous",
            pool_id,
            len(errors),
        )
        for e in errors:
            log.warning("    %s", e)
        return _keep_previous(f"session validation failed ({len(errors)} errors)")

    # Accepted
    log.info(
        "  %s: accepted %d sessions (confidence=%s)",
        pool_id,
        len(extracted.sessions),
        extracted.extraction_confidence,
    )
    return {
        "last_verified": today,
        "status": "ok",
        "season_note": extracted.season_note or "",
        "sessions": [s.model_dump() for s in extracted.sessions],
    }


def _unknown_entry(today: str) -> dict:
    return {
        "last_verified": today,
        "status": "unknown",
        "season_note": "",
        "sessions": [],
    }


# ── Diff report ───────────────────────────────────────────────────────────────


def build_diff_report(
    results: list[dict],
    old_schedules: dict,
    new_schedules: dict,
) -> str:
    """
    Build a human-readable diff report for stdout.
    Each result dict has keys: pool_id, outcome, detail.
    """
    lines = ["", "=" * 60, "  SCRAPE REPORT", "=" * 60]

    for r in results:
        pid = r["pool_id"]
        outcome = r["outcome"]  # fetch_fail | extracted | stale | closed | dry_run | skipped | kept
        detail = r.get("detail", "")

        old = old_schedules.get(pid, {})
        new = new_schedules.get(pid, {})
        old_count = len(old.get("sessions", []))
        new_count = len(new.get("sessions", []))
        new_status = new.get("status", "?")

        if outcome == "fetch_fail":
            symbol = "FAIL"
            summary = f"fetch failed — keeping previous ({old_count} sessions, status={old.get('status','?')})"
        elif outcome == "skipped":
            symbol = "SKIPPED"
            summary = (
                f"all sources need --render (skipped in CI) — "
                f"previous entry untouched ({old_count} sessions, status={old.get('status','?')})"
            )
        elif outcome == "kept":
            symbol = "KEPT"
            summary = (
                f"fetch failed but recently verified — "
                f"keeping as-is ({old_count} sessions, status={old.get('status','?')})"
            )
        elif outcome == "extracted":
            if new_status == "closed":
                symbol = "CLOSED"
                summary = "pool appears closed"
            elif new_status == "stale":
                symbol = "STALE"
                summary = f"extraction rejected — keeping previous ({old_count} sessions)"
            elif new_status == "unknown":
                symbol = "UNKN"
                summary = f"extraction returned no usable data"
            else:
                # ok
                if old_count == new_count and old.get("status") == "ok":
                    symbol = "OK"
                    summary = f"unchanged ({new_count} sessions)"
                elif old_count != new_count:
                    delta = new_count - old_count
                    sign = "+" if delta > 0 else ""
                    symbol = "UPDATED"
                    summary = f"{old_count} → {new_count} sessions ({sign}{delta})"
                else:
                    symbol = "OK"
                    summary = f"updated ({new_count} sessions, was {old.get('status','?')})"
        elif outcome == "dry_run":
            if new_status == "closed":
                symbol = "DRY-CLOSED"
                summary = "pool appears closed (dry run)"
            elif new_status in ("stale", "unknown"):
                symbol = "DRY-STALE"
                summary = f"extraction rejected (dry run) — would keep previous ({old_count} sessions)"
            else:
                delta = new_count - old_count
                sign = "+" if delta > 0 else ""
                symbol = "DRY-OK"
                summary = f"would update: {old_count} → {new_count} sessions ({sign}{delta})"
        else:
            symbol = "?"
            summary = detail

        lines.append(f"  [{symbol:12s}] {pid}  —  {summary}")
        if detail and outcome not in ("extracted", "dry_run", "skipped", "kept"):
            lines.append(f"               {detail}")

    lines.append("=" * 60)
    return "\n".join(lines)


# ── Cost estimator ────────────────────────────────────────────────────────────

# claude-haiku-4-5 pricing (per million tokens, as of mid-2025):
#   input: $1.00 / MTok, output: $5.00 / MTok
#   cache write: $1.25 / MTok, cache read: $0.10 / MTok
HAIKU_PRICE = {
    "input": 1.00 / 1_000_000,
    "output": 5.00 / 1_000_000,
    "cache_write": 1.25 / 1_000_000,
    "cache_read": 0.10 / 1_000_000,
}


def estimate_cost(usage_list: list[dict]) -> float:
    total = 0.0
    for u in usage_list:
        total += u.get("input_tokens", 0) * HAIKU_PRICE["input"]
        total += u.get("output_tokens", 0) * HAIKU_PRICE["output"]
        total += u.get("cache_creation_input_tokens", 0) * HAIKU_PRICE["cache_write"]
        total += u.get("cache_read_input_tokens", 0) * HAIKU_PRICE["cache_read"]
    return total


# ── Main pipeline ─────────────────────────────────────────────────────────────


def load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def save_schedules(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def run(
    pool_filter: Optional[str] = None,
    dry_run: bool = False,
    from_fixtures: bool = False,
    use_render: bool = False,
) -> int:
    """Main entry point. Returns exit code."""
    today = date.today().isoformat()

    # Load registry
    pools_data = load_json(POOLS_PATH)
    pools = pools_data["pools"]

    # Filter to requested pool(s)
    if pool_filter:
        pools = [p for p in pools if p["id"] == pool_filter]
        if not pools:
            log.error("No pool with id=%r found in pools.json", pool_filter)
            return 1

    # Load existing schedules (NEVER start from empty)
    if SCHEDULES_PATH.exists():
        sched_data = load_json(SCHEDULES_PATH)
    else:
        log.warning(
            "schedules.json not found — starting with empty schedules "
            "(this is expected only on first run)"
        )
        sched_data = {"generated_at": "", "schedules": {}}

    old_schedules = {k: dict(v) for k, v in sched_data["schedules"].items()}
    new_schedules = dict(old_schedules)  # copy; will be updated

    # Init Anthropic client
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        log.error(
            "ANTHROPIC_API_KEY environment variable not set. "
            "Cannot run extraction."
        )
        return 1
    anthropic_client = Anthropic(api_key=api_key)

    # Init HTTP client
    http_client = httpx.Client(timeout=30.0)

    results = []
    usage_list = []

    for pool in pools:
        pid = pool["id"]
        log.info("Processing pool: %s (%s)", pid, pool["name"])

        # Step 1: Fetch
        fixtures = FIXTURES_DIR if from_fixtures else None
        content, all_skipped = fetch_sources(
            pool, http_client, fixtures_dir=fixtures, use_render=use_render
        )

        # All sources were render-only and --render not passed: leave untouched
        if all_skipped:
            log.info(
                "  %s: all sources skipped (render-only, no --render) — entry untouched",
                pid,
            )
            # Do not modify new_schedules — keep whatever was in old_schedules
            results.append({
                "pool_id": pid,
                "outcome": "skipped",
            })
            continue

        if content is None:
            log.warning("  All sources failed for %s — keeping previous data", pid)
            prev = old_schedules.get(pid)
            recently_verified = _is_recently_verified(prev, today)

            if recently_verified:
                # Keep completely unchanged — don't even touch status
                log.info(
                    "  %s: recently verified (within %d days) — keeping as-is",
                    pid,
                    RECENT_DAYS,
                )
                results.append({
                    "pool_id": pid,
                    "outcome": "kept",
                    "detail": "fetch failed but recently verified",
                })
            else:
                # Mark stale if it was ok
                if prev:
                    entry = dict(prev)
                    if entry.get("status") == "ok":
                        entry["status"] = "stale"
                    new_schedules[pid] = entry
                else:
                    new_schedules[pid] = _unknown_entry(today)
                results.append({
                    "pool_id": pid,
                    "outcome": "fetch_fail",
                    "detail": "all sources HTTP error",
                })
            continue

        # Step 2: Extract
        try:
            extracted, usage = extract_schedule(
                pool, content, anthropic_client, today
            )
            usage_list.append(usage)
            log.info(
                "  Extracted: %d sessions, confidence=%s, closed=%s, "
                "valid_through=%s, tokens=%d+%d",
                len(extracted.sessions),
                extracted.extraction_confidence,
                extracted.pool_appears_closed,
                extracted.schedule_valid_through,
                usage["input_tokens"],
                usage["output_tokens"],
            )
        except Exception as e:
            log.error("  API error for %s: %s", pid, e)
            prev = old_schedules.get(pid)
            recently_verified = _is_recently_verified(prev, today)
            if recently_verified:
                results.append({
                    "pool_id": pid,
                    "outcome": "kept",
                    "detail": f"API error but recently verified: {e}",
                })
            else:
                if prev:
                    entry = dict(prev)
                    if entry.get("status") == "ok":
                        entry["status"] = "stale"
                    new_schedules[pid] = entry
                else:
                    new_schedules[pid] = _unknown_entry(today)
                results.append({
                    "pool_id": pid,
                    "outcome": "fetch_fail",
                    "detail": f"API error: {e}",
                })
            continue

        # Step 3: Validate & merge
        final_entry = decide_merge(pid, extracted, old_schedules.get(pid), today)

        outcome = "dry_run" if dry_run else "extracted"
        results.append({"pool_id": pid, "outcome": outcome})

        if not dry_run:
            new_schedules[pid] = final_entry
        else:
            # For dry-run diff, use what would have been merged
            new_schedules[pid] = final_entry  # still show in diff, just don't write

    # Step 4: Diff report
    report = build_diff_report(results, old_schedules, new_schedules)
    print(report)

    # Cost report
    if usage_list:
        total_input = sum(u["input_tokens"] for u in usage_list)
        total_output = sum(u["output_tokens"] for u in usage_list)
        cost = estimate_cost(usage_list)
        print(
            f"\n  API usage: {total_input} input tokens, "
            f"{total_output} output tokens across {len(usage_list)} calls"
        )
        print(f"  Estimated cost: ${cost:.4f}")

    # Write
    if not dry_run:
        sched_data["generated_at"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        sched_data["schedules"] = new_schedules
        save_schedules(SCHEDULES_PATH, sched_data)
        log.info("Wrote %s", SCHEDULES_PATH)
    else:
        log.info("Dry run — schedules.json not written")

    return 0


# ── CLI ───────────────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape East Bay pool schedules with Claude Haiku."
    )
    parser.add_argument(
        "--pool",
        metavar="POOL_ID",
        help="Process only this pool (by id, e.g. 'emeryville-eccl')",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Extract and report but do not write schedules.json",
    )
    parser.add_argument(
        "--from-fixtures",
        action="store_true",
        help="Use scraper/fixtures/ content instead of live fetching",
    )
    parser.add_argument(
        "--render",
        action="store_true",
        help=(
            "Use headless Chromium (Playwright) for sources marked render:true. "
            "Requires: pip install playwright && playwright install chromium. "
            "For local use only — CI runs without this flag and skips render sources."
        ),
    )
    args = parser.parse_args()

    sys.exit(run(
        pool_filter=args.pool,
        dry_run=args.dry_run,
        from_fixtures=args.from_fixtures,
        use_render=args.render,
    ))


if __name__ == "__main__":
    main()
