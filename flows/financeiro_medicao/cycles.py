from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

Mode = Literal["current", "final"]


@dataclass(frozen=True)
class CycleWindow:
    cycle_start: date
    cycle_close: date
    query_start: date
    query_end: date
    mode: Mode

    @property
    def cycle_id(self) -> str:
        return f"{self.cycle_start.isoformat()}--{self.cycle_close.isoformat()}"


def _previous_month(day: date) -> tuple[int, int]:
    previous = day.replace(day=1) - timedelta(days=1)
    return previous.year, previous.month


def _next_month(year: int, month: int) -> tuple[int, int]:
    if month == 12:
        return year + 1, 1
    return year, month + 1


def window_for(day: date) -> CycleWindow:
    day = date(day.year, day.month, day.day)

    if day.day <= 11:
        start_year, start_month = _previous_month(day)
    else:
        start_year, start_month = day.year, day.month

    cycle_start = date(start_year, start_month, 11)
    close_year, close_month = _next_month(start_year, start_month)
    cycle_close = date(close_year, close_month, 10)

    if day.day == 11:
        mode: Mode = "final"
        query_end = day - timedelta(days=1)
    else:
        mode = "current"
        query_end = day

    return CycleWindow(
        cycle_start=cycle_start,
        cycle_close=cycle_close,
        query_start=cycle_start,
        query_end=query_end,
        mode=mode,
    )
