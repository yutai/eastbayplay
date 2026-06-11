#!/usr/bin/env python3
"""
scripts/test_scraper.py — Unit tests for scraper/scrape.py.

Covers:
  - merge keeps previous data on fetch failure
  - empty extraction rejected when previous had sessions
  - >40 sessions rejected as hallucination
  - pool_appears_closed handling
  - low-confidence extraction rejected
  - accepted extraction updates status and last_verified

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
    _unknown_entry,
    build_diff_report,
    decide_merge,
    estimate_cost,
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
):
    if sessions is None:
        sessions = [make_session()]
    return PoolSchedule(
        sessions=sessions,
        season_note=season_note,
        pool_appears_closed=pool_appears_closed,
        extraction_confidence=extraction_confidence,
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
        """More than 40 sessions → hallucination guard, keep previous."""
        sessions = [make_session(day="monday")] * 45
        extracted = make_pool_schedule(
            sessions=sessions,
            extraction_confidence="high",
        )
        previous = previous_entry_with_sessions(3, status="ok")
        result = decide_merge("test-pool", extracted, previous, TODAY)
        self.assertEqual(result["status"], "stale")
        self.assertEqual(len(result["sessions"]), 3)

    def test_too_many_sessions_no_previous(self):
        """More than 40 sessions, no previous → unknown."""
        sessions = [make_session()] * 41
        extracted = make_pool_schedule(sessions=sessions, extraction_confidence="high")
        result = decide_merge("test-pool", extracted, None, TODAY)
        self.assertEqual(result["status"], "unknown")

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
                patch("scrape.fetch_sources", return_value=None),
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
                patch("scrape.fetch_sources", return_value="<html>some content</html>"),
                patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}),
            ):
                exit_code = run(pool_filter="test-pool")

            self.assertEqual(exit_code, 0)
            result = json.loads(schedules_path.read_text())
            entry = result["schedules"]["test-pool"]
            self.assertEqual(entry["status"], "stale")
            self.assertEqual(len(entry["sessions"]), 1)

    def test_over_40_sessions_rejected(self):
        """More than 40 sessions → rejected, previous kept and marked stale."""
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

            big_sessions = [make_session()] * 42
            big_schedule = make_pool_schedule(
                sessions=big_sessions, extraction_confidence="high"
            )
            mock_client = self._make_mock_parse(big_schedule)

            with (
                patch("scrape.POOLS_PATH", pools_path),
                patch("scrape.SCHEDULES_PATH", schedules_path),
                patch("scrape.Anthropic", return_value=mock_client),
                patch("scrape.fetch_sources", return_value="some content"),
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
                patch("scrape.fetch_sources", return_value="Pool closed for renovation"),
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
                patch("scrape.fetch_sources", return_value="schedule coming soon"),
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
                patch("scrape.fetch_sources", return_value="Mon/Wed/Fri 1-3pm Rec Swim"),
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
                patch("scrape.fetch_sources", return_value="Mon 1-3pm Rec Swim"),
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
                patch("scrape.fetch_sources", return_value="M/W 1-3pm Rec Swim"),
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


# ─── Run ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
