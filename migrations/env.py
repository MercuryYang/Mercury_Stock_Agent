from alembic import context
from sqlalchemy import create_engine

from trading_agent.core.config import Settings
from trading_agent.persistence.schema import metadata

url = context.config.attributes.get("database_url") or Settings().database_url
if context.is_offline_mode():
    context.configure(url=url, target_metadata=metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=metadata)
        with context.begin_transaction():
            context.run_migrations()
