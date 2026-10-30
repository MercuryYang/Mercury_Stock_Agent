import argparse
import asyncio
import getpass
import json
from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from trading_agent.agents.probe import probe
from trading_agent.core.config import Config, Settings
from trading_agent.core.logging import configure_logging
from trading_agent.persistence.store import Store
from trading_agent.workflows.paper import evaluate_saved
from trading_agent.workflows.reports import render_report
from trading_agent.workflows.service import Workflow


def migrate(settings):
    if settings.database_url.startswith("sqlite:///"):
        Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    cfg = AlembicConfig("alembic.ini")
    cfg.attributes["database_url"] = settings.database_url
    command.upgrade(cfg, "head")


async def async_command(args, settings, config):
    if args.command == "probe-llm":
        result = await probe(settings, config.app["llm"])
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if result["passed"] else 1
    if args.command == "schwab-auth":
        from trading_agent.connectors.schwab.auth import SchwabAuth

        auth = SchwabAuth(settings)
        try:
            if settings.schwab_refresh_token_max_age_seconds is None:
                raise ValueError(
                    "Confirm token lifetime in Developer Portal and configure it before authorizing"
                )
            print(auth.authorization_url())
            url = getpass.getpass("Paste full redirect URL (hidden): ")
            await auth.exchange_redirect(url)
            print("Encrypted OAuth credentials saved.")
        finally:
            await auth.client.aclose()
        return 0
    if args.command == "demo":
        settings = settings.model_copy(update={"data_mode": "demo", "llm_enabled": args.llm})
        migrate(settings)
    store = Store(settings.database_url)
    workflow = Workflow(settings, config, store)
    try:
        if args.command == "sync":
            snapshot = await workflow.sync()
            print(
                json.dumps(
                    {
                        "snapshot_id": snapshot.snapshot_id,
                        "reconciliation": snapshot.reconciliation,
                        "positions": len(snapshot.positions),
                        "issues": snapshot.issues,
                    }
                )
            )
            return 0
        result = await workflow.run("event" if args.command == "events" else "daily")
        print(
            json.dumps(
                {k: v for k, v in result.items() if k not in ("config", "indicator_parameters")}, indent=2
            )
        )
        return 0 if result["status"] == "SUCCESS" else 1
    finally:
        await workflow.aclose()
        store.engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="Mercury read-only trading research service")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init-db", "run-daily", "events", "sync", "doctor", "schwab-auth"):
        commands.add_parser(name)
    demo = commands.add_parser("demo")
    demo.add_argument("--llm", action="store_true", help="Use configured LLM with synthetic data")
    probe_cmd = commands.add_parser("probe-llm")
    probe_cmd.add_argument("--output")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    paper = commands.add_parser("paper-evaluate")
    paper.add_argument("--cost-bps", nargs="+", type=float, required=True)
    report = commands.add_parser("report")
    report.add_argument("--output", required=True)
    args = parser.parse_args()
    configure_logging()
    settings, config = Settings(), None
    config = Config(settings)
    if args.command == "init-db":
        migrate(settings)
        print("Database migrated to head.")
        return
    if args.command == "doctor":
        print(
            json.dumps(
                {
                    "mode": settings.data_mode,
                    "llm_configured": bool(settings.llm_base_url and settings.llm_model),
                    "llm_enabled": settings.llm_enabled,
                    "model": settings.llm_model,
                    "admin_key_configured": bool(settings.admin_api_key.get_secret_value()),
                    "schwab_configured": bool(settings.schwab_client_id.get_secret_value()),
                    "risk_missing": config.risk.missing(),
                    "order_execution": False,
                },
                indent=2,
            )
        )
        return
    if args.command == "serve":
        import uvicorn

        uvicorn.run("trading_agent.main:app", host=args.host, port=args.port, workers=1, access_log=False)
        return
    if args.command == "report":
        store = Store(settings.database_url)
        try:
            latest = {}
            for d in store.list("decisions", limit=10000):
                latest.setdefault(d["symbol"], d["decision_id"])
            body = "\n\n---\n\n".join(render_report(store, key) for key in latest.values())
            target = Path(args.output)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body or "暂无决策记录。\n", encoding="utf-8")
            print(f"Report saved to {target}")
        finally:
            store.engine.dispose()
        return
    if args.command == "paper-evaluate":
        if any(cost < 0 for cost in args.cost_bps):
            parser.error("Cost assumptions cannot be negative")
        store = Store(settings.database_url)
        try:
            print(json.dumps(evaluate_saved(store, args.cost_bps), indent=2))
        finally:
            store.engine.dispose()
        return
    try:
        raise SystemExit(asyncio.run(async_command(args, settings, config)))
    except (ValueError, RuntimeError) as exc:
        # Never print exception bodies from remote APIs or configuration secrets.
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
