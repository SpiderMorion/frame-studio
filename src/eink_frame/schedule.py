"""Pure schedule calculation and due-slot domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo

GENERATION_LEAD = timedelta(minutes=15)


class ScheduleMode(str, Enum):
    """Supported ways to plan frame display times."""

    INTERVAL = "interval"
    CUSTOM = "custom"


@dataclass(frozen=True, slots=True)
class ScheduleSpec:
    """Immutable local-time display schedule."""

    mode: ScheduleMode
    enabled: bool = True
    interval_minutes: int = 60
    anchor_time: time = time(0, 0)
    custom_times: tuple[time, ...] = ()
    quiet_start: time | None = None
    quiet_end: time | None = None
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Moscow"))

    def __post_init__(self) -> None:
        if not isinstance(self.mode, ScheduleMode):
            try:
                object.__setattr__(self, "mode", ScheduleMode(self.mode))
            except ValueError as error:
                raise ValueError("mode must be 'interval' or 'custom'") from error
        if not 30 <= self.interval_minutes <= 1440:
            raise ValueError("interval_minutes must be between 30 and 1440")
        _validate_local_time(self.anchor_time, "anchor_time")
        normalized_times = tuple(sorted(set(self.custom_times)))
        for custom_time in normalized_times:
            _validate_local_time(custom_time, "custom_times")
        object.__setattr__(self, "custom_times", normalized_times)
        if self.mode is ScheduleMode.CUSTOM and not normalized_times:
            raise ValueError("custom_times must be nonempty in custom mode")
        if (self.quiet_start is None) != (self.quiet_end is None):
            raise ValueError("quiet_start and quiet_end must be provided together")
        if self.quiet_start is not None:
            _validate_local_time(self.quiet_start, "quiet_start")
        if self.quiet_end is not None:
            _validate_local_time(self.quiet_end, "quiet_end")
        if not isinstance(self.timezone, ZoneInfo):
            raise TypeError("timezone must be a ZoneInfo")


@dataclass(frozen=True, slots=True)
class DueSlot:
    """A display slot whose frame generation has become due."""

    display_at: datetime
    generation_at: datetime
    idempotency_key: str

    def __post_init__(self) -> None:
        _require_aware(self.display_at, "display_at")
        _require_aware(self.generation_at, "generation_at")


def due_slots(spec: ScheduleSpec, now: datetime, lookback_minutes: int = 5) -> list[DueSlot]:
    """Return slots generated from ``now-lookback`` through ``now``, inclusively."""
    _require_aware(now, "now")
    if lookback_minutes < 0:
        raise ValueError("lookback_minutes must be nonnegative")
    now_utc = now.astimezone(UTC)
    lower_utc = now_utc - timedelta(minutes=lookback_minutes)
    display_start = (lower_utc + GENERATION_LEAD).astimezone(spec.timezone)
    display_end = (now_utc + GENERATION_LEAD + timedelta(microseconds=1)).astimezone(spec.timezone)
    result: list[DueSlot] = []
    for display_at in compute_display_slots(spec, display_start, display_end):
        generation_utc = display_at.astimezone(UTC) - GENERATION_LEAD
        if lower_utc <= generation_utc <= now_utc:
            result.append(
                DueSlot(
                    display_at=display_at,
                    generation_at=generation_utc.astimezone(spec.timezone),
                    idempotency_key=slot_idempotency_key(display_at),
                )
            )
    return result


def slot_idempotency_key(display_at: datetime) -> str:
    """Build a timezone-independent stable key for a planned display instant."""
    _require_aware(display_at, "display_at")
    utc_display = display_at.astimezone(UTC)
    return f"display:{utc_display.strftime('%Y%m%dT%H%M%SZ')}"


def compute_display_slots(spec: ScheduleSpec, start: datetime, end: datetime) -> list[datetime]:
    """Return planned display times in the half-open instant range ``[start, end)``."""
    _require_aware(start, "start")
    _require_aware(end, "end")
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    if not spec.enabled or start_utc >= end_utc:
        return []

    local_start = start.astimezone(spec.timezone)
    local_end = end.astimezone(spec.timezone)
    first_date = local_start.date() - timedelta(days=1)
    last_date = local_end.date() + timedelta(days=1)
    slots: list[datetime] = []
    current_date = first_date
    while current_date <= last_date:
        for local_time in _times_for_date(spec):
            if not _is_quiet(local_time, spec.quiet_start, spec.quiet_end):
                slots.extend(_aware_wall_instants(current_date, local_time, spec.timezone))
        current_date += timedelta(days=1)
    bounded = (slot for slot in slots if start_utc <= slot.astimezone(UTC) < end_utc)
    return sorted(bounded, key=lambda slot: slot.astimezone(UTC))


def _aware_wall_instants(
    local_date: date, local_time: time, timezone: ZoneInfo
) -> tuple[datetime, ...]:
    """Resolve a wall time, omitting gaps and retaining both sides of a fold."""
    wall = datetime.combine(local_date, local_time)
    by_instant: dict[datetime, datetime] = {}
    for fold in (0, 1):
        candidate = wall.replace(tzinfo=timezone, fold=fold)
        instant = candidate.astimezone(UTC)
        round_trip = instant.astimezone(timezone)
        if round_trip.replace(tzinfo=None) == wall and round_trip.fold == fold:
            by_instant[instant] = candidate
    return tuple(by_instant[instant] for instant in sorted(by_instant))


def _times_for_date(spec: ScheduleSpec) -> tuple[time, ...]:
    if spec.mode is ScheduleMode.CUSTOM:
        return spec.custom_times
    anchor_minutes = spec.anchor_time.hour * 60 + spec.anchor_time.minute
    first_minutes = anchor_minutes % spec.interval_minutes
    return tuple(
        time(hour=minute // 60, minute=minute % 60)
        for minute in range(first_minutes, 24 * 60, spec.interval_minutes)
    )


def _is_quiet(value: time, start: time | None, end: time | None) -> bool:
    if start is None or end is None or start == end:
        return False
    if start < end:
        return start <= value < end
    return value >= start or value < end


def _validate_local_time(value: time, name: str) -> None:
    if not isinstance(value, time):
        raise TypeError(f"{name} must contain time values")
    if value.tzinfo is not None or value.second or value.microsecond:
        raise ValueError(f"{name} must contain naive HH:MM values")


def _require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
