"""每日/每周/间隔/Cron 计划的严格模型和纯时间计算。

只计算理论上的下一次本地触发时间；不创建后台任务、不写运行状态。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_TIME_RE = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
_LOCAL_DATETIME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$")
_CRON_FIELDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
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
class CronSchedule:
    timezone: str
    expression: str

    @property
    def kind(self) -> str:
        return "cron"

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": "cron",
            "timezone": self.timezone,
            "expression": self.expression,
        }


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
) -> DailySchedule | WeeklySchedule | IntervalSchedule | CronSchedule | None:
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
    elif kind == "cron":
        allowed = {"kind", "timezone", "expression"}
    else:
        allowed = {"kind", "timezone", "time"}
    unknown = set(value) - allowed
    if unknown:
        raise ScheduleValidationError(
            "schedule",
            "包含未知字段：" + "、".join(sorted(map(str, unknown))),
        )
    if kind not in ("daily", "weekly", "interval", "cron"):
        raise ScheduleValidationError("kind", "只支持 daily、weekly、interval 或 cron")
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
    if kind == "cron":
        raw_expression = value.get("expression")
        if not isinstance(raw_expression, str):
            raise ScheduleValidationError("expression", "必须是字符串")
        expression = _normalize_cron(raw_expression)
        return CronSchedule(timezone=timezone_name, expression=expression)
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
    schedule: DailySchedule | WeeklySchedule | IntervalSchedule | CronSchedule,
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
    if isinstance(schedule, CronSchedule):
        return _next_cron_run(schedule, now, zone)
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


def _normalize_cron(value: str) -> str:
    if len(value) > 128:
        raise ScheduleValidationError("expression", "长度不能超过 128 字符")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value):
        raise ScheduleValidationError("expression", "不能包含控制字符")
    fields = value.split()
    if len(fields) != 5:
        raise ScheduleValidationError("expression", "必须是五字段 Cron 表达式")
    normalized: list[str] = []
    for raw, (minimum, maximum) in zip(fields, _CRON_FIELDS):
        normalized.append(_validate_cron_field(raw, minimum, maximum))
    return " ".join(normalized)


def _ascii_digits(value: str) -> bool:
    return bool(value) and all("0" <= ch <= "9" for ch in value)


def _validate_cron_field(value: str, minimum: int, maximum: int) -> str:
    if not value:
        raise ScheduleValidationError("expression", "字段不能为空")
    if any(ch in value for ch in "?LW#HR"):
        raise ScheduleValidationError("expression", "包含不支持的扩展语法")
    for part in value.split(","):
        if not part:
            raise ScheduleValidationError("expression", "不允许空列表项")
        if part == "*":
            continue
        step = None
        base = part
        if "/" in part:
            pieces = part.split("/")
            if (
                len(pieces) != 2 or not pieces[0]
                or not _ascii_digits(pieces[1])
            ):
                raise ScheduleValidationError("expression", "步长必须是正整数")
            base, step_text = pieces
            step = int(step_text)
            if step <= 0 or step > maximum - minimum + 1:
                raise ScheduleValidationError("expression", "步长超出字段范围")
        if base == "*":
            if step is None:
                raise ScheduleValidationError("expression", "无效步长字段")
            continue
        if step is not None and "-" not in base:
            raise ScheduleValidationError("expression", "步长只支持 */n 或 a-b/n")
        if "-" in base:
            bounds = base.split("-")
            if len(bounds) != 2 or not all(_ascii_digits(piece) for piece in bounds):
                raise ScheduleValidationError("expression", "范围必须是递增整数")
            start, end = map(int, bounds)
            if start >= end:
                raise ScheduleValidationError("expression", "范围必须递增且端点不同")
            if start < minimum or end > maximum:
                raise ScheduleValidationError("expression", "数值超出字段范围")
        else:
            if not _ascii_digits(base):
                raise ScheduleValidationError("expression", "只支持整数、范围、列表和步长")
            start = int(base)
            if start < minimum or start > maximum:
                raise ScheduleValidationError("expression", "数值超出字段范围")
        if step is not None and step > maximum - minimum + 1:
            raise ScheduleValidationError("expression", "步长超出字段范围")
    return value


def _next_cron_run(
    schedule: CronSchedule,
    now: datetime,
    zone: tzinfo,
) -> datetime:
    try:
        from croniter import croniter
    except ImportError:
        raise ScheduleCalculationError("croniter 依赖不可用") from None
    local_now = now.astimezone(zone).replace(tzinfo=None)
    limit = now.astimezone(timezone.utc) + timedelta(days=366 * 8)
    base = local_now - timedelta(microseconds=1)
    try:
        iterator = croniter(
            schedule.expression, base, day_or=True,
            max_years_between_matches=8,
        )
        for _ in range(100000):
            candidate_local = iterator.get_next(datetime)
            candidate = _candidate_for_local_day(
                candidate_local.date(), candidate_local.time(), zone,
            )
            if candidate is None:
                continue
            if candidate > limit:
                break
            if candidate > now.astimezone(timezone.utc):
                return candidate
            if candidate_local > limit.astimezone(zone).replace(tzinfo=None):
                break
    except Exception as exc:
        from croniter import CroniterBadDateError, CroniterError
        if isinstance(exc, CroniterBadDateError):
            raise ScheduleCalculationError("8 年内没有可用的 Cron 候选") from None
        if isinstance(exc, CroniterError):
            raise ScheduleValidationError("expression", "无法解析 Cron 表达式") from None
        raise
    raise ScheduleCalculationError("8 年内没有可用的 Cron 候选")


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
    "CronSchedule",
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
