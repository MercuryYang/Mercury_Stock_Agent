from datetime import timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from trading_agent.core.models import stable_id, utcnow
from trading_agent.scheduler.calendar import NY, calendar, session_bounds


def due_tasks(now, config):
    local = now.astimezone(NY)
    bounds = session_bounds(local.date())
    if bounds is None:
        return []
    opening, closing = bounds
    tasks = []
    if opening - timedelta(minutes=config["premarket_minutes_before_open"]) <= now < opening:
        tasks.append(("premarket", str(local.date())))
    if opening <= now < closing:
        bucket = int((now - opening).total_seconds() // (60 * config["intraday_interval_minutes"]))
        tasks.append(("event", f"{local.date()}:{bucket}"))
    if (
        closing + timedelta(minutes=config["postmarket_minutes_after_close"])
        <= now
        < closing + timedelta(hours=3)
    ):
        tasks.append(("postmarket", str(local.date())))
        next_day = calendar(local.year).next_session(str(local.date())).date()
        if next_day.isocalendar().week != local.date().isocalendar().week:
            tasks.append(("weekly", str(local.date())))
    return tasks


class SchedulerService:
    def __init__(self, workflow):
        self.workflow = workflow
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.leader = None

    async def tick(self, now=None):
        now = now or utcnow()
        store = self.workflow.store
        for task, period in due_tasks(now, self.workflow.config.scheduler):
            key = stable_id("scheduled-job", task, period)
            state = store.get("audit_logs", key)
            if state and (state["status"] == "SUCCESS" or state["attempts"] >= 3):
                continue
            attempts = (state or {}).get("attempts", 0) + 1
            result = await self.workflow.run(task, now)
            store.put(
                "audit_logs",
                key,
                {
                    "operation": "SCHEDULED_JOB",
                    "task": task,
                    "period": period,
                    "status": result["status"],
                    "attempts": attempts,
                    "run_id": result["run_id"],
                },
                replace=True,
            )
        await self.workflow.notifications.flush()

    def start(self):
        self.leader = self.workflow.store.singleton("scheduler-leader")
        self.leader.__enter__()
        self.scheduler.add_job(
            self.tick,
            "interval",
            seconds=self.workflow.config.scheduler["poll_seconds"],
            max_instances=1,
            coalesce=True,
            misfire_grace_time=60,
            next_run_time=utcnow(),
        )
        self.scheduler.start()

    def stop(self):
        self.scheduler.shutdown(wait=False)
        if self.leader:
            self.leader.__exit__(None, None, None)
