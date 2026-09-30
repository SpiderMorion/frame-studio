from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from eink_frame.schedule import (
    GENERATION_LEAD,
    DueSlot,
    ScheduleMode,
    ScheduleSpec,
    compute_display_slots,
    due_slots,
    slot_idempotency_key,
)

MOSCOW = ZoneInfo("Europe/Moscow")


def test_interval_slots_repeat_around_anchor_within_half_open_window() -> None:
    spec = ScheduleSpec(
        mode=ScheduleMode.INTERVAL,
        interval_minutes=120,
        anchor_time=time(1, 30),
        timezone=MOSCOW,
    )

    slots = compute_display_slots(
        spec,
        datetime(2026, 8, 23, 0, 0, tzinfo=MOSCOW),
        datetime(2026, 8, 23, 6, 0, tzinfo=MOSCOW),
    )

    assert slots == [
        datetime(2026, 8, 23, 1, 30, tzinfo=MOSCOW),
        datetime(2026, 8, 23, 3, 30, tzinfo=MOSCOW),
        datetime(2026, 8, 23, 5, 30, tzinfo=MOSCOW),
    ]


def test_schedule_spec_is_immutable() -> None:
    spec = ScheduleSpec(mode=ScheduleMode.INTERVAL, timezone=MOSCOW)

    with pytest.raises(FrozenInstanceError):
        spec.enabled = False  # type: ignore[misc]


@pytest.mark.parametrize("interval", [29, 1441])
def test_interval_minutes_must_be_between_30_and_1440(interval: int) -> None:
    with pytest.raises(ValueError, match="interval_minutes"):
        ScheduleSpec(
            mode=ScheduleMode.INTERVAL,
            interval_minutes=interval,
            timezone=MOSCOW,
        )


def test_custom_times_are_unique_sorted_and_respect_overnight_quiet_hours() -> None:
    spec = ScheduleSpec(
        mode=ScheduleMode.CUSTOM,
        custom_times=(time(23, 0), time(7, 0), time(12, 0), time(7, 0)),
        quiet_start=time(22, 0),
        quiet_end=time(7, 0),
        timezone=MOSCOW,
    )

    slots = compute_display_slots(
        spec,
        datetime(2026, 8, 23, 0, 0, tzinfo=MOSCOW),
        datetime(2026, 8, 24, 0, 0, tzinfo=MOSCOW),
    )

    assert spec.custom_times == (time(7, 0), time(12, 0), time(23, 0))
    assert slots == [
        datetime(2026, 8, 23, 7, 0, tzinfo=MOSCOW),
        datetime(2026, 8, 23, 12, 0, tzinfo=MOSCOW),
    ]


def test_equal_quiet_boundaries_do_not_exclude_slots() -> None:
    spec = ScheduleSpec(
        mode=ScheduleMode.CUSTOM,
        custom_times=(time(9, 0),),
        quiet_start=time(9, 0),
        quiet_end=time(9, 0),
        timezone=MOSCOW,
    )

    assert compute_display_slots(
        spec,
        datetime(2026, 8, 23, 0, 0, tzinfo=MOSCOW),
        datetime(2026, 8, 24, 0, 0, tzinfo=MOSCOW),
    ) == [datetime(2026, 8, 23, 9, 0, tzinfo=MOSCOW)]


def test_custom_mode_requires_at_least_one_time() -> None:
    with pytest.raises(ValueError, match="custom_times"):
        ScheduleSpec(mode=ScheduleMode.CUSTOM, timezone=MOSCOW)


def test_disabled_schedule_has_no_slots() -> None:
    spec = ScheduleSpec(mode=ScheduleMode.INTERVAL, enabled=False, timezone=MOSCOW)

    assert (
        compute_display_slots(
            spec,
            datetime(2026, 8, 23, 0, 0, tzinfo=MOSCOW),
            datetime(2026, 8, 24, 0, 0, tzinfo=MOSCOW),
        )
        == []
    )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (datetime(2026, 8, 23), datetime(2026, 8, 24, tzinfo=MOSCOW)),  # noqa: DTZ001
        (datetime(2026, 8, 23, tzinfo=MOSCOW), datetime(2026, 8, 24)),  # noqa: DTZ001
    ],
)
def test_display_slot_bounds_must_be_timezone_aware(start: datetime, end: datetime) -> None:
    spec = ScheduleSpec(mode=ScheduleMode.INTERVAL, timezone=MOSCOW)

    with pytest.raises(ValueError, match="timezone-aware"):
        compute_display_slots(spec, start, end)


