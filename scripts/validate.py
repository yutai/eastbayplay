#!/usr/bin/env python3
"""
Validate data/pools.json and data/schedules.json.

Checks:
  - Both files parse as valid JSON.
  - pools.json: required fields present on every pool; ids are unique.
  - schedules.json: every schedule key exists in pools.json.
  - schedules.json: status is one of the allowed enum values.
  - schedules.json: last_verified is a valid YYYY-MM-DD date.
  - schedules.json: each session has valid day, category, HH:MM times, start < end.

Exits 0 on success, 1 on any failure.
"""

import json
import sys
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent
POOLS_PATH = ROOT / "data" / "pools.json"
SCHEDULES_PATH = ROOT / "data" / "schedules.json"

ALLOWED_DAYS = {
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"
}
ALLOWED_CATEGORIES = {"rec", "lap", "other"}
ALLOWED_STATUSES = {"ok", "stale", "closed", "unknown"}
POOL_REQUIRED_FIELDS = {"id", "name", "city", "address", "indoor", "heated", "operator", "info_url", "schedule_sources"}
SCHEDULE_SOURCE_REQUIRED = {"url", "type"}
ALLOWED_SOURCE_TYPES = {"html", "pdf"}
SCHEDULE_SOURCE_OPTIONAL = {"render"}  # optional boolean fields

errors = []


def err(msg: str) -> None:
    errors.append(msg)
    print(f"  ERROR: {msg}", file=sys.stderr)


def parse_time(t: str) -> tuple[int, int] | None:
    """Return (hour, minute) if t is valid HH:MM, else None."""
    m = re.fullmatch(r"(\d{2}):(\d{2})", t)
    if not m:
        return None
    h, mn = int(m.group(1)), int(m.group(2))
    if h > 23 or mn > 59:
        return None
    return (h, mn)


def time_to_minutes(t: tuple[int, int]) -> int:
    return t[0] * 60 + t[1]


# ── Load pools.json ───────────────────────────────────────────────────────────
print(f"Validating {POOLS_PATH} ...")
try:
    pools_data = json.loads(POOLS_PATH.read_text())
except Exception as e:
    print(f"  FATAL: could not parse pools.json: {e}", file=sys.stderr)
    sys.exit(1)

if "pools" not in pools_data or not isinstance(pools_data["pools"], list):
    print("  FATAL: pools.json missing top-level 'pools' array", file=sys.stderr)
    sys.exit(1)

pool_ids: set[str] = set()
for i, pool in enumerate(pools_data["pools"]):
    prefix = f"pools[{i}]"
    for field in POOL_REQUIRED_FIELDS:
        if field not in pool:
            err(f"{prefix}: missing required field '{field}'")
    pid = pool.get("id", f"<pool {i}>")
    if pid in pool_ids:
        err(f"{prefix}: duplicate pool id '{pid}'")
    else:
        pool_ids.add(pid)
    if "indoor" in pool and not isinstance(pool["indoor"], bool):
        err(f"{prefix} ({pid}): 'indoor' must be a boolean")
    if "heated" in pool and not isinstance(pool["heated"], bool):
        err(f"{prefix} ({pid}): 'heated' must be a boolean")
    for j, src in enumerate(pool.get("schedule_sources", [])):
        for sf in SCHEDULE_SOURCE_REQUIRED:
            if sf not in src:
                err(f"{prefix} ({pid}) schedule_sources[{j}]: missing '{sf}'")
        if src.get("type") not in ALLOWED_SOURCE_TYPES:
            err(f"{prefix} ({pid}) schedule_sources[{j}]: invalid type '{src.get('type')}', must be one of {ALLOWED_SOURCE_TYPES}")
        if "render" in src and not isinstance(src["render"], bool):
            err(f"{prefix} ({pid}) schedule_sources[{j}]: 'render' must be a boolean")

if not errors:
    print(f"  OK — {len(pool_ids)} pools, all fields valid.")
else:
    print(f"  {len(errors)} error(s) so far.")

# ── Load schedules.json ───────────────────────────────────────────────────────
print(f"Validating {SCHEDULES_PATH} ...")
try:
    sched_data = json.loads(SCHEDULES_PATH.read_text())
except Exception as e:
    print(f"  FATAL: could not parse schedules.json: {e}", file=sys.stderr)
    sys.exit(1)

if "schedules" not in sched_data or not isinstance(sched_data["schedules"], dict):
    print("  FATAL: schedules.json missing top-level 'schedules' object", file=sys.stderr)
    sys.exit(1)

if "generated_at" not in sched_data:
    err("schedules.json: missing 'generated_at'")

date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
schedule_errors_before = len(errors)

for pool_id, sched in sched_data["schedules"].items():
    prefix = f"schedules[{pool_id!r}]"

    # id must exist in pools.json
    if pool_id not in pool_ids:
        err(f"{prefix}: pool id not found in pools.json")

    # status
    status = sched.get("status")
    if status not in ALLOWED_STATUSES:
        err(f"{prefix}: invalid status '{status}', must be one of {ALLOWED_STATUSES}")

    # last_verified
    lv = sched.get("last_verified", "")
    if not date_re.match(lv):
        err(f"{prefix}: 'last_verified' must be YYYY-MM-DD, got '{lv}'")

    # sessions
    sessions = sched.get("sessions", [])
    if not isinstance(sessions, list):
        err(f"{prefix}: 'sessions' must be a list")
        continue

    for k, sess in enumerate(sessions):
        sprefix = f"{prefix} sessions[{k}]"
        day = sess.get("day", "")
        if day not in ALLOWED_DAYS:
            err(f"{sprefix}: invalid day '{day}'")

        cat = sess.get("category", "")
        if cat not in ALLOWED_CATEGORIES:
            err(f"{sprefix}: invalid category '{cat}', must be one of {ALLOWED_CATEGORIES}")

        start_str = sess.get("start", "")
        end_str = sess.get("end", "")
        label = sess.get("label", "")

        start_t = parse_time(start_str)
        if start_t is None:
            err(f"{sprefix}: invalid start time '{start_str}' (expected HH:MM 24h)")
        end_t = parse_time(end_str)
        if end_t is None:
            err(f"{sprefix}: invalid end time '{end_str}' (expected HH:MM 24h)")

        if start_t is not None and end_t is not None:
            if time_to_minutes(start_t) >= time_to_minutes(end_t):
                err(f"{sprefix}: start '{start_str}' must be before end '{end_str}'")

        if not label:
            err(f"{sprefix}: 'label' must not be empty")

new_errors = len(errors) - schedule_errors_before
if new_errors == 0:
    print(f"  OK — {len(sched_data['schedules'])} pool schedules, all sessions valid.")
else:
    print(f"  {new_errors} error(s) in schedules.json.")

# ── Result ────────────────────────────────────────────────────────────────────
print()
if errors:
    print(f"VALIDATION FAILED — {len(errors)} error(s) total:", file=sys.stderr)
    for e in errors:
        print(f"  - {e}", file=sys.stderr)
    sys.exit(1)
else:
    print("VALIDATION PASSED.")
    sys.exit(0)
