from datetime import datetime, timedelta

import pytest

from flows.routerbox_coordination import (
    AlreadyLocked,
    SiteLock,
    finance_may_start,
    wait_for_site_lock,
)


class FakeClock:
    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += timedelta(seconds=seconds)


def test_lock_impede_dois_usuarios_do_routerbox(tmp_path):
    first = SiteLock(tmp_path / "routerbox-site.lock", owner="backlog")
    with first:
        with pytest.raises(AlreadyLocked):
            with SiteLock(tmp_path / "routerbox-site.lock", owner="financeiro"):
                pass


def test_financeiro_espera_quando_backlog_esta_proximo():
    now = datetime(2026, 7, 27, 9, 29)
    assert finance_may_start(now, now + timedelta(seconds=60)) is False
    assert finance_may_start(now, now + timedelta(seconds=91)) is True


def test_espera_pelo_lock_consumindo_o_deadline(tmp_path):
    clock = FakeClock(datetime(2026, 7, 27, 8, 20))
    path = tmp_path / "routerbox-site.lock"
    with SiteLock(path, owner="backlog"):
        with pytest.raises(AlreadyLocked, match="deadline"):
            with wait_for_site_lock(
                path,
                owner="financeiro",
                deadline=clock.now() + timedelta(seconds=5),
                now=clock.now,
                sleep=clock.sleep,
            ):
                pass
    assert clock.now() == datetime(2026, 7, 27, 8, 20, 5)
