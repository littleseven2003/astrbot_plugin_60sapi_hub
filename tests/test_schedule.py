from __future__ import annotations

import unittest
from datetime import datetime, timezone

from core.schedule import (
    DailySchedule,
    ScheduleTimezoneUnavailableError,
    ScheduleValidationError,
    next_daily_run,
    parse_schedule,
)


def dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


class ParseScheduleTest(unittest.TestCase):
    def test_valid_and_null(self) -> None:
        schedule = parse_schedule({
            "kind": "daily", "timezone": "Asia/Shanghai", "time": "08:30",
        })
        self.assertEqual(schedule, DailySchedule("Asia/Shanghai", schedule.time))
        self.assertEqual(schedule.to_dict(), {
            "kind": "daily", "timezone": "Asia/Shanghai", "time": "08:30",
        })
        self.assertIsNone(parse_schedule(None))

    def test_invalid_shapes_types_and_fields(self) -> None:
        invalid = [
            "daily", [], 1,
            {"kind": "weekly", "timezone": "UTC", "time": "08:00"},
            {"kind": "daily", "timezone": "UTC", "time": "8:00"},
            {"kind": "daily", "timezone": "UTC", "time": "24:00"},
            {"kind": "daily", "timezone": "UTC", "time": "08:60"},
            {"kind": "daily", "timezone": "UTC", "time": 830},
            {"kind": "daily", "timezone": "", "time": "08:00"},
            {"kind": "daily", "timezone": "UTC", "time": "08:00", "extra": 1},
            {"kind": "daily", "timezone": "UTC"},
            {"kind": "daily", "time": "08:00"},
            {"timezone": "UTC", "time": "08:00"},
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ScheduleValidationError):
                    parse_schedule(value)

    def test_invalid_timezone_is_explicit(self) -> None:
        with self.assertRaises(ScheduleTimezoneUnavailableError):
            parse_schedule({
                "kind": "daily", "timezone": "Not/A_Real_Zone", "time": "08:00",
            })


class NextDailyRunTest(unittest.TestCase):
    def test_utc_china_and_non_hour_timezone(self) -> None:
        utc = parse_schedule({"kind": "daily", "timezone": "UTC", "time": "08:30"})
        self.assertEqual(
            next_daily_run(utc, dt("2026-01-01T08:29:59+00:00")),
            dt("2026-01-01T08:30:00+00:00"),
        )
        china = parse_schedule({
            "kind": "daily", "timezone": "Asia/Shanghai", "time": "08:30",
        })
        self.assertEqual(
            next_daily_run(china, dt("2026-01-01T00:00:00+00:00")),
            dt("2026-01-01T00:30:00+00:00"),
        )
        nepal = parse_schedule({
            "kind": "daily", "timezone": "Asia/Kathmandu", "time": "08:30",
        })
        self.assertEqual(
            next_daily_run(nepal, dt("2026-01-01T00:00:00+00:00")),
            dt("2026-01-01T02:45:00+00:00"),
        )

    def test_exact_time_cross_day_year_and_leap(self) -> None:
        schedule = parse_schedule({"kind": "daily", "timezone": "UTC", "time": "08:30"})
        self.assertEqual(
            next_daily_run(schedule, dt("2026-01-01T08:30:00+00:00")),
            dt("2026-01-02T08:30:00+00:00"),
        )
        year = parse_schedule({"kind": "daily", "timezone": "UTC", "time": "23:59"})
        self.assertEqual(
            next_daily_run(year, dt("2026-12-31T23:59:00+00:00")),
            dt("2027-01-01T23:59:00+00:00"),
        )
        leap = parse_schedule({"kind": "daily", "timezone": "UTC", "time": "12:00"})
        self.assertEqual(
            next_daily_run(leap, dt("2028-02-28T12:00:00+00:00")),
            dt("2028-02-29T12:00:00+00:00"),
        )

    def test_dst_gap_skips_day(self) -> None:
        schedule = parse_schedule({
            "kind": "daily", "timezone": "America/New_York", "time": "02:30",
        })
        self.assertEqual(
            next_daily_run(schedule, dt("2026-03-08T00:00:00-05:00")),
            dt("2026-03-09T02:30:00-04:00"),
        )

    def test_dst_fold_uses_first_only(self) -> None:
        schedule = parse_schedule({
            "kind": "daily", "timezone": "America/New_York", "time": "01:30",
        })
        first = next_daily_run(schedule, dt("2026-11-01T00:00:00-04:00"))
        self.assertEqual(first, dt("2026-11-01T01:30:00-04:00"))
        self.assertEqual(first.astimezone(timezone.utc), dt("2026-11-01T05:30:00+00:00"))
        # Between the two occurrences: do not select the second one today.
        after_first = next_daily_run(
            schedule, dt("2026-11-01T01:45:00-04:00"),
        )
        self.assertEqual(after_first, dt("2026-11-02T01:30:00-05:00"))
        # Exactly at the first occurrence: next day.
        exact = next_daily_run(
            schedule, dt("2026-11-01T01:30:00-04:00"),
        )
        self.assertEqual(exact, dt("2026-11-02T01:30:00-05:00"))

    def test_naive_now_is_rejected(self) -> None:
        schedule = DailySchedule("UTC", __import__("datetime").time(8, 0))
        with self.assertRaises(ScheduleValidationError):
            next_daily_run(schedule, datetime(2026, 1, 1, 8, 0))
