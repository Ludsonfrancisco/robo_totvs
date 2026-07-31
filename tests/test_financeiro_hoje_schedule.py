from datetime import datetime

from flows.financeiro_hoje.schedule import SLOTS, next_run_at


EXPECTED = (
    "00:30", "06:20", "07:10", "07:50", "08:20", "08:50",
    "09:20", "09:50", "10:20", "10:50", "11:20", "11:50",
    "12:20", "12:50", "13:20", "13:50", "14:20", "14:50",
    "15:20", "16:10", "17:10", "18:10", "19:10", "20:10",
    "21:10", "22:00", "23:00",
)


def test_schedule_tem_exatamente_os_27_slots_aprovados():
    assert tuple(slot.strftime("%H:%M") for slot in SLOTS) == EXPECTED


def test_next_run_pula_para_o_proximo_dia_depois_das_23():
    now = datetime(2026, 7, 27, 23, 0, 1)

    assert next_run_at(now) == datetime(2026, 7, 28, 0, 30)
