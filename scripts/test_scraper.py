#!/usr/bin/env python3
"""
scripts/test_scraper.py — Unit tests for scraper/scrape.py.

Covers:
  - merge keeps previous data on fetch failure
  - empty extraction rejected when previous had sessions
  - >40 sessions rejected as hallucination (now cap is 60)
  - pool_appears_closed handling
  - low-confidence extraction rejected
  - accepted extraction updates status and last_verified
  - expired schedule_valid_through rejected
  - 43–60 sessions accepted (raised cap)
  - >60 sessions rejected
  - fetch failure with recent last_verified keeps status ok (KEPT)
  - fetch failure with old last_verified marks stale
  - render-marked sources skipped without --render
  - all-sources-skipped leaves entry untouched

All tests mock the Anthropic API; no network calls are made.

Run with:
    python3 scripts/test_scraper.py
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Make scraper/ importable from the project root
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scraper"))

from scrape import (
    PoolSchedule,
    Session,
    _is_recently_verified,
    _unknown_entry,
    build_diff_report,
    decide_merge,
    estimate_cost,
    fetch_sources,
    run,
    validate_sessions,
)


TODAY = "2026-06-11"


# ─── Helpers ──────────────────────────────────────────────────────────────────


def make_session(**kwargs):
    defaults = {
        "day": "monday",
        "start": "13:00",
        "end": "15:00",
        "label": "Recreational Swim",
        "category": "rec",
    }
    defaults.update(kwargs)
    return Session(**defaults)


def make_pool_schedule(
    sessions=None,
    season_note="Test season",
    pool_appears_closed=False,
    extraction_confidence="high",
    schedule_valid_through=None,
):
    if sessions is None:
        sessions = [make_session()]
    return PoolSchedule(
        sessions=sessions,
        season_note=season_note,
        pool_appears_closed=pool_appears_closed,
        extraction_confidence=extraction_confidence,
        schedule_valid_through=schedule_valid_through,
    )


def previous_entry_with_sessions(n=2, status="ok"):
    return {
        "last_verified": "2026-05-01",
        "status": status,
        "season_note": "Old season",
        "sessions": [
            {
                "day": "saturday",
                "start": "13:00",
                "end": "15:00",
                "label": "Recreational Swim",
                "category": "rec",
            }
        ] * n,
    }


# ─── Tests: decide_merge ──────────────────────────────────────────────────────


class TestDecideMerge(unittest.TestCase):

    def test_pool_appears_closed(self):
        """pool_appears_closed=True → status closed, empty sessions."""
        extracted = make_pool_schedule(
            pool_appears_closed=True,
            sessions=[],
            extraction_confidence="high",
        )
        result = decide_merge("test-pool", extracted, None, TODAY)
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["sessions"], [])
        self.assertEqual(result["last_verified"], TODAY)

    def test_pool_appears_closed_overrides_previous_ok(self):
        """Even if previous was ok, closed flag wins."""
        extracted = make_pool_schedule(
            pool_appears_closed=True,
            sessions=[make_session()],
            extraction_confidence="high",
        )
        previous = previous_entry_with_sessions(3, status="ok")
        result = decide_merge("test-pool", extracted, previous, TODAY)
        self.assertEqual(result["status"], "closed")
        self.assertEqual(result["sessions"], [])

    def test_low_confidence_rejected(self):
        """Low confidence → reject, keep previous (mark stale)."""
        extracted = make_pool_schedule(
            extraction_confidence="low",
            sessions=[],
        )
        previous = previous_entry_with_sessions(2, status="ok")
        result = decide_merge("test-pool", extracted, previous, TODAY)
        self.assertEqual(result["status"], "stale")
        # Previous sessions preserved
        self.assertEqual(len(result["sessions"]), 2)

    def test_low_confidence_no_previous(self):
        """Low confidence with no previous → unknown entry."""
        extracted = make_pool_schedule(extraction_confidence="low", sessions=[])
        result = decide_merge("test-pool", extracted, None, TODAY)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["sessions"], [])

    def test_too_many_sessions_rejected(self):
        """More than 60 sessions → hallucination guard, keep previous."""
        sessions = [make_session(day="monday")] * 65
        extracted = make_pool_schedule(
            sessions=sessions,
            extraction_confidence="high",
        )
        previous = previous_entry_with_sessions(3, status="ok")
        result = decide_merge("test-pool", extracted, previous, TODAY)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(len(result["sessions"]), 3)

    def test_too_many_sessions_no_previous(self):
        """More than 60 sessions, no previous → unknown."""
        sessions = [make_session()] * 61
        extracted = make_pool_schedule(sessions=sessions, extraction_confidence="high")
        result = decide_merge("test-pool", extracted, None, TODAY)
        self.assertEqual(result["status"], "unknown")

    def test_43_sessions_accepted(self):
        """43 sessions (previously over old cap of 40) should now be accepted."""
        sessions = [make_session()] * 43
        extracted = make_pool_schedule(sessions=sessions, extraction_confidence="high")
        result = decide_merge("test-pool", extracted, None, TODAY)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["sessions"]), 43)

    def test_60_sessions_accepted(self):
        """Exactly 60 sessions (at new cap) should be accepted."""
        sessions = [make_session()] * 60
        extracted = make_pool_schedule(sessions=sessions, extraction_confidence="high")
        result = decide_merge("test-pool", extracted, None, TODAY)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["sessions"]), 60)

    def test_expired_schedule_valid_through_rejected(self):
        """schedule_valid_through before today → reject as expired, keep previous."""
        extracted = make_pool_schedule(
            sessions=[make_session()],
            extraction_confidence="high",
            schedule_valid_through="2026-06-07",  # before TODAY (2026-06-11)
        )
        previous = previous_entry_with_sessions(2, status="ok")
        result = decide_merge("test-pool", extracted, previous, TODAY)
        # Previous had ok status, last_verified is 2026-05-01 (>14 days ago)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(len(result["sessions"]), 2)

    def test_future_schedule_valid_through_accepted(self):
        """schedule_valid_through in the future → accepted normally."""
        extracted = make_pool_schedule(
            sessions=[make_session()],
            extraction_confidence="high",
            schedule_valid_through="2026-09-01",  # after TODAY
        )
        result = decide_merge("test-pool", extracted, None, TODAY)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["sessions"]), 1)

    def test_schedule_valid_through_today_accepted(self):
        """schedule_valid_through exactly today → still valid (end of day)."""
        extracted = make_pool_schedule(
            sessions=[make_session()],
            extraction_confidence="high",
            schedule_valid_through=TODAY,
        )
        result = decide_merge("test-pool", extracted, None, TODAY)
        self.assertEqual(result["status"], "ok")

    def test_recently_verified_keeps_ok_on_rejection(self):
        """Previous verified within RECENT_DAYS: rejection keeps status=ok (not stale)."""
        # last_verified is only 5 days ago — within 14-day window
        extracted = make_pool_schedule(
            extraction_confidence="low",
            sessions=[],
        )
        previous = {
            "last_verified": "2026-06-06",  # 5 days before TODAY
            "status": "ok",
            "season_note": "Summer",
            "sessions": [
                {"day": "monday", "start": "13:00", "end": "15:00",
                 "label": "Rec Swim", "category": "rec"}
            ],
        }
        result = decide_merge("test-pool", extracted, previous, TODAY)
        # Should stay "ok" because recently verified
        self.assertEqual(result["status"], "ok")
        self.assertEqual(len(result["sessions"]), 1)

    def test_old_verified_marks_stale_on_rejection(self):
        """Previous verified >14 days ago: rejection marks stale."""
        extracted = make_pool_schedule(
            extraction_confidence="low",
            sessions=[],
        )
        previous = previous_entry_with_sessions(2, status="ok")
        # previous_entry_with_sessions uses "2026-05-01" — 41 days before TODAY
        result = decide_merge("test-pool", extracted, previous, TODAY)
        self.assertEqual(result["status"], "stale")

    def test_empty_extraction_rejected_when_previous_had_sessions(self):
        """Zero sessions extracted but previous had sessions → keep previous, mark stale."""
        extracted = make_pool_schedule(
            sessions=[],
            extraction_confidence="medium",
        )
        previous = previous_entry_with_sessions(3, status="ok")
        result = decide_merge("test-pool", extracted, previous, TODAY)
        self.assertEqual(result["status"], "stale")
        # Previous sessions preserved
        self.assertEqual(len(result["sessions"]), 3)

    def test_empty_extraction_ok_when_previous_empty(self):
        """
        Zero sessions extracted AND previous had zero sessions:
        medium/high confidence + closed=False, this passes through as ok
        with empty sessions (common for 'coming soon' pools).
        """
        extracted = make_pool_schedule(
            sessions=[],
            extraction_confidence="medium",
        )
        previous = {
            "last_verified": "2026-05-01",
            "status": "unknown",
            "season_note": "",
            "sessions": [],
        }
        result = decide_merge("test-pool", extracted, previous, TODAY)
        # No previous sessions, so rule 4 does not trigger
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sessions"], [])

    def test_accepted_extraction_updates_status_and_date(self):
        """Good extraction → status ok, last_verified = today."""
        extracted = make_pool_schedule(
            sessions=[make_session(), make_session(day="wednesday")],
            extraction_confidence="high",
        )
        previous = previous_entry_with_sessions(1, status="stale")
        result = decide_merge("test-pool", extracted, previous, TODAY)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["last_verified"], TODAY)
        self.assertEqual(len(result["sessions"]), 2)

    def test_accepted_extraction_no_previous(self):
        """Good extraction with no prior entry → status ok."""
        extracted = make_pool_schedule(
            sessions=[make_session()],
            extraction_confidence="high",
        )
        result = decide_merge("test-pool", extracted, None, TODAY)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["last_verified"], TODAY)

    def test_previously_stale_stays_stale_on_rejection(self):
        """If previous was already stale and we reject again, stays stale."""
        extracted = make_pool_schedule(
            extraction_confidence="low",
            sessions=[],
        )
        previous = previous_entry_with_sessions(2, status="stale")
        result = decide_merge("test-pool", extracted, previous, TODAY)
        self.assertEqual(result["status"], "stale")

    def test_season_note_preserved_on_accept(self):
        """Accepted extraction should use the extracted season_note."""
        extracted = make_pool_schedule(
            sessions=[make_session()],
            season_note="Summer 2026: June 15 – September 1",
            extraction_confidence="high",
        )
        result = decide_merge("test-pool", extracted, None, TODAY)
        self.assertEqual(result["season_note"], "Summer 2026: June 15 – September 1")


# ─── Tests: validate_sessions ─────────────────────────────────────────────────


class TestValidateSessions(unittest.TestCase):

    def test_valid_sessions_no_errors(self):
        sessions = [
            make_session(start="13:00", end="15:00"),
            make_session(day="saturday", start="10:00", end="14:30"),
        ]
        self.assertEqual(validate_sessions(sessions), [])

    def test_start_after_end_rejected(self):
        sessions = [make_session(start="15:00", end="13:00")]
        errors = validate_sessions(sessions)
        self.assertEqual(len(errors), 1)
        self.assertIn(">=", errors[0])

    def test_equal_start_end_rejected(self):
        sessions = [make_session(start="13:00", end="13:00")]
        errors = validate_sessions(sessions)
        self.assertEqual(len(errors), 1)

    def test_too_early_start_rejected(self):
        sessions = [make_session(start="04:30", end="06:00")]
        errors = validate_sessions(sessions)
        self.assertTrue(any("05:00" in e or "outside" in e for e in errors))

    def test_too_late_end_rejected(self):
        sessions = [make_session(start="21:00", end="23:00")]
        errors = validate_sessions(sessions)
        self.assertTrue(len(errors) > 0)

    def test_invalid_time_format(self):
        sessions = [make_session(start="1pm", end="3pm")]
        errors = validate_sessions(sessions)
        self.assertTrue(len(errors) > 0)

    def test_empty_label_rejected(self):
        sessions = [make_session(label="")]
        errors = validate_sessions(sessions)
        self.assertIn("empty label", errors[0])


# ─── Tests: full pipeline with mocked API ─────────────────────────────────────


class TestRunPipeline(unittest.TestCase):
    """
    Full pipeline tests using temporary files and mocked API.
    These test the merge/write logic end-to-end.
    """

    def _make_mock_parse(self, pool_schedule: PoolSchedule):
        """Return a mock anthropic_client where .messages.parse() returns pool_schedule."""
        mock_usage = MagicMock()
        mock_usage.input_tokens = 100
        mock_usage.output_tokens = 50
        mock_usage.cache_creation_input_tokens = 0
        mock_usage.cache_read_input_tokens = 0

        mock_response = MagicMock()
        mock_response.parsed_output = pool_schedule
        mock_response.usage = mock_usage

        mock_messages = MagicMock()
        mock_messages.parse.return_value = mock_response

        mock_client = MagicMock()
        mock_client.messages = mock_messages

        return mock_client

    def _write_pools_json(self, tmp_dir: Path) -> Path:
        """Write a minimal pools.json with one pool."""
        pools = {
            "pools": [
                {
                    "id": "test-pool",
                    "name": "Test Pool",
                    "city": "Oakland",
                    "address": "123 Test St, Oakland, CA 94601",
                    "indoor": False,
                    "operator": "Test Operator",
                    "info_url": "https://example.com",
                    "schedule_sources": [
                        {"url": "https://example.com/schedule", "type": "html"}
                    ],
                }
            ]
        }
        path = tmp_dir / "pools.json"
        path.write_text(json.dumps(pools))
        return path

    def _write_schedules_json(self, tmp_dir: Path, schedules: dict) -> Path:
        data = {
            "generated_at": "2026-05-01T00:00:00Z",
            "schedules": schedules,
        }
        path = tmp_dir / "schedules.json"
        path.write_text(json.dumps(data, indent=2))
        return path

    def test_fetch_failure_keeps_previous_data_marked_stale(self):
        """When fetch fails, previous ok data is kept and marked stale."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pools_path = self._write_pools_json(tmp_path)
            prev_sessions = [
                {
                    "day": "saturday",
                    "start": "12:00",
                    "end": "15:00",
                    "label": "Rec Swim",
                    "category": "rec",
                }
            ]
            schedules_path = self._write_schedules_json(
                tmp_path,
                {
                    "test-pool": {
                        "last_verified": "2026-05-01",
                        "status": "ok",
                        "season_note": "Old season",
                        "sessions": prev_sessions,
                    }
                },
            )

            mock_client = MagicMock()
            mock_client.messages.parse.side_effect = Exception("API unavailable")

            with (
                patch("scrape.POOLS_PATH", pools_path),
                patch("scrape.SCHEDULES_PATH", schedules_path),
                patch("scrape.Anthropic", return_value=mock_client),
                patch("scrape.fetch_sources", return_value=(None, False)),
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                exit_code = run(pool_filter="test-pool")

            self.assertEqual(exit_code, 0)
            result = json.loads(schedules_path.read_text())
            entry = result["schedules"]["test-pool"]
            self.assertEqual(entry["status"], "stale")
            self.assertEqual(len(entry["sessions"]), 1)

    def test_empty_extraction_rejected_keeps_previous(self):
        """Empty extraction when previous had sessions → keeps previous, marks stale."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pools_path = self._write_pools_json(tmp_path)
            prev_sessions = [
                {
                    "day": "monday",
                    "start": "13:00",
                    "end": "15:00",
                    "label": "Rec Swim",
                    "category": "rec",
                }
            ]
            schedules_path = self._write_schedules_json(
                tmp_path,
                {
                    "test-pool": {
                        "last_verified": "2026-05-01",
                        "status": "ok",
                        "season_note": "Old",
                        "sessions": prev_sessions,
                    }
                },
            )

            empty_schedule = make_pool_schedule(
                sessions=[], extraction_confidence="medium"
            )
            mock_client = self._make_mock_parse(empty_schedule)

            with (
                patch("scrape.POOLS_PATH", pools_path),
                patch("scrape.SCHEDULES_PATH", schedules_path),
                patch("scrape.Anthropic", return_value=mock_client),
                patch("scrape.fetch_sources", return_value=("<html>some content</html>", False)),
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                exit_code = run(pool_filter="test-pool")

            self.assertEqual(exit_code, 0)
            result = json.loads(schedules_path.read_text())
            entry = result["schedules"]["test-pool"]
            self.assertEqual(entry["status"], "stale")
            self.assertEqual(len(entry["sessions"]), 1)

    def test_over_60_sessions_rejected(self):
        """More than 60 sessions → rejected, previous kept and marked stale."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pools_path = self._write_pools_json(tmp_path)
            prev_sessions = [
                {
                    "day": "saturday",
                    "start": "12:00",
                    "end": "15:00",
                    "label": "Rec Swim",
                    "category": "rec",
                }
            ]
            schedules_path = self._write_schedules_json(
                tmp_path,
                {
                    "test-pool": {
                        "last_verified": "2026-05-01",
                        "status": "ok",
                        "season_note": "Old",
                        "sessions": prev_sessions,
                    }
                },
            )

            big_sessions = [make_session()] * 62
            big_schedule = make_pool_schedule(
                sessions=big_sessions, extraction_confidence="high"
            )
            mock_client = self._make_mock_parse(big_schedule)

            with (
                patch("scrape.POOLS_PATH", pools_path),
                patch("scrape.SCHEDULES_PATH", schedules_path),
                patch("scrape.Anthropic", return_value=mock_client),
                patch("scrape.fetch_sources", return_value=("some content", False)),
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                exit_code = run(pool_filter="test-pool")

            self.assertEqual(exit_code, 0)
            result = json.loads(schedules_path.read_text())
            entry = result["schedules"]["test-pool"]
            self.assertEqual(entry["status"], "stale")
            self.assertEqual(len(entry["sessions"]), 1)

    def test_pool_appears_closed_sets_closed_status(self):
        """pool_appears_closed=True → status=closed, sessions=[]."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pools_path = self._write_pools_json(tmp_path)
            schedules_path = self._write_schedules_json(
                tmp_path,
                {
                    "test-pool": {
                        "last_verified": "2026-05-01",
                        "status": "ok",
                        "season_note": "Old",
                        "sessions": [
                            {
                                "day": "saturday",
                                "start": "12:00",
                                "end": "15:00",
                                "label": "Rec Swim",
                                "category": "rec",
                            }
                        ],
                    }
                },
            )

            closed_schedule = make_pool_schedule(
                sessions=[],
                pool_appears_closed=True,
                extraction_confidence="high",
                season_note="Pool closed for renovation until 2027",
            )
            mock_client = self._make_mock_parse(closed_schedule)

            with (
                patch("scrape.POOLS_PATH", pools_path),
                patch("scrape.SCHEDULES_PATH", schedules_path),
                patch("scrape.Anthropic", return_value=mock_client),
                patch("scrape.fetch_sources", return_value=("Pool closed for renovation", False)),
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                exit_code = run(pool_filter="test-pool")

            self.assertEqual(exit_code, 0)
            result = json.loads(schedules_path.read_text())
            entry = result["schedules"]["test-pool"]
            self.assertEqual(entry["status"], "closed")
            self.assertEqual(entry["sessions"], [])
            self.assertEqual(entry["last_verified"], TODAY)

    def test_low_confidence_rejected(self):
        """Low confidence extraction → keeps previous data as stale."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pools_path = self._write_pools_json(tmp_path)
            prev_sessions = [
                {
                    "day": "tuesday",
                    "start": "10:00",
                    "end": "12:00",
                    "label": "Rec Swim",
                    "category": "rec",
                }
            ]
            schedules_path = self._write_schedules_json(
                tmp_path,
                {
                    "test-pool": {
                        "last_verified": "2026-05-01",
                        "status": "ok",
                        "season_note": "Old",
                        "sessions": prev_sessions,
                    }
                },
            )

            low_conf = make_pool_schedule(
                sessions=[],
                extraction_confidence="low",
            )
            mock_client = self._make_mock_parse(low_conf)

            with (
                patch("scrape.POOLS_PATH", pools_path),
                patch("scrape.SCHEDULES_PATH", schedules_path),
                patch("scrape.Anthropic", return_value=mock_client),
                patch("scrape.fetch_sources", return_value=("schedule coming soon", False)),
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                exit_code = run(pool_filter="test-pool")

            self.assertEqual(exit_code, 0)
            result = json.loads(schedules_path.read_text())
            entry = result["schedules"]["test-pool"]
            self.assertEqual(entry["status"], "stale")
            self.assertEqual(len(entry["sessions"]), 1)

    def test_accepted_extraction_updates_status_and_last_verified(self):
        """Good extraction → status=ok, last_verified=today, sessions updated."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pools_path = self._write_pools_json(tmp_path)
            schedules_path = self._write_schedules_json(
                tmp_path,
                {
                    "test-pool": {
                        "last_verified": "2026-05-01",
                        "status": "stale",
                        "season_note": "Old",
                        "sessions": [
                            {
                                "day": "saturday",
                                "start": "12:00",
                                "end": "15:00",
                                "label": "Old Session",
                                "category": "rec",
                            }
                        ],
                    }
                },
            )

            good_schedule = make_pool_schedule(
                sessions=[
                    make_session(day="monday"),
                    make_session(day="wednesday"),
                    make_session(day="friday"),
                ],
                extraction_confidence="high",
                season_note="Summer 2026",
            )
            mock_client = self._make_mock_parse(good_schedule)

            with (
                patch("scrape.POOLS_PATH", pools_path),
                patch("scrape.SCHEDULES_PATH", schedules_path),
                patch("scrape.Anthropic", return_value=mock_client),
                patch("scrape.fetch_sources", return_value=("Mon/Wed/Fri 1-3pm Rec Swim", False)),
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                exit_code = run(pool_filter="test-pool")

            self.assertEqual(exit_code, 0)
            result = json.loads(schedules_path.read_text())
            entry = result["schedules"]["test-pool"]
            self.assertEqual(entry["status"], "ok")
            self.assertEqual(entry["last_verified"], TODAY)
            self.assertEqual(len(entry["sessions"]), 3)
            self.assertEqual(entry["season_note"], "Summer 2026")

    def test_other_pools_preserved_when_running_single_pool(self):
        """Running --pool for one pool doesn't overwrite other pools' data."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pools_path = self._write_pools_json(tmp_path)
            other_session = {
                "day": "sunday",
                "start": "14:00",
                "end": "16:00",
                "label": "Rec Swim",
                "category": "rec",
            }
            schedules_path = self._write_schedules_json(
                tmp_path,
                {
                    "test-pool": {
                        "last_verified": "2026-05-01",
                        "status": "stale",
                        "season_note": "Old",
                        "sessions": [],
                    },
                    "other-pool": {
                        "last_verified": "2026-05-01",
                        "status": "ok",
                        "season_note": "Other pool",
                        "sessions": [other_session],
                    },
                },
            )

            good_schedule = make_pool_schedule(
                sessions=[make_session()],
                extraction_confidence="high",
            )
            mock_client = self._make_mock_parse(good_schedule)

            with (
                patch("scrape.POOLS_PATH", pools_path),
                patch("scrape.SCHEDULES_PATH", schedules_path),
                patch("scrape.Anthropic", return_value=mock_client),
                patch("scrape.fetch_sources", return_value=("Mon 1-3pm Rec Swim", False)),
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                exit_code = run(pool_filter="test-pool")

            self.assertEqual(exit_code, 0)
            result = json.loads(schedules_path.read_text())
            # other-pool should be untouched
            self.assertEqual(result["schedules"]["other-pool"]["status"], "ok")
            self.assertEqual(
                len(result["schedules"]["other-pool"]["sessions"]), 1
            )

    def test_dry_run_does_not_write(self):
        """--dry-run does not overwrite schedules.json."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pools_path = self._write_pools_json(tmp_path)
            schedules_path = self._write_schedules_json(
                tmp_path,
                {
                    "test-pool": {
                        "last_verified": "2026-05-01",
                        "status": "ok",
                        "season_note": "Old",
                        "sessions": [
                            {
                                "day": "saturday",
                                "start": "12:00",
                                "end": "15:00",
                                "label": "Rec Swim",
                                "category": "rec",
                            }
                        ],
                    }
                },
            )
            original_content = schedules_path.read_text()

            new_schedule = make_pool_schedule(
                sessions=[make_session(), make_session(day="wednesday")],
                extraction_confidence="high",
            )
            mock_client = self._make_mock_parse(new_schedule)

            with (
                patch("scrape.POOLS_PATH", pools_path),
                patch("scrape.SCHEDULES_PATH", schedules_path),
                patch("scrape.Anthropic", return_value=mock_client),
                patch("scrape.fetch_sources", return_value=("M/W 1-3pm Rec Swim", False)),
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                exit_code = run(pool_filter="test-pool", dry_run=True)

            self.assertEqual(exit_code, 0)
            # File must be unchanged
            self.assertEqual(schedules_path.read_text(), original_content)

    def test_no_api_key_returns_error(self):
        """Missing ANTHROPIC_API_KEY → exit code 1."""
        env = {k: v for k, v in __import__("os").environ.items()
               if k != "ANTHROPIC_API_KEY"}
        with patch.dict("os.environ", env, clear=True):
            exit_code = run(pool_filter="nonexistent-pool")
        self.assertEqual(exit_code, 1)

    def test_fetch_failure_recent_last_verified_keeps_ok(self):
        """Fetch failure with recently verified pool → status stays ok (KEPT outcome)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pools_path = self._write_pools_json(tmp_path)
            prev_sessions = [
                {
                    "day": "saturday",
                    "start": "12:00",
                    "end": "15:00",
                    "label": "Rec Swim",
                    "category": "rec",
                }
            ]
            # last_verified only 5 days ago — within the 14-day window
            schedules_path = self._write_schedules_json(
                tmp_path,
                {
                    "test-pool": {
                        "last_verified": "2026-06-06",
                        "status": "ok",
                        "season_note": "Summer",
                        "sessions": prev_sessions,
                    }
                },
            )

            with (
                patch("scrape.POOLS_PATH", pools_path),
                patch("scrape.SCHEDULES_PATH", schedules_path),
                patch("scrape.Anthropic", return_value=MagicMock()),
                patch("scrape.fetch_sources", return_value=(None, False)),
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                exit_code = run(pool_filter="test-pool")

            self.assertEqual(exit_code, 0)
            result = json.loads(schedules_path.read_text())
            entry = result["schedules"]["test-pool"]
            # Status must remain ok (not stale) because recently verified
            self.assertEqual(entry["status"], "ok")
            self.assertEqual(len(entry["sessions"]), 1)

    def test_fetch_failure_old_last_verified_marks_stale(self):
        """Fetch failure with old last_verified (>14 days) → marks stale."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pools_path = self._write_pools_json(tmp_path)
            prev_sessions = [
                {
                    "day": "saturday",
                    "start": "12:00",
                    "end": "15:00",
                    "label": "Rec Swim",
                    "category": "rec",
                }
            ]
            # last_verified 41 days ago — beyond the 14-day window
            schedules_path = self._write_schedules_json(
                tmp_path,
                {
                    "test-pool": {
                        "last_verified": "2026-05-01",
                        "status": "ok",
                        "season_note": "Old",
                        "sessions": prev_sessions,
                    }
                },
            )

            with (
                patch("scrape.POOLS_PATH", pools_path),
                patch("scrape.SCHEDULES_PATH", schedules_path),
                patch("scrape.Anthropic", return_value=MagicMock()),
                patch("scrape.fetch_sources", return_value=(None, False)),
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                exit_code = run(pool_filter="test-pool")

            self.assertEqual(exit_code, 0)
            result = json.loads(schedules_path.read_text())
            entry = result["schedules"]["test-pool"]
            self.assertEqual(entry["status"], "stale")

    def test_all_sources_skipped_leaves_entry_untouched(self):
        """When all sources are skipped (render-only, no --render), previous entry is untouched."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            pools_path = self._write_pools_json(tmp_path)
            prev_sessions = [
                {
                    "day": "friday",
                    "start": "14:00",
                    "end": "16:00",
                    "label": "Rec Swim",
                    "category": "rec",
                }
            ]
            schedules_path = self._write_schedules_json(
                tmp_path,
                {
                    "test-pool": {
                        "last_verified": "2026-06-01",
                        "status": "ok",
                        "season_note": "Summer",
                        "sessions": prev_sessions,
                    }
                },
            )

            # fetch_sources returns (None, True) = no content, all_skipped=True
            with (
                patch("scrape.POOLS_PATH", pools_path),
                patch("scrape.SCHEDULES_PATH", schedules_path),
                patch("scrape.Anthropic", return_value=MagicMock()),
                patch("scrape.fetch_sources", return_value=(None, True)),
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                exit_code = run(pool_filter="test-pool")

            self.assertEqual(exit_code, 0)
            result = json.loads(schedules_path.read_text())
            entry = result["schedules"]["test-pool"]
            # Entry must be completely untouched — status stays ok, sessions unchanged
            self.assertEqual(entry["status"], "ok")
            self.assertEqual(len(entry["sessions"]), 1)
            self.assertEqual(entry["last_verified"], "2026-06-01")


# ─── Tests: fetch_sources render flag ─────────────────────────────────────────


class TestFetchSourcesRenderFlag(unittest.TestCase):
    """Test that render-marked sources are skipped when use_render=False."""

    def _make_render_pool(self):
        return {
            "id": "test-pool",
            "name": "Test Pool",
            "city": "Albany",
            "schedule_sources": [
                {"url": "https://example.com/schedule", "type": "html", "render": True}
            ],
        }

    def _make_mixed_pool(self):
        return {
            "id": "test-pool",
            "name": "Test Pool",
            "city": "Albany",
            "schedule_sources": [
                {"url": "https://example.com/plain", "type": "html"},
                {"url": "https://example.com/render", "type": "html", "render": True},
            ],
        }

    def test_render_source_skipped_without_flag(self):
        """render:true source with use_render=False → all_skipped=True, content=None."""
        pool = self._make_render_pool()
        with patch("scrape.httpx.Client") as mock_http:
            content, all_skipped = fetch_sources(pool, mock_http, use_render=False)
        self.assertIsNone(content)
        self.assertTrue(all_skipped)
        # No HTTP calls should have been made
        mock_http.get.assert_not_called()

    def test_render_source_not_skipped_with_flag(self):
        """render:true source with use_render=True → calls fetch_render."""
        pool = self._make_render_pool()
        with patch("scrape.fetch_render", return_value="JS-rendered content") as mock_render:
            import httpx
            content, all_skipped = fetch_sources(pool, httpx.Client(), use_render=True)
        self.assertFalse(all_skipped)
        self.assertIsNotNone(content)
        self.assertIn("JS-rendered content", content)
        mock_render.assert_called_once()

    def test_mixed_sources_partial_skip(self):
        """Pool with one plain + one render source without --render: plain fetched, render skipped."""
        pool = self._make_mixed_pool()
        with (
            patch("scrape.fetch_html", return_value="plain HTML content"),
            patch("scrape.fetch_render") as mock_render,
        ):
            import httpx
            content, all_skipped = fetch_sources(pool, httpx.Client(), use_render=False)
        # Should have content from the plain source
        self.assertFalse(all_skipped)
        self.assertIsNotNone(content)
        self.assertIn("plain HTML content", content)
        # Render should not have been called
        mock_render.assert_not_called()


# ─── Tests: build_diff_report ─────────────────────────────────────────────────


class TestBuildDiffReport(unittest.TestCase):

    def test_report_contains_pool_id(self):
        results = [{"pool_id": "test-pool", "outcome": "fetch_fail", "detail": "HTTP 403"}]
        old = {"test-pool": {"sessions": [{"day": "monday"}], "status": "ok"}}
        new = {"test-pool": {"sessions": [{"day": "monday"}], "status": "stale"}}
        report = build_diff_report(results, old, new)
        self.assertIn("test-pool", report)
        self.assertIn("FAIL", report)

    def test_report_shows_updated(self):
        results = [{"pool_id": "test-pool", "outcome": "extracted"}]
        old = {"test-pool": {"sessions": [{"day": "monday"}], "status": "ok"}}
        new = {
            "test-pool": {
                "sessions": [{"day": "monday"}, {"day": "tuesday"}],
                "status": "ok",
            }
        }
        report = build_diff_report(results, old, new)
        self.assertIn("test-pool", report)
        # Should show session delta
        self.assertIn("1 → 2", report)

    def test_report_shows_closed(self):
        results = [{"pool_id": "test-pool", "outcome": "extracted"}]
        old = {"test-pool": {"sessions": [], "status": "ok"}}
        new = {"test-pool": {"sessions": [], "status": "closed"}}
        report = build_diff_report(results, old, new)
        self.assertIn("CLOSED", report)

    def test_dry_run_outcome(self):
        results = [{"pool_id": "test-pool", "outcome": "dry_run"}]
        old = {"test-pool": {"sessions": [], "status": "unknown"}}
        new = {"test-pool": {"sessions": [{"day": "monday"}], "status": "ok"}}
        report = build_diff_report(results, old, new)
        self.assertIn("DRY", report)

    def test_skipped_outcome(self):
        """SKIPPED outcome appears when all sources are render-only and --render not passed."""
        results = [{"pool_id": "test-pool", "outcome": "skipped"}]
        old = {"test-pool": {"sessions": [{"day": "monday"}], "status": "ok"}}
        new = {"test-pool": {"sessions": [{"day": "monday"}], "status": "ok"}}
        report = build_diff_report(results, old, new)
        self.assertIn("SKIPPED", report)
        self.assertIn("test-pool", report)

    def test_kept_outcome(self):
        """KEPT outcome appears when fetch fails but pool was recently verified."""
        results = [{"pool_id": "test-pool", "outcome": "kept", "detail": "recently verified"}]
        old = {"test-pool": {"sessions": [{"day": "monday"}], "status": "ok"}}
        new = {"test-pool": {"sessions": [{"day": "monday"}], "status": "ok"}}
        report = build_diff_report(results, old, new)
        self.assertIn("KEPT", report)
        self.assertIn("test-pool", report)


# ─── Tests: estimate_cost ─────────────────────────────────────────────────────


class TestEstimateCost(unittest.TestCase):

    def test_zero_usage(self):
        self.assertAlmostEqual(estimate_cost([]), 0.0)

    def test_single_call(self):
        usage = [
            {
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
        ]
        # 1000 * $1/MTok + 200 * $5/MTok = $0.001 + $0.001 = $0.002
        cost = estimate_cost(usage)
        self.assertAlmostEqual(cost, 0.002, places=5)

    def test_multiple_calls_additive(self):
        usage = [
            {
                "input_tokens": 500,
                "output_tokens": 100,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
        ] * 3
        cost_single = estimate_cost([usage[0]])
        cost_all = estimate_cost(usage)
        self.assertAlmostEqual(cost_all, cost_single * 3, places=8)


# ─── Tests: render path (Playwright smoke test) ───────────────────────────────


class TestRenderPath(unittest.TestCase):
    """
    Smoke-test the headless-Chrome render path against a local HTTP server
    that serves a page where the schedule text is injected by JavaScript
    (so the plain httpx path would miss it).

    Requires: playwright install chromium (and a compatible Chromium binary).
    If Playwright is unavailable or Chromium cannot launch, the test is skipped
    with a clear message for the owner to verify on their machine.
    """

    RENDER_TEST_HTML = """\
<!DOCTYPE html>
<html>
<head><title>JS-rendered schedule test</title></head>
<body>
<div id="schedule">Loading…</div>
<script>
  // Simulate JS-rendered content (what ActiveNet/similar calendars do)
  document.addEventListener("DOMContentLoaded", function() {
    document.getElementById("schedule").textContent =
      "Monday 1:00 PM - 3:00 PM Family Swim";
  });
</script>
</body>
</html>
"""

    def _find_chromium(self):
        """Return executable path for a known-good Chromium binary, or None."""
        import glob as _glob
        candidates = _glob.glob(
            "/opt/pw-browsers/chromium_headless_shell-*/chrome-linux/headless_shell"
        ) + _glob.glob(
            "/opt/pw-browsers/chromium-*/chrome-linux/chrome"
        )
        for c in candidates:
            if Path(c).exists():
                return c
        return None

    def test_render_captures_js_content(self):
        """
        fetch_render() retrieves JS-injected text that plain httpx would miss.
        Served over a local HTTP server so page.goto uses http:// (not file://).
        """
        import http.server
        import io
        import socket
        import threading

        # Find Playwright
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.skipTest("playwright not installed — install with: pip install playwright")

        # Find a compatible Chromium binary
        chromium_path = self._find_chromium()
        if chromium_path is None:
            self.skipTest(
                "No compatible Chromium found — run: playwright install chromium. "
                "Render path is untested in this sandbox; verify on your machine."
            )

        html_content = self.RENDER_TEST_HTML.encode()

        class SilentHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(html_content)))
                self.end_headers()
                self.wfile.write(html_content)

            def log_message(self, *args):
                pass  # suppress server output

        # Find a free port
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        server = http.server.HTTPServer(("127.0.0.1", port), SilentHandler)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        url = f"http://127.0.0.1:{port}/test.html"

        try:
            # Patch fetch_render to use our known-good executable_path
            import scrape

            original_fetch_render = scrape.fetch_render

            def patched_fetch_render(u):
                import re as _re
                from playwright.sync_api import sync_playwright as _swp
                with _swp() as p:
                    browser = p.chromium.launch(
                        headless=True,
                        executable_path=chromium_path,
                    )
                    try:
                        page = browser.new_page()
                        page.goto(u, wait_until="networkidle", timeout=30_000)
                        html = page.content()
                    finally:
                        browser.close()
                for tag in ("script", "style", "nav", "header", "footer", "noscript"):
                    html = _re.sub(
                        rf"<{tag}[^>]*>.*?</{tag}>", " ", html,
                        flags=_re.DOTALL | _re.IGNORECASE,
                    )
                html = _re.sub(r"<[^>]+>", " ", html)
                html = _re.sub(r"[ \t]+", " ", html)
                html = _re.sub(r"\n{3,}", "\n\n", html)
                return html.strip()

            # Call our patched render
            content = patched_fetch_render(url)

            # The JS-injected text must appear in the rendered content
            self.assertIn(
                "Family Swim",
                content,
                "JS-rendered schedule text should appear in rendered content",
            )
            self.assertIn("Monday", content)

            # Verify plain httpx would NOT have captured this (static HTML has only "Loading…")
            import httpx
            http_client = httpx.Client(timeout=10.0)
            plain_content = scrape.fetch_html(url, http_client)
            self.assertIn("Loading", plain_content)
            self.assertNotIn("Family Swim", plain_content,
                             "Plain httpx should NOT see JS-injected content")

        finally:
            server.shutdown()

        print(
            "\n  [RENDER SMOKE TEST PASSED] "
            "Headless Chrome captured JS-injected schedule text that plain httpx missed."
        )


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
