"""每日/每周/间隔计划的严格模型和纯时间计算。

只计算理论上的下一次本地触发时间；不创建后台任务、不写运行状态。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_LOCAL_DATETIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$")
_MAX_DAYS = 370


class ScheduleError(Exception):
    """计划配置错误基类。"""


class ScheduleValidationError(ScheduleError):
    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        self.reason = reason
        super().__init__(f"schedule.{field} 无效：{reason}")


class ScheduleTimezoneUnavailableError(ScheduleError):
    def __init__(self, timezone_name: str) -> None:
        self.timezone_name = timezone_name
        super().__init__(f"时区 {timezone_name} 的时区数据不可用。")


class ScheduleCalculationError(ScheduleError):
    """无法在边界内计算下一次计划时间。"""


@dataclass(frozen=True, slots=True)
class IntervalSchedule:
    timezone: str
    start_at: str
    interval_minutes: int

    @property
    def kind(self) -> str:
        return "interval"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "interval",
            "timezone": self.timezone,
            "start_at": self.start_at,
            "interval_minutes": self.interval_minutes,
        }


@dataclass(frozen=True, slots=True)
class WeeklySchedule:
    timezone: str
    time: time
    weekdays: tuple[int, ...]

    @property
    def kind(self) -> str:
        return "weekly"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": "weekly",
            "timezone": self.timezone,
            "time": self.time.strftime("%H:%M"),
            "weekdays": list(self.weekdays),
        }


@dataclass(frozen=True, slots=True)
class DailySchedule:
    timezone: str
    time: time

    @property
    def kind(self) -> str:
        return "daily"

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": "daily",
            "timezone": self.timezone,
            "time": self.time.strftime("%H:%M"),
        }


def parse_schedule(
    value: object,
) -> DailySchedule | WeeklySchedule | IntervalSchedule | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ScheduleValidationError("schedule", "必须是对象或 null")
    kind = value.get("kind")
    if kind == "weekly":
        allowed = {"kind", "timezone", "time", "weekdays"}
    elif kind == "interval":
        allowed = {
            "kind", "timezone", "start_at", "interval_minutes",
        }
    else:
        allowed = {"kind", "timezone", "time"}
    unknown = set(value) - allowed
    if unknown:
        raise ScheduleValidationError(
            "schedule",
            "包含未知字段：" + "、".join(sorted(map(str, unknown))),
        )
    if kind not in ("daily", "weekly", "interval"):
        raise ScheduleValidationError("kind", "只支持 daily、weekly 或 interval")
    timezone_name = value.get("timezone")
    if not isinstance(timezone_name, str) or not timezone_name.strip():
        raise ScheduleValidationError("timezone", "必须是非空 IANA 时区名称")
    timezone_name = timezone_name.strip()
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise ScheduleTimezoneUnavailableError(timezone_name) from None
    except Exception:
        raise ScheduleValidationError("timezone", "必须是有效 IANA 时区") from None
    if getattr(zone, "key", None) is None:
        raise ScheduleValidationError("timezone", "必须是 IANA 时区名称")
    if kind == "interval":
        raw_start = value.get("start_at")
        if not isinstance(raw_start, str):
            raise ScheduleValidationError(
                "start_at", "必须是 YYYY-MM-DDTHH:mm 本地时间",
            )
        match = _LOCAL_DATETIME_RE.fullmatch(raw_start)
        if match is None:
            raise ScheduleValidationError(
                "start_at", "必须是 YYYY-MM-DDTHH:mm 本地时间",
            )
        try:
            local_start = datetime(
                int(match.group(1)), int(match.group(2)), int(match.group(3)),
                int(match.group(4)), int(match.group(5)),
            )
        except ValueError:
            raise ScheduleValidationError("start_at", "日期或时间无效") from None
        if _candidate_for_local_day(
            local_start.date(), local_start.time(), zone,
        ) is None:
            raise ScheduleValidationError("start_at", "起点落入 DST 缺口")
        raw_interval = value.get("interval_minutes")
        if type(raw_interval) is not int or raw_interval < 1 or raw_interval > 10080:
            raise ScheduleValidationError(
                "interval_minutes", "必须是 1～10080 的整数",
            )
        return IntervalSchedule(
            timezone=timezone_name, start_at=raw_start,
            interval_minutes=raw_interval,
        )
    raw_time = value.get("time")
    if not isinstance(raw_time, str) or not _TIME_RE.fullmatch(raw_time):
        raise ScheduleValidationError("time", "必须是 HH:mm 24 小时制")
    hour, minute = raw_time.split(":")
    parsed_time = time(int(hour), int(minute))
    if kind == "daily":
        return DailySchedule(timezone=timezone_name, time=parsed_time)
    raw_days = value.get("weekdays")
    if not isinstance(raw_days, list) or not raw_days:
        raise ScheduleValidationError("weekdays", "必须是非空数组")
    if len(raw_days) > 7:
        raise ScheduleValidationError("weekdays", "最多包含 7 个星期")
    days = []
    seen = set()
    for day in raw_days:
        if type(day) is not int or day < 1 or day > 7:
            raise ScheduleValidationError("weekdays", "必须是 1～7 的整数")
        if day in seen:
            raise ScheduleValidationError("weekdays", "不能包含重复星期")
        seen.add(day)
        days.append(day)
    return WeeklySchedule(
        timezone=timezone_name, time=parsed_time,
        weekdays=tuple(sorted(days)),
    )


def next_scheduled_run(
    schedule: DailySchedule | WeeklySchedule | IntervalSchedule,
    now: datetime,
    *,
    zone: ZoneInfo | None = None,
) -> datetime:
    """统一入口；根据计划类型计算严格晚于 now 的 UTC 时刻。"""
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ScheduleValidationError("now", "必须带时区")
    try:
        zone = zone or ZoneInfo(schedule.timezone)
    except ZoneInfoNotFoundError:
        raise ScheduleTimezoneUnavailableError(schedule.timezone) from None
    if isinstance(schedule, IntervalSchedule):
        return _next_interval_run(schedule, now, zone)
    local_now = now.astimezone(zone)
    selected = (
        set(schedule.weekdays) if isinstance(schedule, WeeklySchedule) else None
    )
    for offset in range(_MAX_DAYS + 1):
        day = local_now.date() + timedelta(days=offset)
        if selected is not None and day.isoweekday() not in selected:
            continue
        candidate = _candidate_for_local_day(day, schedule.time, zone)
        if candidate is None:
            continue
        if candidate > now.astimezone(timezone.utc):
            return candidate
    raise ScheduleCalculationError("无法在边界内计算下一次每周计划时间")


def next_daily_run(
    schedule: DailySchedule,
    now: datetime,
    *,
    zone: ZoneInfo | None = None,
) -> datetime:
    """返回严格晚于 now 的下一次理论触发时间（UTC）。

    当前时刻恰好等于候选时刻时跳过当天；DST 缺口跳过当天；重复时间只取
    第一次出现，第一次不晚于 now 时也不选择第二次。
    """
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ScheduleValidationError("now", "必须带时区")
    try:
        zone = zone or ZoneInfo(schedule.timezone)
    except ZoneInfoNotFoundError:
        raise ScheduleTimezoneUnavailableError(schedule.timezone) from None
    local_now = now.astimezone(zone)
    for offset in range(_MAX_DAYS + 1):
        day = local_now.date() + timedelta(days=offset)
        candidate = _candidate_for_local_day(day, schedule.time, zone)
        if candidate is None:
            continue
        if candidate > now.astimezone(timezone.utc):
            return candidate
    raise ScheduleCalculationError("无法在边界内计算下一次每日计划时间")


def _next_interval_run(
    schedule: IntervalSchedule,
    now: datetime,
    zone: tzinfo,
) -> datetime:
    match = _LOCAL_DATETIME_RE.fullmatch(schedule.start_at)
    if match is None:
        raise ScheduleValidationError("start_at", "起点格式无效")
    local_start = datetime(
        int(match.group(1)), int(match.group(2)), int(match.group(3)),
        int(match.group(4)), int(match.group(5)),
    )
    anchor = _candidate_for_local_day(
        local_start.date(), local_start.time(), zone,
    )
    if anchor is None:
        raise ScheduleValidationError("start_at", "起点落入 DST 缺口")
    now_utc = now.astimezone(timezone.utc)
    step = timedelta(minutes=schedule.interval_minutes)
    if now_utc < anchor:
        return anchor
    delta = now_utc - anchor
    steps = delta // step + 1
    return anchor + steps * step


def _candidate_for_local_day(
    day: date,
    local_time: time,
    zone: tzinfo,
) -> datetime | None:
    # fold=0 and fold=1 give the two possible offsets. Convert each to UTC and
    # back to verify that the local wall time actually exists.
    candidates: list[datetime] = []
    for fold in (0, 1):
        local = datetime.combine(day, local_time, tzinfo=zone).replace(fold=fold)
        utc = local.astimezone(timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) != datetime.combine(
            day, local_time,
        ):
            continue
        candidates.append(utc)
    if not candidates:
        return None
    # Ambiguous local time: take the earlier UTC instant only. If it is not
    # strictly after now, the caller skips the day rather than using fold=1.
    return min(candidates)


__all__ = [
    "DailySchedule",
    "IntervalSchedule",
    "WeeklySchedule",
    "ScheduleCalculationError",
    "ScheduleError",
    "ScheduleTimezoneUnavailableError",
    "ScheduleValidationError",
    "next_daily_run",
    "next_scheduled_run",
    "parse_schedule",
]
