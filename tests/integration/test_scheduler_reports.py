from datetime import UTC, datetime

from trading_agent.scheduler.service import SchedulerService
from trading_agent.workflows.reports import render_report
from trading_agent.workflows.service import Workflow


async def test_scheduled_job_idempotency(settings, config, store):
    workflow = Workflow(settings, config, store)
    scheduler = SchedulerService(workflow)
    now = datetime(2026, 10, 29, 13, 0, tzinfo=UTC)
    await scheduler.tick(now)
    count = len(store.list("workflow_runs"))
    await scheduler.tick(now)
    assert len(store.list("workflow_runs")) == count == 1
    await workflow.aclose()


async def test_report_has_sources_and_omits_triggers_for_stale_data(settings, config, store):
    workflow = Workflow(settings, config, store)
    result = await workflow.run()
    report = render_report(store, result["decision_ids"][0])
    assert "SYNTHETIC_FIXTURE" in report and "NO_ACTION" in report and "未生成" in report
    assert "关键价位区间" not in report
    await workflow.aclose()
