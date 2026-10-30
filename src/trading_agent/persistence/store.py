from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.pool import StaticPool

from trading_agent.core.models import stable_id, utcnow
from trading_agent.persistence.schema import TABLES


class Store:
    def __init__(self, url: str):
        options = {}
        if url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False, "timeout": 30}
            if ":memory:" in url:
                options["poolclass"] = StaticPool
            elif url.startswith("sqlite:///"):
                Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(url, pool_pre_ping=True, **options)

    def put(
        self,
        name,
        key,
        payload,
        *,
        symbol=None,
        run_id=None,
        source="SYSTEM",
        version="v1",
        replace=False,
        connection=None,
    ):
        table = TABLES[name]
        values = dict(
            id=key,
            created_at=utcnow(),
            symbol=symbol,
            run_id=run_id,
            source=source,
            version=version,
            payload=payload,
        )
        insert = pg_insert if self.engine.dialect.name == "postgresql" else sqlite_insert
        stmt = insert(table).values(**values)
        stmt = (
            stmt.on_conflict_do_update(index_elements=[table.c.id], set_=values)
            if replace
            else stmt.on_conflict_do_nothing(index_elements=[table.c.id])
        )
        # psycopg may report rowcount=-1 for INSERT; RETURNING is authoritative on both backends.
        stmt = stmt.returning(table.c.id)
        if connection is not None:
            return connection.execute(stmt).scalar_one_or_none() is not None
        with self.engine.begin() as conn:
            return conn.execute(stmt).scalar_one_or_none() is not None

    def get(self, name, key):
        table = TABLES[name]
        with self.engine.connect() as conn:
            return conn.execute(select(table.c.payload).where(table.c.id == key)).scalar_one_or_none()

    def list(self, name, symbol=None, limit=100, run_id=None, status_not=None):
        table = TABLES[name]
        query = select(table.c.payload).order_by(table.c.created_at.desc(), table.c.id).limit(limit)
        if symbol:
            query = query.where(table.c.symbol == symbol)
        if run_id:
            query = query.where(table.c.run_id == run_id)
        if status_not:
            query = query.where(table.c.payload["status"].as_string() != status_not)
        with self.engine.connect() as conn:
            return list(conn.execute(query).scalars())

    def replace_positions(self, portfolio, connection):
        # A complete successful account snapshot replaces the current mirror atomically.
        connection.execute(delete(TABLES["positions"]))
        for p in portfolio.positions:
            self.put(
                "positions",
                f"{p.account_ref}:{p.symbol}",
                p.model_dump(mode="json"),
                symbol=p.symbol,
                source=p.source,
                connection=connection,
            )

    def healthy(self):
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            conn.execute(select(TABLES["audit_logs"].c.id).limit(1))

    @contextmanager
    def singleton(self, name="workflow"):
        """Cross-process lock: PostgreSQL session advisory lock; local SQLite uses OS file lock."""
        if self.engine.dialect.name == "postgresql":
            with self.engine.connect() as conn:
                locked = conn.execute(
                    text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": name}
                ).scalar()
                if not locked:
                    raise RuntimeError("WORKFLOW_BUSY")
                try:
                    yield
                finally:
                    conn.execute(text("SELECT pg_advisory_unlock(hashtext(:name))"), {"name": name})
        else:
            # File locks are released by the OS if the process exits; no expired lease can overlap a run.
            import portalocker

            path = self.engine.url.database
            lockfile = (
                Path(f"{path}.{stable_id(name)}.lock")
                if path and path != ":memory:"
                else Path(f"data/test.{stable_id(name)}.lock")
            )
            lockfile.parent.mkdir(parents=True, exist_ok=True)
            try:
                with portalocker.Lock(lockfile, timeout=0):
                    yield
            except portalocker.exceptions.LockException as exc:
                raise RuntimeError("WORKFLOW_BUSY") from exc
