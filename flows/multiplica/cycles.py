from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class CycleWindow:
    cycle_start: date
    cycle_close: date
    query_end: date

    @property
    def query_start(self) -> date:
        return self.cycle_start


def _shift_month(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, day.day)


def _current_window(day: date) -> CycleWindow:
    start_month = day.replace(day=1)
    if day.day <= 10:
        start_month = _shift_month(start_month, -1)
    cycle_start = start_month.replace(day=11)
    cycle_close = _shift_month(start_month, 1).replace(day=10)
    return CycleWindow(cycle_start, cycle_close, min(day, cycle_close))


def collection_windows(day: date) -> list[CycleWindow]:
    current = _current_window(day)
    windows = [current]
    if day.day in range(11, 17):
        previous_start = _shift_month(current.cycle_start, -1)
        windows.append(
            CycleWindow(
                previous_start,
                current.cycle_start - timedelta(days=1),
                current.cycle_start - timedelta(days=1),
            )
        )
    return windows
