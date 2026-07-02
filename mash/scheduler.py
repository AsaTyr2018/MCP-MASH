from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from croniter import croniter

from .config import settings
from .db import db
from .runner import run_script, utc_now
from .scripts import list_scripts


class Scheduler:
    def __init__(self) -> None:
        self.running = False
        self._task: asyncio.Task[None] | None = None
        self.last_tick_at = ""
        self.last_error = ""

    async def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self.running:
            self.last_tick_at = utc_now()
            try:
                await asyncio.to_thread(self.tick)
                self.last_error = ""
            except Exception as exc:
                self.last_error = str(exc)
            await asyncio.sleep(settings.scheduler_interval_seconds)

    def tick(self) -> None:
        now = datetime.now(timezone.utc)
        minute_key = now.strftime("%Y-%m-%dT%H:%MZ")
        for script in list_scripts():
            schedule = str(script.get("schedule", "")).strip()
            if not script.get("enabled") or not schedule:
                continue
            if script.get("last_scheduled_at") == minute_key:
                continue
            if croniter.match(schedule, now):
                run_script(str(script["id"]), dry_run=False, reason="schedule")
                with db() as conn:
                    conn.execute("UPDATE scripts SET last_scheduled_at = ? WHERE id = ?", (minute_key, script["id"]))

    def status(self) -> dict[str, object]:
        return {
            "running": self.running,
            "interval_seconds": settings.scheduler_interval_seconds,
            "last_tick_at": self.last_tick_at,
            "last_error": self.last_error,
        }


scheduler = Scheduler()
