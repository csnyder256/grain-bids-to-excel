"""Automatic-scan scheduling. Consumes the settings document's `schedule`
block and enqueues a scan through the ScanManager - same single lane as
manual scans, so they can never overlap. Runs only while the app is open
(the Settings page says so honestly)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

log = logging.getLogger("bidboard.scheduler")

JOB_ID = "scheduled-scan"

_DOW = {"mon": "mon", "tue": "tue", "wed": "wed", "thu": "thu",
        "fri": "fri", "sat": "sat", "sun": "sun"}


class ScanScheduler:
    def __init__(self, scan_manager):
        self.scan_manager = scan_manager
        self._sched = BackgroundScheduler(daemon=True)
        self._sched.start(paused=False)

    def apply(self, settings: dict) -> None:
        """(Re)arm the cron job from the settings document. Called at
        startup and whenever the schedule settings change."""
        schedule = settings.get("schedule", {})
        existing = self._sched.get_job(JOB_ID)
        if existing:
            existing.remove()
        if not schedule.get("enabled"):
            log.info("automatic scans off")
            return
        day = schedule.get("day_of_week", "daily")
        trigger = CronTrigger(
            day_of_week=_DOW.get(day) if day != "daily" else None,
            hour=int(schedule.get("hour", 7)),
            minute=int(schedule.get("minute", 30)),
        )
        self._sched.add_job(
            self._fire, trigger, id=JOB_ID, replace_existing=True,
            misfire_grace_time=3600,
        )
        log.info("automatic scans armed: %s at %02d:%02d",
                 day, schedule.get("hour", 7), schedule.get("minute", 30))

    def _fire(self) -> None:
        started = self.scan_manager.enqueue_scan(trigger="scheduled")
        if not started:
            log.info("scheduled scan skipped - one is already running")

    def next_run_time(self):
        job = self._sched.get_job(JOB_ID)
        return job.next_run_time if job else None

    def shutdown(self) -> None:
        try:
            self._sched.shutdown(wait=False)
        except Exception:
            pass
