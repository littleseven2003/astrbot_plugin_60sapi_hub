"""每日计划的严格模型和纯时间计算。

只计算理论上的下一次本地触发时间；不创建后台任务、不写运行状态。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
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


def parse_schedule(value: object) -> DailySchedule | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ScheduleValidationError("schedule", "必须是对象或 null")
    allowed = {"kind", "timezone", "time"}
    unknown = set(value) - allowed
    if unknown:
        raise ScheduleValidationError(
            "schedule",
            "包含未知字段：" + "、".join(sorted(map(str, unknown))),
        )
    kind = value.get("kind")
    if kind != "daily":
        raise ScheduleValidationError("kind", "只支持 daily")
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
    raw_time = value.get("time")
    if not isinstance(raw_time, str) or not _TIME_RE.fullmatch(raw_time):
        raise ScheduleValidationError("time", "必须是 HH:mm 24 小时制")
    hour, minute = raw_time.split(":")
    return DailySchedule(timezone=timezone_name, time=time(int(hour), int(minute)))


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
    "ScheduleCalculationError",
    "ScheduleError",
    "ScheduleTimezoneUnavailableError",
    "ScheduleValidationError",
    "next_daily_run",
    "parse_schedule",
]
