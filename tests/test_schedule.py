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


class WeeklyScheduleTest(unittest.TestCase):
    def test_weekly_parsing_normalizes_and_rejects_invalid(self) -> None:
        from core.schedule import WeeklySchedule
        value = {
            "kind": "weekly", "timezone": "Asia/Shanghai", "time": "08:30",
            "weekdays": [5, 1, 3],
        }
        schedule = parse_schedule(value)
        self.assertEqual(schedule, WeeklySchedule(
            "Asia/Shanghai", schedule.time, (1, 3, 5),
        ))
        self.assertEqual(schedule.to_dict()["weekdays"], [1, 3, 5])
        for bad in (
            [], [0], [8], [True], ["1"], [1, 1], [1, 2, 3, 4, 5, 6, 7, 1],
        ):
            with self.subTest(weekdays=bad):
                with self.assertRaises(ScheduleValidationError):
                    parse_schedule({**value, "weekdays": bad})
        with self.assertRaises(ScheduleValidationError):
            parse_schedule({**value, "unknown": 1})
        with self.assertRaises(ScheduleValidationError):
            parse_schedule({"kind": "daily", "timezone": "UTC",
                            "time": "08:30", "weekdays": [1]})

    def test_weekly_next_run_exact_and_weekday(self) -> None:
        from core.schedule import next_scheduled_run
        schedule = parse_schedule({
            "kind": "weekly", "timezone": "Asia/Shanghai", "time": "08:30",
            "weekdays": [1, 3, 5],
        })
        now = dt("2026-01-05T00:30:00+00:00")  # Monday 08:30 local
        self.assertEqual(
            next_scheduled_run(schedule, now),
            dt("2026-01-07T00:30:00+00:00"),
        )

    def test_weekly_local_weekday_differs_from_utc(self) -> None:
        from core.schedule import next_scheduled_run
        schedule = parse_schedule({
            "kind": "weekly", "timezone": "Asia/Shanghai", "time": "08:30",
            "weekdays": [2],
        })
        now = dt("2026-01-05T23:00:00+00:00")  # Tuesday 07:00 local
        self.assertEqual(
            next_scheduled_run(schedule, now),
            dt("2026-01-06T00:30:00+00:00"),
        )

    def test_weekly_cross_year_and_sunday_to_monday(self) -> None:
        from core.schedule import next_scheduled_run
        friday = parse_schedule({
            "kind": "weekly", "timezone": "UTC", "time": "08:30",
            "weekdays": [5],
        })
        self.assertEqual(
            next_scheduled_run(friday, dt("2026-12-31T09:00:00+00:00")),
            dt("2027-01-01T08:30:00+00:00"),
        )
        monday = parse_schedule({
            "kind": "weekly", "timezone": "UTC", "time": "08:30",
            "weekdays": [1],
        })
        self.assertEqual(
            next_scheduled_run(monday, dt("2026-01-04T08:30:00+00:00")),
            dt("2026-01-05T08:30:00+00:00"),
        )

    def test_weekly_dst_gap_and_fold(self) -> None:
        from core.schedule import next_scheduled_run
        gap = parse_schedule({
            "kind": "weekly", "timezone": "America/New_York", "time": "02:30",
            "weekdays": [7],
        })
        self.assertEqual(
            next_scheduled_run(gap, dt("2026-03-08T00:00:00+00:00")),
            dt("2026-03-15T06:30:00+00:00"),
        )
        fold = parse_schedule({
            "kind": "weekly", "timezone": "America/New_York", "time": "01:30",
            "weekdays": [7],
        })
        self.assertEqual(
            next_scheduled_run(fold, dt("2026-11-01T00:00:00+00:00")),
            dt("2026-11-01T05:30:00+00:00"),
        )
        self.assertEqual(
            next_scheduled_run(fold, dt("2026-11-01T05:30:00+00:00")),
            dt("2026-11-08T06:30:00+00:00"),
        )


