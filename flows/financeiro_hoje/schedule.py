from datetime import datetime, time, timedelta


SLOTS = tuple(time.fromisoformat(value) for value in (
    "00:30", "06:20", "07:10", "07:50", "08:20", "08:50",
    "09:20", "09:50", "10:20", "10:50", "11:20", "11:50",
    "12:20", "12:50", "13:20", "13:50", "14:20", "14:50",
    "15:20", "16:10", "17:10", "18:10", "19:10", "20:10",
    "21:10", "22:00", "23:00",
))


def next_run_at(now: datetime) -> datetime:
    for slot in SLOTS:
        candidate = now.replace(
            hour=slot.hour, minute=slot.minute, second=0, microsecond=0
        )
        if candidate > now:
            return candidate
    tomorrow = now + timedelta(days=1)
    first = SLOTS[0]
    return tomorrow.replace(
        hour=first.hour, minute=first.minute, second=0, microsecond=0
    )
