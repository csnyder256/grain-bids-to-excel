"""Scheduler wiring: the schedule settings block must actually arm a job
that enqueues scans (refute finding: it was UI-only before)."""
import time

import pytest

from bidboard.services.scheduler import ScanScheduler


class FakeManager:
    def __init__(self):
        self.calls = []

    def enqueue_scan(self, trigger="manual", company_ids=None):
        self.calls.append(trigger)
        return True


@pytest.fixture
def scheduler():
    mgr = FakeManager()
    s = ScanScheduler(mgr)
    yield s, mgr
    s.shutdown()


def _settings(enabled=True, day="daily", hour=7, minute=30):
    return {"schedule": {"enabled": enabled, "day_of_week": day,
                         "hour": hour, "minute": minute}}


def test_apply_arms_job_when_enabled(scheduler):
    s, _ = scheduler
    s.apply(_settings(enabled=True))
    assert s.next_run_time() is not None


def test_apply_disarms_when_disabled(scheduler):
    s, _ = scheduler
    s.apply(_settings(enabled=True))
    assert s.next_run_time() is not None
    s.apply(_settings(enabled=False))
    assert s.next_run_time() is None


def test_reapply_replaces_schedule(scheduler):
    s, _ = scheduler
    s.apply(_settings(hour=7))
    first = s.next_run_time()
    s.apply(_settings(hour=9))
    second = s.next_run_time()
    assert first != second


def test_fire_enqueues_scheduled_scan(scheduler):
    s, mgr = scheduler
    s._fire()
    assert mgr.calls == ["scheduled"]


def test_weekday_schedule_arms(scheduler):
    s, _ = scheduler
    s.apply(_settings(day="mon", hour=8, minute=0))
    nxt = s.next_run_time()
    assert nxt is not None
    assert nxt.weekday() == 0  # Monday