def test_due_slots_use_fixed_t_minus_15_and_inclusive_lookback_window() -> None:
    spec = ScheduleSpec(
        mode=ScheduleMode.CUSTOM,
        custom_times=(time(10, 9), time(10, 10), time(10, 15), time(10, 16)),
        timezone=MOSCOW,
    )
    now = datetime(2026, 8, 23, 10, 0, tzinfo=MOSCOW)

    result = due_slots(spec, now, lookback_minutes=5)

    assert GENERATION_LEAD.total_seconds() == 15 * 60
    assert result == [
        DueSlot(
            display_at=datetime(2026, 8, 23, 10, 10, tzinfo=MOSCOW),
            generation_at=datetime(2026, 8, 23, 9, 55, tzinfo=MOSCOW),
            idempotency_key="display:20260823T071000Z",
        ),
        DueSlot(
            display_at=datetime(2026, 8, 23, 10, 15, tzinfo=MOSCOW),
            generation_at=now,
            idempotency_key="display:20260823T071500Z",
        ),
    ]


def test_slot_idempotency_key_uses_the_display_utc_instant() -> None:
    display_moscow = datetime(2026, 8, 23, 10, 15, tzinfo=MOSCOW)

    assert slot_idempotency_key(display_moscow) == slot_idempotency_key(
        display_moscow.astimezone(ZoneInfo("UTC"))
    )


def test_due_slot_timestamps_must_be_aware() -> None:
    aware = datetime(2026, 8, 23, 10, 15, tzinfo=MOSCOW)

    with pytest.raises(ValueError, match="timezone-aware"):
        DueSlot(
            display_at=datetime(2026, 8, 23, 10, 15),  # noqa: DTZ001
            generation_at=aware,
            idempotency_key="display:test",
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        DueSlot(
            display_at=aware,
            generation_at=datetime(2026, 8, 23, 10, 0),  # noqa: DTZ001
            idempotency_key="display:test",
        )


def test_due_slots_require_aware_now_and_nonnegative_lookback() -> None:
    spec = ScheduleSpec(mode=ScheduleMode.INTERVAL, timezone=MOSCOW)

    with pytest.raises(ValueError, match="timezone-aware"):
        due_slots(spec, datetime(2026, 8, 23, 10, 0))  # noqa: DTZ001
    with pytest.raises(ValueError, match="lookback_minutes"):
        due_slots(spec, datetime(2026, 8, 23, 10, 0, tzinfo=MOSCOW), -1)


def test_nonexistent_dst_wall_time_is_not_a_display_slot() -> None:
    berlin = ZoneInfo("Europe/Berlin")
    spec = ScheduleSpec(
        mode=ScheduleMode.CUSTOM,
        custom_times=(time(2, 30),),
        timezone=berlin,
    )

    assert (
        compute_display_slots(
            spec,
            datetime(2026, 3, 29, 0, 0, tzinfo=ZoneInfo("UTC")),
            datetime(2026, 3, 30, 0, 0, tzinfo=ZoneInfo("UTC")),
        )
        == []
    )


def test_ambiguous_dst_wall_time_represents_both_real_instants() -> None:
    berlin = ZoneInfo("Europe/Berlin")
    spec = ScheduleSpec(
        mode=ScheduleMode.CUSTOM,
        custom_times=(time(2, 30),),
        timezone=berlin,
    )

    assert compute_display_slots(
        spec,
        datetime(2026, 10, 25, 0, 0, tzinfo=ZoneInfo("UTC")),
        datetime(2026, 10, 25, 3, 0, tzinfo=ZoneInfo("UTC")),
    ) == [
        datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=0),
        datetime(2026, 10, 25, 2, 30, tzinfo=berlin, fold=1),
    ]