class IntervalScheduleTest(unittest.TestCase):
    def test_interval_parse_and_reject_unknown(self) -> None:
        from core.schedule import IntervalSchedule
        schedule = parse_schedule({
            "kind": "interval", "timezone": "Asia/Shanghai",
            "start_at": "2026-11-04T08:30", "interval_minutes": 60,
        })
        self.assertEqual(schedule, IntervalSchedule(
            "Asia/Shanghai", "2026-11-04T08:30", 60,
        ))
        for bad in (
            {"interval_minutes": 0}, {"interval_minutes": 10081},
            {"interval_minutes": True}, {"interval_minutes": "60"},
            {"start_at": "2026-11-04T08:30:00"},
            {"start_at": "2026-11-04T08:30+08:00"},
            {"start_at": "2026-11-04 08:30"},
            {"unknown": 1},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ScheduleValidationError):
                    parse_schedule({
                        "kind": "interval", "timezone": "UTC",
                        "start_at": "2026-11-04T08:30",
                        "interval_minutes": 60, **bad,
                    })
        with self.assertRaises(ScheduleValidationError):
            parse_schedule({
                "kind": "daily", "timezone": "UTC", "time": "08:30",
                "interval_minutes": 60,
            })

    def test_interval_future_past_exact_and_no_drift(self) -> None:
        from core.schedule import next_scheduled_run
        schedule = parse_schedule({
            "kind": "interval", "timezone": "Asia/Shanghai",
            "start_at": "2026-11-04T08:30", "interval_minutes": 60,
        })
        anchor = dt("2026-11-04T00:30:00+00:00")
        self.assertEqual(next_scheduled_run(schedule, dt("2026-11-03T00:00:00+00:00")), anchor)
        self.assertEqual(next_scheduled_run(schedule, anchor), dt("2026-11-04T01:30:00+00:00"))
        self.assertEqual(next_scheduled_run(schedule, dt("2026-11-04T00:31:00+00:00")), dt("2026-11-04T01:30:00+00:00"))
        self.assertEqual(next_scheduled_run(schedule, dt("2026-11-04T03:30:00+00:00")), dt("2026-11-04T04:30:00+00:00"))
        self.assertEqual(next_scheduled_run(schedule, dt("2027-11-04T03:30:00+00:00")), dt("2027-11-04T04:30:00+00:00"))

    def test_interval_dst_gap_and_fixed_elapsed_minutes(self) -> None:
        from core.schedule import next_scheduled_run
        with self.assertRaises(ScheduleValidationError):
            parse_schedule({
                "kind": "interval", "timezone": "America/New_York",
                "start_at": "2026-03-08T02:30", "interval_minutes": 60,
            })
        schedule = parse_schedule({
            "kind": "interval", "timezone": "America/New_York",
            "start_at": "2026-03-07T01:30", "interval_minutes": 60,
        })
        self.assertEqual(
            next_scheduled_run(schedule, dt("2026-03-08T06:30:00+00:00")),
            dt("2026-03-08T07:30:00+00:00"),
        )


class CronScheduleTest(unittest.TestCase):
    def test_cron_parse_and_reject_unsupported(self) -> None:
        from core.schedule import CronSchedule
        schedule = parse_schedule({
            "kind": "cron", "timezone": "Asia/Shanghai",
            "expression": "30 8 * * 1-5",
        })
        self.assertEqual(schedule, CronSchedule("Asia/Shanghai", "30 8 * * 1-5"))
        for bad in (
            "0 0 * *", "0 0 * * * *", "60 0 * * *", "0 24 * * *",
            "0 0 0 * *", "0 0 * 13 *", "0 0 * * 8", "0 0 * * ?",
            "0 0 * * 1/2", "0 0 * * 2-1", "0 0 * * 1-1",
            "*/0 0 * * *", "0 0 * * MON", "0 0 L * *", "0 0 * * 1#2",
            "0 0 * * 1\n",
        ):
            with self.subTest(expression=bad):
                with self.assertRaises(ScheduleValidationError):
                    parse_schedule({"kind": "cron", "timezone": "UTC",
                                    "expression": bad})
        with self.assertRaises(ScheduleValidationError):
            parse_schedule({"kind": "daily", "timezone": "UTC",
                            "time": "08:30", "expression": "0 8 * * *"})

    def test_cron_or_semantics_sunday_and_boundaries(self) -> None:
        from core.schedule import next_scheduled_run
        schedule = parse_schedule({
            "kind": "cron", "timezone": "Asia/Shanghai",
            "expression": "30 8 1 * 1",
        })
        # 2026-11-01 is Sunday; day=1 and Monday OR should pick Sunday.
        self.assertEqual(
            next_scheduled_run(schedule, dt("2026-10-31T00:00:00+00:00")),
            dt("2026-11-01T00:30:00+00:00"),
        )
        sunday = parse_schedule({
            "kind": "cron", "timezone": "UTC", "expression": "0 0 * * 0",
        })
        self.assertEqual(
            next_scheduled_run(sunday, dt("2026-01-03T00:00:00+00:00")),
            dt("2026-01-04T00:00:00+00:00"),
        )
        seven = parse_schedule({
            "kind": "cron", "timezone": "UTC", "expression": "0 0 * * 7",
        })
        self.assertEqual(
            next_scheduled_run(seven, dt("2026-01-03T00:00:00+00:00")),
            dt("2026-01-04T00:00:00+00:00"),
        )

    def test_cron_exact_next_year_and_dst(self) -> None:
        from core.schedule import next_scheduled_run
        schedule = parse_schedule({
            "kind": "cron", "timezone": "UTC", "expression": "0 0 1 1 *",
        })
        self.assertEqual(
            next_scheduled_run(schedule, dt("2026-01-01T00:00:00+00:00")),
            dt("2027-01-01T00:00:00+00:00"),
        )
        gap = parse_schedule({
            "kind": "cron", "timezone": "America/New_York",
            "expression": "30 2 * * *",
        })
        start = dt("2026-03-08T00:00:00+00:00")
        self.assertEqual(
            next_scheduled_run(gap, start),
            dt("2026-03-09T06:30:00+00:00"),
        )


class CronBoundaryTest(unittest.TestCase):
    def test_cron_max_years_and_sparse_candidates(self) -> None:
        from unittest.mock import patch
        from croniter import croniter as real_croniter
        from core.schedule import CronSchedule, ScheduleCalculationError, next_scheduled_run
        seen = {}
        def recording(*args, **kwargs):
            seen.update(kwargs)
            return real_croniter(*args, **kwargs)
        with patch("croniter.croniter", side_effect=recording):
            next_scheduled_run(CronSchedule("UTC", "0 0 * * *"), dt("2026-01-01T00:00:00+00:00"))
        self.assertEqual(seen.get("max_years_between_matches"), 8)
        no_match = CronSchedule("UTC", "0 0 31 2 *")
        with self.assertRaises(ScheduleCalculationError):
            next_scheduled_run(no_match, dt("2026-01-01T00:00:00+00:00"))
        leap = CronSchedule("UTC", "0 0 29 2 *")
        self.assertEqual(
            next_scheduled_run(leap, dt("2026-03-01T00:00:00+00:00")),
            dt("2028-02-29T00:00:00+00:00"),
        )

    def test_cron_rejects_unicode_and_oversized_steps(self) -> None:
        for expression in (
            "٠ 0 * * *", "0 ٠ * * *", "*/61 * * * *",
            "0-59/61 * * * *", "0 0 * * 1,", "0 0 * * 1,,2",
            "0 0 * * 1-2-3", "0 0 * * 1/2",
        ):
            with self.subTest(expression=expression):
                with self.assertRaises(ScheduleValidationError):
                    parse_schedule({
                        "kind": "cron", "timezone": "UTC",
                        "expression": expression,
                    })


class CronDstAndTimezoneTest(unittest.TestCase):
    def test_cron_dst_fold_uses_first_occurrence_only(self) -> None:
        from core.schedule import CronSchedule, next_scheduled_run
        schedule = CronSchedule("America/New_York", "30 1 * * *")
        self.assertEqual(
            next_scheduled_run(schedule, dt("2026-11-01T00:00:00+00:00")),
            dt("2026-11-01T05:30:00+00:00"),
        )
        self.assertEqual(
            next_scheduled_run(schedule, dt("2026-11-01T05:31:00+00:00")),
            dt("2026-11-02T06:30:00+00:00"),
        )

    def test_cron_non_hour_offset_timezone(self) -> None:
        from core.schedule import CronSchedule, next_scheduled_run
        schedule = CronSchedule("Asia/Kolkata", "5 9 * * *")
        self.assertEqual(
            next_scheduled_run(schedule, dt("2026-01-01T00:00:00+00:00")),
            dt("2026-01-01T03:35:00+00:00"),
        )
